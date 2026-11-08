"""Run the full analysis pipeline.

Usage
-----
    python scripts/run_all.py                 # everything, using cached clean data
    python scripts/run_all.py --rebuild       # re-read and re-clean the raw files
    python scripts/run_all.py --steps descriptive od

Outputs are written to ``outputs/`` (figures, maps, tables, GeoPackage).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import warnings
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from micromobility import (battery, config, demand_model, descriptive, finance, od, parking,  # noqa: E402
                           transit)
from micromobility.cleaning import load_clean  # noqa: E402

STEPS = ["clean", "descriptive", "battery", "od", "parking", "transit", "demand_model", "finance"]
log = logging.getLogger("run_all")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", nargs="+", choices=STEPS, default=STEPS)
    ap.add_argument("--rebuild", action="store_true", help="ignore cached cleaned data")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)
    config.ensure_dirs()
    t0 = time.time()

    # cleaning is always needed
    trips, logs = {}, []
    for op in config.OPERATORS:
        trips[op], lg = load_clean(op, use_cache=not args.rebuild)
        lg.insert(0, "operator", config.label(op))
        logs.append(lg)
        log.info("%s: %d valid trips", config.label(op), len(trips[op]))
    pd.concat(logs).to_csv(config.TABLE_DIR / "cleaning_log.csv", index=False)

    results, summary = {}, {}
    if "descriptive" in args.steps:
        log.info("Descriptive statistics ...")
        results["descriptive"] = descriptive.run(trips)
    if "battery" in args.steps or "finance" in args.steps:
        log.info("Battery consumption ...")
        results["battery"] = battery.run(trips)
        summary["battery_pooled_coefficients"] = results["battery"]["pooled_coefs"].to_dict()
    if {"od", "parking", "transit", "demand_model"} & set(args.steps):
        log.info("Origin-destination and zonal indicators ...")
        results["od"] = od.run(trips)
    if "parking" in args.steps:
        log.info("Idle time and relocations ...")
        results["parking"] = parking.run(results["od"]["zoned_trips"], results["od"]["indicators"])
    if "transit" in args.steps:
        log.info("Public transport comparison ...")
        results["transit"] = transit.run(results["od"]["zoned_trips"])
    if "demand_model" in args.steps:
        log.info("Zonal demand model ...")
        results["demand_model"] = demand_model.run(results["od"]["indicators"])
    if "finance" in args.steps:
        log.info("Financial model ...")
        results["finance"] = finance.run(trips, results["battery"]["pooled_coefs"])

    summary["runtime_s"] = round(time.time() - t0, 1)
    (config.OUTPUT_DIR / "run_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    log.info("Done in %.0f s. Outputs in %s", time.time() - t0, config.OUTPUT_DIR)


if __name__ == "__main__":
    main()
