"""live_scoreboard: scores, period, clock and status while games are on.

Polled every 15 seconds by Celery beat, but it only asks the NBA when a game is live
or due to tip off soon, so it costs nothing on off days or overnight. Each change is
pushed to browsers as game.update. When a game goes final, the box scores are synced
a little later (the league needs a few minutes to finalise them).

The live feed (cdn.nba.com todaysScoreboard_00.json) gives per game: gameId,
gameStatus (1 scheduled, 2 in progress, 3 final), gameStatusText ("7:00 pm ET",
"Q3 5:42", "Half", "Final", "PPD"), period, gameClock as ISO 8601 ("PT05M42.00S"),
and homeTeam / awayTeam with score."""

import logging
import re
import time
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from nba.models import Game

from . import client

log = logging.getLogger(__name__)

STATUS_KEY = "live-scoreboard:status"
FINAL_SYNC_KEY = "live-scoreboard:final-sync-queued"
FINAL_SYNC_DELAY = 15 * 60  # seconds after a final before re-reading box scores
LOOK_AHEAD = timedelta(minutes=10)  # start polling this long before tip-off
LOOK_BACK = timedelta(hours=5)  # a game that tipped off this long ago without going final is still polled

CLOCK = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$")


def format_clock(raw: str | None) -> str:
    """As on TV: "PT05M42.00S" -> "5:42"; under a minute, tenths: "PT00M38.60S" -> "38.6"."""
    match = CLOCK.match((raw or "").strip())
    if not match:
        return ""
    minutes = int(match.group(2) or 0) + 60 * int(match.group(1) or 0)
    seconds = float(match.group(3) or 0)
    if minutes == 0:
        return f"{seconds:.1f}"
    return f"{minutes}:{int(seconds):02d}"


def period_name(period: int) -> str:
    return f"Q{period}" if period <= 4 else ("OT" if period == 5 else f"{period - 4}OT")


def live_state(row: dict) -> dict:
    """Our game fields from one live-feed game."""
    status_code = int(row.get("gameStatus") or 0)
    text = str(row.get("gameStatusText") or "").strip()
    lowered = text.lower()
    period = int(row.get("period") or 0) or None
    home, away = row.get("homeTeam") or {}, row.get("awayTeam") or {}

    if lowered.startswith("ppd") or "postponed" in lowered:
        return {"status": Game.Status.POSTPONED, "period": None, "clock": "", "home_score": None, "away_score": None}
    if status_code == 1:
        return {"status": Game.Status.SCHEDULED, "period": None, "clock": "", "home_score": None, "away_score": None}

    state = {"period": period, "home_score": home.get("score"), "away_score": away.get("score")}
    if status_code == 3:
        return {**state, "status": Game.Status.FINAL, "clock": ""}

    clock = format_clock(row.get("gameClock"))
    if "half" in lowered:
        clock = "Half"
    elif clock in ("", "0.0") and period:
        clock = f"End {period_name(period)}"  # between quarters
    return {**state, "status": Game.Status.LIVE, "clock": clock}


def apply_live(rows: list[dict]) -> tuple[list[Game], list[Game]]:
    """Write changes. Returns (changed games, games that just went final)."""
    by_id = {str(row.get("gameId")): row for row in rows if row.get("gameId")}
    games = Game.objects.filter(id__in=by_id).select_related("home_team", "away_team")
    changed, finished = [], []
    for game in games:
        state = live_state(by_id[game.id])
        if game.status == Game.Status.FINAL and state["status"] != Game.Status.FINAL:
            continue  # never step a finished game backwards
        if state["status"] == Game.Status.SCHEDULED and game.status == Game.Status.LIVE:
            continue  # a stale feed right after tip-off
        fields = [name for name, value in state.items() if getattr(game, name) != value]
        if not fields:
            continue
        if "status" in fields and state["status"] == Game.Status.FINAL:
            finished.append(game)
        for name in fields:
            setattr(game, name, state[name])
        game.save(update_fields=[*fields, "updated_at"])
        changed.append(game)
    return changed, finished


def games_in_window(now=None) -> bool:
    now = now or timezone.now()
    return Game.objects.filter(
        Q(status=Game.Status.LIVE)
        | Q(status=Game.Status.SCHEDULED, tipoff_at__lte=now + LOOK_AHEAD, tipoff_at__gte=now - LOOK_BACK)
    ).exists()


def queue_final_sync():
    """Box scores for games that just ended, once per quarter hour however many end."""
    if not cache.add(FINAL_SYNC_KEY, "1", timeout=FINAL_SYNC_DELAY):
        return False
    from nba.seasons import current_season
    from nba.tasks import sync_game_logs

    sync_game_logs.apply_async(kwargs={"seasons": [current_season()]}, countdown=FINAL_SYNC_DELAY)
    return True


def sync_live_scoreboard() -> dict:
    from realtime import push

    if not games_in_window():
        return {"idle": True}
    started = time.time()
    try:
        rows = client.fetch_live_scoreboard()
    except client.NBAUnavailable as exc:
        cache.set(STATUS_KEY, {"ts": started, "ok": False, "error": str(exc)[:300]}, 3600)
        raise
    changed, finished = apply_live(rows)
    for game in changed:
        push.send([push.ALL], "game.update", push.game_update_payload(game))
    if finished:
        queue_final_sync()
    live = sum(1 for row in rows if int(row.get("gameStatus") or 0) == 2)
    cache.set(STATUS_KEY, {"ts": started, "ok": True, "games": len(rows), "live": live, "changed": len(changed)}, 3600)
    return {"games": len(rows), "changed": len(changed), "finished": len(finished)}
