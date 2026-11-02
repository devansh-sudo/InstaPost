from instapost.db import session
from instapost.stats import insights
from tests.test_phase4 import _scored_reel


def test_full_report_has_every_section_and_no_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-should-not-appear")
    _scored_reel("gita", "कर्म करो", 40, days_ago=3)
    _scored_reel("shiv_dhyan", "ॐ नमः शिवाय", 5, days_ago=4, slot="20:45")
    with session() as s:
        text = insights.build(s, 30)
    for heading in ("## Followers", "## Totals", "## Every post", "## What works",
                    "### Theme", "## What the learner believes", "## Costs", "## Health",
                    "## Next 7 days", "### Festivals in the next 6 weeks"):
        assert heading in text, heading
    assert "कर्म करो" in text and "| gita |" in text
    assert "sk-ant" not in text


def test_report_with_no_posts_yet():
    with session() as s:
        text = insights.build(s, 7)
    assert "No published posts in this period yet." in text
    assert "Nothing learned yet" in text
