"""Sample-size calculations for paired binary outcomes."""

from __future__ import annotations

import math

from scipy.stats import norm


def mcnemar_n(p_discordant: float, delta: float, alpha: float, power: float) -> int:
    if (not 0 < p_discordant <= 1 or not 0 < delta <= p_discordant
            or not 0 < alpha < 1 or not 0 < power < 1):
        raise ValueError("require 0 < delta <= p_discordant <= 1, 0 < alpha < 1, and 0 < power < 1")
    z_alpha = norm.ppf(1 - alpha / 2)
    z_power = norm.ppf(power)
    estimate = ((z_alpha * math.sqrt(p_discordant)
                 + z_power * math.sqrt(p_discordant - delta**2)) / delta) ** 2
    return math.ceil(estimate)


def required_prs(n_refs: int, refs_per_pr: float, design_effect: float) -> int:
    if isinstance(n_refs, bool) or not isinstance(n_refs, int) or n_refs <= 0:
        raise ValueError("n_refs must be a positive integer")
    if not math.isfinite(refs_per_pr) or refs_per_pr <= 0:
        raise ValueError("refs_per_pr must be positive and finite")
    if not math.isfinite(design_effect) or design_effect < 1:
        raise ValueError("design_effect must be at least 1 and finite")
    return math.ceil(n_refs * design_effect / refs_per_pr)
