"""Diagnostic: detect clock offsets between operators.

For every month, the hourly demand profile of each operator is
cross-correlated with Lime's profile; the lag maximising the correlation is
reported. With raw (UTC) Voi timestamps the lag is +1 h in winter and +2 h in
daylight-saving months, which is the signature of UTC timestamps. After the
conversion applied in ``micromobility.io`` all lags should be 0.

    python scripts/check_time_offsets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from micromobility.cleaning import load_clean  # noqa: E402


def profile(t: pd.DataFrame, month: int) -> np.ndarray:
    h = t[t.has_timestamp & (t.month == month)].groupby("hour").size().reindex(range(24), fill_value=0)
    return (h / h.sum()).values


def main() -> None:
    trips = {op: load_clean(op)[0] for op in "ABC"}
    rows = []
    for m in range(2, 13):
        ref = profile(trips["A"], m)
        for op in "BC":
            p = profile(trips[op], m)
            lag = max(range(-3, 4), key=lambda L: np.corrcoef(ref, np.roll(p, L))[0, 1])
            rows.append({"month": m, "operator": op, "best_lag_h": lag,
                         "corr_at_lag": np.corrcoef(ref, np.roll(p, lag))[0, 1]})
    print(pd.DataFrame(rows).pivot(index="month", columns="operator", values="best_lag_h"))


if __name__ == "__main__":
    main()
