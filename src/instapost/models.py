"""Data models shared across the studio pipeline.

Models that Claude fills via structured outputs keep every field required (nullable where
optional) and avoid schema constraints; limits are enforced in code by the QA gate instead.
"""

from enum import StrEnum

from pydantic import BaseModel


class Format(StrEnum):
    REEL_BOOK_POV = "reel_book_pov"
    CAROUSEL_SHLOK = "carousel_shlok"
    STORY_LINE = "story_line"


class Pillar(StrEnum):
    JEEVAN_SUTRA = "jeevan_sutra"
    SHIV_DHYAN = "shiv_dhyan"
    GITA = "gita"
    KABIR = "kabir"
    LIFE_LESSON = "life_lesson"


class HookStyle(StrEnum):
    STATEMENT = "statement"
    QUESTION = "question"
    CONDITIONAL = "conditional"
    CONTRAST = "contrast"


class Slide(BaseModel):
    title: str
    text: str


class Draft(BaseModel):
    """One piece of content as written by the writer (all text in Devanagari unless noted)."""

    topic: str
    hook_lines: list[str]
    hook_style: HookStyle
    alternative_hooks: list[str]
    body: str
    reference_id: str | None
    caption_hi: str
    caption_hinglish: str
    cta: str
    hashtags: list[str]
    alt_text: str
    illustration_prompt: str
    scene_prompt: str
    slides: list[Slide]
    story_line: str | None
    poll_question: str | None
    poll_options: list[str]


class FactCheckVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_HUMAN = "needs_human_review"


class FactCheckLLM(BaseModel):
    verdict: FactCheckVerdict
    issues: list[str]


class FactCheckResult(BaseModel):
    verdict: FactCheckVerdict
    issues: list[str]
    reference_text: str | None = None
    reference_label: str | None = None


class QACheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class QAReport(BaseModel):
    passed: bool
    checks: list[QACheck]
