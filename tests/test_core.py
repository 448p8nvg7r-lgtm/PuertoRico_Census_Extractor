"""Offline tests: a fake Census API stands in for api.census.gov."""
import json
from urllib.parse import urlparse

import pandas as pd
import pytest

from census_extractor import cli
from census_extractor.core import (
    CensusAPIError, CensusClient, ExtractRequest, build_in_clause, clean_label,
    resolve_state, run_extract, write_outputs,
)

N_EXTRA = 120  # enough variables to force 3 API batches


def _variables():
    v = {
        "for": {"label": "Census API FIPS 'for' clause"},
        "NAME": {"label": "Geographic Area Name", "predicateType": "string", "group": "N/A"},
        "B01003_001E": {"label": "Estimate!!Total", "concept": "TOTAL POPULATION",
                        "group": "B01003", "predicateType": "int"},
        "B01003_001M": {"label": "Margin of Error!!Total", "concept": "TOTAL POPULATION",
                        "group": "B01003", "predicateType": "int"},
        "B01003_001EA": {"label": "Annotation of Estimate!!Total", "group": "B01003",
                         "predicateType": "string"},
        "B19013_001E": {"label": "Estimate!!Median household income in the past 12 months",
                        "concept": "MEDIAN HOUSEHOLD INCOME", "group": "B19013",
                        "predicateType": "int"},
        "B19013_001M": {"label": "Margin of Error!!Median household income",
                        "concept": "MEDIAN HOUSEHOLD INCOME", "group": "B19013",
                        "predicateType": "int"},
    }
    for i in range(N_EXTRA):
        v[f"X{i:03d}E"] = {"label": f"Estimate!!Item {i}", "concept": "EXTRA",
                           "group": "X", "predicateType": "int"}
    return {"variables": v}


ROWS = {  # (state, county) -> NAME
    ("12", "086"): "Miami-Dade County, Florida",
    ("12", "011"): "Broward County, Florida",
    ("12", "001"): "Alachua County, Florida",
}


def _value(var, state, county):
    if var == "NAME":
        return ROWS[(state, county)]
    if var == "B19013_001E" and county == "001":
        return "-666666666"  # sentinel -> blank
    if var == "B01003_001E":
        return {"086": "2700000", "011": "1950000", "001": "45000"}[county]
    return str(int(county) + len(var))


