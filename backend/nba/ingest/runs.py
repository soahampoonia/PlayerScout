"""Run bookkeeping: every sync writes an ingestion_run row and holds a lock so the
same job never runs twice at once."""

from contextlib import contextmanager

from django.core.cache import cache
from django.utils import timezone

from ops.models import IngestionRun

LOCK_TTL = 20 * 60  # seconds; longer than any job should take


class JobAlreadyRunning(Exception):
    pass


@contextmanager
def ingestion_run(job_name: str):
    """Usage: `with ingestion_run("sync_schedule") as run: ...; run.rows_written = n`."""
    lock_key = f"ingest-lock:{job_name}"
    if not cache.add(lock_key, "1", timeout=LOCK_TTL):
        raise JobAlreadyRunning(job_name)
    run = IngestionRun.objects.create(job_name=job_name, started_at=timezone.now())
    try:
        yield run
    except Exception as exc:
        run.status = IngestionRun.Status.FAILED
        run.error = f"{type(exc).__name__}: {exc}"[:4000]
        raise
    else:
        run.status = IngestionRun.Status.OK
    finally:
        run.finished_at = timezone.now()
        run.save()
        cache.delete(lock_key)
