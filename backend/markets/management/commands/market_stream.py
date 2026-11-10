import asyncio
import logging

from django.core.management.base import BaseCommand

from markets.stream import MarketStream


class Command(BaseCommand):
    help = "Follow Polymarket prices over the CLOB WebSocket (runs until stopped)."

    def handle(self, *args, **options):
        logging.getLogger("markets.stream").setLevel(logging.INFO)
        try:
            asyncio.run(MarketStream().run())
        except KeyboardInterrupt:
            pass
