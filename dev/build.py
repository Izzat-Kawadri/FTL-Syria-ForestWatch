"""
Syria ForestWatch - dashboard builder (Python -> static site for GitHub Pages)
Group 3 | FTL AI4Climate Python Hackathon 2026

WHAT'S FIXED IN THIS VERSION
----------------------------
1) FAOSTAT API now requires a JWT Bearer token (anonymous calls return 401).
   - Register a FREE account at https://www.fao.org/faostat/  ("Sign in" -> register)
   - Set environment variables, then run build.py as usual:
         export FAOSTAT_USER="your@email.com"
         export FAOSTAT_PASSWORD="yourpassword"
     (Windows:  setx FAOSTAT_USER "your@email.com"  &&  setx FAOSTAT_PASSWORD "yourpassword")
   - Or pass a token directly:  export FAOSTAT_API_TOKEN="eyJhbGci..."
   - The token expires after ~60 min; we cache it for 50 min in data/api_cache/fao_token.json
   - We try api/v1 then api/v2 (v1 is being phased out).
2) World Bank CCKP: tries both the legacy /api/v1/ and the documented /cckp/v1/ URL shapes.
3) Fallback chain for EVERY source:  live API -> cached API copy -> local file in data/.
   So the build works offline, in CI, and on GitHub Pages Actions.

Usage:   python build.py            (real data; APIs optional thanks to fallback)
         python build.py --demo     (synthetic data, layout testing only)
Output:  docs/index.html + docs/merged_dataset.csv + docs/figs/*.png
"""
import glob, json, sys, os, time, datetime, urllib.request, urllib.error
import numpy as np, pandas as pd
from scipy import stats

START, END = 1990, 2025
DEMO = "--demo" in sys.argv
D = "data"

THRESH = {"pr": 200, "tas": 19.5, "tasmax": 26.5, "tasmin": 12.5,
          "forest_land": 500, "planted": 250, "woody": 800, "tree_cover": 265}
P1, P2 = (1990, 2007), (2008, 2025)

# ---------- 0. CONFIG / AUTH ----------
CACHE = os.path.join(D, "api_cache"); os.makedirs(CACHE, exist_ok=True)
SOURCES = []

FAO_USER = os.environ.get("FAOSTAT_USER", "")
FAO_PASS = os.environ.get("FAOSTAT_PASSWORD", "")
FAO_TOKEN_ENV = os.environ.get("FAOSTAT_API_TOKEN", "")
TOKEN_PATH = os.path.join(CACHE, "fao_token.json")
TOKEN_TTL = 50 * 60  # refresh 10 min before the 60-min expiry

# CCKP: legacy shape first (the one the team originally used), documented shape second
CCKP_PATTERNS = [
    "https://cckpapi.worldbank.org/api/v1/cru-x0.5_timeseries_{v}_timeseries_annual_1901-2025_mean_historical_cru_ts4.10_mean/SYR?_format=json",
    "https://cckpapi.worldbank.org/cckp/v1/cru-x0.5_timeseries_{v}_timeseries_annual_1901-2025_mean_historical_cru_ts4.10_mean/SYR?_format=json",
]
FAO_BASES = ["https://faostatservices.fao.org/api/v1", "https://faostatservices.fao.org/api/v2"]
FAO_DOMAINS = {"land_use": "RL", "land_cover": "LC", "emissions": "GF", "wood": "FO"}


# ---------- 1. HTTP WITH RETRY + CACHE ----------
def http_json(url, name, headers=None, tries=3, data=None):
    """GET/POST json with retries; cache successes; fall back to the last cached copy."""
    cp = os.path.join(CACHE, name + ".json")
    h = {"User-Agent": "syria-forestwatch/2.0 (+https://github.com/; hackathon)", "Accept": "application/json"}
    if headers: h.update(headers)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = json.loads(r.read().decode("utf-8"))
            json.dump(payload, open(cp, "w"))
            SOURCES.append(name + ": live API")
            return payload
        except Exception as e:
            print(f"  [{name}] attempt {i+1}/{tries} failed: {e}")
            time.sleep(2 * (i + 1))
    if os.path.exists(cp):
        SOURCES.append(name + ": cached API copy")
        print(f"  [{name}] using cached copy {cp}")
        return json.load(open(cp))
    return None


