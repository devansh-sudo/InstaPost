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
        [(400, {"error": {"message": "Invalid aspect ratio"}})], calls))
    with pytest.raises(RuntimeError, match="400: Invalid aspect ratio"):
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
    monkeypatch.setenv("IMAGE_FALLBACK", "none")
    with pytest.raises(SetupError, match="ANTHROPIC_API_KEY, GEMINI_API_KEY not set"):
        pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA)


def test_cli_prints_setup_error_without_traceback(capsys):
    from instapost import cli
    assert cli.main(["make", "reel", "--pillar", "gita"]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ANTHROPIC_API_KEY")


def test_env_file_problems_names_lines_never_values(tmp_path):
    from instapost.config import env_file_problems
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-secret\nCLAUDE_API_KEY=sk-ant-other\n"
                   "GEMINI_API_KEY=abc\nANTHROPIC_API_KEY=\n# ok\nDEMO_MODE=false\n")
    out = "\n".join(env_file_problems(env))
    assert "line 2: CLAUDE_API_KEY" in out and "did you mean ANTHROPIC_API_KEY" in out
    assert "line 4: ANTHROPIC_API_KEY also on line 1" in out and "empty" in out
    assert "secret" not in out and "other" not in out
    assert "GEMINI" not in out and "DEMO_MODE" not in out


def test_key_report_says_where_and_never_shows_the_key(tmp_path, monkeypatch):
    from instapost.config import key_report
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-secret123\nANTHROPIC_API_KEY=\n"
                   "OPENAI_API_KEY=sk-ant-wrongkey\nGEMINI_API_KEY=AQ.newformat\n")
    assert "line 2: EMPTY" in key_report("ANTHROPIC_API_KEY", env)
    assert "NOT start with 'sk-'" in key_report("OPENAI_API_KEY", env)
    assert key_report("GEMINI_API_KEY", env) == ".env line 4: 12 characters"  # any format
    assert key_report("UNSPLASH_ACCESS_KEY", env).startswith("not in")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFromShell")
    out = key_report("GEMINI_API_KEY", env)
    assert "shell environment" in out
    assert "secret" not in out and "Shell" not in out


