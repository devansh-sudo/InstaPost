import httpx

from instapost import approvals
from instapost.db import Publication, session, utcnow
from instapost.models import Draft, FactCheckResult, FactCheckVerdict, HookStyle
from instapost.publish import facebook, publisher
from instapost.publish.instagram import Published, PublishError
from instapost.qa import check_text
from tests.test_music import _reel_item
from tests.test_phase2 import FakeHost


def test_expired_instagram_container_is_made_again(monkeypatch):
    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram"})
    made = []

    class FakeIG:
        def resume(self, cid):
            raise PublishError("Instagram 400: The requested resource does not exist "
                               "(code 24, subcode 2207006)")

        def reel(self, video, caption, cover, on_container=None):
            on_container("new-container")
            made.append(caption)
            return Published("m1", "https://instagram.com/p/x", "new-container")

    monkeypatch.setattr(publisher, "Instagram", FakeIG)
    with session() as s:
        item = _reel_item(s)
        approvals.approve(s, item, "test", when=utcnow())
        item.publications.append(Publication(platform="instagram", status="pending",
                                             attempts=1, container_id="old-expired"))
        s.flush()
        publisher.publish_item(s, item, host=FakeHost())
        pub = item.publications[0]
        assert pub.status == "published" and pub.container_id == "new-container"
    assert len(made) == 1


def test_other_instagram_errors_still_fail_the_attempt(monkeypatch):
    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram"})

    class FakeIG:
        def resume(self, cid):
            raise PublishError("Instagram 500: try again later")

    monkeypatch.setattr(publisher, "Instagram", FakeIG)
    with session() as s:
        item = _reel_item(s)
        approvals.approve(s, item, "test", when=utcnow())
        item.publications.append(Publication(platform="instagram", status="pending",
                                             attempts=1, container_id="c1"))
        s.flush()
        publisher.publish_item(s, item, host=FakeHost())
        assert item.publications[0].container_id == "c1"  # kept for the next try


def test_facebook_reel_is_not_uploaded_twice_when_its_status_is_hidden(monkeypatch):
    monkeypatch.setattr(facebook.tokens, "get", lambda name: "page-token")
    monkeypatch.setenv("FB_PAGE_ID", "123")
    from instapost.config import get_settings
    get_settings.cache_clear()
    reads = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "upload_phase=start" in request.content.decode():
            return httpx.Response(200, json={"video_id": "v1", "upload_url": "https://up/v1"})
        if request.method == "POST":
            return httpx.Response(200, json={"success": True})
        reads.append(1)
        if len(reads) < 3:  # hidden while processing
            return httpx.Response(400, json={"error": {"message": "Unsupported get request. "
                                                       "Object with ID 'v1' does not exist"}})
        return httpx.Response(200, json={"status": {"video_status": "ready"},
                                         "permalink_url": "/reel/v1"})

    fb = facebook.Facebook(client=httpx.Client(transport=httpx.MockTransport(handler)),
                           poll_seconds=0)
    out = fb.reel("https://cdn.example/reel.mp4", "caption")
    assert out.remote_id == "v1" and out.permalink == "https://www.facebook.com/reel/v1"
    assert len(reads) == 3


def _draft(words: int, story: bool) -> Draft:
    line = " ".join(["शब्द"] * words)
    return Draft(topic="t", hook_lines=[line], hook_style=HookStyle.STATEMENT,
                 alternative_hooks=[], body="", reference_id=None, caption_hi="c",
                 caption_hinglish="c", cta="c", hashtags=["#a", "#b", "#c"], alt_text="a",
                 illustration_prompt="", scene_prompt="", slides=[],
                 story_line=line if story else None, poll_question=None, poll_options=[])


def test_story_lines_may_be_longer_than_reel_hooks():
    fact = FactCheckResult(verdict=FactCheckVerdict.PASS, issues=[])

    def length_ok(draft):
        return next(c for c in check_text(draft, fact, []) if c.name == "hook_length").passed

    assert length_ok(_draft(19, story=True))
    # A few words over only warns (the writing is paid for); far over still blocks.
    assert length_ok(_draft(23, story=True)) and not length_ok(_draft(26, story=True))
    assert length_ok(_draft(15, story=False)) and not length_ok(_draft(18, story=False))
