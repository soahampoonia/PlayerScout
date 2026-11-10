"""Player box-score lines -> player_game_log (and any game rows they imply)."""

import logging
from collections import defaultdict
from datetime import date

from django.db import transaction

from nba.models import Game, Player, PlayerGameLog, Team
from nba.seasons import seasons_to_sync
from nba.textnorm import search_key

from . import client
from .runs import ingestion_run

log = logging.getLogger(__name__)

SEASON_TYPES = ("Regular Season", "PlayIn", "Playoffs")
STAT_FIELDS = {
    "pts": "PTS", "reb": "REB", "ast": "AST", "stl": "STL", "blk": "BLK", "tov": "TOV",
    "fg3m": "FG3M", "fgm": "FGM", "fga": "FGA", "ftm": "FTM", "fta": "FTA", "plus_minus": "PLUS_MINUS",
}  # fmt: skip
LOG_UPDATE_FIELDS = ["team", "opponent_team", "game_date", "is_home", "minutes", *STAT_FIELDS]


def _int(value) -> int:
    return int(value) if value not in (None, "") else 0


def apply_game_logs(rows: list[dict], season: str) -> int:
    """Write one season's rows. Idempotent: re-running updates in place."""
    team_ids = set(Team.objects.values_list("id", flat=True))
    rows = [row for row in rows if row.get("TEAM_ID") in team_ids and row.get("GAME_ID") and row.get("PLAYER_ID")]

    # Work out each game's two teams, home side and score from the rows themselves.
    by_game: dict[str, dict] = defaultdict(lambda: {"teams": {}, "date": None, "home": None})
    for row in rows:
        info = by_game[str(row["GAME_ID"])]
        info["teams"][row["TEAM_ID"]] = info["teams"].get(row["TEAM_ID"], 0) + _int(row.get("PTS"))
        info["date"] = date.fromisoformat(str(row["GAME_DATE"])[:10])
        if " vs. " in (row.get("MATCHUP") or ""):
            info["home"] = row["TEAM_ID"]
    by_game = {gid: info for gid, info in by_game.items() if len(info["teams"]) == 2 and info["home"]}

    with transaction.atomic():
        # Games: create the ones the schedule sync has not seen (earlier seasons),
        # and mark any not-yet-final game as final with the summed score.
        existing = {g.id: g for g in Game.objects.filter(id__in=by_game.keys())}
        to_create, to_finalise = [], []
        for game_id, info in by_game.items():
            home_id = info["home"]
            away_id = next(team_id for team_id in info["teams"] if team_id != home_id)
            game = existing.get(game_id)
            if game is None:
                to_create.append(
                    Game(
                        id=game_id, season=season, game_date=info["date"], home_team_id=home_id, away_team_id=away_id,
                        status=Game.Status.FINAL, home_score=info["teams"][home_id], away_score=info["teams"][away_id],
                    )  # fmt: skip
                )
            elif game.status in (Game.Status.SCHEDULED, Game.Status.LIVE):
                game.status = Game.Status.FINAL
                game.home_score = info["teams"].get(game.home_team_id, game.home_score)
                game.away_score = info["teams"].get(game.away_team_id, game.away_score)
                to_finalise.append(game)
        Game.objects.bulk_create(to_create, batch_size=500)
        for game in to_finalise:
            game.save(update_fields=["status", "home_score", "away_score", "updated_at"])

        # Players who appear in a box score before the roster sync has seen them.
        known_players = set(Player.objects.values_list("id", flat=True))
        new_players = {}
        for row in rows:
            if row["PLAYER_ID"] not in known_players and row["PLAYER_ID"] not in new_players:
                name = (row.get("PLAYER_NAME") or "").strip()
                first, _, last = name.partition(" ")
                new_players[row["PLAYER_ID"]] = Player(
                    id=row["PLAYER_ID"], first_name=first, last_name=last, full_name=name, search_name=search_key(name)
                )
        Player.objects.bulk_create(new_players.values(), batch_size=500)

        logs = []
        for row in rows:
            game_id = str(row["GAME_ID"])
            info = by_game.get(game_id)
            if info is None:
                continue
            team_id = row["TEAM_ID"]
            logs.append(
                PlayerGameLog(
                    player_id=row["PLAYER_ID"],
                    game_id=game_id,
                    team_id=team_id,
                    opponent_team_id=next(other for other in info["teams"] if other != team_id),
                    game_date=info["date"],
                    is_home=team_id == info["home"],
                    minutes=row.get("MIN") or 0,
                    **{field: _int(row.get(column)) for field, column in STAT_FIELDS.items()},
                )
            )
        PlayerGameLog.objects.bulk_create(
            logs, update_conflicts=True, unique_fields=["player", "game"], update_fields=LOG_UPDATE_FIELDS, batch_size=2000
        )
    return len(logs)


def sync_game_logs(seasons: list[str] | None = None) -> int:
    """Nightly: current season only is enough, but re-reading two seasons is three
    cheap calls each and heals any gap, so that is the default."""
    with ingestion_run("sync_game_logs") as run:
        total = 0
        for season in seasons or seasons_to_sync():
            for season_type in SEASON_TYPES:
                rows = client.fetch_player_game_logs(season, season_type)
                total += apply_game_logs(rows, season)
                run.rows_written = total
        return total
