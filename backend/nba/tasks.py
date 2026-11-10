"""Celery entry points for the NBA sync jobs. The schedule lives in settings.CELERY_BEAT_SCHEDULE."""

import logging

from celery import shared_task

from nba.ingest import game_logs, reference, schedule, team_stats
from nba.ingest.client import NBAUnavailable
from nba.ingest.runs import JobAlreadyRunning

log = logging.getLogger(__name__)

# If stats.nba.com is down, try again in 10 and 20 minutes. Each attempt leaves its own ingestion_run row.
RETRY = {"autoretry_for": (NBAUnavailable,), "retry_backoff": 600, "retry_jitter": False, "max_retries": 2}

JOBS = {
    "sync_reference": reference.sync_reference,
    "sync_schedule": schedule.sync_schedule,
    "sync_team_stats": team_stats.sync_team_stats,
    "sync_game_logs": game_logs.sync_game_logs,
}


def _run(job_name: str):
    try:
        return {"job": job_name, "rows": JOBS[job_name]()}
    except JobAlreadyRunning:
        log.info("%s is already running; skipped", job_name)
        return {"job": job_name, "skipped": True}


@shared_task(name="nba.sync_reference", **RETRY)
def sync_reference():
    return _run("sync_reference")


@shared_task(name="nba.sync_schedule", **RETRY)
def sync_schedule():
    return _run("sync_schedule")


@shared_task(name="nba.sync_team_stats", **RETRY)
def sync_team_stats():
    return _run("sync_team_stats")


@shared_task(name="nba.sync_game_logs", **RETRY)
def sync_game_logs(seasons=None):
    if seasons is None:
        return _run("sync_game_logs")
    try:  # queued by the live scoreboard after a final: just this season
        return {"job": "sync_game_logs", "rows": game_logs.sync_game_logs(seasons)}
    except JobAlreadyRunning:
        return {"job": "sync_game_logs", "skipped": True}


@shared_task(name="nba.live_scoreboard", ignore_result=True)
def live_scoreboard():
    """Every 15 s from beat. A run that overlaps the previous one is dropped."""
    from django.core.cache import cache

    from nba.ingest.live import sync_live_scoreboard

    if not cache.add("live-scoreboard:lock", "1", timeout=30):
        return {"skipped": True}
    try:
        return sync_live_scoreboard()
    except NBAUnavailable as exc:
        log.warning("live scoreboard: %s", exc)
        return {"error": str(exc)}
    finally:
        cache.delete("live-scoreboard:lock")


@shared_task(name="nba.bootstrap")
def bootstrap():
    """First boot: run every job once, in dependency order. One failing job does not stop the rest."""
    results = []
    for job_name in JOBS:
        try:
            results.append(_run(job_name))
        except Exception as exc:  # already recorded in ingestion_run
            log.error("bootstrap: %s failed: %s", job_name, exc)
            results.append({"job": job_name, "error": str(exc)})
    # Markets next, now that there are games and rosters to match them to.
    from markets.tasks import discover_markets

    discover_markets.delay()
    return results
