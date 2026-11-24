"""Point-and-click web UI for the Census variable extractor.

Launch with:   census-extract ui
          or:  streamlit run census_extractor/app.py
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

# Allow `streamlit run census_extractor/app.py` without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from census_extractor.core import (  # noqa: E402
    STATES, CensusAPIError, CensusClient, ExtractRequest, run_extract,
)

st.set_page_config(page_title="Census Variable Extractor", page_icon="📊", layout="wide")

COMMON_DATASETS = {
    "acs/acs5": "ACS 5-Year Detailed Tables (all areas, down to block group)",
    "acs/acs5/profile": "ACS 5-Year Data Profiles (DP02-DP05)",
    "acs/acs5/subject": "ACS 5-Year Subject Tables (S-tables)",
    "acs/acs1": "ACS 1-Year Detailed Tables (areas with 65,000+ people)",
    "acs/acs1/profile": "ACS 1-Year Data Profiles",
    "dec/dhc": "2020 Census: Demographic & Housing Characteristics",
    "dec/pl": "2020 Census: Redistricting Data (P.L. 94-171)",
}
STATE_OPTIONS = sorted((name, fips) for fips, name in STATES.values())
STATE_LABELS = [f"{name} ({fips})" for name, fips in STATE_OPTIONS]
STATE_FIPS_BY_LABEL = {f"{name} ({fips})": fips for name, fips in STATE_OPTIONS}

# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #
ss = st.session_state
ss.setdefault("selected", {})   # code -> label
ss.setdefault("groups", {})     # table code -> description
ss.setdefault("result", None)
ss.setdefault("last_request", None)


# --------------------------------------------------------------------------- #
# Cached metadata loaders (also cached on disk by CensusClient)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Loading dataset list...")
def load_datasets(api_key: str) -> pd.DataFrame:
    return CensusClient(api_key or None).datasets()


@st.cache_data(show_spinner="Loading variable list (first time can take ~30s)...")
def load_variables(api_key: str, dataset: str, year: int) -> pd.DataFrame:
    df = CensusClient(api_key or None).variables(dataset, year)
    return df[~df["label"].str.lower().str.startswith("annotation of")].reset_index(drop=True)


@st.cache_data(show_spinner="Loading tables...")
def load_groups(api_key: str, dataset: str, year: int) -> pd.DataFrame:
    return CensusClient(api_key or None).groups(dataset, year)


@st.cache_data(show_spinner="Loading geography levels...")
def load_geos(api_key: str, dataset: str, year: int) -> pd.DataFrame:
    return CensusClient(api_key or None).geographies(dataset, year)


def keyword_filter(df: pd.DataFrame, cols: list[str], query: str) -> pd.DataFrame:
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return df
    hay = df[cols].astype(str).agg(" ".join, axis=1).str.lower()
    mask = pd.Series(True, index=df.index)
    for t in terms:
        mask &= hay.str.contains(t, regex=False)
    return df[mask]


# --------------------------------------------------------------------------- #
# Sidebar: key, dataset, year
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("1 · Data source")
    api_key = st.text_input(
        "Census API key", value=os.environ.get("CENSUS_API_KEY", ""), type="password",
        help="Required for data downloads. Free at https://api.census.gov/data/key_signup.html. "
             "Set the CENSUS_API_KEY environment variable to pre-fill this box.",
    )
    if not api_key:
        st.warning("Enter your API key (needed to download data).")

    catalogue_ok = True
    try:
        all_ds = load_datasets(api_key)
    except CensusAPIError as e:
        catalogue_ok = False
        st.error(f"Could not load the Census dataset catalogue: {e}")
        all_ds = pd.DataFrame(columns=["year", "dataset", "title"])

    # Dataset first, then only the years that dataset actually exists for.
    latest_titles = all_ds.drop_duplicates("dataset").set_index("dataset")["title"].to_dict()
    show_all = st.checkbox("Show all datasets", value=False,
                           help="By default only the most-used datasets are listed.")
    if show_all and latest_titles:
        options = dict(sorted(latest_titles.items()))
    else:
        options = {d: t for d, t in COMMON_DATASETS.items()
                   if not catalogue_ok or d in latest_titles}
    keys = list(options) or list(COMMON_DATASETS)
    dataset = st.selectbox(
        "Dataset", keys, index=keys.index("acs/acs5") if "acs/acs5" in keys else 0,
        format_func=lambda d: f"{d} — {options.get(d, COMMON_DATASETS.get(d, ''))}",
    )
    custom = st.text_input("…or type a dataset path", placeholder="e.g. acs/acs5/cprofile")
    if custom.strip():
        dataset = custom.strip().strip("/")

    years = sorted(all_ds.loc[all_ds["dataset"] == dataset, "year"].unique().tolist(), reverse=True)
    if not years:
        if catalogue_ok:
            st.error(f"'{dataset}' isn't in the Census API catalogue. Check the dataset path.")
        years = list(range(date.today().year - 1, 2009, -1))
    year = st.selectbox(
        "Year", years, index=0, key=f"year_{dataset}",
        help="Only years this dataset is published for are listed (newest first).",
    )
    if catalogue_ok and len(years) > 1:
        st.caption(f"Available: {years[-1]}–{years[0]}")

    extra_years = st.multiselect(
        "Also stack these years (optional)", [y for y in years if y != year],
        key=f"extra_{dataset}",
        help="Downloads the same variables for each year into one CSV with a 'year' column. "
             "Variables that don't exist in a year are skipped with a warning.",
    )

    st.divider()
    st.caption(f"Selected: **{len(ss.selected)}** variables, **{len(ss.groups)}** whole tables")
    if st.button("Clear all selections", use_container_width=True):
        ss.selected, ss.groups = {}, {}
        st.rerun()

st.title("📊 US Census Variable Extractor")
st.caption("Pick variables, choose geography, set criteria, download a CSV. "
           f"Source: Census Data API · {dataset} · {year}")

tab_vars, tab_geo, tab_opts, tab_run = st.tabs(
    ["2 · Variables", "3 · Geography", "4 · Options & filters", "5 · Extract & download"]
)

# --------------------------------------------------------------------------- #
# Variables
# --------------------------------------------------------------------------- #
with tab_vars:
    try:
        vars_df = load_variables(api_key, dataset, year)
    except CensusAPIError as e:
        st.error(str(e))
        vars_df = pd.DataFrame(columns=["name", "label", "concept", "group", "predicateType"])

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Search individual variables")
        q = st.text_input("Keywords", placeholder="e.g. median household income",
                          help="All words must match the code, label or concept.")
        if q.strip():
            hits = keyword_filter(vars_df, ["name", "label", "concept"], q).head(300)
            st.caption(f"{len(hits)} matches" + (" (showing first 300)" if len(hits) == 300 else ""))
            if not hits.empty:
                table = hits[["name", "label", "concept"]].copy()
                table.insert(0, "add", table["name"].isin(ss.selected))
                edited = st.data_editor(
                    table, hide_index=True, use_container_width=True, height=380,
                    column_config={
                        "add": st.column_config.CheckboxColumn("Add", width="small"),
                        "name": st.column_config.TextColumn("Code", width="small"),
                    },
                    disabled=["name", "label", "concept"], key=f"hits_{dataset}_{year}_{q}",
                )
                if st.button("Add checked variables", type="primary"):
                    for _, r in edited[edited["add"]].iterrows():
                        ss.selected[r["name"]] = r["label"]
                    st.rerun()

        with st.expander("Paste variable codes directly"):
            pasted = st.text_area("Codes (comma, space or newline separated)",
                                  placeholder="B01003_001E, B19013_001E")
            if st.button("Add pasted codes"):
                known = dict(zip(vars_df["name"], vars_df["label"]))
                codes = [c.strip() for c in pasted.replace(",", " ").split() if c.strip()]
                bad = [c for c in codes if c not in known]
                for c in codes:
                    if c in known:
                        ss.selected[c] = known[c]
                if bad:
                    st.warning(f"Not found in {dataset} {year}: {', '.join(bad)}")
                else:
                    st.rerun()

    with col_b:
        st.subheader("Add whole tables")
        try:
            groups_df = load_groups(api_key, dataset, year)
        except CensusAPIError as e:
            st.error(str(e))
            groups_df = pd.DataFrame(columns=["group", "description"])
        gq = st.text_input("Search tables", placeholder="e.g. educational attainment")
        gh = keyword_filter(groups_df, ["group", "description"], gq) if gq.strip() else groups_df.head(0)
        if not gh.empty:
            choice = st.selectbox(
                f"{len(gh)} matching tables", gh["group"].tolist(),
                format_func=lambda g: f"{g} — {gh.set_index('group').at[g, 'description']}",
            )
            preview = vars_df[vars_df["group"] == choice][["name", "label"]]
            st.caption(f"{len(preview)} variables in {choice} (incl. margins of error)")
            st.dataframe(preview, hide_index=True, use_container_width=True, height=220)
            if st.button(f"Add entire table {choice}"):
                ss.groups[choice] = gh.set_index("group").at[choice, "description"]
                st.rerun()

    st.divider()
    st.subheader("Your selection")
    if not ss.selected and not ss.groups:
        st.info("Nothing selected yet. Search above and tick **Add**, or add a whole table.")
    if ss.selected:
        sel = pd.DataFrame({"remove": False, "code": list(ss.selected),
                            "label": list(ss.selected.values())})
        sel_edit = st.data_editor(
            sel, hide_index=True, use_container_width=True, disabled=["code", "label"],
            column_config={"remove": st.column_config.CheckboxColumn("Remove", width="small")},
            key="selection_editor",
        )
        if st.button("Remove checked variables"):
            for c in sel_edit[sel_edit["remove"]]["code"]:
                ss.selected.pop(c, None)
            st.rerun()
    if ss.groups:
        st.markdown("**Whole tables:**")
        for g, desc in list(ss.groups.items()):
            c1, c2 = st.columns([6, 1])
            c1.write(f"`{g}` — {desc}")
            if c2.button("Remove", key=f"rm_group_{g}"):
                ss.groups.pop(g)
                st.rerun()

# --------------------------------------------------------------------------- #
# Geography
# --------------------------------------------------------------------------- #
within: dict[str, str] = {}
geo_filter = "*"
geography = "state"
with tab_geo:
    try:
        geos = load_geos(api_key, dataset, year)
    except CensusAPIError as e:
        st.error(str(e))
        geos = pd.DataFrame([{"name": "state", "requires": [], "wildcard": [], "level_code": ""}])

    def geo_label(i: int) -> str:
        r = geos.iloc[i]
        return r["name"] + (f"  (within {' › '.join(r['requires'])})" if r["requires"] else "")

    idx = list(range(len(geos)))
    default_idx = next((i for i in idx if geos.iloc[i]["name"] == "county"), 0)
    gi = st.selectbox("One row per…", idx, index=default_idx, format_func=geo_label,
                      help="Geography levels this dataset supports.")
    row = geos.iloc[gi]
    geography = row["name"]

    st.markdown("**Which ones?**")
    if geography == "state":
        picks = st.multiselect("States (leave empty for all)", STATE_LABELS)
        geo_filter = ",".join(STATE_FIPS_BY_LABEL[p] for p in picks) or "*"
    else:
        geo_filter = st.text_input(
            f"{geography} codes", value="*",
            help="'*' = all. Or comma-separated FIPS/codes, e.g. 086,011 for two counties.",
        ).strip() or "*"

    if row["requires"]:
        st.markdown("**Inside which parent area?**")
        for level in row["requires"]:
            wildcard_ok = level in row["wildcard"]
            if level == "state":
                opts = (["* (all states)"] if wildcard_ok else []) + STATE_LABELS
                picks = st.multiselect("State(s)", opts,
                                       default=[opts[0]] if wildcard_ok else None,
                                       key=f"in_state_{gi}")
                if "* (all states)" in picks or (not picks and wildcard_ok):
                    within["state"] = "*"
                else:
                    within["state"] = ",".join(STATE_FIPS_BY_LABEL[p] for p in picks)
            else:
                within[level] = st.text_input(
                    f"{level} code(s)", value="*" if wildcard_ok else "", key=f"in_{level}_{gi}",
                    help=("'*' allowed. " if wildcard_ok else "Required: ")
                    + "use FIPS codes, e.g. 086 for Miami-Dade County.",
                ).strip()
        missing = [k for k, v in within.items() if not v]
        if missing:
            st.warning(f"Please fill in: {', '.join(missing)}")
    in_clause = " ".join(f"{k}:{v}" for k, v in within.items() if v)
    st.code(f"for={geography}:{geo_filter}" + (f"\nin={in_clause}" if in_clause else ""),
            language="text")
    st.caption("Tip: find FIPS codes for counties, places, etc. by running a first extract at "
               "the parent level — codes appear in the output columns.")

# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #
with tab_opts:
    c1, c2 = st.columns(2)
    with c1:
        include_moe = st.checkbox(
            "Include margins of error", value=dataset.startswith("acs"),
            help="Adds the matching ...M column after each ...E estimate (ACS only).",
        )
        headers = st.radio(
            "Column headers", ["code", "both", "label"], horizontal=True,
            format_func={"code": "Codes (B01003_001E)", "both": "Label + [code]",
                         "label": "Labels only"}.get,
            help="A data dictionary with full labels is always provided as well.",
        )
        output = st.text_input("Output file name", value="census_extract.csv")
    with c2:
        filters_txt = st.text_area(
            "Row filters (optional, one per line)",
            placeholder="B01003_001E >= 50000\nB19013_001E < 60000\nNAME.str.contains('County')",
            help="Python/pandas expressions using variable codes. Rows must match all filters.",
            height=140,
        )
    filters = [f.strip() for f in filters_txt.splitlines() if f.strip()]

# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
with tab_run:
    all_years = sorted({year, *extra_years})
    req = ExtractRequest(
        dataset=dataset, years=all_years, variables=list(ss.selected),
        groups=list(ss.groups), geography=geography, geo_filter=geo_filter,
        within={k: v for k, v in within.items()}, include_moe=include_moe,
        headers=headers, filters=filters, output=output,
    )
    st.markdown(
        f"**Dataset:** `{dataset}` · **Year(s):** {', '.join(map(str, all_years))}  \n"
        f"**Variables:** {len(ss.selected)} + **tables:** {', '.join(ss.groups) or 'none'}  \n"
        f"**Geography:** {geography} = `{geo_filter}`"
        + (f" within `{in_clause}`" if in_clause else "")
    )
    blockers = []
    if not api_key:
        blockers.append("Enter your API key in the sidebar.")
    if not ss.selected and not ss.groups:
        blockers.append("Select at least one variable or table (tab 2).")
    if any(not v for v in within.values()):
        blockers.append("Fill in the required parent geography (tab 3).")
    for b in blockers:
        st.warning(b)

    if st.button("▶ Run extraction", type="primary", disabled=bool(blockers)):
        with st.status("Downloading from the Census API…", expanded=True) as status:
            try:
                client = CensusClient(api_key)
                ss.result = run_extract(client, req, progress=st.write)
                ss.last_request = req
                status.update(label="Extraction complete", state="complete")
            except CensusAPIError as e:
                ss.result = None
                status.update(label="Extraction failed", state="error")
                st.error(str(e))

    res = ss.result
    if res is not None:
        for w in res.warnings:
            st.warning(w)
        st.success(f"{len(res.data):,} rows × {len(res.data.columns)} columns")
        st.dataframe(res.data, use_container_width=True, height=420)
        last = ss.last_request or req
        name = last.output if last.output.lower().endswith(".csv") else last.output + ".csv"
        d1, d2, d3 = st.columns(3)
        d1.download_button("⬇ Download CSV", res.data.to_csv(index=False).encode("utf-8-sig"),
                           file_name=name, mime="text/csv", type="primary",
                           use_container_width=True)
        d2.download_button("⬇ Data dictionary", res.dictionary.to_csv(index=False).encode("utf-8-sig"),
                           file_name=name[:-4] + "_dictionary.csv", mime="text/csv",
                           use_container_width=True)
        d3.download_button("⬇ Save settings (YAML)", last.to_yaml().encode("utf-8"),
                           file_name=name[:-4] + "_config.yaml", mime="text/yaml",
                           use_container_width=True,
                           help="Re-run later from the command line: census-extract run <file>")
        with st.expander("Data dictionary"):
            st.dataframe(res.dictionary, hide_index=True, use_container_width=True)
