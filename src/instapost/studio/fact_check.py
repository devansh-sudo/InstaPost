"""Fact check: deterministic reference lookup + an LLM pass for attribution and meaning.

The verse text printed on a post always comes from the verified database. The LLM only judges
whether the hook/body/caption use that verse faithfully (right speaker, right meaning).
"""

import json

from instapost import llm
from instapost.config import get_settings
from instapost.models import Draft, FactCheckLLM, FactCheckResult, FactCheckVerdict
from instapost.scriptures import load_verses

SYSTEM = """You are a careful fact-checker for a Hindi spiritual Instagram page.
You receive a draft post and, if it cites one, the exact verified verse it cites.

Return verdict:
- "fail" if the draft misattributes a teaching (for example saying Shiva taught something from
  the Bhagavad Gita, which is Krishna speaking to Arjuna), contradicts the verse's meaning,
  invents a quote or story and presents it as scripture, or makes medical, political,
  sectarian or fear-based claims.
- "needs_human_review" if you are unsure about any religious or historical fact.
- "pass" only if everything stated as fact is correct and the tone is respectful.

List each problem in `issues` in short Hindi or English. Do not judge writing quality."""


def fact_check(draft: Draft) -> FactCheckResult:
    issues: list[str] = []
    verse = None
    if draft.reference_id is not None:
        verse = load_verses().get(draft.reference_id)
        if verse is None:
            return FactCheckResult(verdict=FactCheckVerdict.FAIL,
                                   issues=[f"unknown reference_id {draft.reference_id!r}"])
        if not verse.reviewed:
            issues.append(f"{verse.id}: verse text not yet reviewed by a human")

    payload = {
        "hook": draft.hook_lines, "body": draft.body, "caption": draft.caption_hi,
        "caption_hinglish": draft.caption_hinglish,
        "slides": [s.model_dump() for s in draft.slides],
        "cited_verse": None if verse is None else {
            "label": verse.label, "speaker": verse.speaker, "text": verse.text,
            "meaning": verse.meaning},
    }
    result = llm.structured(
        model=get_settings().factcheck_model, system=SYSTEM,
        content=json.dumps(payload, ensure_ascii=False, indent=1),
        schema=FactCheckLLM, purpose="fact_check", effort="medium", max_tokens=4000,
        estimated_usd=0.03,
    )
    issues += result.issues
    verdict = result.verdict
    if verdict == FactCheckVerdict.PASS and verse is not None and not verse.reviewed:
        verdict = FactCheckVerdict.NEEDS_HUMAN
    return FactCheckResult(
        verdict=verdict, issues=issues,
        reference_text=verse.text if verse else None,
        reference_label=verse.label if verse else None,
    )


def offline_fact_check(draft: Draft) -> FactCheckResult:
    """Deterministic part only, for offline runs without an API key."""
    if draft.reference_id is None:
        return FactCheckResult(verdict=FactCheckVerdict.NEEDS_HUMAN,
                               issues=["offline run: LLM fact-check skipped"])
    verse = load_verses().get(draft.reference_id)
    if verse is None:
        return FactCheckResult(verdict=FactCheckVerdict.FAIL,
                               issues=[f"unknown reference_id {draft.reference_id!r}"])
    return FactCheckResult(
        verdict=FactCheckVerdict.NEEDS_HUMAN,
        issues=["offline run: LLM fact-check skipped"],
        reference_text=verse.text, reference_label=verse.label,
    )
