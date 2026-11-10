"""Team pace, ratings and what each team allows -> team_stat_snapshot (one row per team per day)."""

from decimal import Decimal

from nba.models import Team, TeamStatSnapshot
from nba.seasons import current_season, today_et

from . import client
from .runs import ingestion_run

FIELDS = ["pace", "off_rating", "def_rating", "opp_pts", "opp_reb", "opp_ast"]


def _dec(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def apply_team_stats(advanced: list[dict], opponent: list[dict], season: str, as_of) -> int:
    team_ids = set(Team.objects.values_list("id", flat=True))
    opponent_by_team = {row["TEAM_ID"]: row for row in opponent}
    snapshots = []
    for row in advanced:
        allowed = opponent_by_team.get(row["TEAM_ID"])
        if row["TEAM_ID"] not in team_ids or allowed is None:
            continue
        snapshots.append(
            TeamStatSnapshot(
                team_id=row["TEAM_ID"],
                season=season,
                as_of=as_of,
                pace=_dec(row.get("PACE")),
                off_rating=_dec(row.get("OFF_RATING")),
                def_rating=_dec(row.get("DEF_RATING")),
                opp_pts=_dec(allowed.get("OPP_PTS")),
                opp_reb=_dec(allowed.get("OPP_REB")),
                opp_ast=_dec(allowed.get("OPP_AST")),
            )
        )
    TeamStatSnapshot.objects.bulk_create(
        snapshots, update_conflicts=True, unique_fields=["team", "season", "as_of"], update_fields=FIELDS
    )
    return len(snapshots)


def sync_team_stats() -> int:
    with ingestion_run("sync_team_stats") as run:
        season = current_season()
        advanced = client.fetch_team_stats(season, "Advanced")
        opponent = client.fetch_team_stats(season, "Opponent")
        run.rows_written = apply_team_stats(advanced, opponent, season, today_et())
        return run.rows_written
