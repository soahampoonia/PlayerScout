"""The LLM adapter. Everything else calls analyze(instructions, payload, schema) and
gets back (output, usage); which provider answers is a setting.

- "openai": the Responses API (POST /v1/responses) with Structured Outputs, so the
  answer must match the JSON schema. Default model gpt-6-luna.
- "fake": no network; a canned answer built from the payload. For tests and for
  trying the app without a key. Its text says it is fake.
"""

import json
import logging
import time
from dataclasses import dataclass

import requests
from django.conf import settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """The provider failed or gave an answer we cannot use. The message is shown to the user."""


class LLMNotConfigured(LLMError):
    pass


@dataclass
class Usage:
    provider: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_ms: int | None = None

    @property
    def cost_usd(self) -> float | None:
        if self.tokens_in is None or self.tokens_out is None:
            return None
        cached = self.cached_tokens or 0
        fresh = max(self.tokens_in - cached, 0)
        return round(
            (
                fresh * settings.LLM_PRICE_INPUT
                + cached * settings.LLM_PRICE_CACHED_INPUT
                + self.tokens_out * settings.LLM_PRICE_OUTPUT
            )
            / 1_000_000,
            6,
        )


def analyze(instructions: str, payload: dict, schema: dict, schema_name: str = "player_analysis") -> tuple[dict, Usage]:
    provider = settings.LLM_PROVIDER
    if provider == "openai":
        return _openai(instructions, payload, schema, schema_name)
    if provider == "fake":
        return _fake(payload)
    raise LLMNotConfigured(f"Unknown LLM_PROVIDER {provider!r}; use openai or fake.")


def is_configured() -> bool:
    return settings.LLM_PROVIDER == "fake" or (settings.LLM_PROVIDER == "openai" and bool(settings.OPENAI_API_KEY))


# ---- OpenAI ------------------------------------------------------------------


def _openai(instructions: str, payload: dict, schema: dict, schema_name: str) -> tuple[dict, Usage]:
    if not settings.OPENAI_API_KEY:
        raise LLMNotConfigured("No OpenAI key: set OPENAI_API_KEY in .env and restart.")
    body = {
        "model": settings.LLM_MODEL,
        "instructions": instructions,
        "input": [{"role": "user", "content": json.dumps(payload, separators=(",", ":"), default=str)}],
        "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        "reasoning": {"effort": settings.LLM_REASONING_EFFORT},
        "max_output_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
        "store": False,
    }
    started = time.monotonic()
    try:
        response = requests.post(
            f"{settings.OPENAI_BASE_URL}/responses",
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=settings.LLM_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise LLMError(f"Could not reach OpenAI: {type(exc).__name__}") from exc
    latency = int((time.monotonic() - started) * 1000)

    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = response.text[:200]
        raise LLMError(f"OpenAI returned HTTP {response.status_code}: {detail}"[:500])
    data = response.json()
    raw_usage = data.get("usage") or {}
    usage = Usage(
        provider="openai",
        model=data.get("model") or settings.LLM_MODEL,
        tokens_in=raw_usage.get("input_tokens"),
        tokens_out=raw_usage.get("output_tokens"),
        cached_tokens=(raw_usage.get("input_tokens_details") or {}).get("cached_tokens"),
        reasoning_tokens=(raw_usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
        latency_ms=latency,
    )

    if data.get("status") == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
        raise LLMError(f"The answer was cut off ({reason}). Try again, or raise LLM_MAX_OUTPUT_TOKENS.")
    text_parts = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "refusal":
                raise LLMError(f"The model declined: {part.get('refusal', '')}"[:500])
            if part.get("type") == "output_text":
                text_parts.append(part.get("text", ""))
    text = "".join(text_parts) or data.get("output_text") or ""
    try:
        return json.loads(text), usage
    except ValueError as exc:
        raise LLMError("The model's answer was not valid JSON.") from exc


# ---- Fake (tests, trying the app without a key) ----------------------------------


def _fake(payload: dict) -> tuple[dict, Usage]:
    """A rule-based stand-in: plain sentences built from the package's own numbers and
    the average of the model and market probabilities. No LLM is called; it says so."""
    name = payload.get("player", {}).get("name", "This player")
    upcoming = payload.get("upcoming_games", [])
    form = payload.get("form") or {}
    last5, season = form.get("last5") or {}, form.get("season") or {}

    def avg(window, stat):
        value = window.get(stat)
        return "–" if value is None else f"{value:.1f}"

    summary = (
        f"{name} is averaging {avg(last5, 'pts')} points, {avg(last5, 'reb')} rebounds and {avg(last5, 'ast')} assists "
        f"over his last 5 games, against {avg(season, 'pts')} / {avg(season, 'reb')} / {avg(season, 'ast')} for the season, "
        f"in {avg(last5, 'minutes')} minutes a night."
    )
    games, leans = [], []
    for game in upcoming:
        for line in game.get("lines", []):
            probs = [p for p in (line.get("model_prob"), line.get("market_prob")) if p is not None]
            prob = round(sum(probs) / len(probs), 3) if probs else 0.5
            market = line.get("market_prob")
            reasons = [f"Model {int(100 * line['model_prob'] + 0.5)}% over {line['line']}" + (f", market {int(100 * market + 0.5)}%." if market is not None else ", no market.")] if line.get("model_prob") is not None else []
            if line.get("market_is_thin"):
                reasons.append("The market is thin, so its price counts for less.")
            games.append(
                {
                    "game_id": game["game_id"],
                    "stat": line["stat"],
                    "line": line["line"],
                    "probability": prob,
                    "confidence": "low",
                    "reasons": reasons,
                }
            )
            if abs(prob - 0.5) >= 0.06:
                leans.append(f"{'over' if prob > 0.5 else 'under'} {line['line']} {line['stat']} vs {game['opponent']}")
    outlook = (
        f"Leans: {'; '.join(leans[:3])}." if leans else "No strong lean: model and market sit near 50% on these lines."
    ) + " (Offline demo provider: averages of the model and market, no LLM was called.)"
    output = {
        "summary": summary,
        "outlook": outlook,
        "games": games,
        "risks": ["Injury news is not in the data."],
        "data_gaps": ["offline demo provider: set LLM_PROVIDER=openai for a real analysis"],
    }
    return output, Usage(provider="fake", model="offline-demo", tokens_in=0, tokens_out=0, latency_ms=1)