class FakeResp:
    def __init__(self, status, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        path = urlparse(url).path
        if "/1999/" in path or "/2025/acs/acs5" in path:
            return FakeResp(404, text="<html>error: unknown/unsupported geography hierarchy</html>")
        if path.endswith("/variables.json"):
            return FakeResp(200, _variables())
        if path.endswith("/groups.json"):
            return FakeResp(200, {"groups": [{"name": "B19013", "description": "MEDIAN HOUSEHOLD INCOME"},
                                             {"name": "B01003", "description": "TOTAL POPULATION"}]})
        if path.endswith("/geography.json"):
            return FakeResp(200, {"fips": [{"name": "state", "geoLevelDisplay": "040"},
                                           {"name": "county", "geoLevelDisplay": "050",
                                            "requires": ["state"], "wildcard": ["state"]}]})
        if path == "/data.json":
            return FakeResp(200, {"dataset": [
                {"c_vintage": 2024, "c_dataset": ["acs", "acs5"], "title": "ACS 5-Year"},
                {"c_vintage": 2023, "c_dataset": ["acs", "acs5"], "title": "ACS 5-Year"},
                {"c_vintage": 2025, "c_dataset": ["acs", "acs1"], "title": "ACS 1-Year"},
                {"c_isTimeseries": True, "c_dataset": ["timeseries", "x"], "title": "TS"}]})
        if "/1999/" in path or "/2025/acs/acs5" in path:
            return FakeResp(404, text="<html>error: unknown/unsupported geography hierarchy</html>")
        # data request
        if params.get("key") != "TESTKEY":
            return FakeResp(200, text="<html>Invalid Key</html>")
        get = params["get"].split(",")
        assert len(get) <= 50, "exceeded API variable limit"
        header = get + ["state", "county"]
        counties = list(ROWS)
        if params["for"] != "county:*":
            wanted = params["for"].split(":")[1].split(",")
            counties = [k for k in counties if k[1] in wanted]
        if not counties:
            return FakeResp(204, text="")
        data = [[_value(v, s, c) for v in get] + [s, c] for s, c in counties]
        return FakeResp(200, [header] + data)


@pytest.fixture
def client():
    return CensusClient(api_key="TESTKEY", session=FakeSession(), cache_dir=None)


def test_helpers():
    assert resolve_state("Florida") == "12"
    assert resolve_state("fl, ga") == "12,13"
    assert resolve_state(6) == "06"
    assert resolve_state("*") == "*"
    with pytest.raises(CensusAPIError):
        resolve_state("Atlantis")
    assert clean_label("Estimate!!Total:!!Male:") == "Estimate > Total > Male"
    assert build_in_clause({"state": "FL", "county": "*"}) == "state:12 county:*"


def test_search_and_metadata(client):
    hits = client.search_variables("acs/acs5", 2024, "median income")
    assert set(hits["name"]) == {"B19013_001E", "B19013_001M"}
    assert "B01003_001EA" not in set(client.search_variables("acs/acs5", 2024, "total")["name"])
    assert client.datasets()["dataset"].tolist() == ["acs/acs1", "acs/acs5", "acs/acs5"]
    assert client.available_years("acs/acs5") == [2024, 2023]
    geos = client.geographies("acs/acs5", 2024)
    assert geos.set_index("name").at["county", "requires"] == ["state"]


def test_expand_groups_and_moe(client):
    v, _ = client.expand_variables("acs/acs5", 2024, groups=["B01003"])
    assert v == ["B01003_001E"]  # MOE + annotation dropped
    v, _ = client.expand_variables("acs/acs5", 2024, ["B19013_001E"], ["B01003"], include_moe=True)
    assert v == ["B01003_001E", "B01003_001M", "B19013_001E", "B19013_001M"]
    with pytest.raises(CensusAPIError, match="not found"):
        client.expand_variables("acs/acs5", 2024, ["NOPE_001E"])


def test_full_extract_chunking_cleaning_filters(client, tmp_path):
    extra = [f"X{i:03d}E" for i in range(N_EXTRA)]
    req = ExtractRequest.from_dict({
        "dataset": "acs/acs5", "year": 2024,
        "variables": ["B01003_001E", "B19013_001E"] + extra,
        "geography": "county", "within": {"state": "Florida"},
        "include_moe": False, "filters": ["B01003_001E >= 40000"],
        "output": str(tmp_path / "out.csv"),
    })
    res = run_extract(client, req)
    data_calls = [c for c in client.session.calls if not c[0].endswith(".json")]
    assert len(data_calls) == 3  # 122 vars -> 3 batches of <= 49 (+NAME)
    assert all(c[1]["in"] == "state:12" for c in data_calls)
    df = res.data
    assert len(df) == 3 and len(df.columns) == 3 + 1 + 122  # GEOID, NAME, state, county + vars
    assert list(df.columns[:4]) == ["GEOID", "NAME", "state", "county"]
    row = df.set_index("GEOID").loc["12001"]
    assert pd.isna(row["B19013_001E"])  # sentinel removed
    assert row["B01003_001E"] == 45000
    assert pd.api.types.is_numeric_dtype(df["B01003_001E"])
    paths = write_outputs(res, req.output)
    assert paths[0].exists() and paths[1].name == "out_dictionary.csv"
    d = pd.read_csv(paths[1])
    assert d.loc[d.code == "B19013_001E", "label"].item().startswith("Estimate > Median")

    req.filters = ["B01003_001E > 1000000"]
    assert len(run_extract(client, req).data) == 2
    req.filters = ["NOT_A_COLUMN > 3"]
    with pytest.raises(CensusAPIError, match="filter"):
        run_extract(client, req)


def test_multi_year_and_labels(client):
    req = ExtractRequest.from_dict({
        "dataset": "acs/acs5", "years": [2023, 2024],
        "variables": ["B01003_001E", "group(B19013)"],
        "geography": "county", "geo_filter": "086,011", "within": {"state": "12"},
        "headers": "both", "include_moe": True,
    })
    assert req.groups == ["B19013"]
    res = run_extract(client, req)
    assert sorted(res.data["year"].unique()) == [2023, 2024] and len(res.data) == 4
    assert "Estimate > Total [B01003_001E]" in res.data.columns
    assert "Margin of Error > Total [B01003_001M]" in res.data.columns


def test_errors(client):
    bad = CensusClient(api_key="WRONG", session=FakeSession(), cache_dir=None)
    with pytest.raises(CensusAPIError, match="key"):
        bad.fetch("acs/acs5", 2024, ["B01003_001E"], "county:*", "state:12")
    nokey = CensusClient(api_key=None, session=FakeSession(), cache_dir=None)
    nokey.api_key = None
    with pytest.raises(CensusAPIError, match="API key is required"):
        nokey.fetch("acs/acs5", 2024, ["B01003_001E"], "county:*")
    with pytest.raises(CensusAPIError, match="404"):
        client.variables("acs/acs5", 1999)
    empty = client.fetch("acs/acs5", 2024, ["B01003_001E"], "county:999", "state:12")
    assert empty.empty
    with pytest.raises(CensusAPIError, match="Unknown config"):
        ExtractRequest.from_dict({"year": 2024, "variables": ["A"], "geograpy": "x"})


def test_disk_cache(tmp_path):
    s = FakeSession()
    CensusClient("TESTKEY", session=s, cache_dir=tmp_path).variables("acs/acs5", 2024)
    s2 = FakeSession()
    CensusClient("TESTKEY", session=s2, cache_dir=tmp_path).variables("acs/acs5", 2024)
    assert len(s.calls) == 1 and len(s2.calls) == 0


def test_cli_end_to_end(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "CensusClient",
                        lambda api_key=None, cache_dir=None: CensusClient("TESTKEY", FakeSession(), cache_dir=None))
    cfg = tmp_path / "c.yaml"
    assert cli.main(["init", str(cfg)]) == 0
    text = cfg.read_text().replace("  - B25077_001E     # Median home value\n", "") \
                          .replace("  - B15003          # Educational attainment\n", "  - B01003\n")
    cfg.write_text(text)
    out = tmp_path / "fl.csv"
    assert cli.main(["run", str(cfg), "-o", str(out), "--quiet"]) == 0
    df = pd.read_csv(out, dtype={"GEOID": str})
    assert set(df["GEOID"]) == {"12086", "12011"}  # Alachua filtered out (< 50000)
    assert "B19013_001M" in df.columns

    assert cli.main(["search", "median income"]) == 0  # year defaults to newest (2024)
    assert "B19013_001E" in capsys.readouterr().out
    out2 = tmp_path / "q.csv"
    assert cli.main(["quick", "--vars", "B01003_001E", "--geo", "county", "--within", "state=FL",
                     "-o", str(out2), "--save-config", str(tmp_path / "s.yaml"), "--quiet"]) == 0
    assert len(pd.read_csv(out2)) == 3
    assert ExtractRequest.from_file(tmp_path / "s.yaml").within == {"state": "FL"}
    assert cli.main(["quick", "--vars", "NOPE"]) == 1


def test_unavailable_year_is_explained(client):
    req = ExtractRequest.from_dict({"dataset": "acs/acs5", "year": 2025,
                                    "variables": ["B01003_001E"], "geography": "county"})
    with pytest.raises(CensusAPIError, match="isn't available for 2025. Available years: 2023-2024"):
        run_extract(client, req)
    req.years = [2024, 2025]  # multi-year: skip the missing year with a warning
    res = run_extract(client, req)
    assert any("Skipped 2025" in w for w in res.warnings) and set(res.data["year"]) == {2024}
    req.dataset = "acs/acs55"
    req.years = [2024]
    with pytest.raises(CensusAPIError, match="isn't in the Census API catalogue"):
        run_extract(client, req)


def test_404_message_shows_url_without_key(client):
    with pytest.raises(CensusAPIError) as e:
        client.fetch("acs/acs5", 2025, ["B01003_001E"], "state:*")
    msg = str(e.value)
    assert "https://api.census.gov/data/2025/acs/acs5?get=NAME" in msg
    assert "TESTKEY" not in msg and "body {" not in msg
