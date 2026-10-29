"""Phase 2: approvals, scheduling, publishing (mocked platforms), Telegram, dashboard."""

import json
from datetime import date, timedelta

import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient

from instapost import approvals, pipeline
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, Publication, session, utcnow
from instapost.models import Format, Pillar
from instapost.publish import instagram, publisher, tokens
from instapost.publish.instagram import Published, PublishError
from instapost.schedule import load_schedule, to_local


def _story(for_date: date | None = None) -> str:
    """Create one real (offline) story item and register it; returns its id."""
    result = pipeline.make(Format.STORY_LINE, Pillar.LIFE_LESSON, offline=True)
    for_date = for_date or load_schedule().local_now().date() + timedelta(days=1)
    with session() as s:
        return approvals.register(s, result, Format.STORY_LINE, "life_lesson", for_date).id


# --- approvals & scheduling ---------------------------------------------------------------

def test_approve_schedules_into_free_slots_without_collisions():
    a, b = _story(), _story()
    with session() as s:
        ia, ib = s.get(Item, a), s.get(Item, b)
        # Make the second one look new so the duplicate check does not matter here.
        ib.status = ItemStatus.NEEDS_REVIEW
        approvals.approve(s, ia, "test")
        approvals.approve(s, ib, "test")
        assert ia.status == ItemStatus.APPROVED and ia.scheduled_at != ib.scheduled_at
        slots = {t for t in load_schedule().slots[Format.STORY_LINE]}
        for it in (ia, ib):
            assert to_local(it.scheduled_at, load_schedule()).strftime("%H:%M") in slots
            assert it.scheduled_at > utcnow()


def test_batch_approval_and_autonomy():
    ids = [_story() for _ in range(2)]
    with session() as s:
        s.get(Item, ids[1]).status = ItemStatus.NEEDS_REVIEW
        approved = approvals.approve_next(s, 1, "test")
        assert [i.id for i in approved] == [ids[0]]
        tomorrow = load_schedule().local_now().date() + timedelta(days=1)
        assert [i.id for i in approvals.approve_day(s, tomorrow, "test")] == [ids[1]]
        with pytest.raises(approvals.QueueError):
            approvals.set_autonomy(s, "L9", "test")


def test_autonomy_never_auto_approves_unverified_facts():
    with session() as s:
        approvals.set_autonomy(s, "L2", "test")
    item_id = _story()  # offline fact check = needs_human_review
    with session() as s:
        assert s.get(Item, item_id).status == ItemStatus.NEEDS_REVIEW


def test_kill_switch_and_due():
    item_id = _story()
    with session() as s:
        item = s.get(Item, item_id)
        approvals.approve(s, item, "test", when=utcnow() - timedelta(minutes=1))
        assert [i.id for i in approvals.due(s)] == [item_id]
        approvals.set_kill_switch(s, True, "test")
        assert approvals.kill_switch_on(s)


# --- Instagram client (mocked HTTP) -------------------------------------------------------

def _ig_transport(log: list, fail_publish: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append((request.method, request.url.path, dict(request.headers),
                    request.content.decode()))
        path = request.url.path
        if path.endswith("/media") and request.method == "POST":
            return httpx.Response(200, json={"id": "c1"})
        if path.endswith("/c1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if path.endswith("/media_publish"):
            if fail_publish:
                return httpx.Response(400, json={"error": {"message": "bad", "code": 9}})
            return httpx.Response(200, json={"id": "m1"})
        if path.endswith("/m1"):
            return httpx.Response(200, json={"permalink": "https://instagram.com/p/x"})
        return httpx.Response(404, json={"error": {"message": "nope"}})
    return httpx.MockTransport(handler)


def test_instagram_reel_flow_keeps_token_out_of_urls(monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "17841")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "SECRET-TOKEN-123")
    get_settings.cache_clear()
    log: list = []
    ig = instagram.Instagram(client=httpx.Client(transport=_ig_transport(log)), poll_seconds=0)
    result = ig.reel("https://cdn/x.mp4", "caption", "https://cdn/c.jpg")
    assert result == Published("m1", "https://instagram.com/p/x", "c1")
    for _method, path, headers, body in log:
        assert "SECRET-TOKEN-123" not in path and "SECRET-TOKEN-123" not in body
        assert headers["authorization"] == "Bearer SECRET-TOKEN-123"
    assert "media_type=REELS" in log[0][3]


def test_instagram_errors_are_redacted(monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "17841")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "SECRET-TOKEN-123")
    get_settings.cache_clear()
    ig = instagram.Instagram(client=httpx.Client(transport=_ig_transport([], True)),
                             poll_seconds=0)
    with pytest.raises(PublishError) as e:
        ig.story("https://cdn/s.jpg")
    assert "SECRET-TOKEN-123" not in str(e.value)
    assert tokens.redact("x?access_token=SECRET-TOKEN-123&y=1") == "x?access_token=[redacted]&y=1"


