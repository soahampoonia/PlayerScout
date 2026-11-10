"""Season strings ("2026-27") and the league's notion of "today" (Eastern time)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone


def league_tz() -> ZoneInfo:
    return ZoneInfo(settings.LEAGUE_TIME_ZONE)


def today_et() -> date:
    return timezone.now().astimezone(league_tz()).date()


def season_for_date(day: date) -> str:
    """A season runs October to June; July to September still counts as the season just ended."""
    start = day.year if day.month >= 10 else day.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def current_season() -> str:
    return settings.NBA_SEASON or season_for_date(today_et())


def previous_season(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


def seasons_to_sync() -> list[str]:
    """Current season first, then as many earlier ones as NBA_LOG_SEASONS asks for."""
    seasons = [current_season()]
    while len(seasons) < max(1, settings.NBA_LOG_SEASONS):
        seasons.append(previous_season(seasons[-1]))
    return seasons


def parse_utc(value: str | None) -> datetime | None:
    """Parse the NBA's "2026-11-10T00:30:00Z" timestamps."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed
