"""Scene reels: a 12-20 second conversation between Shiv and Parvati, told in 3-4 painted
pictures with a voice for each character.

Flow: Claude writes a script from a named scripture episode -> you read and edit it on the
dashboard (Scenes) -> on approval the pictures, voices and video are made -> the reel goes to
the inbox like any other post. Nothing is paid for pictures or voices before you approve.

Faithfulness: every scene follows an episode or teaching from a named source (for example the
Shiv Purana or Shri Ramcharitmanas); the lines are simple Hindi retellings, never invented
teachings, and quoted verse text only ever comes from the verified verse files.
"""

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel

from instapost import llm
from instapost.config import get_settings
from instapost.scriptures import load_verses

SCENE_REEL = "scene_reel"  # Item.format of the finished reels (not a Format: never auto-planned)
SPOKEN_WORDS_MAX = 40      # about 15 s of speech; with title and end card the reel stays near 20 s
MAX_SECONDS = 24.0


@dataclass(frozen=True)
class Character:
    id: str
    name: str        # shown on the subtitle chip
    look: str        # how the painter draws them, the same in every picture
    voice: str       # Gemini prebuilt voice
    delivery: str    # how the voice speaks


CHARACTERS = {c.id: c for c in (
    Character("shiv", "शिव",
              "Lord Shiva: serene compassionate face, blue-grey skin, matted hair tied up with a "
              "crescent moon and the river Ganga flowing from it, third eye, rudraksha beads, "
              "tiger-skin cloth, a calm cobra around the neck, trishul with a small damaru",
              "Charon", "in a deep, slow, calm and kind voice"),
    Character("parvati", "पार्वती",
              "Goddess Parvati: gentle graceful face, warm skin, red silk saree with a gold "
              "border, gold jewellery, red bindi, long dark hair with jasmine flowers",
              "Sulafat", "in a soft, warm and thoughtful voice"),
    Character("narrator", "सूत्रधार", "", "Gacrux",
              "like a warm, gentle storyteller, unhurried"),
)}

ART_STYLE = (
    "Traditional Indian devotional painting in soft warm colours, like classic Indian calendar "
    "art with the delicacy of a miniature painting: gentle golden light, serene expressions, "
    "rich but calm colours, painterly brush texture, respectful and traditional depiction. "
    "Vertical 9:16 composition; faces and the main action in the upper two thirds, the lower "
    "third calm and simple (subtitles go there). No text, no letters, no words, no watermark, "
    "no logo, no frame.")


class ScenePicture(BaseModel):
    prompt: str  # English: setting, who is in it, what they do, light and mood


class SceneLine(BaseModel):
    speaker: str  # "shiv", "parvati" or "narrator"
    text: str     # Hindi (Devanagari): spoken and shown as the subtitle
    picture: int  # which picture (0-based) is on screen while it is spoken


class SceneScript(BaseModel):
    """Filled by Claude; every field required (nullable where optional)."""
    title: str            # Hindi, shown at the start (the hook, at most 8 words)
    source: str           # Hindi: where the episode comes from, e.g. "शिव पुराण, रुद्र संहिता"
    source_note: str      # English, for the reviewer: which episode it follows and how closely
    pictures: list[ScenePicture]
    lines: list[SceneLine]
    reference_id: str | None  # a verified verse shown on the end card, or null
    question: str         # Hindi: the closing question (invites comments)
    caption_hi: str
    hashtags: list[str]
    alt_text: str