# --- publisher orchestration --------------------------------------------------------------

class FakeHost:
    def url_for(self, path):
        return f"https://cdn.example/{path.name}"


def test_publisher_is_idempotent_and_retries(monkeypatch):
    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram"})
    calls = []

    def fake_publish(s, item, pub, files, host):
        calls.append(pub.platform)
        if len(calls) == 1:
            raise PublishError("temporary")
        return Published("m1", "https://instagram.com/p/x")

    monkeypatch.setattr(publisher, "_publish_one", fake_publish)
    item_id = _story()
    with session() as s:
        item = s.get(Item, item_id)
        approvals.approve(s, item, "test", when=utcnow())
        publisher.publish_item(s, item, host=FakeHost())
        assert item.status == ItemStatus.APPROVED  # first failure -> retry later
        assert item.scheduled_at > utcnow()
        publisher.publish_item(s, item, host=FakeHost())
        assert item.status == ItemStatus.PUBLISHED
        item.status = ItemStatus.APPROVED  # even if asked again, it must not post twice
        publisher.publish_item(s, item, host=FakeHost())
    assert calls == ["instagram", "instagram"]
    with session() as s:
        pubs = s.query(Publication).all()
        assert len(pubs) == 1 and pubs[0].status == "published" and pubs[0].attempts == 2


def test_publisher_respects_kill_switch(monkeypatch):
    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram"})
    monkeypatch.setattr(publisher, "_publish_one",
                        lambda *a: pytest.fail("must not publish while kill switch is on"))
    item_id = _story()
    with session() as s:
        item = s.get(Item, item_id)
        approvals.approve(s, item, "test", when=utcnow())
        approvals.set_kill_switch(s, True, "test")
        publisher.publish_item(s, item, host=FakeHost())
        assert item.status == ItemStatus.APPROVED


# --- Telegram -----------------------------------------------------------------------------