# ---------- 2. FAOSTAT JWT AUTH (the actual fix) ----------
def fao_token():
    """Return a Bearer token for FAOSTAT, or None if unauthenticated.
    Priority: FAOSTAT_API_TOKEN env -> fresh cached token -> login with user/pass."""
    if FAO_TOKEN_ENV:
        return FAO_TOKEN_ENV
    if os.path.exists(TOKEN_PATH):
        try:
            tk = json.load(open(TOKEN_PATH))
            if time.time() - tk.get("ts", 0) < TOKEN_TTL:
                return tk["token"]
        except Exception:
            pass
    if not (FAO_USER and FAO_PASS):
        print("  [fao_auth] no credentials -> FAOSTAT API calls would return 401.")
        print("             Set FAOSTAT_USER/FAOSTAT_PASSWORD (free account at fao.org/faostat).")
        print("             Falling back to cached copies / local CSV files.")
        return None
    body = json.dumps({"username": FAO_USER, "password": FAO_PASS}).encode()
    for base in FAO_BASES:
        for ep in ("/auth/token", "/auth/login", "/Authentication/Token", "/token"):
            try:
                req = urllib.request.Request(base + ep, data=body, headers={
                    "Content-Type": "application/json", "Accept": "application/json",
                    "User-Agent": "syria-forestwatch/2.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    js = json.loads(r.read().decode("utf-8"))
                tok = (js.get("token") or js.get("access_token") or js.get("accessToken")
                       or (js.get("data") or {}).get("token"))
                if tok:
                    json.dump({"token": tok, "ts": time.time()}, open(TOKEN_PATH, "w"))
                    print("  [fao_auth] JWT obtained and cached (valid ~60 min).")
                    return tok
            except Exception:
                continue
    print("  [fao_auth] login failed; check FAOSTAT_USER/FAOSTAT_PASSWORD.")
    return None


def fao_df(domain):
    """Fetch a FAOSTAT domain with Bearer auth; try v1 then v2; fall back to cache."""
    headers = {}
    tok = fao_token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    for base in FAO_BASES:
        url = (f"{base}/en/data/{FAO_DOMAINS[domain]}?area=212&area_cs=FAO&show_codes=false"
               "&show_unit=true&show_flags=false&null_values=false&output_type=objects&page_size=100000")
        js = http_json(url, f"fao_{domain}_{base.rsplit('/', 1)[-1]}", headers=headers)
        if js and js.get("data"):
            df = pd.DataFrame(js["data"]); df.columns = [c.strip() for c in df.columns]
            df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
            df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
            return df.dropna(subset=["Value", "Year"]).astype({"Year": int})
    return None


# ---------- 3. CCKP (CRU) ----------
def parse_cckp(js):
    def walk(o):
        if isinstance(o, dict):
            if o and all(str(k)[:4].isdigit() for k in o) and \
               all(isinstance(v, (int, float)) or v is None for v in o.values()):
                return o
            for v in o.values():
                r = walk(v)
                if r: return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v)
                if r: return r
        return None
    d = walk(js.get("data", js)) if js else None
    if not d: return None
    return pd.Series({int(str(k)[:4]): v for k, v in d.items()}, dtype=float)


def cru_from_xlsx():
    f = first("cru-x0.5_timeseries.xlsx")
    if not f: return {}
    out = {}
    for sheet, key in enumerate(["pr", "tas", "tasmax", "tasmin"]):
        row = pd.read_excel(f, sheet_name=sheet).iloc[0]
        s = row[[c for c in row.index if str(c)[:4].isdigit()]]
        s.index = [int(str(i)[:4]) for i in s.index]
        out[key] = s.astype(float)
    SOURCES.append("CRU: local xlsx")
    return out


def first(pattern):
    m = sorted(glob.glob(os.path.join(D, pattern)))
    return m[0] if m else None


# ---------- 4. LOAD ----------
def load_real():
    out = {}
    for v in ["pr", "tas", "tasmax", "tasmin"]:                    # climate: CCKP
        s = None
        for pat in CCKP_PATTERNS:
            s = parse_cckp(http_json(pat.format(v=v), "cckp_" + v + "_" + pat.split("/")[3].replace("?", "_")))
            if s is not None: break
        if s is not None: out[v] = s
    if len(out) < 4:                                               # fill gaps from xlsx
        out = {**cru_from_xlsx(), **out}
    frames = {}
    for k, pat in [("land_use", "*land_use*.csv"), ("land_cover", "*land_cover*.csv"),
                   ("emissions", "*forest_emissions*.csv"), ("wood", "FAOSTAT_data_en_*.csv")]:
        df = fao_df(k)                                             # FAOSTAT API
        if df is None:
            f = first(pat) or (first("*wood*.csv") if k == "wood" else None)
            if f:
                df = pd.read_csv(f); SOURCES.append(f"fao_{k}: local csv")
        frames[k] = df
    missing = [k for k in ["pr", "tas", "tasmax", "tasmin"] if k not in out] + \
              [k for k, v in frames.items() if v is None]
    if missing:
        sys.exit(f"No data for: {missing}.\nFix: check internet + FAOSTAT credentials, "
                 "or put the files in ./data (see README).")
    lu = frames["land_use"]; lu = lu[lu["Element"] == "Area"]
    out["forest_land"] = lu[lu["Item"] == "Forest land"].groupby("Year")["Value"].mean()
    out["planted"] = lu[lu["Item"] == "Planted Forest"].groupby("Year")["Value"].mean()
    lc = frames["land_cover"]
    out["woody"] = lc[lc["Item"] == "Woody crops"].groupby("Year")["Value"].mean()
    out["tree_cover"] = lc[lc["Item"] == "Tree-covered areas"].groupby("Year")["Value"].mean()
    em = frames["emissions"]
    out["net_emis"] = em[em["Element"].astype(str).str.contains("Net emissions") &
                         (em["Item"] == "Forestland")].groupby("Year")["Value"].mean()
    wf = frames["wood"]; wf = wf[wf["Area"].astype(str).str.contains("Syria")]
    wf = wf[wf["Element"].astype(str).str.contains("Production", na=False)]
    for item, g in wf.groupby("Item"): out["wood|" + item] = g.groupby("Year")["Value"].sum()
    empty = [k for k, v in out.items() if len(v) == 0]
    if empty: print("WARNING: empty series (item names may differ in the API):", empty)
    return {k: v for k, v in out.items() if len(v)}


def load_demo():
    rng = np.random.default_rng(7); y = np.arange(START, END + 1); t = y - START
    S = lambda v: pd.Series(v, index=y)
    pr = 300 + rng.normal(0, 50, len(y)); tas = 18.4 + 0.03 * t + rng.normal(0, .35, len(y))
    for yr, v in {1999: 184.58, 2008: 183.77, 2025: 182.98}.items(): pr[yr - START] = v
    tas[2010 - START] = 20.32
    em = np.select([y <= 2000, y <= 2010, y <= 2015, y <= 2020], [-1525.33, -1697.67, -1606.0, -1378.67], -1884.67)
    wy = np.arange(2000, 2025)
    return {"pr": S(pr), "tas": S(tas), "tasmax": S(tas + 6.7), "tasmin": S(tas - 6.9),
            "forest_land": S(450.99 + t * (527.81 - 450.99) / 35), "planted": S(218.15 + t * 2.2),
            "woody": S(594 + t * 10.4 + rng.normal(0, 18, len(y))), "tree_cover": S(265 + rng.normal(0, 1.5, len(y))),
            "net_emis": S(em),
            "wood|Roundwood": pd.Series(900 + rng.normal(0, 40, len(wy)).cumsum(), index=wy),
            "wood|Industrial roundwood": pd.Series(300 + rng.normal(0, 20, len(wy)).cumsum(), index=wy)}


raw = load_demo() if DEMO else load_real()

# ---------- 5. PREPARE ----------
META = {"tas": ("Mean temperature (tas)", "°C", "Climate"), "tasmax": ("Max temperature (tasmax)", "°C", "Climate"),
        "tasmin": ("Min temperature (tasmin)", "°C", "Climate"), "pr": ("Precipitation (pr)", "mm", "Climate"),
        "forest_land": ("Forest land area", "1000 ha", "Forest"), "planted": ("Planted forest area", "1000 ha", "Forest"),
        "woody": ("Woody crops area", "1000 ha", "Forest"), "tree_cover": ("Tree-covered areas", "1000 ha", "Forest"),
        "net_emis": ("Forestland net emissions", "kt CO2e", "Emissions")}
for k in raw:
    if k.startswith("wood|"):
        META[k] = ("Wood: " + k[5:], "t" if k == "wood|Wood fuel" else "m³", "Wood production")
years = list(range(START, END + 1))
df = pd.DataFrame({k: v.reindex(years) for k, v in raw.items()}); df.index.name = "Year"
os.makedirs("docs", exist_ok=True); os.makedirs("docs/figs", exist_ok=True)
df.round(4).reset_index().to_csv("docs/merged_dataset.csv", index=False)

# ---------- 6. ANALYSE ----------
def analyse(s):
    s = s.dropna()
    if len(s) < 3: return None
    lr = stats.linregress(s.index.astype(float), s.values)
    a, b = s.loc[P1[0]:P1[1]].mean(), s.loc[P2[0]:P2[1]].mean()
    fv, lv = s.iloc[0], s.iloc[-1]; n = s.index[-1] - s.index[0]
    cagr = ((lv / fv) ** (1 / n) - 1) * 100 if fv > 0 and lv > 0 and n else None
    return dict(mean=float(s.mean()), min=float(s.min()), min_y=int(s.idxmin()), max=float(s.max()), max_y=int(s.idxmax()),
                first=float(fv), first_y=int(s.index[0]), last=float(lv), last_y=int(s.index[-1]),
                pct=float((lv - fv) / abs(fv) * 100) if fv else None, cagr=cagr,
                slope=float(lr.slope), r2=float(lr.rvalue ** 2), p=float(lr.pvalue), p1=float(a), p2=float(b),
                p_change=float((b - a) / abs(a) * 100) if a else None)

ST = {k: analyse(df[k]) for k in df.columns}
ST = {k: v for k, v in ST.items() if v}

def pear(a, b):
    m = df[[a, b]].dropna()
    if len(m) < 5: return None, None, len(m)
    r, p = stats.pearsonr(m[a], m[b]); return float(r), float(p), len(m)

dd = df.diff()
pairs = []
for f in ["forest_land", "planted", "woody", "tree_cover", "net_emis"]:
    for c in ["tas", "tasmax", "tasmin", "pr"]:
        r, p, n = pear(f, c)
        rd = dd[[f, c]].corr().iloc[0, 1]
        pairs.append(dict(f=f, c=c, r=r, p=p, n=n, rd=None if np.isnan(rd) else float(rd)))
pairs = [q for q in pairs if q["r"] is not None]

exceed = {k: [int(y) for y in df[k].dropna().index[df[k].dropna() > v]] for k, v in THRESH.items() if k in df}
drought = [int(y) for y in df["pr"].dropna().index[df["pr"].dropna() < THRESH["pr"]]]

F = []
t = ST.get("tas")
if t: F.append(f"Mean temperature rose by {t['slope']*10:.2f} °C per decade over {START}–{END} "
               f"(R² = {t['r2']:.2f}, p = {t['p']:.3g}); the {P2[0]}–{P2[1]} average is {t['p2']-t['p1']:+.2f} °C vs {P1[0]}–{P1[1]}.")
p_ = ST.get("pr")
if p_: F.append(f"Precipitation shows no clear monotonic trend ({p_['slope']:+.2f} mm/yr, p = {p_['p']:.2f}) but is highly "
                f"variable: driest year {p_['min_y']} ({p_['min']:.0f} mm); {len(drought)} year(s) below the 200 mm drought threshold.")
fl = ST.get("forest_land")
if fl: F.append(f"Forest land area changed {fl['pct']:+.1f}% ({fl['first']:.1f} → {fl['last']:.1f} thousand ha). "
                f"Planted forest: {ST['planted']['pct']:+.1f}%. Woody crops (1992–2022): {ST['woody']['pct']:+.1f}%. "
                f"Tree-covered areas: {ST['tree_cover']['pct']:+.1f}% (stable).")
e = ST.get("net_emis")
if e: F.append(f"Forestland net emissions are negative throughout (a net sink): mean {e['mean']:.0f} kt, strongest sink "
               f"{e['min_y']} ({e['min']:.0f} kt). The series moves in steps — FAO Tier-1 reporting, not annual observation.")
if pairs:
    top = max(pairs, key=lambda q: abs(q["r"]))
    F.append(f"Strongest forest–climate correlation: {META[top['f']][0]} vs {META[top['c']][0]} (r = {top['r']:+.2f}, n = {top['n']}). "
             f"After removing trends (year-on-year changes) it is r = {top['rd']:+.2f} — most of the link is shared trend, not year-to-year coupling.")

# ---------- 7. NOTEBOOK-STYLE FIGURES (matplotlib) ----------
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FIGD = "docs/figs"
    C = dict(ann="#4a90c2", roll="#d64545", trend="#d9822b", th="#555555", ext="#c22f2f")

    def panel(ax, s, th, below=False, w=10):
        s = s.dropna()
        ax.plot(s.index, s.values, color=C["ann"], alpha=.55, lw=1.2, label="Annual")
        ax.plot(s.index, s.rolling(w).mean(), color=C["roll"], lw=2.2, label=f"{w}-yr rolling mean")
        lr = stats.linregress(s.index.astype(float), s.values)
        ax.plot([s.index[0], s.index[-1]], [lr.intercept + lr.slope * s.index[0], lr.intercept + lr.slope * s.index[-1]],
                ls="--", color=C["trend"], lw=1.8, label=f"Trend ({lr.slope:+.2f}/yr, R²={lr.rvalue**2:.2f})")
        ax.axhline(th, ls=":", color=C["th"], lw=1.2, label=f"Threshold {th}")
        ex = s[s < th] if below else s[s > th]
        ax.scatter(ex.index, ex.values, color=C["ext"], zorder=5, s=26,
                   label="Drought years (<200 mm)" if below else f"Extreme years (>{th})")
        ax.grid(True, ls="--", alpha=.4); ax.legend(loc="best", fontsize=7.5, framealpha=.85); ax.margins(x=.01)

    fig, axes = plt.subplots(4, 1, figsize=(11, 13), sharex=True)
    for ax, k in zip(axes, ["pr", "tas", "tasmax", "tasmin"]):
        panel(ax, df[k] if k != "pr" else raw["pr"], THRESH[k], below=(k == "pr"))
        ax.set_title({"pr": "Precipitation (pr) [mm]", "tas": "Mean temperature (tas) [°C]",
                      "tasmax": "Maximum temperature (tasmax) [°C]", "tasmin": "Minimum temperature (tasmin) [°C]"}[k],
                     fontsize=12, fontweight="bold")
    axes[-1].set_xlabel("Year")
    fig.suptitle("Syria climate 1901–2025 — CRU TS 4.10 (via World Bank CCKP)", fontsize=13)
    fig.tight_layout(); fig.savefig(f"{FIGD}/climate_overview.png", dpi=110, bbox_inches="tight"); plt.close(fig)

    land_panels = [("forest_land", "Forest land area [1000 ha]", 500), ("planted", "Planted forest area [1000 ha]", 250),
                   ("woody", "Woody crops area [1000 ha] (1992–2022)", 800), ("tree_cover", "Tree-covered areas [1000 ha] (1992–2022)", 265)]
    fig, axes = plt.subplots(4, 1, figsize=(11, 13))
    for ax, (k, ttl, th) in zip(axes, land_panels):
        panel(ax, df[k], th, w=5)
        s = df[k].dropna()
        ax.axhline(s.loc[:2007].mean(), ls="--", color="#2f9e5b", lw=1.4, label=f"{s.index[0]}–2007 avg ({s.loc[:2007].mean():.1f})")
        ax.axhline(s.loc[2008:].mean(), ls="--", color="#e08a2b", lw=1.4, label=f"2008–{s.index[-1]} avg ({s.loc[2008:].mean():.1f})")
        ax.legend(loc="best", fontsize=7, framealpha=.85); ax.set_title(ttl, fontsize=12, fontweight="bold")
    axes[-1].set_xlabel("Year")
    fig.suptitle("Syria forest & land-cover indicators — FAOSTAT", fontsize=13)
    fig.tight_layout(); fig.savefig(f"{FIGD}/forest_land_panels.png", dpi=110, bbox_inches="tight"); plt.close(fig)

    s = df["net_emis"].dropna()
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(s.index, s.values, marker="o", color="#2f7d4f", lw=2)
    ax.axhline(0, color="#888", lw=1); ax.fill_between(s.index, s.values, 0, alpha=.12, color="#2f7d4f")
    ax.axhline(s.loc[2000:2004].mean(), ls="--", color="#e08a2b", lw=1.4, label=f"2000–2004 avg ({s.loc[2000:2004].mean():.0f} kt)")
    ax.axhline(s.loc[2020:2024].mean(), ls="--", color="#8a5cc5", lw=1.4, label=f"2020–2024 avg ({s.loc[2020:2024].mean():.0f} kt)")
    ax.set_title("Forestland net emissions/removals — Syria (FAO Tier 1, FAOSTAT)", fontsize=13, fontweight="bold")
    ax.set_ylabel("kt CO2 (negative = net sink)"); ax.set_xlabel("Year")
    ax.grid(True, ls="--", alpha=.4); ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout(); fig.savefig(f"{FIGD}/forest_emissions.png", dpi=110, bbox_inches="tight"); plt.close(fig)

    wk = [k for k in df.columns if k.startswith("wood|")]
    fig, axes = plt.subplots(len(wk), 1, figsize=(11, 3 * len(wk) + 1), sharex=True)
    for ax, k in zip(axes, wk):
        w = df[k].dropna()
        ax.plot(w.index, w.values, marker="o", ms=3, color=C["ann"], label="Annual production")
        ax.plot(w.index, w.rolling(5).mean(), color=C["roll"], lw=2, label="5-yr mean")
        lr = stats.linregress(w.index.astype(float), w.values)
        ax.plot([w.index[0], w.index[-1]], [lr.intercept + lr.slope * w.index[0], lr.intercept + lr.slope * w.index[-1]],
                ls="--", color=C["trend"], lw=1.6, label=f"Trend ({lr.slope:+,.0f}/yr, R²={lr.rvalue**2:.2f})")
        st = ST[k]
        ax.set_title(f"{k[5:]} — {st['first']:,.0f} (2000) → {st['last']:,.0f} (2024), {st['pct']:+.1f}%",
                     fontsize=11, fontweight="bold")
        ax.grid(True, ls="--", alpha=.4); ax.legend(loc="best", fontsize=7.5)
    axes[-1].set_xlabel("Year")
    fig.suptitle("Syria wood production by item — FAOSTAT Forestry Production and Trade", fontsize=13)
    fig.tight_layout(); fig.savefig(f"{FIGD}/wood_production.png", dpi=110, bbox_inches="tight"); plt.close(fig)

    fl_ = ["forest_land", "planted", "woody", "tree_cover", "net_emis"]; cl_ = ["tas", "tasmax", "tasmin", "pr"]
    Z = np.array([[next(q["r"] for q in pairs if q["f"] == f and q["c"] == c) for c in cl_] for f in fl_])
    Zd = np.array([[next((q["rd"] for q in pairs if q["f"] == f and q["c"] == c), np.nan) or np.nan for c in cl_] for f in fl_])
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    for ax, Zm, ttl in zip(axes, [Z, Zd], ["Raw Pearson r (levels)", "Detrended Pearson r (year-on-year changes)"]):
        im = ax.imshow(Zm, cmap="RdBu_r", vmin=-1, vmax=1)
        ax.set_xticks(range(len(cl_)), cl_); ax.set_yticks(range(len(fl_)), fl_)
        for i in range(len(fl_)):
            for j in range(len(cl_)):
                v = Zm[i, j]
                ax.text(j, i, "–" if np.isnan(v) else f"{v:.2f}", ha="center", va="center", fontsize=9,
                        color="white" if abs(v) > .6 else "black")
        ax.set_title(ttl, fontsize=11, fontweight="bold")
    fig.colorbar(im, ax=axes, shrink=.85, label="Pearson r")
    fig.suptitle("Forest ↔ climate correlations — Syria 1990–2025", fontsize=12.5, fontweight="bold")
    fig.tight_layout(); fig.savefig(f"{FIGD}/correlation_heatmaps.png", dpi=110, bbox_inches="tight"); plt.close(fig)

    m = df[["forest_land", "tas"]].dropna()
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    sc = ax.scatter(m["tas"], m["forest_land"], c=m.index, cmap="viridis", s=42, zorder=3)
    lr = stats.linregress(m["tas"], m["forest_land"])
    xs = np.linspace(m["tas"].min(), m["tas"].max(), 10)
    ax.plot(xs, lr.intercept + lr.slope * xs, "--", color=C["trend"], lw=2, label=f"Raw fit: r = {lr.rvalue:.2f}")
    md_ = m.diff().dropna(); lrd = stats.linregress(md_["tas"], md_["forest_land"])
    ax.plot(xs, m["forest_land"].mean() + lrd.slope * (xs - m["tas"].mean()), ":", color="#c0392b", lw=2,
            label=f"YoY changes: r = {lrd.rvalue:.2f}")
    ax.set_xlabel("Mean temperature (°C)"); ax.set_ylabel("Forest land (1000 ha)")
    ax.set_title("Forest land vs mean temperature — mostly shared trend", fontsize=11.5, fontweight="bold")
    fig.colorbar(sc, label="Year"); ax.legend(fontsize=9); ax.grid(True, ls="--", alpha=.4)
    fig.tight_layout(); fig.savefig(f"{FIGD}/scatter_forest_tas.png", dpi=110, bbox_inches="tight"); plt.close(fig)
    print("Figures written to docs/figs/")
except ImportError:
    print("matplotlib not installed — skipping figure generation (pip install matplotlib)")

# ---------- 8. RENDER ----------
payload = dict(demo=DEMO,
               source=("Synthetic demo data" if DEMO else "Data: " + "; ".join(SOURCES) + " · built " + datetime.date.today().isoformat()),
               years=years, meta=META, thresh=THRESH, p1=P1, p2=P2,
               series={k: [None if pd.isna(v) else round(float(v), 4) for v in df[k]] for k in df.columns},
               stats=ST, pairs=pairs, exceed=exceed, findings=F, drought=drought)
html = open("template.html", encoding="utf-8").read().replace("__PAYLOAD__", json.dumps(payload, default=float))
open("docs/index.html", "w", encoding="utf-8").write(html)
open("docs/.nojekyll", "w").close()
print(f"Built docs/index.html  ({'DEMO data' if DEMO else 'real data'}, {len(df.columns)} variables, {len(pairs)} forest-climate pairs)")