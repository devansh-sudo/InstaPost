import json

import pytest
from PIL import ImageFont

from instapost import budget, history
from instapost.brand import load_brand
from instapost.models import FactCheckVerdict, Format
from instapost.pipeline import load_sample
from instapost.render.text import font, strip_emoji, wrap
from instapost.scriptures import load_verses
from instapost.studio import fact_check, safety


def test_conjuncts_are_shaped():
    """With HarfBuzz shaping, half-forms join into conjuncts, so the word is narrower than
    the unshaped sequence of full letters plus visible halant marks."""
    path = load_brand().font_path("body")
    word = "त्यक्त्वा"
    shaped = font(path, 48).getlength(word)
    basic = ImageFont.truetype(str(path), 48, layout_engine=ImageFont.Layout.BASIC)
    assert shaped < basic.getlength(word)


def test_emoji_removed_from_drawn_text():
    assert strip_emoji("आज का संकल्प 🙏") == "आज का संकल्प"
    f = font(load_brand().font_path("body"), 40)
    assert wrap(f, "शांति 🙏 रहे", 2000) == ["शांति रहे"]


def test_verse_ids_and_samples_are_consistent():
    verses = load_verses()
    assert verses, "verse database is empty"
    for fmt in Format:
        draft = load_sample(fmt)
        assert draft.reference_id is None or draft.reference_id in verses


@pytest.mark.parametrize("text", [
    "शेयर नहीं किया तो बुरा होगा",
    "कमेंट में ॐ नमः शिवाय लिखें",
    "type 'om' below",
    "इससे हर रोग दूर होगा",
])
def test_safety_catches_bad_lines(text):
    draft = load_sample(Format.REEL_BOOK_POV).model_copy(update={"body": text})
    assert safety.check(draft)


def test_samples_pass_safety():
    for fmt in Format:
        assert safety.check(load_sample(fmt)) == []


def test_unknown_reference_fails_fact_check():
    draft = load_sample(Format.REEL_BOOK_POV).model_copy(update={"reference_id": "gita-99.99"})
    assert fact_check.offline_fact_check(draft).verdict == FactCheckVerdict.FAIL


def test_reference_text_comes_from_database():
    draft = load_sample(Format.REEL_BOOK_POV)
    result = fact_check.offline_fact_check(draft)
    assert result.reference_text == load_verses()["gita-2.48"].text


def test_budget_caps(monkeypatch):
    budget.record("test", "x", 0.55)
    budget.ensure_room(0.04)
    with pytest.raises(budget.BudgetExceeded):
        budget.ensure_room(0.10)


def test_duplicate_detection():
    history.add("a", "reel", "gita", "t", "जिसे बदल नहीं सकते, उसे स्वीकार करना ही शांति है।")
    ratio, _ = history.most_similar("जिसे बदल नहीं सकते उसे स्वीकार करना ही शांति है")
    assert ratio > 0.8
    assert json.loads(history._path().read_text().splitlines()[0])["id"] == "a"
