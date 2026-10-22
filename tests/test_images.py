import httpx
import pytest

from instapost import producer
from instapost.models import Format, Pillar
from instapost.studio import images

URL = "https://example.test/generate"


def _fake_post(responses, calls):
    def post(url, **kw):
        calls.append(kw)
        status, body = responses.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))
    return post


def _quota_429(message, delay=None):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}] \
        if delay else []
    return 429, {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED",
                           "details": details}}


def test_free_tier_quota_fails_at_once_with_clear_reason(monkeypatch):
    calls, waits = [], []
    monkeypatch.setattr(images.httpx, "post", _fake_post([_quota_429(
        "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, "
        "model: gemini-2.5-flash-image")], calls))
    with pytest.raises(images.QuotaError, match="billing"):
        images.post_with_retry("gemini", URL, sleep=waits.append, json={})
    assert len(calls) == 1 and waits == []


def test_rate_limit_waits_as_asked_then_succeeds(monkeypatch):
    calls, waits = [], []
    monkeypatch.setattr(images.httpx, "post", _fake_post([
        _quota_429("Resource has been exhausted (e.g. check quota).", "17s"),
        (503, {"error": {"message": "overloaded"}}),
        (200, {"ok": True}),
    ], calls))
    r = images.post_with_retry("gemini", URL, sleep=waits.append, json={})
    assert r.json() == {"ok": True}
    assert waits == [17.0, 30]


def test_gives_up_after_retries_with_api_message(monkeypatch):
    calls, waits = [], []
    monkeypatch.setattr(images.httpx, "post", _fake_post(
        [(503, {"error": {"message": "model overloaded"}})] * 4, calls))
    with pytest.raises(RuntimeError, match="503: model overloaded"):
        images.post_with_retry("gemini", URL, sleep=waits.append, json={})
    assert len(calls) == 4


def test_other_errors_are_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(images.httpx, "post", _fake_post(
        [(400, {"error": {"message": "API key not valid"}})], calls))
    with pytest.raises(RuntimeError, match="400: API key not valid"):
        images.post_with_retry("gemini", URL, sleep=lambda _: None, json={})
    assert len(calls) == 1


def test_generate_day_stops_on_quota_error(monkeypatch):
    attempts, messages = [], []

    def fail(fmt, pillar, day, actor, entry_id=None):
        attempts.append(fmt)
        raise images.QuotaError("no image quota")

    monkeypatch.setattr(producer, "_make_and_register", fail)
    monkeypatch.setattr(producer, "load_schedule", lambda: type("S", (), {
        "plan_for": lambda self, day, offset: [(Format.REEL_BOOK_POV, Pillar.GITA),
                                               (Format.STORY_LINE, Pillar.KABIR)]})())
    from datetime import date
    assert producer.generate_day(date(2026, 10, 23), messages.append, lambda _: None) == []
    assert len(attempts) == 1
    assert "quota" in messages[0]


def test_missing_keys_stop_before_any_paid_call(monkeypatch):
    from instapost import pipeline
    from instapost.config import SetupError

    def boom(*a, **k):
        raise AssertionError("writer must not be called without keys")

    monkeypatch.setattr(pipeline.writer, "write", boom)
    with pytest.raises(SetupError, match="ANTHROPIC_API_KEY, GEMINI_API_KEY not set"):
        pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA)


def test_cli_prints_setup_error_without_traceback(capsys):
    from instapost import cli
    assert cli.main(["make", "reel", "--pillar", "gita"]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ANTHROPIC_API_KEY")
