"""Teams and players.

Two layers: a seed from the data bundled inside nba_api (no network, so search works
the moment the stack is up), then the live PlayerIndex call for current teams,
positions and jersey numbers."""

import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from nba.models import Player, Team
from nba.seasons import current_season
from nba.textnorm import search_key

from . import client
from .runs import ingestion_run

log = logging.getLogger(__name__)

# The bundled team list has no conference, and conferences do not change.
EAST = {"ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DET", "IND", "MIA", "MIL", "NYK", "ORL", "PHI", "TOR", "WAS"}
MIN_PLAUSIBLE_ROSTER_ROWS = 300  # 30 teams x at least 10 players


def seed_static() -> int:
    """Load all teams and every player nba_api knows about. Safe to run repeatedly."""
    from nba_api.stats.static import players as static_players
    from nba_api.stats.static import teams as static_teams

    teams = [
        Team(
            id=row["id"],
            abbreviation=row["abbreviation"],
            city=row["city"],
            name=row["nickname"],
            full_name=row["full_name"],
            search_name=search_key(f'{row["full_name"]} {row["abbreviation"]}'),
            conference="East" if row["abbreviation"] in EAST else "West",
        )
        for row in static_teams.get_teams()
    ]
    Team.objects.bulk_create(
        teams,
        update_conflicts=True,
        unique_fields=["id"],
        update_fields=["abbreviation", "city", "name", "full_name", "search_name", "conference"],
    )

    known = set(Player.objects.values_list("id", flat=True))
    new_players = [
        Player(
            id=row["id"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            full_name=row["full_name"],
            search_name=search_key(row["full_name"]),
            is_active=row["is_active"],
        )
        for row in static_players.get_players()
        if row["id"] not in known
    ]
    Player.objects.bulk_create(new_players, batch_size=1000)
    cache.delete(settings.SEARCH_INDEX_CACHE_KEY)
    return len(teams) + len(new_players)


def apply_player_index(rows: list[dict]) -> int:
    """Upsert current rosters and mark everyone else inactive."""
    team_ids = set(Team.objects.values_list("id", flat=True))
    on_roster = [row for row in rows if row.get("ROSTER_STATUS") in (1, 1.0, "1", "1.0") and row.get("TEAM_ID") in team_ids]
    if len(on_roster) < MIN_PLAUSIBLE_ROSTER_ROWS:
        # A short answer would wrongly deactivate most of the league. Refuse it.
        raise client.NBAUnavailable(
            f"PlayerIndex returned only {len(on_roster)} rostered players; expected {MIN_PLAUSIBLE_ROSTER_ROWS}+"
        )

    players = []
    for row in on_roster:
        first = (row.get("PLAYER_FIRST_NAME") or "").strip()
        last = (row.get("PLAYER_LAST_NAME") or "").strip()
        full_name = f"{first} {last}".strip()
        players.append(
            Player(
                id=row["PERSON_ID"],
                first_name=first,
                last_name=last,
                full_name=full_name,
                search_name=search_key(full_name),
                team_id=row["TEAM_ID"],
                position=(row.get("POSITION") or "")[:8],
                jersey=str(row.get("JERSEY_NUMBER") or "")[:4],
                is_active=True,
            )
        )

    with transaction.atomic():
        Player.objects.bulk_create(
            players,
            update_conflicts=True,
            unique_fields=["id"],
            update_fields=["first_name", "last_name", "full_name", "search_name", "team", "position", "jersey", "is_active"],
            batch_size=500,
        )
        Player.objects.filter(is_active=True).exclude(id__in=[p.id for p in players]).update(is_active=False, team=None)
    cache.delete(settings.SEARCH_INDEX_CACHE_KEY)
    return len(players)


def sync_reference() -> int:
    with ingestion_run("sync_reference") as run:
        written = seed_static()
        written += apply_player_index(client.fetch_player_index(current_season()))
        run.rows_written = written
        return written