def test_quota_error_in_cli_is_one_line_and_points_to_saved_draft(monkeypatch, capsys):
    from instapost import cli, pipeline
    from instapost.models import FactCheckVerdict

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setenv("IMAGE_FALLBACK", "none")
    from instapost.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline.writer, "write", lambda *a, **k: pipeline.load_sample(
        Format.REEL_BOOK_POV))
    real_offline = pipeline.fact_check.offline_fact_check

    def ok_fact(draft):
        f = real_offline(draft)
        return f.model_copy(update={"verdict": FactCheckVerdict.PASS, "issues": []})

    monkeypatch.setattr(pipeline.fact_check, "fact_check", ok_fact)

    def refuse(self, prompt, portrait):
        raise images.QuotaError("Gemini refused: limit: 0")

    monkeypatch.setattr(images.GeminiProvider, "generate", refuse)
    assert cli.main(["make", "reel", "--pillar", "gita"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: Gemini refused") and "--draft" in err and "draft.json" in err


def test_rejected_key_is_hopeless(monkeypatch):
    calls = []
    monkeypatch.setattr(images.httpx, "post", _fake_post(
        [(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})], calls))
    with pytest.raises(images.QuotaError, match="rejected the API key"):
        images.post_with_retry("gemini", URL, sleep=lambda _: None, json={})



def test_free_tier_refusal_is_short_remembered_and_not_ready(monkeypatch):
    from instapost.setup_status import checklist
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setenv("IMAGE_FALLBACK", "none")
    from instapost.config import get_settings
    get_settings.cache_clear()
    msg = ("You exceeded your current quota. * Quota exceeded for metric: generate_content_"
           "free_tier_requests, limit: 0, model: gemini-2.5-flash-preview-image * Quota "
           "exceeded for metric: free_tier_input_token_count, limit: 0, model: "
           "gemini-2.5-flash-preview-image")
    monkeypatch.setattr(images.httpx, "post", _fake_post([_quota_429(msg)], []))
    with pytest.raises(images.QuotaError) as e:
        images.post_with_retry("gemini", URL, sleep=lambda _: None, json={})
    assert len(str(e.value)) < 260 and "gemini-2.5-flash-preview-image)" in str(e.value)
    assert "free tier" in images.recent_refusal("gemini")
    step = next(st for st in checklist() if st.label.startswith("Image source"))
    assert not step.done and "none ready" in step.label
    # A later success clears it.
    monkeypatch.setattr(images.httpx, "post", _fake_post([(200, {"ok": 1})], []))
    images.post_with_retry("gemini", URL, sleep=lambda _: None, json={})
    assert images.recent_refusal("gemini") is None


def test_writer_rewrites_a_reel_that_is_too_long_to_read(monkeypatch):
    from instapost.brand import load_brand
    from instapost.pipeline import load_sample
    from instapost.render import video
    from instapost.studio import writer

    long = load_sample(Format.REEL_BOOK_POV).model_copy(update={"body": " ".join(["शब्द"] * 30)})
    short = load_sample(Format.REEL_BOOK_POV)
    answers, prompts = [long, short], []

    def fake(**kw):
        prompts.append(kw["content"])
        return answers.pop(0)

    monkeypatch.setattr(writer.llm, "structured", fake)
    out = writer.write(load_brand(), Format.REEL_BOOK_POV, Pillar.GITA, None, [])
    assert out is short and len(prompts) == 2 and "at most 29 words" in prompts[1]
    # The limit really fits the video: 29 words read in time within 12 s.
    read = video.read_time_seconds(" ".join(["x"] * writer.MAX_REEL_WORDS))
    assert video.reel_duration(read) >= read + 1.0


def test_placeholder_handle_is_not_printed_and_real_handle_is_not_a_watermark(monkeypatch):
    from PIL import Image

    from instapost import qa
    from instapost.brand import load_brand

    brand = load_brand()
    assert brand.model_copy(update={"handle": "@your_handle"}).printed_handle == ""
    real = brand.model_copy(update={"handle": "@jeevan.sutra"})
    assert real.printed_handle == "@jeevan.sutra"
    seen = {}

    def fake(**kw):
        seen["text"] = kw["content"][1]["text"]
        return qa.VisionVerdict(readable=True, looks_real=True, page_fits=True, problems=[])

    monkeypatch.setattr(qa.llm, "structured", fake)
    assert qa.check_vision(Image.new("RGB", (1080, 1920)), real).passed
    assert "'@jeevan.sutra'" in seen["text"] and "not problems" in seen["text"]


def test_check_reports_bad_ig_user_id_without_crashing(monkeypatch, capsys):
    from instapost import cli
    from instapost.publish import instagram

    monkeypatch.setenv("IG_USER_ID", "thou.ghtory")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "IGAAtest")
    from instapost.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(instagram.Instagram, "me", lambda self: {"user_id": "1784"})

    def boom(self):
        raise instagram.PublishError("Instagram 400: Unsupported get request")

    monkeypatch.setattr(instagram.Instagram, "publishing_limit", boom)
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "Set IG_USER_ID=1784" in out
    monkeypatch.setenv("IG_USER_ID", "1784")
    get_settings.cache_clear()
    assert cli.main(["check"]) == 0
    assert "! instagram test failed: Instagram 400" in capsys.readouterr().out


def test_youtube_channel_summary_and_no_channel(monkeypatch):
    from instapost.publish import youtube
    from instapost.publish.instagram import PublishError

    class Svc:
        def __init__(self, items):
            self.items = items

        def channels(self):
            return self

        def list(self, **kw):
            assert kw == {"part": "snippet,statistics", "mine": True}
            return self

        def execute(self):
            return {"items": self.items}

    monkeypatch.setattr(youtube, "_service", lambda *a: Svc([{
        "id": "UC1", "snippet": {"title": "Thoughtory"}, "statistics": {"subscriberCount": "3"}}]))
    assert youtube.channel() == {"id": "UC1", "title": "Thoughtory", "subscribers": "3"}
    monkeypatch.setattr(youtube, "_service", lambda *a: Svc([]))
    with pytest.raises(PublishError, match="no YouTube channel"):
        youtube.channel()


