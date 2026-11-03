"""Free fixes to a draft before it is checked, so small slips never block a post (and never
waste the money already spent on writing it).

- Mantras and sacred names written with a colon (:) or without the visarga (ः) are written
  correctly: "ॐ नम: शिवाय" and "ॐ नम शिवाय" become "ॐ नमः शिवाय". A colon and a visarga look
  alike, but only the visarga is correct Hindi, and the image check rightly flags the other.
- Hashtags are kept to 3-5 (extra ones are dropped, missing ones filled with the page's own).
"""

import re

from instapost.models import Draft

# (wrong form as a regex, correct form). The colon is the ASCII ":" or the look-alike "꞉".
FIXES = [
    (r"नम\s*[:꞉]\s*शिवाय", "नमः शिवाय"),
    (r"नम\s+शिवाय", "नमः शिवाय"),
    (r"नमो\s+नम\s*[:꞉](?=[\s।,!]|$)", "नमो नमः"),
    (r"दु\s*[:꞉]\s*ख", "दुःख"),
    (r"(अत|पुन|प्रात)[:꞉](?=\s|$)", r"\1ः"),
]
DEFAULT_TAGS = ["#जीवनसूत्र", "#हिंदीविचार", "#शांति"]


def text(value: str) -> str:
    for wrong, right in FIXES:
        value = re.sub(wrong, right, value)
    return value


def draft(d: Draft) -> tuple[Draft, list[str]]:
    """The fixed draft and what was changed (for the item's record)."""
    changes: list[str] = []

    def fix(v):
        if isinstance(v, str):
            new = text(v)
            if new != v:
                changes.append(f"spelling: {v[:40]} → {new[:40]}")
            return new
        return v

    update = {k: fix(getattr(d, k)) for k in ("body", "caption_hi", "caption_hinglish", "cta",
                                              "story_line", "poll_question", "alt_text",
                                              "topic")
              if getattr(d, k) is not None}
    for k in ("hook_lines", "alternative_hooks", "poll_options"):
        update[k] = [fix(x) for x in getattr(d, k)]
    update["slides"] = [s.model_copy(update={"title": fix(s.title), "text": fix(s.text)})
                        for s in d.slides]
    tags = list(dict.fromkeys("#" + t.lstrip("#").strip() for t in d.hashtags if t.strip()))
    if len(tags) > 5:
        changes.append(f"hashtags: kept 5 of {len(tags)}")
        tags = tags[:5]
    if len(tags) < 3:
        extra = [t for t in DEFAULT_TAGS if t not in tags][:3 - len(tags)]
        changes.append(f"hashtags: added {' '.join(extra)}")
        tags += extra
    update["hashtags"] = tags
    return d.model_copy(update=update), changes
