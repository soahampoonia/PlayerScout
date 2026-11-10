from django.urls import path

from nba import api
from predictions import api as analyses

urlpatterns = [
    path("games", api.games),
    path("games/<str:game_id>", api.game_detail),
    path("search-index", api.search_index),
    path("players/<int:player_id>", api.player_detail),
    path("players/<int:player_id>/markets", api.player_markets),
    path("teams/<int:team_id>", api.team_detail),
    path("status", api.status),
    path("players/<int:player_id>/analyses", analyses.create_analysis),
    path("analyses", analyses.analysis_list),
    path("analyses/<uuid:analysis_id>", analyses.analysis_detail),
]
