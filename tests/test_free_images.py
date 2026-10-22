import io
import shutil

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw

from instapost.config import ROOT, get_settings
from instapost.studio import free_images, images
from instapost.studio.free_images import LibraryProvider, keywords, pencil_sketch, subject_of


def _photo(color=(150, 90, 40), size=(600, 900)) -> Image.Image:
    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    d.ellipse([150, 200, 450, 600], fill=(240, 200, 120))
    d.rectangle([250, 600, 350, 850], fill=(60, 40, 30))
    return img


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "data"
    shutil.copytree(ROOT / "data", root, ignore=shutil.ignore_patterns("images"))
    (root / "images" / "scenes").mkdir(parents=True)
    (root / "images" / "subjects").mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(root))
    get_settings.cache_clear()
    for name in ("diya-temple-evening", "mountain-river-sunrise"):
        _photo().save(root / "images" / "scenes" / f"{name}.jpg")
    for name in ("shiva-statue-meditating", "krishna-flute"):
        _photo((90, 120, 160)).save(root / "images" / "subjects" / f"{name}.jpg")
    return root / "images"


def test_subject_and_keywords_from_our_prompts():
    p = images.scene_prompt("diya lamps on a temple step at dusk")
    assert subject_of(p) == "diya lamps on a temple step at dusk"
    q = images.illustration_prompt("Lord Shiv meditating on Kailash", "fine pencil sketch")
    assert subject_of(q) == "Lord Shiv meditating on Kailash"
    assert keywords("Mahadev with lamps near Ganga") == ["shiva", "diya", "river"]


def test_library_matches_names_and_rotates(library):
    lib = LibraryProvider()
    prompt = images.illustration_prompt("Lord Shiv meditating", "pencil")
    first = lib.generate(prompt, portrait=False)
    assert first.size[0] > 0
    usage = free_images._load_usage()
    assert any("shiva-statue" in k for k in usage)
    # Krishna wins on words even though Shiva... and photos become sketches (mostly white).
    k = lib.generate(images.illustration_prompt("Krishna playing the flute", "pencil"), False)
    assert np.asarray(k.convert("L")).mean() > 170
    # Scenes: both match ("river", "temple"), so they take turns instead of repeating.
    s1 = images.scene_prompt("river temple at dusk")
    lib.generate(s1, True)
    lib.generate(s1, True)
    used = [k for k in free_images._load_usage() if "scenes" in k]
    assert len(used) == 2
    # A post that matches nothing still gets a picture.
    assert lib.generate(images.scene_prompt("ocean waves"), True).size[0] > 0


def test_library_empty_is_reported(tmp_path, monkeypatch):
    lib = LibraryProvider(tmp_path / "none")
    with pytest.raises(free_images.ImageUnavailable, match="no pictures"):
        lib.generate(images.scene_prompt("x"), True)


def test_pencil_sketch_keeps_size_and_is_light():
    out = pencil_sketch(_photo())
    assert out.mode == "RGB" and max(out.size) <= 1400
    arr = np.asarray(out.convert("L"))
    assert arr.mean() > 170 and arr.min() < 120  # white paper with some dark strokes


def test_fallback_uses_library_when_gemini_has_no_quota(library, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setenv("IMAGE_FALLBACK", "unsplash,library")
    get_settings.cache_clear()
    images._skip_until.clear()
    calls = []

    def refuse(self, prompt, portrait):
        calls.append(1)
        raise images.QuotaError("limit: 0")

    monkeypatch.setattr(images.GeminiProvider, "generate", refuse)
    provider = images.get_provider()
    assert [p.name for p in provider.providers] == ["gemini", "unsplash", "library"]
    provider.generate(images.scene_prompt("diya temple"), True)
    provider.generate(images.scene_prompt("diya temple"), True)
    assert provider.used == ["library", "library"]
    assert len(calls) == 1  # gemini is skipped for an hour after a quota refusal
    images._skip_until.clear()


def test_all_sources_failing_is_one_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IMAGE_PROVIDER", "unsplash")
    monkeypatch.setenv("IMAGE_FALLBACK", "library")
    get_settings.cache_clear()
    with pytest.raises(images.QuotaError) as e:
        images.get_provider().generate(images.scene_prompt("diya"), True)
    assert "UNSPLASH_ACCESS_KEY" in str(e.value) and "no pictures" in str(e.value)


def test_placeholder_never_falls_back(monkeypatch):
    assert images.chain_names("placeholder") == ["placeholder"]


def test_unsplash_search_download_credit_and_no_repeats(monkeypatch):
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key")
    get_settings.cache_clear()
    buf = io.BytesIO()
    _photo().save(buf, "JPEG")
    seen = []

    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=False):
        seen.append((url, params, headers))
        req = httpx.Request("GET", url)
        if url.endswith("/search/photos"):
            results = [{"id": pid, "urls": {"raw": f"https://images.example/{pid}"},
                        "links": {"download_location": f"https://api.unsplash.com/dl/{pid}",
                                  "html": f"https://unsplash.com/photos/{pid}"},
                        "user": {"name": "Asha"}} for pid in ("p1", "p2")]
            return httpx.Response(200, json={"results": results}, request=req)
        if "/dl/" in url:
            return httpx.Response(200, json={}, request=req)
        return httpx.Response(200, content=buf.getvalue(), request=req)

    monkeypatch.setattr(free_images.httpx, "get", fake_get)
    u = free_images.UnsplashProvider()
    a = u.generate(images.scene_prompt("diya lamps temple dusk"), True)
    b = u.generate(images.scene_prompt("diya lamps temple dusk"), True)
    assert a.info["credit"].startswith("Photo by Asha on Unsplash: https://unsplash.com/photos/p1")
    assert "p2" in b.info["credit"]  # p1 is not used twice
    search = [s for s in seen if s[0].endswith("/search/photos")][0]
    assert search[1]["query"] == "diya temple dusk" and search[1]["orientation"] == "portrait"
    assert search[2]["Authorization"] == "Client-ID test-key"
    assert any("/dl/p1" in s[0] for s in seen)  # download reported, as Unsplash asks


def test_carousel_made_from_library_with_no_image_keys(library, monkeypatch):
    from instapost import pipeline
    from instapost.models import FactCheckVerdict, Format, Pillar

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("IMAGE_FALLBACK", "unsplash,library")
    get_settings.cache_clear()
    images._skip_until.clear()
    monkeypatch.setattr(pipeline.writer, "write",
                        lambda *a, **k: pipeline.load_sample(Format.CAROUSEL_SHLOK))
    off = pipeline.fact_check.offline_fact_check
    monkeypatch.setattr(pipeline.fact_check, "fact_check", lambda d: off(d).model_copy(
        update={"verdict": FactCheckVerdict.PASS, "issues": []}))
    result = pipeline.make(Format.CAROUSEL_SHLOK, Pillar.GITA)
    assert list(result.folder.glob("slide_*.jpg"))
    images._skip_until.clear()
