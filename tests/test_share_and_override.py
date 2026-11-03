import json
from pathlib import Path

import pytest

from instapost import approvals, pipeline, producer, qa
from instapost.db import Item, ItemStatus, session, utcnow
from instapost.models import Format, Pillar
from instapost.render import video
from tests.test_phase2 import _csrf, _login, web  # noqa: F401


def test_reels_are_15_to_20_seconds_with_an_end_card():
    assert video.reel_duration(2.0) == 15.0       # short text: still 15 s
    assert video.reel_duration(20.0) == 20.0      # long text: at most 20 s
    d = 16.0
    assert video.outro_alpha(0.1, d) == 0 and video.outro_alpha(d - 0.05, d) == 0  # loop seam
    assert video.outro_alpha(d - 2.5, d) == 1.0


def test_caption_asks_to_follow():
    draft = pipeline.load_sample(Format.REEL_BOOK_POV)
    assert qa.FOLLOW_LINE in qa.caption_text(draft)


def _blocked_reel(monkeypatch) -> str:
    monkeypatch.setattr(video, "encode", lambda *a, **k: Path(a[3]).write_bytes(b"mp4"))
    draft = pipeline.load_sample(Format.REEL_BOOK_POV)
    draft = draft.model_copy(update={"hook_lines": [" ".join(["शब्द"] * 20)]})  # far too long
    r = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True, draft=draft)
    assert r.status == "blocked" and not (r.folder / "reel.mp4").exists()
    with session() as s:
        return approvals.register(s, r, Format.REEL_BOOK_POV, "gita", utcnow().date()).id


def test_make_it_anyway_then_post(monkeypatch):
    from instapost.config import get_settings
    monkeypatch.setenv("DEMO_MODE", "true")  # offline remake: no paid calls
    get_settings.cache_clear()
    item_id = _blocked_reel(monkeypatch)
    with session() as s:
        with pytest.raises(approvals.QueueError):  # nothing to post yet
            approvals.approve_anyway(s, s.get(Item, item_id), "test")
    new_id = producer.make_anyway(item_id, "test")
    with session() as s:
        old, new = s.get(Item, item_id), s.get(Item, new_id)
        assert old.status == ItemStatus.REJECTED
        assert new.status == ItemStatus.NEEDS_REVIEW and "hook_length" in new.qa_summary
        assert (Path(new.folder) / "reel.mp4").exists()
        report = json.loads((Path(new.folder) / "qa.json").read_text())
        assert not report["passed"]  # the failed check stays on record


def test_post_anyway_from_the_item_page(web, monkeypatch):  # noqa: F811
    item_id = _blocked_reel(monkeypatch)
    with session() as s:  # pretend it was blocked after the video was made
        (Path(s.get(Item, item_id).folder) / "reel.mp4").write_bytes(b"mp4")
    _login(web)
    page = web.get(f"/items/{item_id}")
    assert "Post anyway" in page.text
    token = _csrf(web, f"/items/{item_id}")
    web.post(f"/items/{item_id}/approve-anyway", data={"token": token})
    with session() as s:
        assert s.get(Item, item_id).status == ItemStatus.APPROVED


def test_mantra_spellings_and_hashtags_are_fixed_for_free():
    from instapost.studio import tidy
    draft = pipeline.load_sample(Format.STORY_LINE).model_copy(update={
        "story_line": "ॐ नम: शिवाय — आज कम बोलें", "hook_lines": ["ॐ नम शिवाय"],
        "hashtags": ["#a", "#b", "#c", "#d", "#e", "#f", "#g"]})
    fixed, changes = tidy.draft(draft)
    assert fixed.story_line == "ॐ नमः शिवाय — आज कम बोलें"
    assert fixed.hook_lines == ["ॐ नमः शिवाय"] and len(fixed.hashtags) == 5 and changes
    assert tidy.text("संदर्भ: गीता") == "संदर्भ: गीता"  # real colons are left alone


def test_a_slightly_long_hook_only_warns():
    from instapost.models import FactCheckResult, FactCheckVerdict
    fact = FactCheckResult(verdict=FactCheckVerdict.PASS, issues=[])
    draft = pipeline.load_sample(Format.REEL_BOOK_POV)
    warn = draft.model_copy(update={"hook_lines": [" ".join(["शब्द"] * 14)]})
    check = next(c for c in qa.check_text(warn, fact, []) if c.name == "hook_length")
    assert check.passed and check.detail.startswith(qa.WARN)
    assert qa.warnings([check]) == ["hook_length: 14 words (aim 12)"]


def test_rejected_post_can_be_posted_anyway(monkeypatch):
    item_id = _blocked_reel(monkeypatch)
    with session() as s:
        item = s.get(Item, item_id)
        (Path(item.folder) / "reel.mp4").write_bytes(b"mp4")
        approvals.reject(s, item, "test")
        approvals.approve_anyway(s, item, "test")
        assert item.status == ItemStatus.APPROVED
