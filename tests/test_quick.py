from urllib.parse import unquote_plus

from instapost import approvals, cli, jobs, quick
from instapost.db import Item, ItemStatus, Job, session
from instapost.models import Format, Pillar
from instapost.publish import publisher
from tests.test_phase2 import _bot, _story


def test_parse_words():
    assert quick.parse(["reel", "gita", "गुस्सा", "पर", "काबू"]) == (
        Format.REEL_BOOK_POV, Pillar.GITA, "गुस्सा पर काबू")
    assert quick.parse(["story", "shiv"])[:2] == (Format.STORY_LINE, Pillar.SHIV_DHYAN)
    fmt, pillar, topic = quick.parse([])
    assert fmt == Format.REEL_BOOK_POV and isinstance(pillar, Pillar) and topic is None
    assert quick.parse(["कबीर", "दोहा"])[2] == "कबीर दोहा"  # unknown words are the topic


def test_post_now_from_waiting():
    a = _story()
    with session() as s:
        quick.post_now(s, s.get(Item, a), "telegram")
        assert s.get(Item, a).status == ItemStatus.APPROVED
        assert a in [i.id for i in approvals.due(s)]


def test_post_now_from_approved_moves_it_to_now():
    b = _story()
    with session() as s:
        approvals.approve(s, s.get(Item, b), "web")
        assert b not in [i.id for i in approvals.due(s)]  # its slot is later
        quick.post_now(s, s.get(Item, b), "telegram")
        assert b in [i.id for i in approvals.due(s)]


def test_post_now_refuses_rejected():
    c = _story()
    with session() as s:
        approvals.reject(s, s.get(Item, c), "web")
        try:
            quick.post_now(s, s.get(Item, c), "telegram")
        except approvals.QueueError as e:
            assert "rejected" in str(e)
        else:
            raise AssertionError("rejected item must not be posted")


def test_telegram_post_now_button_and_new_command(monkeypatch):
    sent: list = []
    bot = _bot(monkeypatch, sent)
    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram"})
    item_id = _story()
    bot.send_item(item_id)
    assert f"pn:{item_id}" in sent[0][1]
    bot.handle_update({"update_id": 1, "callback_query": {
        "id": "q", "from": {"id": 42}, "data": f"pn:{item_id}",
        "message": {"chat": {"id": 42}, "message_id": 7}}})
    with session() as s:
        assert item_id in [i.id for i in approvals.due(s)]
    texts = [unquote_plus(body) for _, body in sent]
    assert any("Posting now to instagram" in t for t in texts), texts

    bot.handle_update({"update_id": 2, "message": {"from": {"id": 42}, "chat": {"id": 42},
                                                   "text": "/new reel kabir धीरज"}})
    job = jobs.take_queued()
    assert job.kind == "make" and job.payload["pillar"] == "kabir"
    assert job.payload["topic"] == "धीरज" and job.payload["actor"] == "telegram"


def test_cli_new_queues_a_job(capsys):
    assert cli.main(["new", "carousel", "gita"]) == 0
    with session() as s:
        job = s.query(Job).one()
        assert job.payload["format"] == "carousel_shlok" and job.status == "queued"
    assert "Post now" in capsys.readouterr().out


def test_post_now_button_without_platforms_does_nothing(monkeypatch):
    sent: list = []
    bot = _bot(monkeypatch, sent)
    item_id = _story()
    bot.handle_update({"update_id": 1, "callback_query": {
        "id": "q", "from": {"id": 42}, "data": f"pn:{item_id}", "message": {}}})
    with session() as s:
        assert s.get(Item, item_id).status == ItemStatus.NEEDS_REVIEW
