"""Season schedule -> game table."""

import logging

from nba.models import Game, Team
from nba.seasons import current_season, parse_utc

from . import client
from .runs import ingestion_run

log = logging.getLogger(__name__)

# game_id prefix: 002 regular season, 004 playoffs, 005 play-in, 006 NBA Cup final.
# Preseason (001) and All-Star (003) are left out.
KEPT_PREFIXES = ("002", "004", "005", "006")
STATUS_BY_CODE = {1: Game.Status.SCHEDULED, 2: Game.Status.LIVE, 3: Game.Status.FINAL}
UPDATE_FIELDS = [
    "season", "game_date", "tipoff_at", "home_team", "away_team",
    "status", "home_score", "away_score", "updated_at",
]  # fmt: skip


def _is_postponed(row: dict) -> bool:
    text = (row.get("gameStatusText") or "").strip().lower()
    return text.startswith("ppd") or "postponed" in text or row.get("postponedStatus") in ("Y", "P")


def _score(team: dict):
    value = team.get("score")
    return int(value) if value not in (None, "") else None


def apply_schedule(rows: list[dict], season: str) -> int:
    team_ids = set(Team.objects.values_list("id", flat=True))
    existing = {g.id: g for g in Game.objects.filter(season=season).only("id", "status", "home_score", "away_score")}

    games = []
    for row in rows:
        game_id = str(row.get("gameId") or "")
        home, away = row.get("homeTeam") or {}, row.get("awayTeam") or {}
        if not game_id.startswith(KEPT_PREFIXES):
            continue
        if home.get("teamId") not in team_ids or away.get("teamId") not in team_ids:
            continue  # playoff slots not decided yet, exhibition opponents
        game_date = (row.get("gameDateEst") or "")[:10]
        if len(game_date) != 10:
            continue

        status = Game.Status.POSTPONED if _is_postponed(row) else STATUS_BY_CODE.get(row.get("gameStatus"), Game.Status.SCHEDULED)
        started = status in (Game.Status.LIVE, Game.Status.FINAL)
        home_score = _score(home) if started else None
        away_score = _score(away) if started else None

        # The live scoreboard (build step 4) is fresher than the schedule file:
        # never step a finished game backwards or blank a known score.
        before = existing.get(game_id)
        if before is not None:
            if before.status == Game.Status.FINAL and status != Game.Status.FINAL:
                status, home_score, away_score = before.status, before.home_score, before.away_score
            if home_score is None:
                home_score = before.home_score
            if away_score is None:
                away_score = before.away_score

        games.append(
            Game(
                id=game_id,
                season=season,
                game_date=game_date,
                tipoff_at=parse_utc(row.get("gameDateTimeUTC")),
                home_team_id=home["teamId"],
                away_team_id=away["teamId"],
                status=status,
                home_score=home_score,
                away_score=away_score,
            )
        )

    Game.objects.bulk_create(
        games, update_conflicts=True, unique_fields=["id"], update_fields=UPDATE_FIELDS, batch_size=500
    )
    return len(games)


def sync_schedule() -> int:
    with ingestion_run("sync_schedule") as run:
        season = current_season()
        run.rows_written = apply_schedule(client.fetch_schedule(season), season)
        return run.rows_written
