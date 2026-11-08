# Shared e-scooters in Turin (2024): demand, spatial dynamics and economic viability

Reproducible analysis of one year (2024) of trip-level data from the three
shared e-scooter operators active in Turin: **A - Lime**, **B - Voi**, **C - Bird**.
The pipeline goes from the raw operator files to every table, chart and map
used in the report, with explicit statistical tests and uncertainty.

Course project, *Digital Skills for Sustainable Societal Transitions*,
Politecnico di Torino, A.Y. 2025/26.

## What the pipeline does

| Step | Module | Main outputs |
|---|---|---|
| Harmonise and clean the three operators' raw files | `io.py`, `cleaning.py` | sequential cleaning log |
| Demand profiles, trip characteristics, fleet utilisation | `descriptive.py` | monthly, weekday and hourly profiles; Lorenz/Gini; operator tests |
| Battery consumption (A, B) | `battery.py` | Tukey regimes; regression of battery drop on distance and duration |
| OD matrices and zonal indicators | `od.py`, `spatial.py` | self-containment, net flow, tidal index with FDR-controlled tests; Moran's I and LISA |
| Idle time between trips and operator relocations | `parking.py` | median idle time and relocation-rate maps |
| Comparison with scheduled public transport (GTT GTFS) | `transit.py` | bivariate hexagon maps, direct-line and metro-access shares |
| Zonal demand model and equity (ISTAT IDISE) | `demand_model.py` | negative binomial IRRs, SLX robustness, residual map |
| Profit and loss, Monte Carlo, break-even | `finance.py` | P&L under two fleet bases, P(profit > 0), break-even utilisation |

All parameters (cleaning thresholds, time bands, cost assumptions, Monte Carlo
ranges) are in [`src/micromobility/config.py`](src/micromobility/config.py).

## Corrections with respect to the first version of the analysis

The rewrite found several issues in the original scripts that changed results:

| Issue | Effect in the first report | Fix |
|---|---|---|
| Intra-zonal trips removed before computing self-containment | Self-containment 0.01–0.08 % | Kept; real value is ~11 % for all operators |
| Voi timestamps: day overwritten with `01` | Voi weekday/hour/idle-time results were artefacts (e.g. "Sunday busiest", "most liquid fleet") | Timestamps parsed correctly |
| Voi timestamps are in UTC (lag +1 h winter / +2 h summer vs other operators) | Voi peaks reported at 06–07 and 16–17 | Converted to Europe/Rome (`scripts/check_time_offsets.py`) |
| Voi "2024" file actually merged 2024 + Jan–Oct 2025, plus 59k duplicated rows | 256k trips, 1,440 vehicles, 39 % reserved | 98k trips, 1,180 vehicles, 23 % reserved |
| GTFS services matched to months by month number only, and all service variants summed | December/July supply +931 % | Feed has a full timetable only until 28 Feb 2026, so a seasonal PT comparison is not possible with it; one reference weekday is used |
| Weekday totals not normalised (2024 has 53 Mondays and Tuesdays) | Distorted weekday ranking | Seasonal index plus Friedman test with weeks as blocks |
| Overlapping per-filter counts in the cleaning table | Rows did not add up | Sequential (waterfall) log |
| Means of heavily skewed idle times | Inflated, outlier-driven values | Medians, gaps > 24 h reported separately |

## Repository layout

```
turin-micromobility/
├── src/micromobility/      # analysis package (one module per step)
├── scripts/
│   ├── run_all.py          # full pipeline
│   └── check_time_offsets.py
├── report/
│   ├── report.md           # report text (source)
│   └── build_report.py     # builds the .docx with figures and tables
├── outputs/                # generated: figures/, maps/, tables/, zonal_indicators.gpkg
├── requirements.txt
└── README.md
```

## How to run

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows (use source .venv/bin/activate elsewhere)
pip install -r requirements.txt

# point to the folder that contains "DATI MONOPATTINI SHARING/", "from_Geoportale/" and "IDISE/"
set MICROMOBILITY_DATA_ROOT=C:\path\to\data          # default: parent folder of the repository

python scripts/run_all.py --rebuild                  # about 1-2 minutes
python report/build_report.py                        # builds the Word report locally (not versioned)
```

Run a subset with `--steps descriptive od transit`. Cleaned trips are cached
in `outputs/cache/` (git-ignored).

### Expected data layout

```
<DATA_ROOT>/
├── DATI MONOPATTINI SHARING/
│   ├── OPERATORE A - Lime/Dati/Torino_Corse24-25_MENSILI_senza_percorso/2024/*.csv
│   ├── OPERATORE B - Voi/Dati/2024/DATINOLEGGI_2024MM.xlsx
│   ├── OPERATORE C - Bird/Dati/Bird Torino - 2024.csv
│   └── TPL/gtt_gtfs/*.txt                  # GTT GTFS feed
├── from_Geoportale/zone_statistiche_geo/   # 94 statistical zones (Comune di Torino)
└── IDISE/DATI/Torino_IDISE_2021/Torino_IDISE_2021_ASC.xlsx   # ISTAT IDISE 2021
```

The trip-level data were provided by the Municipality of Turin for teaching
purposes and are **not** included. Only aggregated, zone-level outputs are versioned.

## Statistical methods at a glance

* **Uncertainty**: exact order-statistic CIs for medians, Wilson CIs for
  proportions, t-intervals for means of daily series.
* **Group comparisons**: Kruskal–Wallis with ε², pairwise Cliff's δ (effect
  sizes matter more than p-values with n ≈ 1.6 M), Friedman test for the
  weekday effect with weeks as blocks.
* **Zonal tests**: exact binomial test (inflow vs outflow) and two-proportion
  z-test (AM vs PM attraction share), with Benjamini–Hochberg FDR at 5 %.
  Non-significant zones are hatched on the maps.
* **Spatial statistics**: global Moran's I and LISA clusters (queen
  contiguity, 999 permutations).
* **Models**: OLS with HC3 errors (battery drop); negative binomial GLM with
  area offset and an SLX robustness check (zonal demand).
* **Finance**: Monte Carlo propagation of parameter uncertainty (20,000
  draws, triangular priors) and analytical break-even utilisation.

## Known limitations

* Distances are straight-line O–D distances (no route polylines for B and C).
* The GTT feed (2025–26) is a proxy for 2024 and has no summer timetable.
* Cost parameters are literature- and market-based assumptions, not operator accounts.
* Residual spatial autocorrelation remains in the zonal demand model
  (Moran's I ≈ 0.2), so its standard errors are likely somewhat optimistic.
