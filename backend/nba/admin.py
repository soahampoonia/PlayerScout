from django.contrib import admin

from .models import Game, InjuryReport, Player, PlayerGameLog, Team

# TeamStatSnapshot has a composite primary key, which the admin does not support.


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("abbreviation", "full_name", "conference")
    search_fields = ("full_name", "abbreviation")


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "team", "position", "jersey", "is_active")
    list_filter = ("is_active", "team")
    search_fields = ("full_name", "search_name")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("id", "game_date", "away_team", "home_team", "status", "away_score", "home_score")
    list_filter = ("status", "season")
    date_hierarchy = "game_date"


@admin.register(PlayerGameLog)
class PlayerGameLogAdmin(admin.ModelAdmin):
    list_display = ("player", "game_date", "team", "opponent_team", "minutes", "pts", "reb", "ast")
    raw_id_fields = ("player", "game")


@admin.register(InjuryReport)
class InjuryReportAdmin(admin.ModelAdmin):
    list_display = ("player", "status", "reason", "reported_at", "source")
    raw_id_fields = ("player", "game")
