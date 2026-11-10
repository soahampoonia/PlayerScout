from django.db import models


class Watchlist(models.Model):
    """Players whose analyses refresh automatically (build step 8)."""

    player = models.OneToOneField("nba.Player", primary_key=True, on_delete=models.CASCADE, related_name="watch")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "watchlist"


class IngestionRun(models.Model):
    """One row per run of a sync job, so a broken feed is visible instead of silent."""

    class Status(models.TextChoices):
        RUNNING = "running"
        OK = "ok"
        FAILED = "failed"

    id = models.BigAutoField(primary_key=True)
    job_name = models.CharField(max_length=40)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.RUNNING)
    rows_written = models.IntegerField(default=0)
    error = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "ingestion_run"
        indexes = [
            models.Index(fields=["job_name", "-started_at"], name="run_job_started_idx"),
        ]

    def __str__(self):
        return f"{self.job_name} {self.status} {self.started_at:%Y-%m-%d %H:%M}"
