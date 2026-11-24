"""Command-line interface:  census-extract <command> ...

Run `census-extract --help` or `census-extract <command> --help` for details.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd

from .core import (
    DEFAULT_CACHE_DIR, CensusAPIError, CensusClient, ExtractRequest, run_extract, write_outputs,
)

EXAMPLE_CONFIG = """\
# Census extraction config - edit and run with:
#   census-extract run this_file.yaml
#
# dataset: API path. Common ones:
#   acs/acs5          ACS 5-year detailed tables (all geographies, down to block group)
#   acs/acs5/profile  ACS 5-year data profiles (DP02, DP03, DP04, DP05)
#   acs/acs5/subject  ACS 5-year subject tables (S1901, S2701, ...)
#   acs/acs1          ACS 1-year (only areas with 65,000+ people)
#   dec/dhc           2020 Decennial Census, Demographic & Housing Characteristics
#   dec/pl            2020 Redistricting data (P.L. 94-171)
dataset: acs/acs5

# One year, or a list to stack several years (adds a 'year' column):
year: 2024
# years: [2019, 2024]

# Individual variable codes (find them with: census-extract search "..."):
variables:
  - B01003_001E     # Total population
  - B19013_001E     # Median household income
  - B25077_001E     # Median home value

# Whole tables (every variable in the table):
groups:
  - B15003          # Educational attainment

# Geography level to return one row per:
geography: county
# Which ones: '*' for all, or comma-separated codes (e.g. "086,011")
geo_filter: "*"
# Parent geography (state accepts names, 2-letter codes or FIPS):
within:
  state: Florida

include_moe: true    # add margin-of-error columns next to each estimate
headers: code        # code | label | both
filters:             # optional row filters, written with variable codes
  - B01003_001E >= 50000
