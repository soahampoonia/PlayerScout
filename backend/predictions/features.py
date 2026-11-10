"""Features for one player and one upcoming game (feature_snapshot.features).

Everything is computed from data dated before the game, so the same code serves an
upcoming game and a backtest of a past one. build() reads the database; from_logs()
does the arithmetic on rows already in memory, which the backtest uses to avoid a
query per game."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from statistics import mean, pstdev

from django.db.models import Q

from nba.models import Game, PlayerGameLog, TeamStatSnapshot
from nba.seasons import previous_season

VERSION = "features-v2"
STATS = ("pts", "reb", "ast", "fg3m")
WINDOWS = (("last5", 5), ("last10", 10), ("last20", 20))
MIN_SEASON_GAMES = 5  # below this, last season stands in as the prior


@dataclass
class Line:
    """One box-score line, with the team totals needed for usage."""

    game_id: str
    game_date: date
    season: str
    minutes: float
    pts: int
    reb: int
    ast: int
    fg3m: int
    fga: int
    fta: int
    tov: int
    team_fga: int = 0
    team_fta: int = 0
    team_tov: int = 0
    team_minutes: float = 240.0

    @property
    def usage(self) -> float | None:
        """Usage rate: share of team plays the player used while on the floor (%)."""
        team_plays = self.team_fga + 0.44 * self.team_fta + self.team_tov
        if self.minutes <= 0 or team_plays <= 0:
            return None
        plays = self.fga + 0.44 * self.fta + self.tov
        return 100 * plays * (self.team_minutes / 5) / (self.minutes * team_plays)


def _r(value, digits=2):
    return None if value is None else round(value, digits)


def window(lines: list[Line]) -> dict | None:
    """Per-game averages and per-minute rates over some games (newest first)."""
    if not lines:
        return None
    minutes = sum(line.minutes for line in lines)
    usages = [u for u in (line.usage for line in lines) if u is not None]
    return {
        "games": len(lines),
        "minutes": _r(minutes / len(lines)),
        **{stat: _r(mean(getattr(line, stat) for line in lines)) for stat in STATS},
        "per_min": {stat: _r(sum(getattr(line, stat) for line in lines) / minutes, 4) if minutes else None for stat in STATS},
        "usage": _r(mean(usages)) if usages else None,
    }


def from_logs(
    *,
    player_id: int,
    game_id: str,
    game_date: date,
    season: str,
    team_id: int | None,
    opponent_id: int | None,
    is_home: bool,
    lines: list[Line],
    last_team_game: date | None,
    opponent: dict | None,
    team_pace: float | None,
    league: dict | None,
) -> dict:
    """The features dict. `lines` are the player's games before game_date, newest first,
    from this season and the previous one; DNPs (0 minutes) are dropped here."""
    played = [line for line in lines if line.minutes > 0 and line.game_date < game_date]
    this_season = [line for line in played if line.season == season]
    last_season = [line for line in played if line.season == previous_season(season)]
    gaps = ["injuries: no injury source is connected yet"]

    windows = {name: window(this_season[:n]) for name, n in WINDOWS}
    windows["season"] = window(this_season)
    prior = None
    if len(this_season) < MIN_SEASON_GAMES:
        prior = window(last_season)
        gaps.append(f"only {len(this_season)} games this season; last season used as the prior")

    spread_source = this_season if len(this_season) >= 8 else played[:82]
    stdev = {stat: _r(pstdev([getattr(line, stat) for line in spread_source])) if len(spread_source) >= 3 else None for stat in STATS}

    minutes_trend = None
    if windows["last5"] and windows["season"]:
        minutes_trend = _r(windows["last5"]["minutes"] - windows["season"]["minutes"])

    rest_days = (game_date - last_team_game).days if last_team_game else None
    if opponent is None:
        gaps.append("no team stats for the opponent yet; no opponent adjustment")

    return {
        "version": VERSION,
        "player_id": player_id,
        "game_id": game_id,
        "game_date": game_date.isoformat(),
        "season": season,
        "team_id": team_id,
        "opponent_id": opponent_id,
        "is_home": is_home,
        "rest_days": rest_days,
        "back_to_back": rest_days == 1,
        "windows": windows,
        "prior": prior,
        "minutes_trend": minutes_trend,
        "stdev": stdev,
        "opponent": opponent,
        "team_pace": team_pace,
        "league": league,
        "injury": None,
        "data_gaps": gaps,
    }


def data_hash(features: dict) -> str:
    return hashlib.sha256(json.dumps(features, sort_keys=True, default=str).encode()).hexdigest()


# ---- Reading the database ------------------------------------------------------


def team_numbers(as_of: date, season: str) -> tuple[dict[int, dict], dict | None]:
    """({team_id: numbers}, league averages) from the newest snapshot on or before as_of."""
    latest = (
        TeamStatSnapshot.objects.filter(season=season, as_of__lte=as_of).order_by("-as_of").values_list("as_of", flat=True).first()
    )
    if latest is None:
        return {}, None
    rows = TeamStatSnapshot.objects.filter(season=season, as_of=latest)
    teams = {
        row.team_id: {
            "def_rating": float(row.def_rating),
            "off_rating": float(row.off_rating),
            "pace": float(row.pace),
            "opp_pts": float(row.opp_pts),
            "opp_reb": float(row.opp_reb),
            "opp_ast": float(row.opp_ast),
            "as_of": latest.isoformat(),
        }
        for row in rows
    }
    league = {
        key: round(mean(t[key] for t in teams.values()), 2) for key in ("def_rating", "pace", "opp_pts", "opp_reb", "opp_ast")
    }
    return teams, league


def load_lines(player_id: int, seasons: list[str], before: date) -> list[Line]:
    logs = list(
        PlayerGameLog.objects.filter(player_id=player_id, game__season__in=seasons, game_date__lt=before)
        .select_related("game")
        .order_by("-game_date")
    )
    if not logs:
        return []
    # Team totals for usage: one aggregate over the same games.
    totals = {}
    for row in PlayerGameLog.objects.filter(game_id__in=[log.game_id for log in logs]).values(
        "game_id", "team_id", "fga", "fta", "tov", "minutes"
    ):
        key = (row["game_id"], row["team_id"])
        t = totals.setdefault(key, [0, 0, 0, 0.0])
        t[0] += row["fga"]
        t[1] += row["fta"]
        t[2] += row["tov"]
        t[3] += float(row["minutes"])
    lines = []
    for log in logs:
        fga, fta, tov, mins = totals.get((log.game_id, log.team_id), (0, 0, 0, 240.0))
        lines.append(
            Line(
                game_id=log.game_id, game_date=log.game_date, season=log.game.season, minutes=float(log.minutes),
                pts=log.pts, reb=log.reb, ast=log.ast, fg3m=log.fg3m, fga=log.fga, fta=log.fta, tov=log.tov,
                team_fga=fga, team_fta=fta, team_tov=tov, team_minutes=mins or 240.0,
            )  # fmt: skip
        )
    return lines


def build(player, game: Game) -> dict:
    """Features for `player` in upcoming `game`, from the database."""
    team_id = player.team_id if player.team_id in (game.home_team_id, game.away_team_id) else None
    if team_id is None:  # traded or not on either roster: take the side he last played for in this matchup
        team_id = game.home_team_id
    is_home = team_id == game.home_team_id
    opponent_id = game.away_team_id if is_home else game.home_team_id

    lines = load_lines(player.id, [game.season, previous_season(game.season)], game.game_date)
    last = (
        Game.objects.filter(Q(home_team_id=team_id) | Q(away_team_id=team_id), game_date__lt=game.game_date)
        .exclude(status=Game.Status.POSTPONED)
        .values_list("game_date", flat=True)
        .order_by("-game_date")
        .first()
    )

    teams, league = team_numbers(game.game_date, game.season)
    return from_logs(
        player_id=player.id,
        game_id=game.id,
        game_date=game.game_date,
        season=game.season,
        team_id=team_id,
        opponent_id=opponent_id,
        is_home=is_home,
        lines=lines,
        last_team_game=last,
        opponent=teams.get(opponent_id),
        team_pace=(teams.get(team_id) or {}).get("pace"),
        league=league,
    )
