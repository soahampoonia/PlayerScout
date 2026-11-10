from django.core.management.base import BaseCommand

from nba.seasons import current_season, previous_season
from predictions.backtest import run
from predictions.model import MODEL_VERSION


class Command(BaseCommand):
    help = "Replay the baseline model over a finished season and report how honest its probabilities are."

    def add_arguments(self, parser):
        parser.add_argument("--season", default=None, help="e.g. 2025-26 (default: last season)")

    def handle(self, *args, **options):
        season = options["season"] or previous_season(current_season())
        report = run(season)
        if not report:
            self.stdout.write(f"No games to replay for {season}. Has sync_game_logs run?")
            return
        self.stdout.write(f"{MODEL_VERSION} on {season}, stand-in line = season-to-date average rounded to .5\n")
        self.stdout.write("Brier score: lower is better; always saying 50% scores 0.250.\n")
        for stat, r in report.items():
            self.stdout.write(
                f"{stat.upper():>4}  games {r['n']:>6}  Brier model {r['brier_model']:.4f}  "
                f"season over-rate {r['brier_season_rate']:.4f}  coin flip 0.2500   "
                f"MAE model {r['mae_model']:.2f}  season avg {r['mae_season_avg']:.2f}  last-10 avg {r['mae_last10_avg']:.2f}"
            )
            for b in r["calibration"]:
                self.stdout.write(f"        {b['bucket']:>8}  n={b['n']:>5}  predicted {b['predicted']:.3f}  happened {b['actual']:.3f}")
