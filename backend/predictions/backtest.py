"""Replay the baseline model over a finished season, using only games before each one.

There is no history of real prop lines, so each game gets a stand-in line: the player's
season-to-date average rounded to the nearest .5 below. The question is whether the
model's probabilities are honest (calibrated) and better than two simple guesses:
always 50%, and how often he went over that line so far this season."""

import math
from collections import defaultdict

from nba.models import PlayerGameLog
from nba.seasons import previous_season
from predictions.features import Line, from_logs
from predictions.model import p_over, project

STATS = ("pts", "reb", "ast")
MIN_PRIOR_GAMES = 10


def load(season: str) -> dict[int, list[Line]]:
    rows = list(
        PlayerGameLog.objects.filter(game__season__in=[season, previous_season(season)])
        .values("player_id", "game_id", "game_date", "game__season", "team_id", "minutes", "pts", "reb", "ast", "fg3m", "fga", "fta", "tov")
    )
    totals = defaultdict(lambda: [0, 0, 0, 0.0])
    for r in rows:
        t = totals[(r["game_id"], r["team_id"])]
        t[0] += r["fga"]; t[1] += r["fta"]; t[2] += r["tov"]; t[3] += float(r["minutes"])  # noqa: E702
    by_player: dict[int, list[Line]] = defaultdict(list)
    for r in rows:
        t = totals[(r["game_id"], r["team_id"])]
        by_player[r["player_id"]].append(
            Line(
                game_id=r["game_id"], game_date=r["game_date"], season=r["game__season"], minutes=float(r["minutes"]),
                pts=r["pts"], reb=r["reb"], ast=r["ast"], fg3m=r["fg3m"], fga=r["fga"], fta=r["fta"], tov=r["tov"],
                team_fga=t[0], team_fta=t[1], team_tov=t[2], team_minutes=t[3] or 240.0,
            )  # fmt: skip
        )
    for lines in by_player.values():
        lines.sort(key=lambda line: line.game_date, reverse=True)
    return by_player


def run(season: str) -> dict:
    by_player = load(season)
    results = {stat: [] for stat in STATS}
    for player_id, lines in by_player.items():
        for i, line in enumerate(lines):
            if line.season != season or line.minutes <= 0 or not line.game_id.startswith("002"):
                continue
            before = lines[i + 1 :]
            so_far = [x for x in before if x.season == season and x.minutes > 0]
            if len(so_far) < MIN_PRIOR_GAMES:
                continue
            features = from_logs(
                player_id=player_id, game_id=line.game_id, game_date=line.game_date, season=season, team_id=None,
                opponent_id=None, is_home=False, lines=before, last_team_game=before[0].game_date if before else None,
                opponent=None, team_pace=None, league=None,
            )  # fmt: skip
            for stat in STATS:
                proj = project(features, stat)
                if not proj:
                    continue
                avg = sum(getattr(x, stat) for x in so_far) / len(so_far)
                stand_in = math.floor(avg) + 0.5 if avg - math.floor(avg) >= 0.5 else math.floor(avg) - 0.5
                if stand_in < 0.5:
                    continue
                actual = getattr(line, stat)
                over = 1.0 if actual > stand_in else 0.0
                rate = sum(1 for x in so_far if getattr(x, stat) > stand_in) / len(so_far)
                results[stat].append(
                    {
                        "model": p_over(proj["center"], proj["sd"], stand_in),
                        "rate": min(max(rate, 0.01), 0.99),
                        "over": over,
                        "mean": proj["mean"],
                        "avg": avg,
                        "last10": sum(getattr(x, stat) for x in so_far[:10]) / min(10, len(so_far)),
                        "actual": actual,
                    }
                )
    return summarize(results)


def summarize(results: dict) -> dict:
    report = {}
    for stat, rows in results.items():
        if not rows:
            continue
        n = len(rows)
        brier = lambda key: sum((r[key] - r["over"]) ** 2 for r in rows) / n  # noqa: E731
        mae = lambda key: sum(abs(r[key] - r["actual"]) for r in rows) / n  # noqa: E731
        buckets = []
        for lo in range(0, 100, 10):
            inside = [r for r in rows if lo / 100 <= r["model"] < (lo + 10) / 100 or (lo == 90 and r["model"] == 1)]
            if inside:
                buckets.append(
                    {
                        "bucket": f"{lo}-{lo + 10}%",
                        "n": len(inside),
                        "predicted": round(sum(r["model"] for r in inside) / len(inside), 3),
                        "actual": round(sum(r["over"] for r in inside) / len(inside), 3),
                    }
                )
        report[stat] = {
            "n": n,
            "brier_model": round(brier("model"), 4),
            "brier_season_rate": round(brier("rate"), 4),
            "brier_coin_flip": 0.25,
            "mae_model": round(mae("mean"), 2),
            "mae_season_avg": round(mae("avg"), 2),
            "mae_last10_avg": round(mae("last10"), 2),
            "calibration": buckets,
        }
    return report
