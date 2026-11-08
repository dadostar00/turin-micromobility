"""Build the Word report from ``report/report.md`` and the pipeline outputs.

The Markdown source supports: ``%`` title lines, ``#``/``##``/``###`` headings,
paragraphs with **bold**, *italic* and `code`, bullet (``*``) and numbered
(``1.``) lists, images ``![caption](path)`` and table placeholders
``{{table:key}}`` filled from the CSV files in ``outputs/tables``.

    python report/build_report.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from micromobility import config  # noqa: E402

T = config.TABLE_DIR
OPS = {"A": "A - Lime", "B": "B - Voi", "C": "C - Bird"}


def fnum(x, d=0):
    return f"{x:,.{d}f}"


# ---------------------------------------------------------------------------
# Tables (title, DataFrame)
# ---------------------------------------------------------------------------
def t_cleaning():
    d = pd.read_csv(T / "cleaning_log.csv")
    d["cell"] = [fnum(r) if pd.isna(p) else f"{fnum(r)} ({p:.2f}%)" for r, p in zip(d.records, d.removed_pct_of_raw)]
    out = d.pivot(index="step", columns="operator", values="cell").reindex(d.step.unique())
    return "Sequential cleaning log (records removed at each step, % of raw records)", out.reset_index().rename(columns={"step": "Step"})


def t_summary():
    d = pd.read_csv(T / "desc_summary.csv").set_index("operator").T
    lab = {"valid_trips": "Valid trips", "unique_vehicles": "Vehicles (unique IDs)",
           "mean_daily_active_vehicles": "Mean vehicles active per day", "trips_per_vehicle_day": "Trips per active vehicle-day [95% CI]",
           "median_duration_min": "Median duration, min [95% CI]", "median_distance_km": "Median straight-line distance, km [95% CI]",
           "median_speed_kmh": "Median speed, km/h [95% CI]", "reserved_share_pct": "Reserved trips, % [95% CI]",
           "gini_trips_per_vehicle": "Gini of trips per vehicle", "mean_trips_per_day": "Mean trips per day"}
    d = d.loc[list(lab)].rename(index=lab)
    for r in ["Valid trips", "Vehicles (unique IDs)", "Mean vehicles active per day", "Mean trips per day"]:
        d.loc[r] = [fnum(float(v)) for v in d.loc[r]]
    d.loc["Gini of trips per vehicle"] = [f"{float(v):.2f}" for v in d.loc["Gini of trips per vehicle"]]
    return "Key indicators by operator, 2024", d.reset_index().rename(columns={"index": "Indicator"})


def t_battery():
    d = pd.read_csv(T / "battery_quadrants.csv")
    d["Operator"] = d.operator.map(OPS)
    d["Trips"] = d.trips.map(fnum)
    d["Share (%)"] = d.share_pct.map(lambda v: f"{v:.1f}")
    d["Battery drop, median [IQR]"] = [f"{m:.0f} [{a:.0f}–{b:.0f}]" for m, a, b in zip(d.median_drop, d.q1, d.q3)]
    return "Tukey consumption regimes", d[["Operator", "quadrant", "Trips", "Share (%)", "Battery drop, median [IQR]"]].rename(columns={"quadrant": "Regime"})


def t_od_annual():
    d = pd.read_csv(T / "od_annual.csv").set_index("operator")
    out = pd.DataFrame({
        "Trips with both ends in the city": d.trips_both_ends_in_city.map(fnum),
        "Intra-zonal trips": d.intra_zonal_trips.map(fnum),
        "Self-containment, % [95% CI]": [f"{v:.2f} {c}" for v, c in zip(d.self_containment_pct, d.self_containment_ci)],
        "Zones generating trips (of 94)": d.zones_generating_trips.astype(int).astype(str),
        "Connectivity, % of 8,742 OD pairs": d.connectivity_pct.map(lambda v: f"{v:.1f}"),
        "Pairs with ≥ 1 trip per week, %": d["pairs_with_>=1_trip_per_week_pct"].map(lambda v: f"{v:.1f}"),
        "Share of 10 busiest pairs, %": d.top10_pairs_share_pct.map(lambda v: f"{v:.1f}"),
    }).T
    return "Annual OD indicators", out.reset_index().rename(columns={"index": "Indicator"})


def t_od_sig():
    d = pd.read_csv(T / "od_significance.csv")
    names = {"am": "Net flow, AM peak", "interpeak": "Net flow, inter-peak", "pm": "Net flow, PM peak",
             "night": "Net flow, night", "tidal": "Tidal index"}
    d["cell"] = [f"{s}/{t}  (+{a} / −{g})" for s, t, a, g in zip(d.zones_significant, d.zones_tested,
                                                                   d.significant_attractors, d.significant_generators)]
    out = d.pivot(index="band", columns="operator", values="cell").reindex(list(names)).rename(index=names, columns=OPS)
    return ("Zones with a significant imbalance (5% FDR): significant/tested (+ attractors / − generators)",
            out.reset_index().rename(columns={"band": "Indicator"}))


def t_idle():
    d = pd.read_csv(T / "idle_summary.csv").set_index("operator")
    out = pd.DataFrame({
        "Idle periods": d.idle_events.map(fnum),
        "Gaps > 24 h, %": d.share_gaps_over_24h_pct.map(lambda v: f"{v:.1f}"),
        "Median idle time ≤ 24 h, h [95% CI]": d.median_idle_h,
        "Mean idle time ≤ 24 h, h": d.mean_idle_h.map(lambda v: f"{v:.1f}"),
        "Relocation > 300 m, % [95% CI]": d.relocation_rate_pct,
        "Kruskal–Wallis across time bands (ε²)": d.kruskal_bands_eps2.map(lambda v: f"{v:.3f}"),
    }).T
    return "Idle time and relocations", out.reset_index().rename(columns={"index": "Indicator"})


def t_pt_relation():
    d = pd.read_csv(T / "pt_relation.csv")
    d["cell"] = [f"{v:.0f}" if pd.isna(lo) else f"{v:.1f} [{lo:.1f}, {hi:.1f}]" for v, lo, hi in zip(d.share_pct, d.ci_low, d.ci_high)]
    out = d.pivot(index="indicator", columns="season", values="cell").reindex(d.indicator.unique())[["July", "December"]]
    out.loc["Trips analysed (working days, all operators)"] = [fnum(d[d.season == s].trips.iloc[0]) for s in ["July", "December"]]
    return "Trip-level relationship with the urban transit network (%, 95% CI)", out.reset_index().rename(columns={"indicator": "Indicator"})


def t_pt_assoc():
    d = pd.read_csv(T / "pt_association.csv")
    out = pd.DataFrame({
        "Scenario": d.scenario, "Active hexagons": d.active_hexagons,
        "Spearman ρ": d.spearman_rho.map(lambda v: f"{v:.2f}"), "Cramér's V": d.cramers_v.map(lambda v: f"{v:.2f}"),
        "Class 33 (both high)": d.hex_33_both_high, "Class 13 (micro high, PT low)": d.hex_13_micro_high_pt_low,
        "Class 31 (PT high, micro low)": d.hex_31_pt_high_micro_low})
    return "Association between PT supply and micromobility in 250 m hexagons", out


def t_demand():
    d = pd.read_csv(T / "demand_model_coefficients.csv").rename(columns={"Unnamed: 0": "Variable"})
    d = d[d.Variable != "Intercept"]
    out = pd.DataFrame({"Variable": d.Variable,
                        "IRR [95% CI]": [f"{a:.2f} [{b:.2f}, {c:.2f}]" for a, b, c in zip(d.IRR, d.IRR_low, d.IRR_high)],
                        "p-value": d.p_value.map(lambda v: "< 0.001" if v < 0.001 else f"{v:.3f}"),
                        "VIF": d.VIF.map(lambda v: f"{v:.1f}")})
    return "Negative binomial model of annual trip origins per zone (offset: log area; n = 89 zones)", out


def t_finance():
    d = pd.read_csv(T / "finance_pnl.csv")
    mc = pd.read_csv(T / "finance_monte_carlo.csv").set_index("operator")
    rows = ["revenue", "fixed: Fleet amortisation", "fixed: Insurance", "fixed: App / platform", "fixed: Warehouse",
            "fixed: Staff", "variable: Energy", "variable: Rebalancing & logistics", "variable: Maintenance",
            "variable: Connectivity", "total_cost", "profit"]
    lab = {"revenue": "Revenue", "total_cost": "Total cost", "profit": "Profit"}
    cols = {}
    for _, r in d.iterrows():
        key = f"{r.operator.split(' - ')[1]} ({'all IDs' if r.fleet_basis == 'unique' else 'deployed'})"
        cols[key] = [fnum(r[x] / 1000) for x in rows] + [f"{r.fleet_used:,.0f}"]
    out = pd.DataFrame(cols, index=[lab[x] if x in lab else x.split(": ")[1] for x in rows] + ["Fleet used for costing"])
    extra = {}
    for c in out.columns:
        op = next(o for o in mc.index if o.split(" - ")[1] == c.split(" (")[0])
        extra[c] = (f"{mc.loc[op, 'prob_profit_positive']:.0%}" if "deployed" in c else "")
    out.loc["Monte Carlo P(profit > 0)"] = pd.Series(extra)
    return "Profit and loss, thousand € per year (two fleet bases)", out.reset_index().rename(columns={"index": "Item"})


def t_errata():
    rows = [
        ("Self-containment", "Intra-zonal trips removed before computing the indicator", "0.01–0.08 %", "≈ 11 % for all operators"),
        ("Voi dataset", "Merged file contained Jan–Oct 2025 and 59,226 duplicated rows", "256,412 trips; 1,440 vehicles; 39.4 % reserved", "98,295 trips; 1,180 vehicles; 23.0 % reserved"),
        ("Voi timestamps", "Day overwritten with '01' when parsing", "'Sunday busiest'; 'most liquid fleet'", "Fri/Sat busiest; longest idle times"),
        ("Voi time zone", "Timestamps in UTC", "Peaks at 06–07 and 16–17", "Peaks at 08 and 18, as for other operators"),
        ("GTFS seasonal supply", "Services matched by month number; all variants summed; feed has no summer timetable", "+931 % December vs July", "Seasonal PT comparison withdrawn; one reference weekday"),
        ("Weekday analysis", "Totals not normalised (53 Mondays/Tuesdays in 2024); seasonal confounding", "Raw totals", "Seasonal index + Friedman test"),
        ("Cleaning table", "Filters counted against raw data (overlaps)", "Rows did not add up", "Sequential log"),
        ("Idle time", "Means of a skewed variable; Voi dates wrong", "Mean durations", "Medians, >24 h share, relocation rate"),
        ("Socio-economic equity", "Visual overlay only", "'Double isolation' of deprived areas", "No IDISE effect (IRR 1.06, p = 0.58)"),
        ("Finance", "Fleet = every vehicle ID × 365 days; Voi inputs from merged file", "Voi −1.23 M€; Bird −1.27 M€", "Two fleet bases + Monte Carlo"),
    ]
    return "Main corrections", pd.DataFrame(rows, columns=["Topic", "Problem", "First edition", "Revised"])


TABLES = {"cleaning": t_cleaning, "summary": t_summary, "battery_quadrants": t_battery, "od_annual": t_od_annual,
          "od_significance": t_od_sig, "idle": t_idle, "pt_relation": t_pt_relation, "pt_association": t_pt_assoc,
          "demand_model": t_demand, "finance": t_finance, "errata": t_errata}


# ---------------------------------------------------------------------------
# docx helpers
# ---------------------------------------------------------------------------
INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")


def add_runs(par, text):
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**"):
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("`"):
            r = par.add_run(part[1:-1])
            r.font.name = "Consolas"
            r.font.size = Pt(9.5)
        elif part.startswith("*") and len(part) > 2:
            par.add_run(part[1:-1]).italic = True
        else:
            par.add_run(part)


def shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def caption(doc, text):
    p = doc.add_paragraph(style="Caption")
    add_runs(p, text)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER


def add_table(doc, title, df, n):
    caption(doc, f"Table {n} – {title}")
    tab = doc.add_table(rows=1, cols=len(df.columns))
    tab.style = "Table Grid"
    tab.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, c in enumerate(df.columns):
        cell = tab.rows[0].cells[i]
        cell.text = str(c)
        cell.paragraphs[0].runs[0].bold = True
        shade(cell, "DCE8F7")
    for _, row in df.iterrows():
        cells = tab.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = "" if pd.isna(v) else str(v)
    for r in tab.rows:
        for c in r.cells:
            for p in c.paragraphs:
                p.paragraph_format.space_after = Pt(0)
                for run in p.runs:
                    run.font.size = Pt(8.5)
    doc.add_paragraph()


def build(src: Path, out: Path) -> None:
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(11)
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Cm(2.2)
    lines = src.read_text(encoding="utf-8").splitlines()
    titles = [l[2:] for l in lines if l.startswith("% ")]
    if titles:
        for _ in range(6):
            doc.add_paragraph()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run("Politecnico di Torino\nInteruniversity Department of Regional and Urban Studies and Planning\n"
                      "Master's degree in Digital Skills for Sustainable Societal Transitions")
        r.font.size = Pt(11)
        doc.add_paragraph()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(titles[0])
        r.bold = True
        r.font.size = Pt(18)
        r.font.color.rgb = RGBColor(0x18, 0x4F, 0x95)
        for t in titles[1:]:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run(t).font.size = Pt(11)
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    fig_n = tab_n = 0
    para: list[str] = []

    def flush():
        if para:
            p = doc.add_paragraph()
            add_runs(p, " ".join(para))
            p.paragraph_format.space_after = Pt(6)
            para.clear()

    for line in lines:
        s = line.strip()
        if s.startswith("% "):
            continue
        if not s:
            flush()
            continue
        if s.startswith("#"):
            flush()
            level = len(s) - len(s.lstrip("#"))
            doc.add_heading(s[level:].strip(), level=level)
        elif m := re.match(r"!\[(.*)\]\((.*)\)", s):
            flush()
            path = (ROOT / "report" / m.group(2)).resolve()
            if path.exists():
                doc.add_picture(str(path), width=Cm(16.5))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                fig_n += 1
                caption(doc, f"Figure {fig_n} – {m.group(1)}")
            else:
                doc.add_paragraph(f"[missing figure: {m.group(2)}]")
        elif m := re.match(r"\{\{table:(\w+)\}\}", s):
            flush()
            tab_n += 1
            add_table(doc, *TABLES[m.group(1)](), tab_n)
        elif s.startswith("* "):
            flush()
            add_runs(doc.add_paragraph(style="List Bullet"), s[2:])
        elif re.match(r"\d+\. ", s):
            flush()
            add_runs(doc.add_paragraph(style="List Number"), re.sub(r"^\d+\. ", "", s))
        else:
            para.append(s)
    flush()
    doc.save(out)
    print(f"Saved {out}  ({fig_n} figures, {tab_n} tables)")


if __name__ == "__main__":
    build(ROOT / "report" / "report.md", ROOT / "report" / "Turin_micromobility_report.docx")
