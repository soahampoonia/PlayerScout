"""Settings for Player Scout v2. Everything environment-specific comes from env vars
(see .env.example); the defaults work for the Docker Compose stack."""

import os
from pathlib import Path

from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,api")

INSTALLED_APPS = [
    "daphne",  # must be first: gives runserver ASGI + WebSocket support
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "channels",
    "nba",
    "markets",
    "predictions",
    "ops",
    "realtime",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "playerscout"),
        "USER": env("POSTGRES_USER", "scout"),
        "PASSWORD": env("POSTGRES_PASSWORD", "scout"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL", "redis://localhost:6379")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"{REDIS_URL}/1",
        "KEY_PREFIX": "scout",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [f"{REDIS_URL}/2"]},
    }
}

# --- Time -------------------------------------------------------------------
# Timestamps are stored in UTC. The NBA runs on Eastern time, so "today's games"
# and every schedule below use LEAGUE_TIME_ZONE.
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True
LEAGUE_TIME_ZONE = "America/New_York"

# --- Celery -----------------------------------------------------------------
CELERY_BROKER_URL = f"{REDIS_URL}/0"
CELERY_RESULT_BACKEND = f"{REDIS_URL}/0"
CELERY_TIMEZONE = LEAGUE_TIME_ZONE
CELERY_TASK_TIME_LIMIT = 15 * 60
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    # Box scores are final a few hours after the last West Coast game.
    "sync-game-logs-nightly": {"task": "nba.sync_game_logs", "schedule": crontab(hour=4, minute=0)},
    "sync-team-stats-nightly": {"task": "nba.sync_team_stats", "schedule": crontab(hour=4, minute=30)},
    # Rosters and schedule changes (trades, postponements) before the day starts.
    "sync-reference-daily": {"task": "nba.sync_reference", "schedule": crontab(hour=6, minute=0)},
    "sync-schedule-daily": {"task": "nba.sync_schedule", "schedule": crontab(hour=6, minute=10)},
    # Polymarket: find and match markets, and a price fallback for when market_stream is down.
    "discover-markets": {"task": "markets.discover_markets", "schedule": 10 * 60},
    "poll-midpoints": {"task": "markets.poll_midpoints", "schedule": 60},
    # Scores while games are on. Does nothing (no request) when no game is near tip-off or live.
    "live-scoreboard": {"task": "nba.live_scoreboard", "schedule": 15},
    "recover-stuck-analyses": {"task": "predictions.recover_stuck_analyses", "schedule": 10 * 60},
    "prune-snapshots-daily": {"task": "markets.prune_snapshots", "schedule": crontab(hour=5, minute=15)},
}

# --- REST -------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

STATIC_URL = "static/"

# --- NBA ingestion ----------------------------------------------------------
# Leave NBA_SEASON empty to derive it from today's date ("2026-27" from October on).
NBA_SEASON = env("NBA_SEASON", "") or None
# How many seasons of game logs to keep (current season + N-1 before it).
NBA_LOG_SEASONS = int(env("NBA_LOG_SEASONS", "2"))
# stats.nba.com rate-limits hard; keep about one request per second.
NBA_REQUEST_INTERVAL = float(env("NBA_REQUEST_INTERVAL", "1.1"))
NBA_REQUEST_TIMEOUT = int(env("NBA_REQUEST_TIMEOUT", "30"))
NBA_REQUEST_RETRIES = int(env("NBA_REQUEST_RETRIES", "3"))
# Optional HTTP proxy for stats.nba.com (it blocks many cloud IP ranges).
NBA_PROXY = env("NBA_PROXY", "") or None

# --- Polymarket --------------------------------------------------------------
# Market data is public: no account or key needed.
POLYMARKET_GAMMA_URL = env("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")
POLYMARKET_CLOB_URL = env("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")
POLYMARKET_WS_URL = env("POLYMARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
POLYMARKET_TIMEOUT = int(env("POLYMARKET_TIMEOUT", "30"))
# Polymarket's NBA series. Looked up from /sports on each run; this is the fallback.
POLYMARKET_NBA_SERIES_ID = env("POLYMARKET_NBA_SERIES_ID", "10345")
# Discovery looks at games from 2 days ago to this many days ahead.
MARKET_DISCOVERY_DAYS_AHEAD = int(env("MARKET_DISCOVERY_DAYS_AHEAD", "8"))
# market_stream follows games that tip off within this many hours (and games in progress).
MARKET_STREAM_HOURS_AHEAD = int(env("MARKET_STREAM_HOURS_AHEAD", "36"))
MARKET_STREAM_ASSETS_PER_CONNECTION = int(env("MARKET_STREAM_ASSETS_PER_CONNECTION", "200"))
# A player-name match below this confidence is not shown in the app (it is listed in the admin).
MARKET_MATCH_THRESHOLD = 0.75
# Snapshot a market when its probability moves this much, or after this many seconds regardless.
MARKET_SNAPSHOT_MOVE = 0.01
MARKET_SNAPSHOT_HEARTBEAT = int(env("MARKET_SNAPSHOT_HEARTBEAT", "600"))
MARKET_SNAPSHOT_RETENTION_DAYS = 30
# "Thin" markets are greyed out: a wide bid/ask spread or little trading in the last 24 h.
MARKET_THIN_SPREAD = float(env("MARKET_THIN_SPREAD", "0.05"))
MARKET_THIN_MIN_VOLUME_24H = float(env("MARKET_THIN_MIN_VOLUME_24H", "100"))
# Polymarket shows the last trade instead of the midpoint when the spread is wider than this.
MARKET_MIDPOINT_MAX_SPREAD = 0.10

# --- LLM ----------------------------------------------------------------------
# openai: the Responses API with Structured Outputs. fake: no network, canned answers (testing).
LLM_PROVIDER = env("LLM_PROVIDER", "openai")
LLM_MODEL = env("LLM_MODEL", "gpt-6-luna")
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_REASONING_EFFORT = env("LLM_REASONING_EFFORT", "medium")  # none, low, medium, high, xhigh, max
LLM_MAX_OUTPUT_TOKENS = int(env("LLM_MAX_OUTPUT_TOKENS", "12000"))
LLM_TIMEOUT = int(env("LLM_TIMEOUT", "180"))
# US dollars per million tokens, for the cost shown with each analysis (gpt-6-luna list prices, Nov 2026).
LLM_PRICE_INPUT = float(env("LLM_PRICE_INPUT", "0.10"))
LLM_PRICE_CACHED_INPUT = float(env("LLM_PRICE_CACHED_INPUT", "0.01"))
LLM_PRICE_OUTPUT = float(env("LLM_PRICE_OUTPUT", "0.50"))

SEARCH_INDEX_CACHE_KEY = "search-index:v1"
SEARCH_INDEX_TTL = 24 * 60 * 60

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