def _fake_client_secret():
    import json as _json

    from instapost.config import get_settings
    d = get_settings().secrets_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "youtube_client_secret.json").write_text(_json.dumps({"installed": {
        "client_id": "123.apps.googleusercontent.com", "client_secret": "s3cret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}))


def test_youtube_paste_login_saves_refresh_token(monkeypatch, capsys):
    from google_auth_oauthlib.flow import Flow

    from instapost.config import get_settings
    from instapost.publish import youtube
    _fake_client_secret()
    got = {}

    def fake_fetch(self, **kw):
        got.update(kw, verifier=self.code_verifier)
        self.oauth2session.token = {"access_token": "at", "refresh_token": "rt",
                                    "token_type": "Bearer", "expires_in": 3600,
                                    "expires_at": 4102444800}

    monkeypatch.setattr(Flow, "fetch_token", fake_fetch)
    youtube.login(paste=True, read=lambda _: "http://localhost:8085/?state=x&code=4/abc&scope=y")
    printed = capsys.readouterr().out
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8085%2F" in printed
    assert "prompt=consent" in printed and "code_challenge=" in printed
    assert got["authorization_response"].startswith("https://localhost:8085/?")
    assert got["verifier"]  # PKCE: the code only works with this run's secret
    token = (get_settings().secrets_dir / "youtube_token.json")
    assert '"refresh_token": "rt"' in token.read_text()
    assert oct(token.stat().st_mode & 0o777) == "0o600"


def test_youtube_paste_login_rejects_wrong_paste(monkeypatch):
    from instapost.publish import youtube
    from instapost.publish.instagram import PublishError
    _fake_client_secret()
    with pytest.raises(PublishError, match="code="):
        youtube.login(paste=True, read=lambda _: "https://accounts.google.com/o/oauth2/auth?x")
    with pytest.raises(PublishError, match="access_denied"):
        youtube.login(paste=True, read=lambda _: "http://localhost:8085/?error=access_denied&code=")


def test_key_report_same_value_from_docker_env_names_the_line(tmp_path, monkeypatch):
    from instapost.config import key_report
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=AIzaSame\n")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSame")
    assert key_report("GEMINI_API_KEY", env) == ".env line 1: 8 characters"


def test_video_hosting_step_needs_https_for_local(monkeypatch):
    from instapost.config import get_settings
    from instapost.setup_status import checklist

    def step():
        get_settings.cache_clear()
        return next(s for s in checklist() if s.label.startswith("Video hosting"))

    monkeypatch.setenv("MEDIA_HOST", "local")
    assert not step().done
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://46.224.187.54:4010")
    assert not step().done
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://thoughtory.duckdns.org")
    assert step().done


def test_logs_never_show_the_telegram_token(monkeypatch, capsys):
    import logging

    from instapost.publish import tokens

    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    root.handlers = []
    try:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "8885195839:AAG-secretSECRET_123")
        from instapost.config import get_settings
        get_settings.cache_clear()
        tokens.setup_logging()
        url = "https://api.telegram.org/bot8885195839:AAG-secretSECRET_123/getUpdates"
        logging.getLogger("httpx").info("HTTP Request: POST %s", url)  # silenced
        logging.getLogger("instapost.worker").warning("telegram failed for %s", url)
        err = capsys.readouterr().err
        assert "AAG-secret" not in err and "8885195839:" not in err
        assert "telegram failed for https://api.telegram.org/bot[redacted]/getUpdates" in err
        assert "HTTP Request" not in err
    finally:
        for h in root.handlers:
            root.removeHandler(h)
        root.handlers, root.level = old_handlers, old_level
        logging.getLogger("httpx").setLevel(logging.NOTSET)
