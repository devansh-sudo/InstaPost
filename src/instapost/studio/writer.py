"""Writer: turns a format + pillar (+ optional topic) into a structured Draft via Claude."""

import json

from instapost import llm
from instapost.brand import Brand
from instapost.config import get_settings
from instapost.models import Draft, Format, Pillar
from instapost.scriptures import catalogue_for_prompt

SYSTEM = """You write content for a Hindi spiritual and peace-of-mind Instagram page
(Shiva, Krishna and the Bhagavad Gita, Sant Kabir, everyday life lessons).
The audience scrolls fast. Your job is to make them stop, read to the end, feel calmer,
save the post and send it to someone who needs it.

Voice: warm, calm, simple Hindi (Devanagari) that a 16-year-old and a 60-year-old both
understand. No preaching, no fear, no exaggeration. Respectful toward every faith.

Hook (hook_lines, 1-2 lines, at most 12 words total): a relatable truth or a gentle
question about everyday life (worry, anger, waiting, relationships, failure, comparison).
It must work on its own in two seconds. Give two different alternative_hooks too.

Body (1-2 short sentences): explains the hook and gives one practical thought. It may say
who taught the idea only if that is correct. For reel_book_pov the reel is at most 12 seconds
and must be readable in time, so hook_lines and body together have at most 29 words;
for other formats the body has at most 28 words.

Reference: set reference_id to one id from the verified list in the request, or null.
Never cite anything that is not in that list, and never write verse text yourself; the
verse text is added later from the verified database. Attribution must be correct: the
Bhagavad Gita is Krishna speaking to Arjuna, dohe are Kabir's. A Shiva illustration can
accompany any calm message, but do not claim Shiva said something he did not.

Caption: caption_hi = first line adds meaning beyond the image (so people tap "more"),
then one open question that invites a real answer in the comments. caption_hinglish = one
short Hinglish line (Roman script) for readers who prefer it. cta = a soft line asking to
send it to someone who needs it. Never ask people to type a word in the comments, never
threaten bad luck, never make medical or political claims.

hashtags: 3 to 5 specific ones (Hindi or English), no generic spam tags.
alt_text: one plain Hindi sentence describing the image for screen readers.

illustration_prompt (English): subject only for a pencil sketch, for example
"Lord Shiva meditating on a rock facing the Himalayas, trishul beside him, crescent moon".
No text, letters or words in the image. Respectful and traditional depiction.
scene_prompt (English): pick or lightly vary one of the scene presets in the request.

slides: only for format carousel_shlok, 5 slides (cover hook, the verse idea, meaning,
how to use it today, closing line); otherwise an empty list.
story_line, poll_question, poll_options: only for format story_line (a one-line morning or
night thought plus a two-option poll); otherwise null and an empty list.

The request may contain a "plan" from the weekly planner. Follow it: use its hook_style,
use its scene as scene_prompt (light variation is fine), and match its length: hook and body
together "short" = at most 17 words, "medium" = at most 23 words, "long" = at most 29 words."""

# A reel lasts at most 12 s and needs 1 s more than the reading time (qa.check_reel).
MAX_REEL_WORDS = int((12.0 - 1.0) * 2.7)  # 29 words at 2.7 words per second


def reel_words(draft: Draft) -> int:
    return sum(len(t.split()) for t in [*draft.hook_lines, draft.body] if t)


def write(
    brand: Brand,
    fmt: Format,
    pillar: Pillar,
    topic: str | None,
    recent_hooks: list[str],
    plan: dict | None = None,
) -> Draft:
    request = {
        "format": fmt.value,
        "pillar": pillar.value,
        "topic": topic or "choose a fresh, relatable everyday topic for this pillar",
        "page_name": brand.page_name,
        "scene_presets": brand.scene_presets,
        "avoid_repeating_these_recent_hooks": recent_hooks[-30:],
    }
    if plan:
        request["plan"] = plan
    # The verse list is the same for every post, so it sits in the cached system prompt;
    # only the short request below is new (and fully priced) on each call.
    system = SYSTEM + "\n\nVerified references you may cite (by id):\n" + catalogue_for_prompt()
    content = "Request:\n" + json.dumps(request, ensure_ascii=False, indent=1)
    st = get_settings()
    story = fmt == Format.STORY_LINE

    def ask(text: str) -> Draft:
        return llm.structured(
            model=st.story_writer_model if story else st.writer_model, system=system,
            content=text, schema=Draft, purpose=f"write:{fmt.value}",
            effort="low" if story else "medium", max_tokens=8000, estimated_usd=0.05,
        )

    draft = ask(content)
    if fmt == Format.REEL_BOOK_POV and (n := reel_words(draft)) > MAX_REEL_WORDS:
        # One rewrite is cheaper than images and a video that the length check would block.
        draft = ask(content + f"\n\nYour previous draft had {n} words in hook_lines + body. "
                    f"Rewrite it with at most {MAX_REEL_WORDS} words in hook_lines + body "
                    "together (keep the same idea; shorten the body first).\nPrevious draft:\n"
                    + draft.model_dump_json())
    return draft
