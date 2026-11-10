"""Made-up responses in the exact shape nba_api returns, for tests.

Field names follow nba_api 1.12 (PlayerIndex, ScheduleLeagueV2, LeagueGameLog,
LeagueDashTeamStats). The numbers are random: nothing here is real NBA data."""

import random
from datetime import date, timedelta

from nba_api.stats.static import players as static_players
from nba_api.stats.static import teams as static_teams

TEAMS = sorted(static_teams.get_teams(), key=lambda t: t["id"])
POSITIONS = ["G", "G", "G-F", "F", "F", "F-C", "C", "G", "F", "C", "G", "F", "G", "F", "C"]


def player_index_rows(per_team: int = 15) -> list[dict]:
    """Deal the bundled active players out to the 30 teams, per_team each."""
    active = sorted((p for p in static_players.get_players() if p["is_active"]), key=lambda p: p["id"])
    rows = []
    for i, player in enumerate(active[: per_team * len(TEAMS)]):
        team = TEAMS[i % len(TEAMS)]
        rows.append(
            {
                "PERSON_ID": player["id"],
                "PLAYER_LAST_NAME": player["last_name"],
                "PLAYER_FIRST_NAME": player["first_name"],
                "TEAM_ID": team["id"],
                "TEAM_ABBREVIATION": team["abbreviation"],
                "JERSEY_NUMBER": str(i % 56),
                "POSITION": POSITIONS[(i // len(TEAMS)) % len(POSITIONS)],
                "ROSTER_STATUS": 1.0,
            }
        )
    return rows


def season_fixture(today: date, days_back: int = 20, days_ahead: int = 8, games_per_day: int = 6, seed: int = 7):
    """Returns (schedule_rows, game_log_rows) for a stretch of one season around `today`.

    Past days are final with box scores, today and later are scheduled."""
    rng = random.Random(seed)
    rosters: dict[int, list[dict]] = {}
    for row in player_index_rows():
        rosters.setdefault(row["TEAM_ID"], []).append(row)
    abbreviation = {t["id"]: t["abbreviation"] for t in TEAMS}
    season_year = today.year if today.month >= 10 else today.year - 1

    schedule, logs, counter = [], [], 0
    for offset in range(-days_back, days_ahead + 1):
        day = today + timedelta(days=offset)
        order = TEAMS[:]
        rng.shuffle(order)
        for slot in range(games_per_day):
            home, away = order[2 * slot], order[2 * slot + 1]
            counter += 1
            game_id = f"002{season_year % 100:02d}{counter:05d}"
            final = offset < 0
            scores = {}
            if final:
                for team, matchup in (
                    (home, f'{home["abbreviation"]} vs. {away["abbreviation"]}'),
                    (away, f'{away["abbreviation"]} @ {home["abbreviation"]}'),
                ):
                    total = 0
                    for rank, player in enumerate(rosters[team["id"]][:10]):
                        pts = max(0, int(rng.gauss(24 - 2.2 * rank, 6)))
                        fg3m = min(pts // 3, rng.randint(0, 5))
                        ftm = min(pts - 3 * fg3m, rng.randint(0, 8))
                        fgm = fg3m + (pts - 3 * fg3m - ftm) // 2
                        pts = 3 * fg3m + 2 * (fgm - fg3m) + ftm
                        total += pts
                        logs.append(
                            {
                                "SEASON_ID": f"2{season_year}",
                                "PLAYER_ID": player["PERSON_ID"],
                                "PLAYER_NAME": f'{player["PLAYER_FIRST_NAME"]} {player["PLAYER_LAST_NAME"]}'.strip(),
                                "TEAM_ID": team["id"],
                                "TEAM_ABBREVIATION": team["abbreviation"],
                                "GAME_ID": game_id,
                                "GAME_DATE": day.isoformat(),
                                "MATCHUP": matchup,
                                "WL": None,
                                "MIN": max(4, int(rng.gauss(34 - 2.5 * rank, 4))),
                                "FGM": fgm, "FGA": fgm + rng.randint(2, 9), "FG3M": fg3m, "FG3A": fg3m + rng.randint(0, 5),
                                "FTM": ftm, "FTA": ftm + rng.randint(0, 2),
                                "REB": rng.randint(0, 12), "AST": rng.randint(0, 10), "STL": rng.randint(0, 3),
                                "BLK": rng.randint(0, 3), "TOV": rng.randint(0, 5), "PF": rng.randint(0, 5),
                                "PTS": pts, "PLUS_MINUS": rng.randint(-18, 18),
                            }  # fmt: skip
                        )
                    scores[team["id"]] = total
                if scores[home["id"]] == scores[away["id"]]:  # no ties in basketball
                    logs[-1]["PTS"] += 2
                    logs[-1]["FGM"] += 1
                    scores[away["id"]] += 2
            schedule.append(
                {
                    "gameId": game_id,
                    "gameStatus": 3 if final else 1,
                    "gameStatusText": "Final" if final else "7:30 pm ET",
                    "gameDateEst": f"{day.isoformat()}T00:00:00Z",
                    "gameDateTimeUTC": f"{(day + timedelta(days=1)).isoformat()}T00:{30 if slot % 2 else 0:02d}:00Z",
                    "postponedStatus": "A",
                    "homeTeam": {"teamId": home["id"], "teamTricode": abbreviation[home["id"]], "score": scores.get(home["id"], 0)},
                    "awayTeam": {"teamId": away["id"], "teamTricode": abbreviation[away["id"]], "score": scores.get(away["id"], 0)},
                }
            )
    return schedule, logs


def team_stat_rows(seed: int = 7) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    advanced, opponent = [], []
    for team in TEAMS:
        advanced.append(
            {
                "TEAM_ID": team["id"],
                "PACE": round(rng.uniform(96, 104), 2),
                "OFF_RATING": round(rng.uniform(108, 121), 1),
                "DEF_RATING": round(rng.uniform(108, 121), 1),
            }
        )
        opponent.append(
            {
                "TEAM_ID": team["id"],
                "OPP_PTS": round(rng.uniform(106, 122), 1),
                "OPP_REB": round(rng.uniform(40, 47), 1),
                "OPP_AST": round(rng.uniform(23, 29), 1),
            }
        )
    return advanced, opponent
