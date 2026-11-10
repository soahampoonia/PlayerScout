import re

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from realtime import push

GAME_ID = re.compile(r"^\d{10}$")
MAX_SUBSCRIPTIONS = 60


class LiveConsumer(AsyncJsonWebsocketConsumer):
    """/ws/live. Every browser is in the "live" group. A page then says what else it
    shows: {"subscribe": {"games": ["0022600203"], "players": [1629029]}}. Each
    subscribe message replaces the previous one."""

    async def connect(self):
        self.joined: set[str] = set()
        await self.accept()
        await self._set_groups({push.ALL})
        await self.send_json({"type": "hello", "data": {}})

    async def disconnect(self, code):
        await self._set_groups(set())

    async def receive_json(self, content, **kwargs):
        subscribe = content.get("subscribe") if isinstance(content, dict) else None
        if not isinstance(subscribe, dict):
            return
        wanted = {push.ALL}
        for game_id in subscribe.get("games") or []:
            if isinstance(game_id, str) and GAME_ID.match(game_id):
                wanted.add(push.game_group(game_id))
        for player_id in subscribe.get("players") or []:
            if isinstance(player_id, int) and not isinstance(player_id, bool) and 0 < player_id < 10**10:
                wanted.add(push.player_group(player_id))
        wanted = set(sorted(wanted)[: MAX_SUBSCRIPTIONS + 1]) | {push.ALL}
        await self._set_groups(wanted)
        await self.send_json({"type": "subscribed", "data": {"groups": sorted(wanted)}})

    async def _set_groups(self, wanted: set[str]):
        for group in self.joined - wanted:
            await self.channel_layer.group_discard(group, self.channel_name)
        for group in wanted - self.joined:
            await self.channel_layer.group_add(group, self.channel_name)
        self.joined = set(wanted)

    async def push(self, message):
        await self.send_json({"type": message["event"], "data": message["data"]})