output: florida_counties.csv
write_dictionary: true   # also writes <output>_dictionary.csv with labels
"""


def _client(args) -> CensusClient:
    return CensusClient(api_key=args.key, cache_dir=None if args.no_cache else DEFAULT_CACHE_DIR)


def _year(args, client: CensusClient) -> int:
    """Use --year if given, else the newest year the dataset is published for."""
    if args.year:
        return args.year
    years = client.available_years(args.dataset)
    year = years[0] if years else 2024
    print(f"(using {args.dataset} {year} - pass --year to choose another)", file=sys.stderr)
    return year


def _print_df(df: pd.DataFrame, max_rows: int, max_colwidth: int = 90) -> None:
    if df.empty:
        print("No results.")
        return
    with pd.option_context("display.max_rows", max_rows, "display.max_colwidth", max_colwidth,
                           "display.width", 200):
        print(df.head(max_rows).to_string(index=False))
    if len(df) > max_rows:
        print(f"... {len(df) - max_rows} more rows (use --limit to show more)")


def cmd_datasets(args):
    df = _client(args).datasets()
    if args.year:
        df = df[df["year"] == args.year]
    if args.search:
        terms = args.search.lower().split()
        hay = (df["dataset"] + " " + df["title"]).str.lower()
        for t in terms:
            df = df[hay.loc[df.index].str.contains(t, regex=False)]
    _print_df(df, args.limit)


def cmd_search(args):
    c = _client(args)
    df = c.search_variables(args.dataset, _year(args, c), args.query, limit=args.limit)
    _print_df(df[["name", "label", "concept"]], args.limit)


def cmd_groups(args):
    c = _client(args)
    y = _year(args, c)
    df = c.search_groups(args.dataset, y, args.search) if args.search else c.groups(args.dataset, y)
    _print_df(df, args.limit)


def cmd_table(args):
    c = _client(args)
    meta = c.variables(args.dataset, _year(args, c))
    df = meta[(meta["group"] == args.group.upper()) & ~meta["label"].str.lower().str.startswith("annotation of")]
    _print_df(df[["name", "label"]], args.limit, 120)


def cmd_geos(args):
    c = _client(args)
    df = c.geographies(args.dataset, _year(args, c)).copy()
    df["requires"] = df["requires"].map(lambda r: ", ".join(r) if r else "-")
    df["wildcard_ok"] = df["wildcard"].map(lambda r: ", ".join(r) if r else "-")
    _print_df(df[["name", "requires", "wildcard_ok"]], args.limit)


def _run_request(args, req: ExtractRequest):
    if args.output:
        req.output = args.output
    client = _client(args)
    progress = (lambda m: print("  " + m)) if not args.quiet else None
    result = run_extract(client, req, progress=progress)
    for w in result.warnings:
        print("WARNING: " + w, file=sys.stderr)
    paths = write_outputs(result, req.output, req.write_dictionary)
    print(f"\nWrote {len(result.data)} rows to {paths[0]}")
    if len(paths) > 1:
        print(f"Data dictionary: {paths[1]}")
    if args.preview and not result.data.empty:
        print()
        _print_df(result.data, args.preview, 40)


def cmd_run(args):
    req = ExtractRequest.from_file(args.config)
    _run_request(args, req)


def cmd_quick(args):
    variables = [v for v in args.vars.split(",") if v.strip()] if args.vars else []
    within = {}
    for item in args.within or []:
        if "=" not in item:
            raise CensusAPIError(f"--within expects level=value, got {item!r}")
        k, v = item.split("=", 1)
        within[k.strip()] = v.strip()
    years = args.year
    if not years:
        found = _client(args).available_years(args.dataset)
        years = [found[0] if found else 2024]
        print(f"(using {args.dataset} {years[0]} - pass --year to choose another)", file=sys.stderr)
    d = {
        "dataset": args.dataset,
        "years": years,
        "variables": variables,
        "groups": [g for g in (args.groups or "").split(",") if g.strip()],
        "geography": args.geo,
        "geo_filter": args.geo_filter,
        "within": within,
        "include_moe": args.moe,
        "headers": args.headers,
        "filters": args.filter or [],
        "output": args.output or "census_extract.csv",
    }
    req = ExtractRequest.from_dict(d)
    if args.save_config:
        Path(args.save_config).write_text(req.to_yaml(), encoding="utf-8")
        print(f"Saved config to {args.save_config}")
    _run_request(args, req)


def cmd_init(args):
    p = Path(args.path)
    if p.exists() and not args.force:
        raise CensusAPIError(f"{p} already exists (use --force to overwrite).")
    p.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"Wrote example config to {p}. Edit it, then run:  census-extract run {p}")


def cmd_ui(args):
    app = Path(__file__).with_name("app.py")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app)]
    if args.port:
        cmd += ["--server.port", str(args.port)]
    try:
        return subprocess.call(cmd)
    except FileNotFoundError:
        raise CensusAPIError("Streamlit is not installed. Run:  pip install streamlit")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="census-extract",
        description="Search US Census Data API variables and extract them to CSV.",
    )
    p.add_argument("--key", help="Census API key (default: CENSUS_API_KEY env variable)")
    p.add_argument("--no-cache", action="store_true", help="Don't use the on-disk metadata cache")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def ds(sp, year_required=True):
        sp.add_argument("--dataset", "-d", default="acs/acs5", help="Dataset path (default acs/acs5)")
        sp.add_argument("--year", "-y", type=int, help="Data year (default: newest available)")
        sp.add_argument("--limit", type=int, default=50, help="Max rows to print")

    s = sub.add_parser("datasets", help="List available datasets")
    s.add_argument("--year", "-y", type=int)
    s.add_argument("--search", "-s", help="Keyword(s) to filter by")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(func=cmd_datasets)

    s = sub.add_parser("search", help="Find variables by keyword")
    s.add_argument("query", help='Keywords, e.g. "median household income"')
    ds(s)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("groups", help="List or search tables (groups)")
    s.add_argument("--search", "-s", help="Keyword(s) to filter by")
    ds(s)
    s.set_defaults(func=cmd_groups)

    s = sub.add_parser("table", help="Show every variable in one table, e.g. B19001")
    s.add_argument("group")
    ds(s)
    s.set_defaults(func=cmd_table)

    s = sub.add_parser("geos", help="List geography levels a dataset supports")
    ds(s)
    s.set_defaults(func=cmd_geos)

    def out_opts(sp):
        sp.add_argument("--output", "-o", help="Output CSV path")
        sp.add_argument("--preview", type=int, default=5, help="Rows to preview (0 = none)")
        sp.add_argument("--quiet", "-q", action="store_true")

    s = sub.add_parser("run", help="Run an extraction from a YAML/JSON config file")
    s.add_argument("config")
    out_opts(s)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("quick", help="Run an extraction from command-line options")
    s.add_argument("--dataset", "-d", default="acs/acs5")
    s.add_argument("--year", "-y", type=int, nargs="+",
                   help="One or more years (default: newest available for the dataset)")
    s.add_argument("--vars", help="Comma-separated variable codes")
    s.add_argument("--groups", help="Comma-separated table codes, e.g. B19001,B15003")
    s.add_argument("--geo", default="state", help='Geography level, e.g. county, tract, "block group"')
    s.add_argument("--geo-filter", default="*", help="Which ones (* or comma-separated codes)")
    s.add_argument("--within", action="append", metavar="LEVEL=VALUE",
                   help="Parent geography, repeatable: --within state=FL --within county=086")
    s.add_argument("--moe", action="store_true", help="Include margins of error")
    s.add_argument("--headers", choices=["code", "label", "both"], default="code")
    s.add_argument("--filter", action="append", help='Row filter, e.g. "B01003_001E > 10000"')
    s.add_argument("--save-config", metavar="FILE", help="Also save these options as a YAML config")
    out_opts(s)
    s.set_defaults(func=cmd_quick)

    s = sub.add_parser("init", help="Write a commented example config file")
    s.add_argument("path", nargs="?", default="census_config.yaml")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("ui", help="Launch the point-and-click web interface")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_ui)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        rv = args.func(args)
        return int(rv or 0)
    except CensusAPIError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
