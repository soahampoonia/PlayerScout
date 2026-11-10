"""Celery entry points for Polymarket. The schedule lives in settings.CELERY_BEAT_SCHEDULE."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from markets import polymarket
from markets.discovery import discover_markets as run_discovery
from markets.models import MarketSnapshot
from markets.pricing import record_prices, stream_is_live
from markets.stream import wanted_markets
from nba.ingest.runs import JobAlreadyRunning, ingestion_run
from realtime.push import push_markets

log = logging.getLogger(__name__)


@shared_task(name="markets.discover_markets")
def discover_markets():
    try:
        rows = run_discovery()
        from predictions.tasks import refresh_model

        refresh_model.delay()  # new lines need model probabilities
        return {"job": "discover_markets", "rows": rows}
    except JobAlreadyRunning:
        return {"job": "discover_markets", "skipped": True}


def poll_prices() -> int:
    """Prices over REST for every market the stream would follow. Returns markets priced."""
    wanted = wanted_markets()
    if not wanted:
        return 0
    tokens = list(wanted)
    mids = polymarket.fetch_midpoints(tokens)
    spreads = polymarket.fetch_spreads(tokens)
    updates = []
    for token, mid in mids.items():
        if token not in wanted:
            continue
        half = spreads.get(token, 0) / 2
        updates.append({"market_id": wanted[token], "bid": max(mid - half, 0), "ask": min(mid + half, 1)})
    moved = record_prices(updates)
    push_markets(moved)
    return len(updates)


@shared_task(name="markets.poll_midpoints")
def poll_midpoints():
    """Fallback for when market_stream is not running or has lost its connections."""
    if stream_is_live():
        return {"job": "poll_midpoints", "skipped": "stream is live"}
    try:
        with ingestion_run("poll_midpoints") as run:
            run.rows_written = poll_prices()
            return {"job": "poll_midpoints", "rows": run.rows_written}
    except JobAlreadyRunning:
        return {"job": "poll_midpoints", "skipped": True}


def prune(now=None) -> int:
    """Delete snapshots older than the retention window, except ones an analysis points to."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.MARKET_SNAPSHOT_RETENTION_DAYS)
    old = MarketSnapshot.objects.filter(captured_at__lt=cutoff)
    try:
        from predictions.models import AnalysisGamePrediction

        old = old.exclude(id__in=AnalysisGamePrediction.objects.filter(market_snapshot__isnull=False).values("market_snapshot_id"))
    except ImportError:  # pragma: no cover
        pass
    deleted, _ = old.delete()
    return deleted


@shared_task(name="markets.prune_snapshots")
def prune_snapshots():
    with ingestion_run("prune_snapshots") as run:
        run.rows_written = prune()
        return {"job": "prune_snapshots", "rows": run.rows_written}

