import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from instapost import approvals, scene_maker
from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, Job, session
from instapost.publish import publisher
from instapost.render import scene_reel
from instapost.render.scene import FRAME_H, FRAME_W
from instapost.studio import images, scenes, voice
from tests.test_phase2 import _csrf, _login, web  # noqa: F401


def _motion(with_voice: bool = False):
    s = scenes.SAMPLE
    pics = [Image.new("RGB", (900, 1600), c) for c in ((120, 60, 40), (40, 60, 120),
                                                        (60, 110, 60))]
    lines = [scene_reel.Line(scenes.CHARACTERS[ln.speaker].name, ln.text, ln.picture,
                             np.ones(24000, np.float32) * 0.2 if with_voice else None)
             for ln in s.lines]
    return scene_reel.SceneMotion(load_brand(), pics, lines, s.title, scene_maker.KICKER,
                                  "ॐ नमः शिवाय", "पंचाक्षर मंत्र", s.question)


def test_sample_script_is_valid_and_problems_are_caught():
    assert scenes.problems(scenes.SAMPLE) == []
    bad = scenes.SAMPLE.model_copy(update={"lines": [
        scenes.SceneLine(speaker="ganesh", text="एक " * 50, picture=7)]})
    found = " ".join(scenes.problems(bad))
    assert "unknown speaker" in found and "picture 8" in found and "spoken words" in found
    assert "1 lines" in found


def test_timeline_follows_the_voices_and_stays_short():
    m = _motion(with_voice=True)
    # 4 lines of 1 s voice each, plus title, pauses and end card.
    assert abs(m.duration - (scene_reel.TITLE_S + 4 * (1 + scene_reel.LEAD_S
                                                       + scene_reel.GAP_S)
                             + scene_reel.END_S)) < 0.01
    silent = _motion()
    assert silent.duration <= scenes.MAX_SECONDS
    from instapost import qa
    assert not qa.unsafe_boxes(scene_reel.text_boxes(silent))
    assert m.frame_at(0.5).shape == (FRAME_H, FRAME_W, 3)
    # The end card is shown at the end; the subtitles differ from the bare picture.
    assert np.array_equal(m.frame_at(m.duration - 0.05), m.end_arr.astype(np.uint8))
    tl = m.timed[1]
    assert np.abs(m.frame_at(tl.start + 0.5).astype(int)
                  - m.frame_at(tl.end + 0.3).astype(int)).mean() > 1
    track = scene_reel.voice_track(m.timed, m.duration)
    assert track.shape[1] == 2 and np.abs(track).max() > 0.5


