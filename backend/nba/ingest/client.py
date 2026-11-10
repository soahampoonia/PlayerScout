"""Thin wrapper over nba_api: one place for throttling, timeouts and retries.

Every function returns plain lists of dicts, so the sync jobs never touch nba_api
directly and tests can replace these functions with fixtures."""

import logging
import threading
import time

from django.conf import settings

log = logging.getLogger(__name__)

_throttle_lock = threading.Lock()
_last_request = 0.0


def _brief(error: Exception, limit: int = 240) -> str:
    """requests errors quote the whole URL; keep the start and the end, where the cause
    is. The full text is in the worker log."""
    text = str(error)
    return text if len(text) <= limit else f"{text[:90]} … {text[-130:]}"


class NBAUnavailable(Exception):
    """stats.nba.com did not give a usable answer after all retries."""


def _throttle():
    """Keep to roughly one request per second (per worker process)."""
    global _last_request
    with _throttle_lock:
        wait = settings.NBA_REQUEST_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _call(endpoint_cls, retries: int | None = None, throttle: bool = True, **params):
    retries = retries or settings.NBA_REQUEST_RETRIES
    last_error = None
    for attempt in range(1, retries + 1):
        if throttle:
            _throttle()
        try:
            return endpoint_cls(timeout=settings.NBA_REQUEST_TIMEOUT, proxy=settings.NBA_PROXY, **params)
        except Exception as exc:  # network errors, timeouts, non-JSON block pages
            last_error = exc
            log.warning("%s attempt %s/%s failed: %s", endpoint_cls.__name__, attempt, retries, exc)
            if attempt < retries:
                time.sleep(2**attempt)
    raise NBAUnavailable(f"{endpoint_cls.__name__}: {type(last_error).__name__}: {_brief(last_error)}") from last_error


def _rows(endpoint, result_set: str) -> list[dict]:
    try:
        return endpoint.get_normalized_dict()[result_set]
    except Exception as exc:
        raise NBAUnavailable(f"{type(endpoint).__name__}: unexpected response shape ({exc})") from exc


def fetch_player_index(season: str) -> list[dict]:
    """Every player on a roster this season, with team, position and jersey. One call."""
    from nba_api.stats.endpoints import playerindex

    return _rows(_call(playerindex.PlayerIndex, season=season), "PlayerIndex")


def fetch_schedule(season: str) -> list[dict]:
    """The season's games as the NBA's own nested dicts (homeTeam / awayTeam inside)."""
    from nba_api.stats.endpoints import scheduleleaguev2

    endpoint = _call(scheduleleaguev2.ScheduleLeagueV2, season=season)
    try:
        game_dates = endpoint.get_dict()["leagueSchedule"]["gameDates"]
        return [game for game_date in game_dates for game in game_date["games"]]
    except Exception as exc:
        raise NBAUnavailable(f"ScheduleLeagueV2: unexpected response shape ({exc})") from exc


def fetch_player_game_logs(season: str, season_type: str) -> list[dict]:
    """One row per player per game for a whole season. One call per season type."""
    from nba_api.stats.endpoints import leaguegamelog

    endpoint = _call(
        leaguegamelog.LeagueGameLog,
        season=season,
        season_type_all_star=season_type,
        player_or_team_abbreviation="P",
    )
    return _rows(endpoint, "LeagueGameLog")


def fetch_team_stats(season: str, measure: str) -> list[dict]:
    """Per-game team stats. measure is "Advanced" (pace, ratings) or "Opponent" (allowed)."""
    from nba_api.stats.endpoints import leaguedashteamstats

    endpoint = _call(
        leaguedashteamstats.LeagueDashTeamStats,
        season=season,
        measure_type_detailed_defense=measure,
        per_mode_detailed="PerGame",
    )
    return _rows(endpoint, "LeagueDashTeamStats")


def fetch_live_scoreboard() -> list[dict]:
    """Today's games from the NBA's live feed (cdn.nba.com, not stats.nba.com): status,
    period, clock and score, refreshed by the NBA every few seconds. One quick try: the
    next poll is 15 seconds away anyway."""
    from nba_api.live.nba.endpoints import scoreboard

    endpoint = _call(scoreboard.ScoreBoard, retries=1, throttle=False)
    try:
        return endpoint.get_dict()["scoreboard"]["games"]
    except Exception as exc:
        raise NBAUnavailable(f"ScoreBoard: unexpected response shape ({exc})") from exc
