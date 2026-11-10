"""baseline-v2: the stats model the market and the LLM are compared against.

    mean   = per-minute rate x projected minutes x opponent and pace adjustments
    sd     = the player's game-to-game standard deviation x sd_scale (never below 0.9 x sqrt(mean))
    center = mean - shift x sd   (points, rebounds and assists are skewed: the typical
                                  game falls a little below the average one)
    P(over line) = 1 - Phi((line - center) / sd)

The per-minute rate blends recent windows with the season (last season stands in
early on); projected minutes lean on recent games and drop 3% on the second night of a
back-to-back. The weights, sd_scale and shift for each stat are in PARAMS.
Points move with the opponent's defensive rating and the expected pace; rebounds and
assists move half as much as the opponent's allowed rebounds / assists differ from the
league average. Home teams get 1% more points.

A boosted-tree model can replace this later under a new MODEL_VERSION."""

import math

MODEL_VERSION = "baseline-v2"

# Tuned per stat on 2025-26 games before Jan 15, 2026, and checked on the rest of that
# season (37,572 held-out predictions): Brier 0.2405 -> 0.2371, calibration error
# 4.6 -> 0.8 points against baseline-v1. See docs/model-tuning.md.
PARAMS = {
    "pts": {
        "rate": (("last10", 0.30), ("last20", 0.30), ("season", 0.40)),
        "minutes": (("last5", 0.50), ("last10", 0.25), ("season", 0.25)),
        "sd_scale": 1.3,
        "shift": 0.10,
    },
    "reb": {
        "rate": (("last5", 0.10), ("last10", 0.10), ("last20", 0.10), ("season", 0.70)),
        "minutes": (("last5", 0.40), ("last10", 0.30), ("last20", 0.15), ("season", 0.15)),
        "sd_scale": 1.2,
        "shift": 0.05,
    },
    "ast": {
        "rate": (("last5", 0.10), ("last10", 0.10), ("last20", 0.10), ("season", 0.70)),
        "minutes": (("last5", 0.60), ("last10", 0.20), ("season", 0.20)),
        "sd_scale": 1.2,
        "shift": 0.10,
    },
}
DEFAULT = PARAMS["pts"]
OPPONENT_KEY = {"reb": "opp_reb", "ast": "opp_ast"}


def _blend(features: dict, weights, pick) -> float | None:
    windows = dict(features["windows"])
    if features.get("prior"):
        windows["season"] = features["prior"] if not windows.get("season") or windows["season"]["games"] < 5 else windows["season"]
    total = value = 0.0
    for name, weight in weights:
        w = windows.get(name)
        v = pick(w) if w else None
        if v is not None:
            value += weight * v
            total += weight
    return value / total if total else None


def project(features: dict, stat: str) -> dict | None:
    """{"mean", "sd", "center", "minutes", "rate", "adjustment"} for one stat, or None with
    no history. mean is the expected value; center (a little below it, because these
    counts are skewed right) is what probabilities are measured from."""
    params = PARAMS.get(stat, DEFAULT)
    rate = _blend(features, params["rate"], lambda w: (w.get("per_min") or {}).get(stat))
    minutes = _blend(features, params["minutes"], lambda w: w.get("minutes"))
    if rate is None or minutes is None:
        return None
    if features.get("back_to_back"):
        minutes *= 0.97

    adjust = 1.0
    opp, league = features.get("opponent"), features.get("league")
    if opp and league:
        if stat == "pts":
            adjust *= opp["def_rating"] / league["def_rating"]
            team_pace = features.get("team_pace") or league["pace"]
            adjust *= ((team_pace + opp["pace"]) / 2) / league["pace"]
        elif stat in OPPONENT_KEY:
            key = OPPONENT_KEY[stat]
            adjust *= 1 + 0.5 * (opp[key] / league[key] - 1)
    if stat == "pts" and features.get("is_home"):
        adjust *= 1.01

    mu = rate * minutes * adjust
    sd = ((features.get("stdev") or {}).get(stat) or 0.0) * params["sd_scale"]
    sd = max(sd, 0.9 * math.sqrt(max(mu, 0.0)), 0.75)
    center = mu - params["shift"] * sd
    return {
        "mean": round(mu, 2),
        "sd": round(sd, 2),
        "center": round(center, 2),
        "minutes": round(minutes, 1),
        "rate": round(rate, 4),
        "adjustment": round(adjust, 3),
    }


def p_over(center: float, sd: float, line: float) -> float:
    """Chance of finishing above `line` (lines end in .5, so there are no pushes)."""
    z = (line - center) / sd
    p = 0.5 * math.erfc(z / math.sqrt(2))
    return round(min(max(p, 0.01), 0.99), 4)
