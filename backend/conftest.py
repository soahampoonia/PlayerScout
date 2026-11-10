from datetime import date

import pytest

TODAY = date(2026, 11, 9)


@pytest.fixture(autouse=True)
def _isolated_settings(settings):
    """Tests use an in-memory cache (no Redis needed) and never sleep between NBA calls."""
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.NBA_SEASON = "2026-27"
    settings.NBA_REQUEST_INTERVAL = 0
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def today(monkeypatch):
    """Pin "today in Eastern time" everywhere it is read."""
    for module in ("nba.seasons", "nba.api", "nba.ingest.team_stats"):
        monkeypatch.setattr(f"{module}.today_et", lambda: TODAY)
    return TODAY


@pytest.fixture
def league(db, today):
    """Teams, rosters, three weeks of finished games with box scores, a week of upcoming games, team stats."""
    from nba.ingest import game_logs, reference, schedule, team_stats
    from nba.tests import factories

    reference.seed_static()
    reference.apply_player_index(factories.player_index_rows())
    schedule_rows, log_rows = factories.season_fixture(today)
    schedule.apply_schedule(schedule_rows, "2026-27")
    game_logs.apply_game_logs(log_rows, "2026-27")
    team_stats.apply_team_stats(*factories.team_stat_rows(), "2026-27", today)
    return {"schedule": schedule_rows, "logs": log_rows}
