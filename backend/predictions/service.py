"""Store and read model output: one feature_snapshot per (player, game) for as long
as its inputs do not change, and one model_prediction per stat and line."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from markets.models import Market
from nba.models import Game, Player
from nba.ingest.runs import ingestion_run
from predictions import features as feature_builder
from predictions.model import MODEL_VERSION, p_over, project
from predictions.models import FeatureSnapshot, ModelPrediction

STATS = ("pts", "reb", "ast")


def snapshot_for(player: Player, game: Game) -> FeatureSnapshot:
    """The current feature snapshot, reused while nothing it depends on has changed."""
    features = feature_builder.build(player, game)
    digest = feature_builder.data_hash(features)
    latest = FeatureSnapshot.objects.filter(player=player, game=game).order_by("-computed_at").first()
    if latest and latest.data_hash == digest:
        return latest
    return FeatureSnapshot.objects.create(
        player=player, game=game, computed_at=timezone.now(), features=features, data_hash=digest
    )


def projections(snapshot: FeatureSnapshot) -> dict[str, dict]:
    result = {}
    for stat in STATS:
        projected = project(snapshot.features, stat)
        if projected:
            result[stat] = projected
    return result


def predict_lines(snapshot: FeatureSnapshot, lines: list[tuple[str, float]]) -> dict[tuple[str, float], float]:
    """P(over) for each (stat, line), stored as model_prediction rows (reused if present)."""
    projected = projections(snapshot)
    existing = {
        (p.stat, float(p.line)): p
        for p in ModelPrediction.objects.filter(feature_snapshot=snapshot, model_version=MODEL_VERSION)
    }
    result, new = {}, []
    for stat, line in set(lines):
        proj = projected.get(stat)
        if proj is None:
            continue
        key = (stat, float(line))
        if key in existing:
            result[key] = float(existing[key].p_over)
            continue
        prob = p_over(proj["center"], proj["sd"], float(line))
        result[key] = prob
        new.append(
            ModelPrediction(
                feature_snapshot=snapshot, player_id=snapshot.player_id, game_id=snapshot.game_id, stat=stat,
                line=Decimal(str(line)), model_version=MODEL_VERSION, mean=Decimal(str(proj["mean"])),
                std=Decimal(str(proj["sd"])), p_over=Decimal(str(prob)),
            )  # fmt: skip
        )
    ModelPrediction.objects.bulk_create(new)
    return result


def refresh_model(hours_ahead: int = 72) -> int:
    """Model probabilities for every matched prop line in games over the next few days."""
    with ingestion_run("refresh_model") as run:
        now = timezone.now()
        props = (
            Market.objects.filter(
                market_type=Market.MarketType.PLAYER_PROP,
                closed=False,
                player__isnull=False,
                game__status=Game.Status.SCHEDULED,
                game__tipoff_at__gte=now - timedelta(hours=1),
                game__tipoff_at__lte=now + timedelta(hours=hours_ahead),
                match_confidence__gte=settings.MARKET_MATCH_THRESHOLD,
            )
            .values_list("player_id", "game_id", "stat", "line")
        )
        wanted: dict[tuple[int, str], list] = {}
        for player_id, game_id, stat, line in props:
            if stat in STATS and line is not None:
                wanted.setdefault((player_id, game_id), []).append((stat, float(line)))
        players = Player.objects.in_bulk({p for p, _ in wanted})
        games = Game.objects.in_bulk({g for _, g in wanted})
        for (player_id, game_id), lines in wanted.items():
            snapshot = snapshot_for(players[player_id], games[game_id])
            run.rows_written += len(predict_lines(snapshot, lines))
        return run.rows_written
