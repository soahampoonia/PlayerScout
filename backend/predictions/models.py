"""Features, baseline model output, and LLM analyses. Created now so the schema is
complete; filled by build steps 5 to 7."""

import uuid

from django.db import models


class FeatureSnapshot(models.Model):
    id = models.BigAutoField(primary_key=True)
    player = models.ForeignKey("nba.Player", on_delete=models.CASCADE, related_name="feature_snapshots")
    game = models.ForeignKey("nba.Game", on_delete=models.CASCADE, related_name="feature_snapshots")
    computed_at = models.DateTimeField()
    features = models.JSONField()
    data_hash = models.CharField(max_length=64)

    class Meta:
        db_table = "feature_snapshot"
        indexes = [
            models.Index(fields=["player", "game", "-computed_at"], name="feature_player_game_idx"),
        ]


class ModelPrediction(models.Model):
    id = models.BigAutoField(primary_key=True)
    feature_snapshot = models.ForeignKey(FeatureSnapshot, on_delete=models.CASCADE, related_name="predictions")
    player = models.ForeignKey("nba.Player", on_delete=models.CASCADE, related_name="model_predictions")
    game = models.ForeignKey("nba.Game", on_delete=models.CASCADE, related_name="model_predictions")
    stat = models.CharField(max_length=8)
    line = models.DecimalField(max_digits=5, decimal_places=1)
    model_version = models.CharField(max_length=40)
    mean = models.DecimalField(max_digits=6, decimal_places=2)
    std = models.DecimalField(max_digits=6, decimal_places=2)
    p_over = models.DecimalField(max_digits=5, decimal_places=4)

    class Meta:
        db_table = "model_prediction"
        indexes = [
            models.Index(fields=["player", "game", "stat"], name="prediction_player_game_idx"),
        ]


class Analysis(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    class StaleReason(models.TextChoices):
        NEW_GAME = "new_game"
        MARKET_MOVE = "market_move"
        INJURY_CHANGE = "injury_change"
        MANUAL = "manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    player = models.ForeignKey("nba.Player", on_delete=models.CASCADE, related_name="analyses")
    cache_key = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED)
    prompt_version = models.CharField(max_length=20)
    llm_provider = models.CharField(max_length=40)
    llm_model = models.CharField(max_length=80)
    input_payload = models.JSONField(default=dict)
    output = models.JSONField(null=True, blank=True)
    summary = models.TextField(blank=True, default="")
    outlook = models.TextField(blank=True, default="")
    tokens_in = models.IntegerField(null=True, blank=True)
    tokens_out = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    is_stale = models.BooleanField(default=False)
    stale_reason = models.CharField(max_length=14, choices=StaleReason.choices, null=True, blank=True)
    superseded_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="supersedes")
    pinned = models.BooleanField(default=False)
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "analysis"
        indexes = [
            models.Index(fields=["player", "-created_at"], name="analysis_player_created_idx"),
        ]


class AnalysisGamePrediction(models.Model):
    class Confidence(models.TextChoices):
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"

    class Outcome(models.TextChoices):
        OVER = "over"
        UNDER = "under"
        VOID = "void"

    id = models.BigAutoField(primary_key=True)
    analysis = models.ForeignKey(Analysis, on_delete=models.CASCADE, related_name="game_predictions")
    game = models.ForeignKey("nba.Game", on_delete=models.CASCADE, related_name="analysis_predictions")
    stat = models.CharField(max_length=8)
    line = models.DecimalField(max_digits=5, decimal_places=1)
    market = models.ForeignKey("markets.Market", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    market_snapshot = models.ForeignKey(
        "markets.MarketSnapshot", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    model_prob = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    market_prob = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    llm_prob = models.DecimalField(max_digits=5, decimal_places=4)
    confidence = models.CharField(max_length=6, choices=Confidence.choices)
    reasons = models.JSONField(default=list)
    is_locked = models.BooleanField(default=False)
    actual_value = models.SmallIntegerField(null=True, blank=True)
    outcome = models.CharField(max_length=5, choices=Outcome.choices, null=True, blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "analysis_game_prediction"
        indexes = [
            models.Index(fields=["game", "is_locked"], name="agp_game_locked_idx"),
        ]
