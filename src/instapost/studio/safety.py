"""Rule-based tone and policy checks that run before any paid step.

These catch the cheap, obvious cases (chain-message threats, engagement bait, medical claims,
politics). Subtler problems are left to the LLM fact-check.
"""

import re

from instapost.models import Draft

_RULES: list[tuple[str, re.Pattern]] = [
    ("fear/chain message", re.compile(
        r"शेयर\s*नहीं|share\s*nahi|share\s*na\s*kiya|वरना|अशुभ\s*होगा|बुरा\s*होगा|"
        r"\d+\s*(लोगों|logon)\s*को\s*भेज", re.I)),
    ("engagement bait", re.compile(
        r"(कमेंट|comment)\s*(में|me|mein)?\s*.{0,25}(लिखें|लिखो|likho|likhein|type)|"
        r"type\s+['\"]?(om|ॐ|har|jai)", re.I)),
    ("medical claim", re.compile(r"रोग\s*दूर|बीमारी\s*ठीक|इलाज|cure|ilaaj|ilaj", re.I)),
    ("politics", re.compile(r"मोदी|राहुल|भाजपा|बीजेपी|कांग्रेस|\bbjp\b|congress|election|चुनाव",
                            re.I)),
]


def check(draft: Draft) -> list[str]:
    """Return a list of problems; empty means the draft passed."""
    text = "\n".join([
        *draft.hook_lines, draft.body, draft.caption_hi, draft.caption_hinglish, draft.cta,
        *(s.title + " " + s.text for s in draft.slides),
        draft.story_line or "", draft.poll_question or "",
    ])
    return [f"{name}: '{m.group(0)}'" for name, rx in _RULES if (m := rx.search(text))]
