"""NBA reference and stat tables. Table and column names follow the design doc."""

from django.contrib.postgres.fields import ArrayField
from django.db import models


class Team(models.Model):
    id = models.IntegerField(primary_key=True)  # NBA team_id
    abbreviation = models.CharField(max_length=3, unique=True)
    city = models.CharField(max_length=40)
    name = models.CharField(max_length=40)  # "Lakers"
    full_name = models.CharField(max_length=80)  # "Los Angeles Lakers"
    search_name = models.CharField(max_length=120)  # lowercase, accents removed: city + name + abbreviation
    conference = models.CharField(max_length=4)  # East / West
    polymarket_team_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        db_table = "team"
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name


class Player(models.Model):
    id = models.IntegerField(primary_key=True)  # NBA person_id
    first_name = models.CharField(max_length=60)
    last_name = models.CharField(max_length=60)
    full_name = models.CharField(max_length=120)  # as the NBA spells it, accents kept
    search_name = models.CharField(max_length=120, db_index=True)  # lowercase, accents removed
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="players")
    position = models.CharField(max_length=8, blank=True, default="")
    jersey = models.CharField(max_length=4, blank=True, default="")
    is_active = models.BooleanField(default=False, db_index=True)
    # Other spellings seen on Polymarket, used by market matching (step 3).
    name_aliases = ArrayField(models.CharField(max_length=120), default=list, blank=True)

    class Meta:
        db_table = "player"
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return self.full_name


class Game(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled"
        LIVE = "live"
        FINAL = "final"
        POSTPONED = "postponed"

    id = models.CharField(primary_key=True, max_length=10)  # NBA game_id, e.g. 0022600123
    season = models.CharField(max_length=7)  # "2026-27"
    game_date = models.DateField(db_index=True)  # the date in Eastern time
    tipoff_at = models.DateTimeField(null=True, blank=True)  # UTC
    home_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="home_games")
    away_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="away_games")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    period = models.SmallIntegerField(null=True, blank=True)
    clock = models.CharField(max_length=12, blank=True, default="")
    home_score = models.SmallIntegerField(null=True, blank=True)
    away_score = models.SmallIntegerField(null=True, blank=True)
    polymarket_event_id = models.CharField(max_length=64, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "game"
        ordering = ["game_date", "tipoff_at", "id"]

    def __str__(self):
        return f"{self.away_team_id} @ {self.home_team_id} {self.game_date}"


class PlayerGameLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="game_logs")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="player_logs")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="+")
    opponent_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="+")
    game_date = models.DateField()
    is_home = models.BooleanField()
    started = models.BooleanField(null=True, blank=True)  # not in the league game log; filled from box scores later
    minutes = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    pts = models.SmallIntegerField(default=0)
    reb = models.SmallIntegerField(default=0)
    ast = models.SmallIntegerField(default=0)
    stl = models.SmallIntegerField(default=0)
    blk = models.SmallIntegerField(default=0)
    tov = models.SmallIntegerField(default=0)
    fg3m = models.SmallIntegerField(default=0)
    fgm = models.SmallIntegerField(default=0)
    fga = models.SmallIntegerField(default=0)
    ftm = models.SmallIntegerField(default=0)
    fta = models.SmallIntegerField(default=0)
    plus_minus = models.SmallIntegerField(default=0)
    usage_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "player_game_log"
        constraints = [
            models.UniqueConstraint(fields=["player", "game"], name="player_game_log_player_game_uniq"),
        ]
        indexes = [
            models.Index(fields=["player", "-game_date"], name="pgl_player_date_idx"),
        ]

    def __str__(self):
        return f"{self.player_id} {self.game_id}"


class TeamStatSnapshot(models.Model):
    pk = models.CompositePrimaryKey("team_id", "season", "as_of")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="stat_snapshots")
    season = models.CharField(max_length=7)
    as_of = models.DateField()
    pace = models.DecimalField(max_digits=6, decimal_places=2)
    off_rating = models.DecimalField(max_digits=6, decimal_places=2)
    def_rating = models.DecimalField(max_digits=6, decimal_places=2)
    opp_pts = models.DecimalField(max_digits=6, decimal_places=2)
    opp_reb = models.DecimalField(max_digits=6, decimal_places=2)
    opp_ast = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        db_table = "team_stat_snapshot"

    def __str__(self):
        return f"{self.team_id} {self.season} {self.as_of}"


class InjuryReport(models.Model):
    class Status(models.TextChoices):
        OUT = "out"
        DOUBTFUL = "doubtful"
        QUESTIONABLE = "questionable"
        PROBABLE = "probable"
        AVAILABLE = "available"

    id = models.BigAutoField(primary_key=True)
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="injury_reports")
    game = models.ForeignKey(Game, null=True, blank=True, on_delete=models.SET_NULL, related_name="injury_reports")
    status = models.CharField(max_length=12, choices=Status.choices)
    reason = models.CharField(max_length=200, blank=True, default="")
    reported_at = models.DateTimeField()
    source = models.CharField(max_length=40)

    class Meta:
        db_table = "injury_report"
        indexes = [
            models.Index(fields=["player", "-reported_at"], name="injury_player_reported_idx"),
        ]
