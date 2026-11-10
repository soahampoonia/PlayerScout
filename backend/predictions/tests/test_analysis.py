import asyncio
import json
from decimal import Decimal

import pytest
from channels.layers import get_channel_layer
from rest_framework.test import APIClient

from markets.models import Market
from markets.pricing import record_prices
from nba.models import Game, PlayerGameLog
from predictions import analysis as analysis_module
from predictions import llm
from predictions.analysis import OUTPUT_SCHEMA, build_package, validate
from predictions.models import Analysis, AnalysisGamePrediction
from realtime import push


# ---- Adapter -----------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def openai_body(text=None, status="completed", refusal=None):
    content = [{"type": "refusal", "refusal": refusal}] if refusal else [{"type": "output_text", "text": text, "annotations": []}]
    return {
        "id": "resp_1", "object": "response", "status": status, "model": "gpt-6-luna",
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant", "content": content},
        ],
        "usage": {
            "input_tokens": 12000, "input_tokens_details": {"cached_tokens": 2000},
            "output_tokens": 3000, "output_tokens_details": {"reasoning_tokens": 1800}, "total_tokens": 15000,
        },
    }  # fmt: skip


@pytest.fixture
def openai(settings, monkeypatch):
    settings.LLM_PROVIDER, settings.OPENAI_API_KEY, settings.LLM_MODEL = "openai", "sk-test", "gpt-6-luna"
    calls = []

    def respond(status=200, body=None):
        def post(url, headers, json, timeout):
            calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return FakeResponse(status, body)

        monkeypatch.setattr(llm.requests, "post", post)

    return calls, respond


def test_openai_request_and_answer(openai, settings):
    calls, respond = openai
    respond(body=openai_body(json.dumps({"summary": "s"})))
    output, usage = llm.analyze("be careful", {"player": {"name": "X"}}, OUTPUT_SCHEMA)
    assert output == {"summary": "s"}
    sent = calls[0]
    assert sent["url"] == "https://api.openai.com/v1/responses"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    body = sent["json"]
    assert body["model"] == "gpt-6-luna" and body["instructions"] == "be careful" and body["store"] is False
    assert body["text"]["format"] == {"type": "json_schema", "name": "player_analysis", "schema": OUTPUT_SCHEMA, "strict": True}
    assert body["reasoning"] == {"effort": settings.LLM_REASONING_EFFORT}
    assert json.loads(body["input"][0]["content"]) == {"player": {"name": "X"}}
    assert (usage.tokens_in, usage.tokens_out, usage.cached_tokens, usage.reasoning_tokens) == (12000, 3000, 2000, 1800)
    # $0.10 per 1M fresh input, $0.01 cached, $0.50 output
    assert usage.cost_usd == round((10000 * 0.10 + 2000 * 0.01 + 3000 * 0.50) / 1e6, 6)


@pytest.mark.parametrize(
    "status, body, message",
    [
        (200, openai_body(refusal="I can't help with that."), "The model declined: I can't help with that."),
        (200, openai_body("{", status="incomplete"), "The answer was cut off (max_output_tokens)"),
        (200, openai_body("not json"), "not valid JSON"),
        (401, {"error": {"message": "Incorrect API key provided"}}, "HTTP 401: Incorrect API key provided"),
    ],
)
def test_openai_failures_become_readable_errors(openai, status, body, message):
    _, respond = openai
    respond(status, body)
    with pytest.raises(llm.LLMError, match=message.replace("(", r"\(").replace(")", r"\)")):
        llm.analyze("x", {}, OUTPUT_SCHEMA)


def test_missing_key(settings):
    settings.LLM_PROVIDER, settings.OPENAI_API_KEY = "openai", ""
    assert llm.is_configured() is False
    with pytest.raises(llm.LLMNotConfigured):
        llm.analyze("x", {}, OUTPUT_SCHEMA)


def test_schema_is_valid_for_strict_mode():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(OUTPUT_SCHEMA)


# ---- Package, cache, run --------------------------------------------------------


