"""WebSocket routes."""

from django.urls import path

from realtime.consumers import LiveConsumer

websocket_urlpatterns = [
    path("ws/live", LiveConsumer.as_asgi()),
]