SYSTEM = """You write short spoken scenes for a Hindi spiritual Instagram page: a conversation
between Lord Shiva and Goddess Parvati that a viewer watches in 12-20 seconds and remembers.

Faithfulness comes first. Every scene follows a real episode or teaching from a named source
(for example the Shiv Purana, Shri Ramcharitmanas where Shiva tells Uma the story of Ram, the
Vigyan Bhairav Tantra where Devi asks and Bhairava answers, or well-known Puranic episodes such
as Parvati's tapasya). Put that source in `source` (Hindi) and explain in `source_note`
(English) which episode you follow and what you simplified. The lines are simple Hindi
retellings of that episode's meaning; never give Shiva or Parvati a teaching the source does
not support, never invent miracles, never use fear, and never quote verse text in the lines.
If the request names a topic you cannot ground in a source, choose the closest real episode.

Shape: `title` = a hook of at most 8 Hindi words about an everyday feeling (worry, anger,
waiting, love, loss, patience). 3 to 5 `lines`: Parvati usually asks or wonders, Shiva answers
calmly; an optional first or last line by the "narrator". Speakers are exactly "shiv",
"parvati" or "narrator". All lines together have at most 40 words, each line at most 16 words,
warm and simple Hindi that a 16-year-old and a 60-year-old both understand.

`pictures`: 3 or 4, in order, in English: the setting (Mount Kailash, a Himalayan meadow at
dawn, a forest ashram ...), who is in the picture and what they do, light and mood. Describe
the action, not the characters' looks (their looks are added for you). Respectful, calm,
traditional; no text in the pictures. Each line names the picture shown while it is spoken;
pictures go in order and every picture is used.

`reference_id`: one id from the verified list in the request that truly fits the scene, or
null. The verse text is added from the verified database; never write it yourself.
`question`: one gentle Hindi question to the viewer that invites a real answer.
`caption_hi`: two or three short lines: what the scene teaches for today's life, the source in
one line, then the question. hashtags: 3 to 5 specific ones. alt_text: one Hindi sentence
describing the scene for screen readers."""


def words(script: SceneScript) -> int:
    return sum(len(line.text.split()) for line in script.lines)


def problems(script: SceneScript) -> list[str]:
    """What is wrong with a script (empty list = fine); checked again after your edits."""
    out = []
    if not 3 <= len(script.lines) <= 6:
        out.append(f"{len(script.lines)} lines (3-6)")
    if not 2 <= len(script.pictures) <= 4:
        out.append(f"{len(script.pictures)} pictures (2-4)")
    if (n := words(script)) > SPOKEN_WORDS_MAX:
        out.append(f"{n} spoken words (at most {SPOKEN_WORDS_MAX}, or the reel gets too long)")
    for i, line in enumerate(script.lines, 1):
        if line.speaker not in CHARACTERS:
            out.append(f"line {i}: unknown speaker {line.speaker!r}")
        if not 0 <= line.picture < len(script.pictures):
            out.append(f"line {i}: picture {line.picture + 1} does not exist")
        if not line.text.strip():
            out.append(f"line {i} is empty")
    used = {line.picture for line in script.lines}
    if script.pictures and used != set(range(len(script.pictures))):
        out.append("every picture must be shown during at least one line")
    if script.reference_id and script.reference_id not in load_verses():
        out.append(f"unknown verse {script.reference_id}")
    if len(script.title.split()) > 10:
        out.append("title longer than 10 words")
    return out


def write(topic: str | None = None) -> SceneScript:
    """One new script from Claude (about $0.02)."""
    from instapost.scriptures import catalogue_for_prompt

    req = {"characters": ["shiv", "parvati", "narrator"],
           "topic": topic or "choose a calm everyday theme that fits a real episode",
           "verified_verses": catalogue_for_prompt()}
    return llm.structured(
        model=get_settings().writer_model, system=SYSTEM,
        content=json.dumps(req, ensure_ascii=False), schema=SceneScript,
        purpose="scene_script", effort="medium", max_tokens=4000, estimated_usd=0.03)


# --- storage: state/scenes/<id>.json ----------------------------------------------------------

STATUSES = ("draft", "approved", "making", "made", "rejected")


def _dir() -> Path:
    d = get_settings().state_dir / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(scene_id: str) -> Path:
    if not scene_id.replace("-", "").isalnum():
        raise KeyError(scene_id)
    return _dir() / f"{scene_id}.json"