def _bot(monkeypatch, sent: list):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "42")
    get_settings.cache_clear()
    from instapost.telegram_bot import Bot

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path.rsplit("/", 1)[-1], request.content.decode(errors="ignore")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    return Bot(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_telegram_ignores_strangers_and_approves_for_admin(monkeypatch):
    sent: list = []
    bot = _bot(monkeypatch, sent)
    item_id = _story()
    bot.handle_update({"update_id": 1, "callback_query": {
        "id": "q", "from": {"id": 999}, "data": f"ap:{item_id}", "message": {}}})
    bot.handle_update({"update_id": 2, "message": {"from": {"id": 999},
                                                   "chat": {"id": 999}, "text": "/kill"}})
    with session() as s:
        assert s.get(Item, item_id).status == ItemStatus.NEEDS_REVIEW
        assert not approvals.kill_switch_on(s)
    assert sent == []

    bot.handle_update({"update_id": 3, "callback_query": {
        "id": "q", "from": {"id": 42}, "data": f"ap:{item_id}",
        "message": {"chat": {"id": 42}, "message_id": 7}}})
    with session() as s:
        assert s.get(Item, item_id).status == ItemStatus.APPROVED
    bot.handle_update({"update_id": 4, "message": {"from": {"id": 42}, "chat": {"id": 42},
                                                   "text": "/kill"}})
    with session() as s:
        assert approvals.kill_switch_on(s)


def test_telegram_sends_item_with_buttons(monkeypatch):
    sent: list = []
    bot = _bot(monkeypatch, sent)
    item_id = _story()
    bot.send_item(item_id)
    method, body = sent[0]
    assert method == "sendPhoto"
    assert f"ap:{item_id}" in body


# --- dashboard ----------------------------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from instapost.web import auth
    monkeypatch.setenv("SESSION_SECRET", "x" * 40)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", auth.hash_password("correct horse battery"))
    monkeypatch.setenv("ADMIN_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    get_settings.cache_clear()
    auth._failures.clear()
    from instapost.web.app import create_app
    return TestClient(create_app())


def _csrf(client, path="/login") -> str:
    html = client.get(path).text
    return html.split('name="token" value="')[1].split('"')[0]


def _login(client, code=None):
    return client.post("/login", data={
        "token": _csrf(client), "username": "admin", "password": "correct horse battery",
        "code": code or pyotp.TOTP("JBSWY3DPEHPK3PXP").now()}, follow_redirects=False)


def test_dashboard_requires_login_and_totp(web):
    assert web.get("/", follow_redirects=False).headers["location"] == "/login"
    assert _login(web, code="000000").headers["location"] == "/login"
    assert web.get("/", follow_redirects=False).status_code == 303
    assert _login(web).headers["location"] == "/"
    page = web.get("/")
    assert page.status_code == 200 and "Needs your approval" in page.text
    assert "Stop everything" in web.get("/settings").text  # the kill switch
    assert "default-src 'self'" in page.headers["content-security-policy"]


def test_dashboard_lockout_after_failures(web):
    for _ in range(5):
        _login(web, code="000000")
    assert _login(web).headers["location"] == "/login"  # correct code, but locked out


def test_dashboard_approve_needs_csrf(web):
    item_id = _story()
    _login(web)
    r = web.post(f"/items/{item_id}/approve", data={"token": "wrong"}, follow_redirects=False)
    assert r.status_code == 403
    r = web.post(f"/items/{item_id}/approve", data={"token": _csrf(web, "/queue")},
                 follow_redirects=False)
    assert r.status_code == 303
    with session() as s:
        assert s.get(Item, item_id).status == ItemStatus.APPROVED
    assert web.get(f"/items/{item_id}").status_code == 200


def test_dashboard_file_access_is_restricted(web):
    item_id = _story()
    _login(web)
    assert web.get(f"/files/{item_id}/story.jpg").status_code == 200
    assert web.get(f"/files/{item_id}/draft.json").status_code == 404
    assert web.get(f"/files/{item_id}/..%2F..%2Fsecrets").status_code == 404
    # Signed public media links reject bad signatures.
    assert web.get("/media/x/story.jpg?e=9999999999&s=bad").status_code == 403


def test_make_request_is_queued_for_worker(web):
    _login(web)
    r = web.post("/make", data={"token": _csrf(web, "/"), "format": "story_line",
                                "pillar": "gita", "for_date": "2026-12-01", "topic": ""},
                 follow_redirects=False)
    assert r.status_code == 303
    from instapost.db import Job
    with session() as s:
        job = s.query(Job).one()
        assert job.status == "queued" and job.payload["format"] == "story_line"
        assert json.dumps(job.payload)


def test_every_dashboard_page_renders(web):
    item_id = _story()
    _login(web)
    for path in ("/", "/?days=30", "/inbox", "/calendar", "/create", "/library",
                 "/library?status=needs_review&format=story_line", "/analytics",
                 "/analytics?platform=instagram&days=7&sort=score", "/learning", "/reports",
                 "/plan", "/activity", "/notifications", "/settings", f"/items/{item_id}",
                 "/music", "/music?pillar=gita", "/costs", "/costs?days=7",
                 "/calendar?days=30", "/calendar?days=7&start=2026-01-05", "/calendar?days=9",
                 "/library?page=3",
                 "/partials/running", f"/thumb/{item_id}"):
        r = web.get(path)
        assert r.status_code == 200, path
    assert web.get("/queue", follow_redirects=False).headers["location"] == "/inbox"
    assert web.get("/stats", follow_redirects=False).headers["location"] == "/analytics"
    # Notifications: the waiting post shows up, and "mark all read" works.
    page = web.get("/notifications").text
    assert "waiting for your approval" in page
    r = web.post("/notifications/read", data={"token": _csrf(web, "/notifications")},
                 follow_redirects=False)
    assert r.status_code == 303