def test_voice_line_from_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    get_settings.cache_clear()
    pcm = np.concatenate([np.zeros(6000), np.sin(np.arange(24000) / 5) * 0.5,
                          np.zeros(6000)])
    seen = {}

    class R:
        def json(self):
            data = base64.b64encode((pcm * 32767).astype("<i2").tobytes()).decode()
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": data}}]}}]}

    def fake_post(provider, url, **kw):
        seen.update(url=url, body=kw["json"])
        return R()

    monkeypatch.setattr(voice, "post_with_retry", fake_post)
    out = voice.speak("नहीं उमा, सच्ची निष्ठा कभी व्यर्थ नहीं जाती।", "Charon", "calmly")
    assert "tts" in seen["url"] and seen["body"]["generationConfig"]["speechConfig"][
        "voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Charon"
    assert 1.0 < len(out) / voice.SR < 1.2  # silence trimmed


def test_pictures_after_the_first_carry_the_first_as_reference(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    get_settings.cache_clear()
    bodies = []

    class R:
        def json(self):
            buf = io.BytesIO()
            Image.new("RGB", (90, 160), (200, 100, 50)).save(buf, "PNG")
            data = base64.b64encode(buf.getvalue()).decode()
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": data}}]}}]}

    monkeypatch.setattr(images, "post_with_retry",
                        lambda provider, url, **kw: bodies.append(kw["json"]) or R())
    folder = get_settings().output_dir / "t"
    folder.mkdir(parents=True)
    pics = scene_maker._pictures(scenes.SAMPLE, folder, offline=False, step=lambda _: None)
    assert len(pics) == 3 and (folder / "picture_3.jpg").exists()
    first, second = bodies[0]["contents"][0]["parts"], bodies[1]["contents"][0]["parts"]
    assert len(first) == 1 and "inlineData" in second[0]
    text = second[-1]["text"]
    assert "Lord Shiva" in text and "Goddess Parvati" in text and "reference" in text
    assert "No text" in text


def test_approved_scene_becomes_a_reel_in_the_inbox(monkeypatch):
    monkeypatch.setattr(scene_reel, "encode", lambda m, out, **k: out.write_bytes(b"mp4"))
    entry = scenes.new(scenes.SAMPLE)
    scenes.set_status(entry["id"], "approved")
    with session() as s:
        item = scene_maker.make(s, entry["id"], offline=True)
        assert item.format == scenes.SCENE_REEL == approvals.SCENE_REEL
        assert item.status == ItemStatus.NEEDS_REVIEW
        assert "AI की मदद" in item.caption and "शिव पुराण" in item.caption
        assert "आवाज़ें" not in item.caption  # offline: no voices, so only the pictures
        item_id = item.id
    assert scenes.get(entry["id"])["status"] == "made"
    with session() as s:
        item = s.get(Item, item_id)
        draft = json.loads((Path(item.folder) / "draft.json").read_text())
        assert draft["hook_lines"] == [scenes.SAMPLE.title]
        assert publisher.planned_platforms(item.format) == publisher.planned_platforms(
            "reel_book_pov")
        approvals.approve(s, item, "test")
        assert item.scheduled_at is not None  # a reel slot


def test_scene_must_be_approved_first():
    entry = scenes.new(scenes.SAMPLE)
    with session() as s:
        try:
            scene_maker.make(s, entry["id"], offline=True)
        except ValueError as e:
            assert "not approved" in str(e)
        else:
            raise AssertionError("made a reel from a draft")


def test_scenes_pages_edit_and_approve(web):  # noqa: F811
    _login(web)
    assert web.get("/scenes").status_code == 200
    token = _csrf(web, "/scenes")
    r = web.post("/scenes/sample", data={"token": token}, follow_redirects=False)
    assert r.status_code == 303
    scene_id = r.headers["location"].rsplit("/", 1)[1]
    page = web.get(f"/scenes/{scene_id}")
    assert page.status_code == 200 and "पार्वती" in page.text and "शिव पुराण" in page.text
    form = {"token": token, "title": "नया शीर्षक", "source": "शिव पुराण",
            "question": "आप क्या सोचते हैं?", "caption_hi": "कैप्शन", "hashtags": "#शिव #धैर्य",
            "reference_id": "shiv-panchakshari",
            "picture_0": "Parvati in tapasya", "picture_1": "Shiva appears",
            "speaker_0": "parvati", "pic_0": "1", "text_0": "प्रभु, क्या प्रतीक्षा व्यर्थ थी?",
            "speaker_1": "shiv", "pic_1": "2", "text_1": "नहीं उमा, निष्ठा व्यर्थ नहीं जाती।",
            "speaker_2": "shiv", "pic_2": "2", "text_2": "धैर्य राह दिखाता है।"}
    r = web.post(f"/scenes/{scene_id}", data=form, follow_redirects=False)
    assert r.status_code == 303
    saved = scenes.script_of(scenes.get(scene_id))
    assert saved.title == "नया शीर्षक" and len(saved.lines) == 3 and len(saved.pictures) == 2
    r = web.post(f"/scenes/{scene_id}", data={**form, "approve": "1"}, follow_redirects=False)
    assert scenes.get(scene_id)["status"] == "approved"
    with session() as s:
        job = s.query(Job).filter(Job.kind == "scene_make").one()
        assert job.payload["scene_id"] == scene_id
    assert web.get("/scenes/nope-x").status_code == 404
