"""Core logic for the US Census variable extractor.

Talks to the official Census Data API (https://api.census.gov/data), which serves
the same tables you see on data.census.gov.

Main pieces:
    CensusClient   - metadata lookups (datasets, variables, tables, geographies)
                     and data requests (auto-chunked past the 50-variable limit)
    ExtractRequest - a description of what you want (loadable from YAML/JSON)
    run_extract    - turns an ExtractRequest into a clean pandas DataFrame
    write_outputs  - writes the CSV plus a data-dictionary CSV
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("census_extractor")

API_BASE = "https://api.census.gov/data"
MAX_VARS_PER_CALL = 50  # Census API hard limit per request (NAME counts toward it)

# Special values the Census uses in place of real estimates (e.g. "estimate not
# available", "sample too small"). They are turned into blank cells.
SENTINELS = [-999999999, -888888888, -666666666, -555555555, -333333333, -222222222]

# Pseudo-variables that appear in variables.json but are query parameters.
RESERVED = {"for", "in", "ucgid", "time", "GEO_ID"}

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "census_extractor"


class CensusAPIError(RuntimeError):
    """Raised for problems talking to the Census API or invalid requests."""


class CensusNotFound(CensusAPIError):
    """HTTP 404: the dataset/year (or metadata file) doesn't exist on the API."""


def _html_to_text(text: str, limit: int = 300) -> str:
    """Reduce an HTML error page to its readable text (drops <style>/<script> blocks)."""
    text = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _public_url(url: str, params: dict | None) -> str:
    """The request URL with the API key removed, safe to show in error messages."""
    shown = {k: v for k, v in (params or {}).items() if k != "key"}
    return requests.Request("GET", url, params=shown).prepare().url or url


# --------------------------------------------------------------------------- #
# State lookup (lets users type "Florida" or "FL" instead of "12")
# --------------------------------------------------------------------------- #
STATES: dict[str, tuple[str, str]] = {
    "AL": ("01", "Alabama"), "AK": ("02", "Alaska"), "AZ": ("04", "Arizona"),
    "AR": ("05", "Arkansas"), "CA": ("06", "California"), "CO": ("08", "Colorado"),
    "CT": ("09", "Connecticut"), "DE": ("10", "Delaware"),
    "DC": ("11", "District of Columbia"), "FL": ("12", "Florida"),
    "GA": ("13", "Georgia"), "HI": ("15", "Hawaii"), "ID": ("16", "Idaho"),
    "IL": ("17", "Illinois"), "IN": ("18", "Indiana"), "IA": ("19", "Iowa"),
    "KS": ("20", "Kansas"), "KY": ("21", "Kentucky"), "LA": ("22", "Louisiana"),
    "ME": ("23", "Maine"), "MD": ("24", "Maryland"), "MA": ("25", "Massachusetts"),
    "MI": ("26", "Michigan"), "MN": ("27", "Minnesota"), "MS": ("28", "Mississippi"),
    "MO": ("29", "Missouri"), "MT": ("30", "Montana"), "NE": ("31", "Nebraska"),
    "NV": ("32", "Nevada"), "NH": ("33", "New Hampshire"), "NJ": ("34", "New Jersey"),
    "NM": ("35", "New Mexico"), "NY": ("36", "New York"),
    "NC": ("37", "North Carolina"), "ND": ("38", "North Dakota"), "OH": ("39", "Ohio"),
    "OK": ("40", "Oklahoma"), "OR": ("41", "Oregon"), "PA": ("42", "Pennsylvania"),
    "RI": ("44", "Rhode Island"), "SC": ("45", "South Carolina"),
    "SD": ("46", "South Dakota"), "TN": ("47", "Tennessee"), "TX": ("48", "Texas"),
    "UT": ("49", "Utah"), "VT": ("50", "Vermont"), "VA": ("51", "Virginia"),
    "WA": ("53", "Washington"), "WV": ("54", "West Virginia"),
    "WI": ("55", "Wisconsin"), "WY": ("56", "Wyoming"), "PR": ("72", "Puerto Rico"),
}
_STATE_BY_NAME = {name.lower(): fips for fips, name in STATES.values()}


