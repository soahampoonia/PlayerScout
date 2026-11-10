from django.contrib import admin

from .models import Analysis, AnalysisGamePrediction, FeatureSnapshot, ModelPrediction


@admin.register(Analysis)
class AnalysisAdmin(admin.ModelAdmin):
    list_display = ("id", "player", "status", "llm_model", "is_stale", "pinned", "created_at")
    list_filter = ("status", "is_stale", "llm_model")
    raw_id_fields = ("player", "superseded_by")


@admin.register(AnalysisGamePrediction)
class AnalysisGamePredictionAdmin(admin.ModelAdmin):
    list_display = ("analysis", "game", "stat", "line", "llm_prob", "market_prob", "model_prob", "outcome")
    raw_id_fields = ("analysis", "game", "market", "market_snapshot")


@admin.register(FeatureSnapshot)
class FeatureSnapshotAdmin(admin.ModelAdmin):
    list_display = ("player", "game", "computed_at")
    raw_id_fields = ("player", "game")


@admin.register(ModelPrediction)
class ModelPredictionAdmin(admin.ModelAdmin):
    list_display = ("player", "game", "stat", "line", "model_version", "p_over")
    raw_id_fields = ("feature_snapshot", "player", "game")
