import subprocess
from urllib.parse import unquote_plus

import numpy as np
import pytest

from instapost import approvals, quick
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, session
from instapost.models import Pillar
from instapost.publish import instagram, publisher
from instapost.render import audio, music
from tests.test_phase2 import _bot, _story


def _tone(path, freq, seconds):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}", "-ac", "2", str(path)],
                   check=True)


@pytest.fixture
def library(tmp_path, monkeypatch):
    folder = tmp_path / "data" / "music"
    folder.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    _tone(folder / "krishna-bansuri-calm@2.mp3", 440, 20)
    _tone(folder / "shiv-om-chant.mp3", 220, 4)
    return folder


def test_pick_matches_pillar_and_rotates(library):
    assert music.pick(Pillar.GITA).name.startswith("krishna")
    assert music.pick(Pillar.SHIV_DHYAN).name.startswith("shiv")
    assert music.start_offset(library / "krishna-bansuri-calm@2.mp3") == 2.0
    # Kabir has no matching track: any track, least recently used first.
    assert music.pick(Pillar.KABIR) is not None


def test_ambience_only_when_no_library_or_mode_off(tmp_path, monkeypatch, library):
    monkeypatch.setenv("MUSIC_MODE", "ambience")
    get_settings.cache_clear()
    assert music.pick(Pillar.GITA) is None


def test_mix_is_full_length_loops_short_tracks_and_never_ends_silent(tmp_path, library):
    amb = tmp_path / "amb.wav"
    audio.write_wav(amb, audio.ambience(10.0))
    out = tmp_path / "mix.wav"
    music.mix(amb, library / "shiv-om-chant.mp3", 10.0, out)  # 4 s track, 10 s reel
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(out), "-ac", "1",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32)
    assert abs(a.size / 44100 - 10.0) < 0.05
    seg = a[44100 * 7:44100 * 8]  # after the track's own length: it must have looped
    spec, f = np.abs(np.fft.rfft(seg)), np.fft.rfftfreq(seg.size, 1 / 44100)
    assert spec[(f > 215) & (f < 225)].mean() > 10 * spec[(f > 150) & (f < 200)].mean()
    assert a[-4410:].std() > 50  # the ambience is still there at the very end


def test_manual_platforms_are_skipped_by_the_publisher(monkeypatch):
    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram"})
    item_id = _story()
    with session() as s:
        item = s.get(Item, item_id)
        assert publisher.targets_for(item) == ["instagram"]
        item.manual_platforms = ["instagram"]
        assert publisher.targets_for(item) == []


def test_shortcode():
    assert instagram.shortcode("https://www.instagram.com/reel/DAbc_12-x/?igsh=1") == "DAbc_12-x"
    assert instagram.shortcode("https://instagram.com/thou.ghtory/p/C0de/") == "C0de"
    assert instagram.shortcode("https://example.com/reel/x") is None


def _reel_item(s):
    item = s.get(Item, _story())
    item.format = "reel_book_pov"
    return item


def test_trending_flow_posts_other_platforms_now(monkeypatch):
    monkeypatch.setattr(publisher, "configured_platforms",
                        lambda: {"instagram", "facebook", "youtube"})
    with session() as s:
        item = _reel_item(s)
        others = quick.trending(s, item, "telegram")
        assert others == ["facebook", "youtube"]
        assert item.status == ItemStatus.APPROVED and item.scheduled_at is not None
        assert item.manual_platforms == ["instagram"]


def test_trending_with_instagram_only_waits_for_posted(monkeypatch):
    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram"})
    with session() as s:
        item = _reel_item(s)
        assert quick.trending(s, item, "telegram") == []
        assert item.scheduled_at is None  # nothing automatic: waits for /posted
        assert item.id not in [i.id for i in approvals.due(s)]

        monkeypatch.setenv("IG_USER_ID", "1784")
        monkeypatch.setenv("IG_ACCESS_TOKEN", "IGAAtest")
        get_settings.cache_clear()
        monkeypatch.setattr(instagram.Instagram, "find_media", lambda self, url: {"id": "999"})
        msg = quick.record_manual_post(s, item, "https://www.instagram.com/reel/Abc1/", "tg")
        assert "Linked" in msg and item.status == ItemStatus.PUBLISHED
        pub = next(p for p in item.publications if p.platform == "instagram")
        assert pub.remote_id == "999" and pub.status == "published"
        with pytest.raises(approvals.QueueError):
            quick.record_manual_post(s, item, "not a link", "tg")


def test_trending_button_only_on_reels(monkeypatch):
    sent: list = []
    bot = _bot(monkeypatch, sent)
    with session() as s:
        story = s.get(Item, _story())
        assert "ts:" not in str(bot.keyboard(story))
        story.format = "reel_book_pov"
        assert f"ts:{story.id}" in str(bot.keyboard(story))
    assert all("Trending" not in unquote_plus(b) for _, b in sent)
