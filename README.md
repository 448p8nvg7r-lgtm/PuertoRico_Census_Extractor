# Puerto Rico Census Extractor

Pick Census variables, choose the geography you want, set any criteria, and get
a clean CSV, plus optional boundary files for mapping. Built with Puerto Rico in
mind, and it works for any U.S. state too.

Data comes from the official **Census Data API** (`api.census.gov`). That's the
same data as data.census.gov, in a format built for downloading. Boundaries come
from the Census Bureau's **TIGERweb** map service.

There are three ways to use it:

| Version | What it is | Best for |
|---|---|---|
| **Browser version**: [`census-extractor.html`](census-extractor.html) | One HTML file. Double-click to open it in Chrome, Edge or Firefox. Nothing to install. | Most people |
| **Desktop web UI** (Streamlit) | A local app that opens in your browser | Python users |
| **Command line** (`census-extract`) | Scriptable, and repeatable from a saved config file | Automation and big pulls |

## Browser version (`census-extractor.html`)

Download [`census-extractor.html`](census-extractor.html) (click it, then **Download raw file**),
double-click it, and paste your free [Census API key](https://api.census.gov/data/key_signup.html).

- **🇵🇷 Puerto Rico by default.** *Puerto Rico only* limits every search and
  download to Puerto Rico areas: municipios (`county`), barrios (`county subdivision`),
  tracts and block groups. *Only Puerto Rico–specific tables* shows the tables
  published only for Puerto Rico, whose codes end in `PR` (e.g. `B05001PR`). Untick
  either one to see everything.
- **English / Español** switch. Your choice is remembered.
- **❓ Help & examples** with eight one-click Puerto Rico examples, municipio
  codes, filter syntax and troubleshooting.
- **Only real years:** the Year list shows only years the chosen dataset actually has.
- **Downloads:** a CSV (UTF-8, Excel-friendly), a data dictionary and a settings
  file. The settings file is JSON, which the command-line tool can also run:
  `census-extract run file.json`.
- **🗺 Boundaries:** GeoJSON and a zipped shapefile, joined to your data by
  `GEOID`, plus a shaded preview map. Choose simplified boundaries clipped to the
  shoreline (best for maps) or full TIGER/Line detail.

The rest of this README covers the Python desktop app and the command-line tool.
The Windows installer is described in [`windows/BUILDING.md`](windows/BUILDING.md);
GitHub Actions can build it for you (**Actions → Build Windows installer → Run workflow**).

---

## Contents

1. [Install](#1-install)
2. [Get a Census API key](#2-get-a-census-api-key-required)
3. [Using the web UI](#3-using-the-web-ui)
4. [Using the command line](#4-using-the-command-line)
5. [Config file reference](#5-config-file-reference)
6. [Row filters (criteria)](#6-row-filters-criteria)
7. [Choosing datasets, variables and geographies](#7-choosing-datasets-variables-and-geographies)
8. [What's in the output](#8-whats-in-the-output)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Install

### Windows: use the installer (easiest, no Python needed)

1. Run **`CensusExtractor-Setup-1.0.1.exe`**. If Windows shows
   *"Windows protected your PC"*, click **More info → Run anyway**.
2. Follow the wizard. On the **Census API key** page, paste your key (see
   [section 2](#2-get-a-census-api-key-required)) or leave it blank and enter it in the app later.
3. Start **Census Variable Extractor** from the Start menu or desktop. Your browser
   opens the app, and a small console window stays open. **Close that window to quit.**

For command-line use, open **Census Variable Extractor - Command Prompt** from
the Start menu, or tick *Add to PATH* during install. All the `census-extract`
commands in [section 4](#4-using-the-command-line) then work as written.
Uninstall from *Settings → Apps*.

> Need to build the installer yourself? See `windows/BUILDING.md`.

### Any OS: install with Python

You need **Python 3.9 or newer**. Check with `python --version`, or
`python3 --version` on macOS. Download Python from python.org if needed. On
Windows, tick **"Add Python to PATH"** in the installer.

Unzip the project, open a terminal in the `census-extractor` folder, and run:

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install .
```

This installs the `census-extract` command. Each time you open a new terminal,
run the `activate` line again before using it.

> No-install alternative: `pip install -r requirements.txt`, then use
> `python -m census_extractor ...` anywhere this guide says `census-extract ...`.

## 2. Get a Census API key (required)

The Census Bureau requires a key for all data requests.

1. Go to <https://api.census.gov/data/key_signup.html> and enter your email.
2. **Click the activation link in the email.** The key won't work until you do.
3. Save the key as an environment variable so you don't have to paste it every time:

**macOS / Linux.** Add this line to `~/.zshrc` or `~/.bashrc` so it persists:
```bash
export CENSUS_API_KEY="your_key_here"
```
**Windows.** Run this once, then open a new terminal:
```powershell
setx CENSUS_API_KEY "your_key_here"
```

You can also paste the key into the UI's sidebar, or pass `--key YOUR_KEY` on
the command line.

---

## 3. Using the web UI

```bash
census-extract ui
```
Your browser opens at <http://localhost:8501>. To stop it, press **Ctrl+C** in the terminal.

The steps are numbered across the sidebar and the tabs:

**1 · Data source (sidebar)**
- **API key**: filled in automatically if you set `CENSUS_API_KEY`.
- **Dataset**, then **Year**: the list shows the most common datasets. Tick
  *Show all datasets* to see everything, or type any dataset path. The Year list
  only offers years that dataset is actually published for.
- **Also stack these years**: optional. Pulls the same variables for several
  years into one CSV and adds a `year` column.

**2 · Variables**
- **Search individual variables**: type keywords like `median household income`.
  Every word must match. Tick **Add** on the rows you want, then click **Add checked variables**.
- **Add whole tables**: search tables like `educational attainment`, preview the
  variables, then click **Add entire table**.
- **Paste variable codes directly**: useful if you already know the codes.
- **Your selection**: tick **Remove** to drop items.

> The first time you open a dataset/year, the variable list takes 10–30 seconds
> to load. It's cached on your computer after that, so later loads are instant.

**3 · Geography**
- **One row per…**: the level you want, such as state, county, tract, place, zip code (ZCTA) or congressional district.
- **Which ones?** `*` means all of them, or list specific codes such as `086,011`.
- **Inside which parent area?**: shown when the level needs one. For example,
  tracts must be inside a state, and in some years a county as well.
- The box at the bottom shows the exact `for=` and `in=` query that will be sent.

**4 · Options & filters**
- **Include margins of error**: adds the `…M` column after each `…E` estimate.
  Recommended for ACS data, especially for small areas.
- **Column headers**: codes (best for analysis), labels (most readable), or both.
- **Row filters**: keep only rows that meet your criteria. See [section 6](#6-row-filters-criteria).

**5 · Extract & download**
- Click **▶ Run extraction**, check the preview, then use:
  - **Download CSV**: your data
  - **Data dictionary**: what every column code means
  - **Save settings (YAML)**: a config file for re-running the same pull from the
    command line (`census-extract run that_file.yaml`), for example next year
    with only the year changed.

---

## 4. Using the command line

Run `census-extract --help`, or `census-extract <command> --help`, for every option.

### Find what you need
```bash
# Search variables (dataset defaults to acs/acs5; year defaults to the newest one that dataset has)
census-extract search "median household income"
census-extract search "poverty" --dataset acs/acs5/subject --year 2023

# Search tables, then list every variable in one
census-extract groups --search "health insurance"
census-extract table B27001

# Which geographies does a dataset support, and what do they need?
census-extract geos --dataset acs/acs5 --year 2024

# What datasets exist?
census-extract datasets --year 2024 --search acs
```

### Extract with options (quick)
```bash
# Population and median income for every county in Florida, with margins of error
census-extract quick --vars B01003_001E,B19013_001E --geo county --within state=FL --moe -o fl_counties.csv

# Every census tract in Miami-Dade County (state 12, county 086), whole table B19001
census-extract quick --groups B19001 --geo tract --within state=12 --within county=086 -o miami_tracts.csv

# All states, labelled headers, only states with 5M+ people, two years stacked
census-extract quick --vars B01003_001E,B19013_001E --geo state --year 2019 2024 \
    --headers both --filter "B01003_001E >= 5000000" -o big_states.csv

# Save the options as a config for next time
census-extract quick --vars B01003_001E --geo county --within state=TX --save-config tx.yaml
```

### Extract from a config file (repeatable)
```bash
census-extract init my_extract.yaml     # writes a fully commented example
# ...edit my_extract.yaml...
census-extract run my_extract.yaml
census-extract run my_extract.yaml -o different_name.csv
```

---

## 5. Config file reference

The config can be YAML (`.yaml`/`.yml`) or JSON. Example:

```yaml
dataset: acs/acs5
year: 2024                  # or:  years: [2019, 2024]
variables:
  - B01003_001E             # Total population
  - B19013_001E             # Median household income
  - group(B25003)           # whole tables can go here too...
groups:
  - B15003                  # ...or here
geography: county
geo_filter: "*"             # or "086,011"
within:
  state: Florida            # name, 2-letter code or FIPS; "FL,GA" for several
include_moe: true
headers: code               # code | label | both
filters:
  - B01003_001E >= 50000
output: florida_counties.csv
write_dictionary: true
```

| Option | Required | Default | Meaning |
|---|---|---|---|
| `dataset` | yes | `acs/acs5` | API dataset path (see [section 7](#7-choosing-datasets-variables-and-geographies)) |
| `year` / `years` | yes | | One year, or a list to stack |
| `variables` | one of these two | | Variable codes; `group(X)` also accepted |
| `groups` | one of these two | | Whole table codes |
| `geography` | | `state` | Level to return, e.g. `county`, `tract`, `block group`, `place`, `zip code tabulation area` |
| `geo_filter` | | `*` | Which units at that level |
| `within` | if the level needs it | | Parent geographies, in order (state, then county, ...) |
| `include_moe` | | `false` | Add margin-of-error columns |
| `headers` | | `code` | Column header style |
| `filters` | | | List of row filters |
| `output` | | `census_extract.csv` | CSV path |
| `write_dictionary` | | `true` | Also write `<output>_dictionary.csv` |

Variables are checked before anything downloads. A typo or a code that doesn't
exist that year stops a single-year run with a clear message. In a multi-year
run, that variable is skipped for the affected year with a warning.

---

## 6. Row filters (criteria)

Filters are pandas expressions written with **variable codes**. A row is kept
only if it passes **every** filter. Filters can only use variables that are in the
extract, so add a variable first if you want to filter on it.

| Goal | Filter |
|---|---|
| Population at least 50,000 | `B01003_001E >= 50000` |
| Median income under $60k | `B19013_001E < 60000` |
| Between two values | `100000 <= B25077_001E <= 300000` |
| Either condition | `B01003_001E > 1e6 or B19013_001E > 90000` |
| Has a value (not blank) | `B19013_001E.notna()` |
| Name contains text | `NAME.str.contains("County")` |
| Specific states | `state in ["12", "13"]` |
| Ratio of two variables | `B17001_002E / B17001_001E > 0.2` (poverty rate over 20%) |

---

## 7. Choosing datasets, variables and geographies

### Common datasets
| Path | What it is | Variable codes look like |
|---|---|---|
| `acs/acs5` | ACS 5-year detailed tables. Covers every area, down to block group. **Best default.** | `B19013_001E` |
| `acs/acs5/profile` | ACS 5-year data profiles (ready-made percentages) | `DP03_0062E`, `DP03_0062PE` |
| `acs/acs5/subject` | ACS 5-year subject tables | `S1901_C01_012E` |
| `acs/acs1` | ACS 1-year. Only areas with **65,000+ people** | `B19013_001E` |
| `dec/dhc` | 2020 Census, Demographic & Housing Characteristics | `P1_001N` |
| `dec/pl` | 2020 Census redistricting counts | `P1_001N` |

### Variable codes
- `B19013_001E`: table **B19013**, line **001**, **E**stimate. The matching
  **M** (`B19013_001M`) is the margin of error.
- Profile tables also have percent versions: `…PE` and `…PM`.
- Codes and table contents can change between years. When comparing years, use
  `census-extract search` or the UI on each year, and check the ACS
  "Table & Geography Changes" pages on census.gov.

### Geographies
Run `census-extract geos` for the full list for your dataset. Common ones:

| Level | Usually needs | Example |
|---|---|---|
| `us` | | `--geo us --geo-filter 1` |
| `state` | | `--geo state` |
| `county` | (optional) state | `--geo county --within state=CA` |
| `tract` | state (+ county in some years) | `--geo tract --within state=12 --within county=086` |
| `block group` | state, county | `--geo "block group" --within state=12 --within county=086` |
| `place` (cities/towns) | state | `--geo place --within state=TX` |
| `zip code tabulation area` | (some years: state) | `--geo "zip code tabulation area" --geo-filter 33139,33140` |
| `congressional district` | state | `--geo "congressional district" --within state=NY` |
| `metropolitan statistical area/micropolitan statistical area` | | quote it on the command line |

**Finding codes:** run a first extract one level up, and the codes appear in the
output columns. For example, a county pull for a state shows every county's
3-digit code.

---

## 8. What's in the output

**`your_file.csv`**
- `GEOID`: the full geographic ID (state + county + tract…). It joins directly to
  Census TIGER/Line shapefiles for mapping.
- `NAME`: a readable area name.
- The separate geography code columns (`state`, `county`, …).
- Your variables, as **real numbers**. Census "no data" codes such as
  `-666666666` are turned into **blank cells**.
- `year`, when you stacked several years.
- Saved as UTF-8 with a byte-order mark, so Excel shows accented place names
  (like Doña Ana County) correctly.

**`your_file_dictionary.csv`**: every variable code with its full label, concept and table.

---

## 9. Troubleshooting

| Message / symptom | Fix |
|---|---|
| "A Census API key is required" | Set `CENSUS_API_KEY` (see [section 2](#2-get-a-census-api-key-required)), open a new terminal, or use `--key`. |
| "rejected the API key" / "Invalid Key" | Use the key exactly as emailed, and make sure you clicked the activation link. |
| HTTP 404 / "isn't available for YEAR" | That dataset isn't published for that year. For example, ACS 5-year currently ends at 2024 while ACS 1-year goes to 2025. The error lists the available years and the exact URL that failed. Run `census-extract datasets --search acs` to see what exists. |
| HTTP 400 "unknown/unsupported geography hierarchy" | The level needs different parents. Run `census-extract geos` and check the *requires* column. |
| "Variable(s) not found" | The code is wrong or doesn't exist that year. Search again for that year. |
| Empty result / 0 rows | Nothing matched your geography codes, or your filters removed everything. |
| Blank cells | The Census Bureau has no reliable estimate for that cell (common for small areas or ACS 1-year data). |
| Very large pulls (every US tract) | Pull one state at a time with `--within state=XX`, or list several states (`state=CA,OR,WA`). |
| Slow first search | The variable list is downloading. It's cached in `~/.cache/census_extractor` after that. Delete that folder, or use `--no-cache`, to refresh it. |
| `census-extract: command not found` | Activate the virtual environment, or use `python -m census_extractor`. On Windows installs, use the Start-menu Command Prompt shortcut. |

### Notes
- More than 50 variables? That's fine. The tool splits the request into batches
  (the API's limit per call) and joins the results automatically.
- Temporary API errors (rate limits, server hiccups) are retried automatically with back-off.
- Time-series API datasets (`timeseries/...`) aren't supported. This tool covers
  the year-based datasets, which include ACS and the decennial census.

### Running the tests
```bash
pip install pytest
pytest
```
The tests use a simulated Census API, so they run offline and don't need a key.