def resolve_state(value: str | int) -> str:
    """Convert '12', 12, 'FL', 'Florida' or 'FL,GA' into FIPS codes ('12', '12,13').
    '*' is passed through unchanged."""
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    out = []
    for p in parts:
        if p == "*":
            out.append("*")
        elif p.isdigit():
            out.append(p.zfill(2))
        elif p.upper() in STATES:
            out.append(STATES[p.upper()][0])
        elif p.lower() in _STATE_BY_NAME:
            out.append(_STATE_BY_NAME[p.lower()])
        else:
            raise CensusAPIError(f"Unknown state: {p!r}. Use a name, 2-letter code or FIPS.")
    return ",".join(out)


def _year_ranges(years: list[int]) -> str:
    """[2009, 2010, 2011, 2015] -> '2009-2011, 2015'"""
    out, start, prev = [], None, None
    for y in sorted(years):
        if start is None:
            start = prev = y
        elif y == prev + 1:
            prev = y
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = y
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)


def clean_label(label: str) -> str:
    """'Estimate!!Total:!!Male:' -> 'Estimate > Total > Male'"""
    parts = [p.strip().rstrip(":") for p in str(label).split("!!")]
    return " > ".join(p for p in parts if p)


def _is_annotation(label: str) -> bool:
    return str(label).lower().startswith("annotation of")


def build_in_clause(within: dict[str, Any] | None) -> str | None:
    """{'state': 'FL', 'county': '*'} -> 'state:12 county:*'"""
    if not within:
        return None
    parts = []
    for level, codes in within.items():
        codes = str(codes).strip()
        if level.lower() == "state":
            codes = resolve_state(codes)
        if not codes:
            raise CensusAPIError(f"No value given for parent geography {level!r}.")
        parts.append(f"{level}:{codes}")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5, backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "census-extractor/1.0"
    return s