@pytest.fixture
def setup(league, settings):
    settings.LLM_PROVIDER = "fake"
    game = Game.objects.filter(status="scheduled").order_by("game_date", "tipoff_at", "id").first()
    log = PlayerGameLog.objects.filter(team=game.home_team, player__team=game.home_team).order_by("-pts").first()
    player = log.player
    Market.objects.create(
        id="m1", condition_id="m1", event_id="e", event_slug="s", question=f"{player.full_name}: Points O/U 22.5",
        market_type="player_prop", stat="pts", line=Decimal("22.5"), game=game, player=player, team=player.team,
        match_method="roster_name", match_confidence=Decimal("1"), yes_token_id="y1", no_token_id="n1",
    )  # fmt: skip
    record_prices([{"market_id": "m1", "bid": 0.4, "ask": 0.42, "volume_24h": 5000}])
    return {"game": game, "player": player}


def test_package_targets_market_lines_and_model_lines(setup):
    package = build_package(setup["player"])
    targets = package["targets"]
    first = [t for t in targets if t["game_id"] == setup["game"].id]
    assert {t["stat"] for t in first} == {"pts", "reb", "ast"}
    pts = next(t for t in first if t["stat"] == "pts")
    assert (pts["line"], pts["market_id"], pts["market_prob"], pts["market_is_thin"]) == (22.5, "m1", 0.41, False)
    reb = next(t for t in first if t["stat"] == "reb")
    assert reb["market_id"] is None and reb["line"] % 1 == 0.5 and 0 < reb["model_prob"] < 1
    payload = package["payload"]
    assert payload["player"]["name"] == setup["player"].full_name
    assert len(payload["last_10_games"]["rows"]) == min(10, setup["player"].game_logs.count()) and len(payload["upcoming_games"]) == 3
    assert payload["form"]["season"]["games"] >= 5 and "per_min" not in payload["form"]["season"]
    assert all(v is not None for g in payload["upcoming_games"] for line in g["lines"] for v in line.values())
    line = payload["upcoming_games"][0]["lines"][0]
    assert line["line_source"] == "polymarket" and line["market_prob"] == 0.41
    json.dumps(payload)  # must serialise


def test_validate_keeps_only_asked_lines():
    targets = [
        {"game_id": "g1", "stat": "pts", "line": 22.5, "market_id": None, "model_prob": 0.5, "market_prob": None, "market_is_thin": None, "snapshot_id": 1},
        {"game_id": "g1", "stat": "reb", "line": 6.5, "market_id": None, "model_prob": 0.5, "market_prob": None, "market_is_thin": None, "snapshot_id": 1},
    ]  # fmt: skip
    output = {
        "games": [
            {"game_id": "g1", "stat": "pts", "line": 22.5, "probability": 0.62, "confidence": "medium", "reasons": ["a"]},
            {"game_id": "g1", "stat": "pts", "line": 24.5, "probability": 0.5, "confidence": "low", "reasons": []},
            {"game_id": "g9", "stat": "ast", "line": 5.5, "probability": 1.4, "confidence": "low", "reasons": []},
        ]
    }
    rows, problems = validate(output, targets)
    assert [(r["stat"], r["llm_prob"], r["confidence"]) for r in rows] == [("pts", 0.62, "medium")]
    assert any("not asked about (pts 24.5)" in p for p in problems)
    assert any("no probability for rebounds 6.5" in p for p in problems)


@pytest.fixture
def client(setup, monkeypatch):
    from predictions import tasks

    monkeypatch.setattr(tasks.run_analysis, "delay", lambda analysis_id: tasks.run_analysis(analysis_id))
    return APIClient()


def test_analyze_then_reuse_then_refresh(client, setup):
    player = setup["player"]
    first = client.post(f"/api/players/{player.id}/analyses", {}, format="json")
    assert first.status_code == 202 and first.json()["cached"] is False
    analysis_id = first.json()["analysis"]["id"]

    detail = client.get(f"/api/analyses/{analysis_id}").json()
    assert detail["status"] == "done" and detail["summary"].startswith(setup["player"].full_name)
    assert "no LLM was called" in detail["outlook"] and detail["llm_model"] == "offline-demo"
    game = detail["games"][0]
    pts = next(p for p in game["predictions"] if p["stat"] == "pts")
    assert (pts["line"], pts["market_prob"], pts["line_source"]) == (22.5, 0.41, "polymarket")
    assert pts["llm_prob"] == pytest.approx((pts["model_prob"] + 0.41) / 2, abs=0.001)  # the package rounds to 3 decimals
    assert len(game["predictions"]) == 3 and AnalysisGamePrediction.objects.filter(market_id="m1").count() == 1
    assert AnalysisGamePrediction.objects.get(market_id="m1").market_snapshot_id is not None

    again = client.post(f"/api/players/{player.id}/analyses", {}, format="json")
    assert again.status_code == 200 and again.json()["cached"] is True and again.json()["analysis"]["id"] == analysis_id

    record_prices([{"market_id": "m1", "bid": 0.42, "ask": 0.44}])  # 2 points: still the same analysis
    small = client.post(f"/api/players/{player.id}/analyses", {}, format="json")
    assert small.status_code == 200 and small.json()["analysis"]["id"] == analysis_id

    record_prices([{"market_id": "m1", "bid": 0.5, "ask": 0.52}])  # 10 points: a new analysis
    moved = client.post(f"/api/players/{player.id}/analyses", {}, format="json")
    assert moved.status_code == 202 and moved.json()["analysis"]["id"] != analysis_id
    first_one = Analysis.objects.get(pk=analysis_id)
    assert first_one.superseded_by_id is not None and first_one.stale_reason == "market_move"

    forced = client.post(f"/api/players/{player.id}/analyses", {"force": True}, format="json")
    assert forced.status_code == 202
    old = Analysis.objects.get(pk=moved.json()["analysis"]["id"])
    assert old.is_stale and old.stale_reason == "manual"
    assert Analysis.objects.count() == 3


