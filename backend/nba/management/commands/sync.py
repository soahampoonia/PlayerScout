from django.core.management.base import BaseCommand, CommandError

from nba.ingest.runs import JobAlreadyRunning
from nba.tasks import JOBS


class Command(BaseCommand):
    help = "Run an NBA sync job right now, in this process: reference, schedule, team_stats, game_logs, or all."

    def add_arguments(self, parser):
        parser.add_argument("job", choices=["reference", "schedule", "team_stats", "game_logs", "all"])

    def handle(self, *args, **options):
        names = list(JOBS) if options["job"] == "all" else [f'sync_{options["job"]}']
        failed = []
        for name in names:
            self.stdout.write(f"{name} ...", ending=" ")
            self.stdout.flush()
            try:
                self.stdout.write(f"{JOBS[name]()} rows")
            except JobAlreadyRunning:
                self.stdout.write("already running, skipped")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"failed: {exc}"))
                failed.append(name)
        if failed:
            raise CommandError(f"Failed: {', '.join(failed)}. Details are in the ingestion_run table and /api/status.")