def save(entry: dict) -> dict:
    entry["updated"] = datetime.now(UTC).isoformat()
    path = _path(entry["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return entry


def get(scene_id: str) -> dict:
    try:
        return json.loads(_path(scene_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError(scene_id) from None


def script_of(entry: dict) -> SceneScript:
    return SceneScript.model_validate(entry["script"])


def all_scenes() -> list[dict]:
    out = []
    for f in _dir().glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda e: e.get("created", ""), reverse=True)


def new(script: SceneScript, pillar: str = "shiv_dhyan", topic: str | None = None,
        for_date: date | None = None) -> dict:
    scene_id = f"{date.today().isoformat()}-scene-{secrets.token_hex(3)}"
    return save({"id": scene_id, "created": datetime.now(UTC).isoformat(), "status": "draft",
                 "pillar": pillar, "topic": topic, "script": script.model_dump(mode="json"),
                 "for_date": (for_date or date.today()).isoformat(), "item_id": None,
                 "error": None})


def set_status(scene_id: str, status: str, **fields) -> dict:
    if status not in STATUSES:
        raise ValueError(status)
    entry = get(scene_id)
    entry.update(status=status, **fields)
    return save(entry)


def update_script(scene_id: str, script: SceneScript) -> dict:
    entry = get(scene_id)
    if entry["status"] not in ("draft", "approved"):
        raise ValueError(f"the scene is {entry['status']}; it can no longer be edited")
    entry["script"] = script.model_dump(mode="json")
    return save(entry)


SAMPLE = SceneScript(
    title="जब मन किसी की राह देखते-देखते थक जाए…",
    source="शिव पुराण, रुद्र संहिता (पार्वती की तपस्या)",
    source_note=("Follows Parvati's long tapasya for Shiva in the Shiv Purana (Rudra Samhita, "
                 "Parvati Khanda) and Shiva accepting her; the dialogue is a simple retelling "
                 "of its meaning (steady devotion and patience bear fruit), not a quotation."),
    pictures=[
        ScenePicture(prompt="Parvati in deep tapasya in a snowy Himalayan forest at dawn, "
                            "sitting in meditation beside a small fire, soft mist"),
        ScenePicture(prompt="Shiva appearing before Parvati in a glow of soft light, the "
                            "forest around them blooming, she looks up with folded hands"),
        ScenePicture(prompt="Shiva and Parvati sitting together on Mount Kailash at sunrise, "
                            "peaceful, Nandi resting nearby, golden clouds"),
    ],
    lines=[
        SceneLine(speaker="narrator", picture=0,
                  text="बरसों तक पार्वती ने तप किया, केवल शिव के लिए।"),
        SceneLine(speaker="parvati", picture=1,
                  text="प्रभु, इतने वर्ष बीत गए… क्या मेरी प्रतीक्षा व्यर्थ थी?"),
        SceneLine(speaker="shiv", picture=1,
                  text="नहीं उमा, सच्ची निष्ठा कभी व्यर्थ नहीं जाती।"),
        SceneLine(speaker="shiv", picture=2,
                  text="धैर्य वह दीपक है, जो समय आने पर राह दिखाता है।"),
    ],
    reference_id="shiv-panchakshari",
    question="आप किस चीज़ का धैर्य से इंतज़ार कर रहे हैं?",
    caption_hi=("सच्चा इंतज़ार कमज़ोरी नहीं, निष्ठा है।\n"
                "शिव पुराण: पार्वती की तपस्या की कथा से।\n"
                "आप किस चीज़ का धैर्य से इंतज़ार कर रहे हैं?"),
    hashtags=["#शिवपार्वती", "#महादेव", "#शिवपुराण", "#धैर्य"],
    alt_text="हिमालय में तपस्या करती पार्वती और उन्हें दर्शन देते शिव का चित्र।",
)