def test_a_failed_analysis_is_retried(client, setup, monkeypatch):
    def broken(*args):
        raise llm.LLMError("OpenAI returned HTTP 500: overloaded")

    monkeypatch.setattr(llm, "analyze", broken)
    player = setup["player"]
    body = client.post(f"/api/players/{player.id}/analyses", {}, format="json").json()
    failed = client.get(f"/api/analyses/{body['analysis']['id']}").json()
    assert failed["status"] == "failed" and "overloaded" in failed["error"]
    monkeypatch.undo()
    from predictions import tasks

    monkeypatch.setattr(tasks.run_analysis, "delay", lambda analysis_id: tasks.run_analysis(analysis_id))
    retry = client.post(f"/api/players/{player.id}/analyses", {}, format="json")
    assert retry.status_code == 202 and retry.json()["analysis"]["id"] != body["analysis"]["id"]


def test_not_configured_and_no_games(client, setup, settings):
    settings.LLM_PROVIDER, settings.OPENAI_API_KEY = "openai", ""
    assert client.post(f"/api/players/{setup['player'].id}/analyses", {}, format="json").status_code == 503
    settings.LLM_PROVIDER = "fake"
    from nba.models import Player

    free_agent = Player.objects.filter(team__isnull=True).first()
    response = client.post(f"/api/players/{free_agent.id}/analyses", {}, format="json")
    assert response.status_code == 409 and "no upcoming games" in response.json()["detail"]


def test_history_list_and_paging(client, setup):
    player = setup["player"]
    for _ in range(3):
        client.post(f"/api/players/{player.id}/analyses", {"force": True}, format="json")
    page = client.get("/api/analyses?limit=2").json()
    assert len(page["items"]) == 2 and page["next_before"]
    rest = client.get(f"/api/analyses?limit=2&before={page['next_before']}").json()
    assert len(rest["items"]) == 1 and rest["next_before"] is None
    created = [i["created_at"] for i in page["items"] + rest["items"]]
    assert created == sorted(created, reverse=True)
    assert client.get(f"/api/analyses?player_id={player.id}").json()["items"][0]["player"]["id"] == player.id
    assert client.get("/api/analyses?player_id=1").json()["items"] == []
    assert client.get("/api/analyses?player_id=abc").status_code == 400


def test_analysis_done_is_pushed(setup):
    analysis, created = analysis_module.request_analysis(setup["player"])
    layer = get_channel_layer()

    async def listen():
        channel = await layer.new_channel()
        await layer.group_add(push.player_group(setup["player"].id), channel)
        return channel

    channel = asyncio.run(listen())
    analysis_module.run_analysis(analysis.id)
    message = asyncio.run(layer.receive(channel))
    assert message["event"] == "analysis.done" and message["data"] == {
        "analysis_id": str(analysis.id), "player_id": setup["player"].id, "status": "done",
    }  # fmt: skip


def test_stuck_analyses_are_failed(setup):
    from datetime import timedelta

    from django.utils import timezone

    analysis, _ = analysis_module.request_analysis(setup["player"])
    Analysis.objects.filter(pk=analysis.pk).update(created_at=timezone.now() - timedelta(minutes=30))
    assert analysis_module.recover_stuck() == 1
    assert Analysis.objects.get(pk=analysis.pk).status == "failed"
