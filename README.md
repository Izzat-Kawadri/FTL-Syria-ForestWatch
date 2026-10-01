# Syria ForestWatch — Forestry & Climate Dashboard

FTL AI4Climate Python Hackathon 2026 · Group 3

**Question:** To what extent do changes in Syria's forest cover and forestry
emissions (1990–2025) correlate with long-term temperature and precipitation trends?

## Quick start

1. Put the 5 data files in `data/` (see table below — they are already in the repo):
   `cru-x0.5_timeseries.xlsx` (sheets: pr, tas, tasmax, tasmin) ·
   `FAOSTAT_data_land_use.csv` · `FAOSTAT_data_land_cover.csv` ·
   `FAOSTAT_data_forest emissions.csv` · `FAOSTAT_data_en_9-29-2026.csv`
2. `pip install -r requirements.txt` (pandas, numpy, scipy, openpyxl, matplotlib)
3. `python build.py`
4. Open `docs/index.html`. The header badge shows where the data came from
   (live APIs vs cached copies vs local files).

The build NEVER hard-fails on network issues: for every source it tries
live API → cached API copy → local file.

## Getting live API data (the authentication fix)

FAOSTAT now returns **401 Unauthorized** for anonymous API calls. To enable live
FAOSTAT data (optional — local CSVs work fine without it):

1. Create a **free** account at https://www.fao.org/faostat/ (Sign in → Register).
2. Set environment variables before running build.py:
   - macOS/Linux: `export FAOSTAT_USER="your@email.com"` · `export FAOSTAT_PASSWORD="yourpassword"`
   - Windows: `setx FAOSTAT_USER "your@email.com"` · `setx FAOSTAT_PASSWORD "yourpassword"`
   - Or reuse an existing token: `export FAOSTAT_API_TOKEN="eyJ..."`
3. build.py fetches a JWT Bearer token automatically, caches it 50 minutes
   (tokens expire after 60), and tries api/v1 then api/v2.

World Bank CCKP (CRU climate) needs no key; build.py tries both the legacy
`/api/v1/` and the documented `/cckp/v1/` endpoint shapes.

## Host on GitHub Pages

- Option A: commit everything → Settings → Pages → Deploy from branch → `main` / `/docs`.
- Option B (auto-build): Settings → Pages → Source: _GitHub Actions_. The workflow
  runs `python build.py` on every push — the local `data/` files make it robust
  even if the FAOSTAT API is down or unauthenticated.

## Hackathon deliverables (all in this repo)

| Required item                 | Where                                                         |
| ----------------------------- | ------------------------------------------------------------- |
| Python notebook (.ipynb)      | `notebooks/` — CRU plot, land use, emissions, wood production |
| Dataset or link               | `data/` local files + API links (CCKP, FAOSTAT) in References |
| Final presentation ≤ 7 slides | `Group3_Frontier_Tech_Leaders_Final_7_Slides.pptx`            |
| Short project summary         | see below                                                     |
| Colab/GitHub link             | this repo (Pages URL)                                         |

## Short project summary (max ~150 words)

Syria's climate is warming (~+0.5 °C/decade, p&lt;0.001, 1990–2025) while
precipitation shows no significant trend but repeated drought years (1999, 2008,
2017, 2025). Over the same period, reported forest land grew +17% (451→528 kha),
planted forest +35%, and woody crops +37%, while tree-covered areas stayed
stable. Forestland remained a net carbon sink throughout (≈ −1,600 kt CO2/yr,
FAO Tier 1). Raw forest–climate correlations are strong (r ≈ +0.70 between
forest land and temperature) but collapse to r ≈ +0.05 after detrending —
the association is driven by shared long-term trends, not year-to-year coupling.
We deliver an open, API-driven Python dashboard (Syria ForestWatch) for
monitoring these indicators, with early-warning thresholds, and propose
satellite-data + ML extensions. Supports SDG 13 and SDG 15.
