from django.contrib import admin

from .models import Market, MarketSnapshot


@admin.register(Market)
class MarketAdmin(admin.ModelAdmin):
    """Unmatched or low-confidence markets are listed here, not in the app. To fix one,
    set its game / player / team: it is then marked manual and discovery leaves it alone."""

    list_display = ("question", "event_slug", "market_type", "stat", "line", "match_method", "match_confidence", "closed")
    list_filter = ("match_method", "market_type", "stat", "closed")
    search_fields = ("question", "event_slug")
    raw_id_fields = ("game", "player", "team")
    readonly_fields = ("raw",)

    def save_model(self, request, obj, form, change):
        if change and {"game", "player", "team"} & set(form.changed_data):
            obj.match_method = Market.MatchMethod.MANUAL
            obj.match_confidence = 1
        super().save_model(request, obj, form, change)


@admin.register(MarketSnapshot)
class MarketSnapshotAdmin(admin.ModelAdmin):
    list_display = ("market", "captured_at", "implied_prob", "best_bid", "best_ask", "volume_24h", "is_thin")
    raw_id_fields = ("market",)
