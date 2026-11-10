from django.contrib import admin

from .models import IngestionRun, Watchlist


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = ("job_name", "status", "started_at", "finished_at", "rows_written")
    list_filter = ("job_name", "status")


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("player", "added_at")
    raw_id_fields = ("player",)
