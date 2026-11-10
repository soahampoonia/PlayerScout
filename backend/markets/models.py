"""Polymarket tables. Created now so the schema is complete; filled by build step 3."""

from django.db import models


class Market(models.Model):
    class MarketType(models.TextChoices):
        MONEYLINE = "moneyline"
        SPREAD = "spread"
        TOTAL = "total"
        PLAYER_PROP = "player_prop"
        FUTURE = "future"
        OTHER = "other"

    class Stat(models.TextChoices):
        PTS = "pts"
        REB = "reb"
        AST = "ast"
        OTHER = "other"

    class MatchMethod(models.TextChoices):
        TEAM_DATE = "team_date"
        ROSTER_NAME = "roster_name"
        MANUAL = "manual"
        UNMATCHED = "unmatched"

    class Outcome(models.TextChoices):
        YES = "yes"
        NO = "no"
        VOID = "void"

    id = models.CharField(primary_key=True, max_length=80)  # Polymarket market id
    condition_id = models.CharField(max_length=80, unique=True)
    event_id = models.CharField(max_length=64, db_index=True)
    event_slug = models.CharField(max_length=200)
    question = models.TextField()
    market_type = models.CharField(max_length=12, choices=MarketType.choices, default=MarketType.OTHER)
    stat = models.CharField(max_length=8, choices=Stat.choices, null=True, blank=True)
    line = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    game = models.ForeignKey("nba.Game", null=True, blank=True, on_delete=models.SET_NULL, related_name="markets")
    player = models.ForeignKey("nba.Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="markets")
    team = models.ForeignKey("nba.Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="markets")
    match_method = models.CharField(max_length=12, choices=MatchMethod.choices, default=MatchMethod.UNMATCHED)
    match_confidence = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    yes_token_id = models.CharField(max_length=100)
    no_token_id = models.CharField(max_length=100)
    active = models.BooleanField(default=True)
    closed = models.BooleanField(default=False)
    resolved_outcome = models.CharField(max_length=4, choices=Outcome.choices, null=True, blank=True)
    raw = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market"

    def __str__(self):
        return self.question[:80]


class MarketSnapshot(models.Model):
    id = models.BigAutoField(primary_key=True)
    market = models.ForeignKey(Market, on_delete=models.CASCADE, related_name="snapshots")
    captured_at = models.DateTimeField()
    best_bid = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    best_ask = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    midpoint = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    last_trade = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    spread = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    implied_prob = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    volume_24h = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    liquidity = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    is_thin = models.BooleanField(default=False)

    class Meta:
        db_table = "market_snapshot"
        indexes = [
            models.Index(fields=["market", "-captured_at"], name="snapshot_market_captured_idx"),
        ]