class CensusClient:
    """Thin, cached wrapper around the Census Data API."""

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        timeout: int = 120,
        base_url: str = API_BASE,
        cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
    ):
        self.api_key = (api_key or os.environ.get("CENSUS_API_KEY") or "").strip() or None
        self.session = session or _make_session()
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._mem: dict[str, Any] = {}

    # ---- low-level -------------------------------------------------------
    def _request(self, url: str, params: dict | None = None, require_key: bool = True) -> Any:
        params = dict(params or {})
        if self.api_key:
            params["key"] = self.api_key
        elif require_key:
            raise CensusAPIError(
                "A Census API key is required. Get a free one at "
                "https://api.census.gov/data/key_signup.html and set the "
                "CENSUS_API_KEY environment variable (or pass --key)."
            )
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise CensusAPIError(f"Could not reach the Census API: {e}") from e

        if resp.status_code == 204:  # valid query, no matching rows
            return []
        text = resp.text or ""
        shown_url = _public_url(url, params)
        if resp.status_code == 404:
            raise CensusNotFound(
                f"Not found on the Census API (HTTP 404): {shown_url}\n"
                "This usually means that dataset isn't published for that year "
                "(for example, ACS 5-year data stops at the latest 5-year release)."
            )
        if resp.status_code != 200:
            raise CensusAPIError(
                f"Census API returned HTTP {resp.status_code}: {_html_to_text(text, 400)}\n"
                f"Request: {shown_url}"
            )
        try:
            return resp.json()
        except ValueError:
            snippet = _html_to_text(text)
            if "key" in snippet.lower():
                raise CensusAPIError(
                    f"The Census API rejected the API key: {snippet}. Check the key, and "
                    "make sure you activated it from the confirmation email."
                )
            raise CensusAPIError(
                f"Unexpected (non-JSON) response from Census API: {snippet}\nRequest: {shown_url}"
            )

    def _cached_metadata(self, url: str) -> Any:
        """Fetch metadata JSON with in-memory + on-disk caching (metadata rarely changes)."""
        if url in self._mem:
            return self._mem[url]
        path = None
        if self.cache_dir:
            path = self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self._mem[url] = data
                    return data
                except (OSError, ValueError):
                    pass
        data = self._request(url, require_key=False)
        self._mem[url] = data
        if path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                pass
        return data

    def dataset_url(self, dataset: str, year: int | str) -> str:
        return f"{self.base_url}/{year}/{dataset.strip('/')}"

    # ---- metadata --------------------------------------------------------
    def datasets(self) -> pd.DataFrame:
        """All year-specific datasets the API offers: year, dataset path, title."""
        data = self._cached_metadata(self.base_url + ".json")
        rows = []
        for d in data.get("dataset", []):
            if d.get("c_isTimeseries") or "c_vintage" not in d:
                continue
            rows.append({
                "year": int(d["c_vintage"]),
                "dataset": "/".join(d.get("c_dataset", [])),
                "title": d.get("title", ""),
            })
        df = pd.DataFrame(rows, columns=["year", "dataset", "title"])
        return df.sort_values(["year", "dataset"], ascending=[False, True]).reset_index(drop=True)

    def available_years(self, dataset: str) -> list[int]:
        """Years for which a dataset is published (newest first). Empty if unknown."""
        try:
            df = self.datasets()
        except CensusAPIError:
            return []
        return sorted(df.loc[df["dataset"] == dataset.strip("/"), "year"].unique().tolist(), reverse=True)

    def check_available(self, dataset: str, year: int | str) -> None:
        """Raise a clear error if the dataset isn't published for that year.
        If the dataset catalogue can't be loaded, the check is skipped."""
        try:
            df = self.datasets()
        except CensusAPIError:
            return
        if df.empty:
            return
        dataset = dataset.strip("/")
        years = sorted(df.loc[df["dataset"] == dataset, "year"].unique().tolist())
        if not years:
            raise CensusNotFound(
                f"Dataset '{dataset}' isn't in the Census API catalogue. "
                "Check the spelling (e.g. acs/acs5, acs/acs1, dec/dhc) or run: census-extract datasets"
            )
        if int(year) not in years:
            raise CensusNotFound(
                f"'{dataset}' isn't available for {year}. Available years: "
                f"{_year_ranges(years)}. Latest: {years[-1]}."
            )

    def variables(self, dataset: str, year: int | str) -> pd.DataFrame:
        """Every variable in a dataset: name, label, concept, group, predicateType."""
        data = self._cached_metadata(self.dataset_url(dataset, year) + "/variables.json")
        rows = []
        for name, v in data.get("variables", {}).items():
            if name in RESERVED:
                continue
            rows.append({
                "name": name,
                "label": clean_label(v.get("label", "")),
                "concept": str(v.get("concept", "") or "").strip(),
                "group": v.get("group", "N/A"),
                "predicateType": v.get("predicateType", ""),
            })
        df = pd.DataFrame(rows, columns=["name", "label", "concept", "group", "predicateType"])
        return df.sort_values("name").reset_index(drop=True)

    def groups(self, dataset: str, year: int | str) -> pd.DataFrame:
        """Tables (groups) in a dataset, e.g. B19013 = Median household income."""
        try:
            data = self._cached_metadata(self.dataset_url(dataset, year) + "/groups.json")
        except CensusNotFound:  # some datasets have no table groupings
            return pd.DataFrame(columns=["group", "description"])
        rows = [{"group": g.get("name"), "description": g.get("description", "")}
                for g in data.get("groups", [])]
        return pd.DataFrame(rows, columns=["group", "description"]).sort_values("group").reset_index(drop=True)

    def geographies(self, dataset: str, year: int | str) -> pd.DataFrame:
        """Supported geography levels and which parent geographies each requires."""
        data = self._cached_metadata(self.dataset_url(dataset, year) + "/geography.json")
        rows = []
        for g in data.get("fips", []):
            rows.append({
                "name": g.get("name"),
                "requires": list(g.get("requires") or []),
                "wildcard": list(g.get("wildcard") or []),
                "level_code": g.get("geoLevelDisplay", ""),
            })
        return pd.DataFrame(rows, columns=["name", "requires", "wildcard", "level_code"])

    def search_variables(self, dataset: str, year: int | str, query: str, limit: int = 300) -> pd.DataFrame:
        """Keyword search across variable codes, labels and concepts (all words must match)."""
        df = self.variables(dataset, year)
        df = df[~df["label"].map(_is_annotation)]
        terms = [t.lower() for t in query.split() if t.strip()]
        if not terms:
            return df.head(0)
        hay = (df["name"] + " " + df["label"] + " " + df["concept"]).str.lower()
        mask = pd.Series(True, index=df.index)
        for t in terms:
            mask &= hay.str.contains(t, regex=False)
        return df[mask].head(limit).reset_index(drop=True)

    def search_groups(self, dataset: str, year: int | str, query: str) -> pd.DataFrame:
        df = self.groups(dataset, year)
        terms = [t.lower() for t in query.split() if t.strip()]
        hay = (df["group"] + " " + df["description"]).str.lower()
        mask = pd.Series(True, index=df.index)
        for t in terms:
            mask &= hay.str.contains(t, regex=False)
        return df[mask].reset_index(drop=True)

    # ---- variable list expansion ----------------------------------------
    def expand_variables(
        self,
        dataset: str,
        year: int | str,
        variables: Iterable[str] = (),
        groups: Iterable[str] = (),
        include_moe: bool = False,
        strict: bool = True,
    ) -> tuple[list[str], list[str]]:
        """Resolve tables + individual variables into a final ordered variable list.

        Returns (variables, warnings). With strict=True unknown variables raise an
        error; otherwise they are skipped with a warning (useful across years).
        """
        meta = self.variables(dataset, year)
        known = set(meta["name"])
        warnings: list[str] = []
        wanted: list[str] = []

        for g in groups:
            g = g.strip().upper()
            rows = meta[(meta["group"] == g) & ~meta["label"].map(_is_annotation)]
            if rows.empty:
                msg = f"Table {g} not found in {dataset} {year}."
                if strict:
                    raise CensusAPIError(msg)
                warnings.append(msg + " Skipped.")
                continue
            names = list(rows["name"])
            if not include_moe:  # drop margins of error unless asked for
                names = [n for n in names if not (n.endswith("M") and n[:-1] + "E" in known)]
            wanted.extend(names)

        missing = []
        for v in variables:
            v = v.strip()
            if not v:
                continue
            if v in known:
                wanted.append(v)
            elif v.upper() in known:
                wanted.append(v.upper())
            else:
                missing.append(v)
        if missing:
            msg = (f"Variable(s) not found in {dataset} {year}: {', '.join(missing)}. "
                   "Use the search to find valid codes (they can change between years).")
            if strict:
                raise CensusAPIError(msg)
            warnings.append(msg + " Skipped.")

        final: list[str] = []
        seen = set()
        for v in wanted:
            if v in seen:
                continue
            final.append(v)
            seen.add(v)
            if include_moe and v.endswith("E"):
                moe = v[:-1] + "M"
                if moe in known and moe not in seen:
                    final.append(moe)
                    seen.add(moe)
        if not final:
            raise CensusAPIError("No valid variables to download.")
        return final, warnings

    # ---- data ------------------------------------------------------------
    def fetch(
        self,
        dataset: str,
        year: int | str,
        variables: list[str],
        for_geo: str,
        in_geo: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> pd.DataFrame:
        """Download raw data (strings), splitting into multiple calls if > 50 variables."""
        url = self.dataset_url(dataset, year)
        size = MAX_VARS_PER_CALL - 1  # leave room for NAME
        chunks = [variables[i:i + size] for i in range(0, len(variables), size)] or [[]]
        merged: pd.DataFrame | None = None
        geo_cols: list[str] = []
        for i, chunk in enumerate(chunks):
            get = (["NAME"] if i == 0 else []) + chunk
            params = {"get": ",".join(get), "for": for_geo}
            if in_geo:
                params["in"] = in_geo
            if progress:
                progress(f"{year}: requesting batch {i + 1} of {len(chunks)} ({len(chunk)} variables)")
            rows = self._request(url, params)
            if not rows:
                return pd.DataFrame(columns=["NAME"] + variables)
            header, data = rows[0], rows[1:]
            df = pd.DataFrame(data, columns=header)
            if merged is None:
                merged = df
                geo_cols = [c for c in header if c not in get]
            else:
                merged = merged.merge(df, on=geo_cols, how="outer")
        return merged if merged is not None else pd.DataFrame()


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #
def clean_frame(df: pd.DataFrame, variables: list[str], meta: pd.DataFrame) -> pd.DataFrame:
    """Numeric conversion, sentinel -> blank, GEOID column, tidy column order."""
    ptypes = dict(zip(meta["name"], meta["predicateType"]))
    cols: dict[str, pd.Series] = {}
    geo_cols = [c for c in df.columns if c not in variables and c != "NAME"]
    if geo_cols and all(df[c].astype(str).str.fullmatch(r"\d+").all() for c in geo_cols):
        cols["GEOID"] = df[geo_cols].astype(str).agg("".join, axis=1)
    if "NAME" in df.columns:
        cols["NAME"] = df["NAME"]
    for c in geo_cols:
        cols[c] = df[c]
    for v in variables:
        if v not in df.columns:
            continue
        num = pd.to_numeric(df[v], errors="coerce")
        looks_numeric = num.notna().sum() == df[v].replace("", pd.NA).notna().sum()
        if ptypes.get(v) in ("int", "float") or looks_numeric:
            num = num.mask(num.isin(SENTINELS))
            valid = num.dropna()
            if len(valid) and (valid == valid.round()).all() and valid.abs().max() < 2**53:
                num = num.astype("Int64")  # keep whole numbers whole even with blanks
            cols[v] = num
        else:
            cols[v] = df[v]
    return pd.DataFrame(cols, index=df.index)


# --------------------------------------------------------------------------- #
# Extraction requests (what the config file / UI describes)
# --------------------------------------------------------------------------- #
_GROUP_RE = re.compile(r"^group\((\w+)\)$", re.I)


@dataclass
class ExtractRequest:
    dataset: str = "acs/acs5"
    years: list[int] = field(default_factory=lambda: [2024])
    variables: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    geography: str = "state"
    geo_filter: str = "*"
    within: dict[str, str] = field(default_factory=dict)
    include_moe: bool = False
    headers: str = "code"  # code | label | both
    filters: list[str] = field(default_factory=list)
    output: str = "census_extract.csv"
    write_dictionary: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExtractRequest":
        d = dict(d)
        years = d.pop("years", None) or d.pop("year", None)
        if years is None:
            raise CensusAPIError("Config must include 'year' or 'years'.")
        years = [int(y) for y in (years if isinstance(years, list) else [years])]
        variables, groups = [], [str(g).upper() for g in (d.pop("groups", None) or [])]
        for v in d.pop("variables", None) or []:
            m = _GROUP_RE.match(str(v).strip())
            (groups.append(m.group(1).upper()) if m else variables.append(str(v).strip()))
        within = {str(k): str(v) for k, v in (d.pop("within", None) or {}).items()}
        filters = d.pop("filters", None) or []
        if isinstance(filters, str):
            filters = [filters]
        known = set(cls.__dataclass_fields__)
        unknown = set(d) - known
        if unknown:
            raise CensusAPIError(f"Unknown config option(s): {', '.join(sorted(unknown))}")
        req = cls(years=years, variables=variables, groups=groups, within=within,
                  filters=[str(f) for f in filters], **d)
        if "geo_filter" in d:
            req.geo_filter = str(d["geo_filter"])
        if req.headers not in ("code", "label", "both"):
            raise CensusAPIError("headers must be one of: code, label, both")
        if not req.variables and not req.groups:
            raise CensusAPIError("Config must list at least one variable or table (group).")
        return req

    @classmethod
    def from_file(cls, path: str | Path) -> "ExtractRequest":
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if len(d["years"]) == 1:
            d["year"] = d.pop("years")[0]
        if not d["within"]:
            d.pop("within")
        if not d["filters"]:
            d.pop("filters")
        if not d["groups"]:
            d.pop("groups")
        return d

    def to_yaml(self) -> str:
        import yaml
        return yaml.safe_dump(self.to_dict(), sort_keys=False)

    def for_clause(self) -> str:
        codes = self.geo_filter.strip() or "*"
        if self.geography.lower() == "state":
            codes = resolve_state(codes)
        return f"{self.geography}:{codes}"


@dataclass
class ExtractResult:
    data: pd.DataFrame
    dictionary: pd.DataFrame
    warnings: list[str]


def run_extract(
    client: CensusClient,
    req: ExtractRequest,
    progress: Callable[[str], None] | None = None,
) -> ExtractResult:
    """Run a full extraction: expand variables, download, clean, filter, label."""
    warnings: list[str] = []
    frames = []
    dict_rows: dict[str, dict] = {}
    multi = len(req.years) > 1
    for_geo, in_geo = req.for_clause(), build_in_clause(req.within)

    for year in req.years:
        try:
            client.check_available(req.dataset, year)
        except CensusNotFound as e:
            if not multi:
                raise
            warnings.append(f"Skipped {year}: {e}")
            continue
        if progress:
            progress(f"{year}: loading variable list for {req.dataset}")
        variables, w = client.expand_variables(
            req.dataset, year, req.variables, req.groups,
            include_moe=req.include_moe, strict=not multi,
        )
        warnings += w
        meta = client.variables(req.dataset, year)
        raw = client.fetch(req.dataset, year, variables, for_geo, in_geo, progress=progress)
        df = clean_frame(raw, variables, meta)
        if multi:
            df.insert(0, "year", year)
        frames.append(df)
        for _, r in meta[meta["name"].isin(variables)].iterrows():
            dict_rows[r["name"]] = {"code": r["name"], "label": r["label"],
                                    "concept": r["concept"], "table": r["group"]}

    if not frames:
        raise CensusAPIError("None of the requested years are available. " + " ".join(warnings))
    data = pd.concat(frames, ignore_index=True, sort=False)

    for f in req.filters:
        if not f.strip():
            continue
        try:
            before = len(data)
            data = data.query(f, engine="python")
            if progress:
                progress(f"Filter '{f}' kept {len(data)} of {before} rows")
        except Exception as e:  # noqa: BLE001 - surface any pandas query error nicely
            missing = [t for t in re.findall(r"\b[A-Z]{1,3}\d+[A-Z]?_\d+[A-Z]*\b", f)
                       if t not in data.columns]
            if missing:
                raise CensusAPIError(
                    f"Filter {f!r} uses {', '.join(missing)}, which isn't in this extract. "
                    "Add it to your variables first (filters can only use downloaded columns)."
                ) from e
            raise CensusAPIError(
                f"Could not apply filter {f!r}: {e}. Filters use variable codes, "
                "e.g.  B01003_001E >= 50000"
            ) from e

    var_order = [c for c in data.columns if c in dict_rows]
    dictionary = pd.DataFrame([dict_rows[c] for c in var_order],
                              columns=["code", "label", "concept", "table"])

    if req.headers != "code" and not data.empty:
        rename, used = {}, set()
        for code in var_order:
            info = dict_rows[code]
            label = info["label"]
            if info["concept"] and req.headers == "label":
                label = f"{info['concept'].title()}: {label}"
            new = label if req.headers == "label" else f"{label} [{code}]"
            if new in used:
                new = f"{new} [{code}]"
            used.add(new)
            rename[code] = new
        data = data.rename(columns=rename)

    if progress:
        progress(f"Done: {len(data)} rows x {len(data.columns)} columns")
    return ExtractResult(data=data.reset_index(drop=True), dictionary=dictionary, warnings=warnings)


def write_outputs(result: ExtractResult, output: str | Path, write_dictionary: bool = True) -> list[Path]:
    """Write the data CSV (Excel-friendly UTF-8) and optional data dictionary."""
    out = Path(output)
    if out.suffix.lower() != ".csv":
        out = out.with_suffix(".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    result.data.to_csv(out, index=False, encoding="utf-8-sig")
    paths = [out]
    if write_dictionary:
        dpath = out.with_name(out.stem + "_dictionary.csv")
        result.dictionary.to_csv(dpath, index=False, encoding="utf-8-sig")
        paths.append(dpath)
    return paths
