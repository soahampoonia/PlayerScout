from django.core.management.base import BaseCommand

from nba.ingest.reference import seed_static
from nba.models import Team
from ops.models import IngestionRun


class Command(BaseCommand):
    help = "Seed teams and players from bundled data, then queue the first full NBA sync if it has not succeeded yet."

    def add_arguments(self, parser):
        parser.add_argument("--no-queue", action="store_true", help="Seed only; do not queue the Celery sync.")

    def handle(self, *args, **options):
        if not Team.objects.exists():
            rows = seed_static()
            self.stdout.write(f"Seeded {rows} teams and players from bundled data.")

        done = set(
            IngestionRun.objects.filter(status=IngestionRun.Status.OK).values_list("job_name", flat=True).distinct()
        )
        needed = {"sync_reference", "sync_schedule", "sync_team_stats", "sync_game_logs"} - done
        if options["no_queue"]:
            return
        try:
            if needed:
                from nba.tasks import bootstrap

                bootstrap.delay()  # finishes by queueing market discovery
                self.stdout.write(f"Queued the first NBA sync (still needed: {', '.join(sorted(needed))}).")
            else:
                from markets.tasks import discover_markets

                discover_markets.delay()  # prices right away instead of at the first 10-minute tick
                self.stdout.write("Queued Polymarket market discovery.")
        except Exception as exc:  # broker not up yet: not fatal, the scheduled jobs will catch up
            self.stderr.write(f"Could not queue the sync: {exc}")
