"""Small, dependency-light statistical helpers used across the analysis."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as st
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportion_confint

from . import config


def rng(seed: int = config.RANDOM_SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def median_ci(x, alpha: float = config.ALPHA) -> tuple[float, float, float]:
    """Median with exact distribution-free CI based on order statistics.

    The rank bounds come from the Binomial(n, 0.5) distribution, so no
    distributional assumption is needed (Conover, 1999).
    """
    x = np.sort(np.asarray(pd.Series(x).dropna(), dtype=float))
    n = len(x)
    lo_rank = int(st.binom.ppf(alpha / 2, n, 0.5))
    hi_rank = int(st.binom.isf(alpha / 2, n, 0.5))
    lo_rank = max(lo_rank - 1, 0)
    hi_rank = min(hi_rank, n - 1)
    return float(np.median(x)), float(x[lo_rank]), float(x[hi_rank])


def bootstrap_ci(x, func=np.mean, n_boot: int = config.N_BOOT, alpha: float = config.ALPHA,
                 seed: int = config.RANDOM_SEED, chunk: int = 50) -> tuple[float, float, float]:
    """Point estimate and percentile bootstrap CI (memory-safe, chunked)."""
    x = np.asarray(pd.Series(x).dropna(), dtype=float)
    g = rng(seed)
    est = float(func(x))
    boots = []
    for start in range(0, n_boot, chunk):
        m = min(chunk, n_boot - start)
        idx = g.integers(0, len(x), size=(m, len(x)))
        boots.append(np.apply_along_axis(func, 1, x[idx]))
    lo, hi = np.quantile(np.concatenate(boots), [alpha / 2, 1 - alpha / 2])
    return est, float(lo), float(hi)


def wilson_ci(successes: int, n: int, alpha: float = config.ALPHA) -> tuple[float, float, float]:
    lo, hi = proportion_confint(successes, n, alpha=alpha, method="wilson")
    return successes / n, lo, hi


def mean_ci(x, alpha: float = config.ALPHA) -> tuple[float, float, float]:
    x = np.asarray(pd.Series(x).dropna(), dtype=float)
    m = x.mean()
    h = st.t.ppf(1 - alpha / 2, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))
    return m, m - h, m + h


def gini(x) -> float:
    """Gini coefficient of a non-negative distribution (0 = perfect equality)."""
    x = np.sort(np.asarray(x, dtype=float))
    n = len(x)
    if n == 0 or x.sum() == 0:
        return np.nan
    cum = np.cumsum(x)
    return float((n + 1 - 2 * (cum / cum[-1]).sum()) / n)


def lorenz(x) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(np.asarray(x, dtype=float))
    y = np.concatenate([[0], np.cumsum(x) / x.sum()])
    p = np.linspace(0, 1, len(y))
    return p, y


def kruskal_epsilon2(groups: list) -> dict:
    """Kruskal-Wallis H test with epsilon-squared effect size."""
    groups = [np.asarray(g, dtype=float) for g in groups if len(g)]
    h, p = st.kruskal(*groups)
    n = sum(len(g) for g in groups)
    eps2 = h / ((n ** 2 - 1) / (n + 1))
    return {"H": h, "p_value": p, "epsilon2": eps2, "n": n}


def cliffs_delta(a, b, max_n: int = 20_000, seed: int = config.RANDOM_SEED) -> float:
    """Cliff's delta = P(a > b) - P(a < b), computed via Mann-Whitney U."""
    g = rng(seed)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) > max_n:
        a = g.choice(a, max_n, replace=False)
    if len(b) > max_n:
        b = g.choice(b, max_n, replace=False)
    u = st.mannwhitneyu(a, b, alternative="two-sided").statistic
    return float(2 * u / (len(a) * len(b)) - 1)


def fdr(pvalues, alpha: float = config.ALPHA) -> tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg adjustment. Returns (reject, q-values)."""
    p = np.asarray(pvalues, dtype=float)
    out_rej = np.zeros(len(p), dtype=bool)
    out_q = np.full(len(p), np.nan)
    ok = ~np.isnan(p)
    if ok.any():
        rej, q, _, _ = multipletests(p[ok], alpha=alpha, method="fdr_bh")
        out_rej[ok], out_q[ok] = rej, q
    return out_rej, out_q


def binom_two_sided(k, n) -> np.ndarray:
    """Exact two-sided binomial test p-values for H0: p = 0.5 (vectorised)."""
    k = np.asarray(k, dtype=int)
    n = np.asarray(n, dtype=int)
    p = np.full(len(k), np.nan)
    for i, (ki, ni) in enumerate(zip(k, n)):
        if ni > 0:
            p[i] = st.binomtest(int(ki), int(ni), 0.5).pvalue
    return p


def two_proportion_z(x1, n1, x2, n2) -> np.ndarray:
    """Two-sided z-test p-values for H0: x1/n1 == x2/n2 (vectorised)."""
    x1, n1, x2, n2 = map(lambda v: np.asarray(v, dtype=float), (x1, n1, x2, n2))
    p_pool = (x1 + x2) / (n1 + n2)
    se = np.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (x1 / n1 - x2 / n2) / se
    return 2 * st.norm.sf(np.abs(z))


def fmt_ci(est, lo, hi, digits: int = 2) -> str:
    return f"{est:.{digits}f} [{lo:.{digits}f}, {hi:.{digits}f}]"
