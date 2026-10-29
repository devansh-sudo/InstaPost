"""Turns an approved scene script into a finished scene reel in the inbox: pictures (Gemini,
the same characters in every picture), one voice per character, video, caption."""

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import date
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from instapost import budget, qa
from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, audit
from instapost.render import music, scene_reel
from instapost.scriptures import load_verses
from instapost.studio import images, scenes, voice
from instapost.studio.scenes import CHARACTERS, SCENE_REEL, SceneScript

AI_NOTE = "🎨🎙 चित्र और आवाज़ें AI की मदद से बनाए गए हैं; कथा: {source}"
KICKER = "॥ शिव-पार्वती संवाद ॥"


def picture_prompt(script: SceneScript, n: int, first: bool) -> str:
    """The painter's prompt for picture n: style, the characters' fixed looks, the action."""
    pic = script.pictures[n].prompt
    who = [c for c in CHARACTERS.values()
           if c.look and (c.id in pic.lower()  # "shiv" also matches "Shiva"
                          or any(ln.speaker == c.id and ln.picture == n for ln in script.lines))]
    looks = " ".join(f"{c.look}." for c in who)
    keep = "" if first else (" Keep the characters exactly as in the reference picture: the "
                             "same faces, skin, hair, clothes, ornaments and the same painting "
                             "style and colours.")
    return f"{scenes.ART_STYLE} {looks} Scene: {pic}.{keep}"


def _pictures(script: SceneScript, folder: Path, offline: bool,
              step: Callable[[str], None]) -> list[Image.Image]:
    out: list[Image.Image] = []
    provider = (images.get_provider("placeholder") if offline
                else images.GeminiProvider())
    for n in range(len(script.pictures)):
        step(f"painting picture {n + 1} of {len(script.pictures)}")
        prompt = picture_prompt(script, n, first=not out)
        if out and isinstance(provider, images.GeminiProvider):
            img = provider.generate(prompt, portrait=True, refs=out[:1])
        else:
            img = provider.generate(prompt, portrait=True)
        img = img.convert("RGB")
        img.save(folder / f"picture_{n + 1}.jpg", quality=92)
        out.append(img)
    return out


def _lines(script: SceneScript, offline: bool, step) -> list[scene_reel.Line]:
    speak = voice.available() and not offline
    out = []
    for n, ln in enumerate(script.lines, 1):
        c = CHARACTERS[ln.speaker]
        samples = None
        if speak:
            step(f"voice {n} of {len(script.lines)} ({c.name})")
            samples = voice.speak(ln.text, c.voice, c.delivery)
        out.append(scene_reel.Line(c.name, ln.text, ln.picture, samples, voice.SR))
    return out


def caption(script: SceneScript, ai_voices: bool) -> str:
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in script.hashtags[:5])
    note = AI_NOTE.format(source=script.source)
    if not ai_voices:
        note = note.replace("🎨🎙 चित्र और आवाज़ें", "🎨 चित्र")
    return f"{script.caption_hi.strip()}\n\n{note}\n\n{tags}"


def make(s: Session, scene_id: str, actor: str = "agent", offline: bool = False,
         on_step: Callable[[str], None] | None = None) -> Item:
    """Make the reel for an approved scene and put it in the inbox (needs your approval)."""
    step = on_step or (lambda _label: None)
    entry = scenes.get(scene_id)
    if entry["status"] not in ("approved", "making"):
        raise ValueError(f"scene {scene_id} is {entry['status']}, not approved")
    script = scenes.script_of(entry)
    if bad := scenes.problems(script):
        raise ValueError("the script needs fixing first: " + "; ".join(bad))
    scenes.set_status(scene_id, "making", error=None)
    for_date = date.fromisoformat(entry.get("for_date") or date.today().isoformat())
    item_id = f"{date.today().isoformat()}-{SCENE_REEL}-{secrets.token_hex(3)}"
    folder = get_settings().output_dir / date.today().isoformat() / item_id
    folder.mkdir(parents=True, exist_ok=True)
    brand = load_brand()
    try:
        with budget.tagged(item_id=item_id, format=SCENE_REEL, pillar=entry["pillar"]):
            pictures = _pictures(script, folder, offline, step)
            lines = _lines(script, offline, step)
        verse = load_verses().get(script.reference_id or "")
        motion = scene_reel.SceneMotion(
            brand, pictures, lines, script.title, KICKER,
            verse.text if verse else None, verse.label if verse else None, script.question)
        checks = [
            qa.QACheck(name="duration", passed=motion.duration <= scenes.MAX_SECONDS,
                       detail=f"{motion.duration:.1f}s (at most {scenes.MAX_SECONDS:.0f}s)"),
            qa.QACheck(name="safe_area", passed=not qa.unsafe_boxes(
                scene_reel.text_boxes(motion)), detail=""),
        ]
        cover = motion.cover()
        cover.save(folder / "cover.jpg", quality=92)
        if get_settings().vision_check and not offline:
            checks.append(qa.check_vision(cover, brand))
        report = qa.report(checks)
        track = music.pick(entry["pillar"])
        if report.passed:
            step("encoding video")
            seed = int(hashlib.sha1(item_id.encode()).hexdigest()[:8], 16)
            scene_reel.encode(motion, folder / "reel.mp4", music=track, seed=seed)
            if track is not None:
                (folder / "music.txt").write_text(music.rel(track) + "\n", encoding="utf-8")
    except Exception as e:
        scenes.set_status(scene_id, "approved", error=str(e)[:500])
        raise
    ai_voices = any(ln.voice is not None for ln in lines)
    text = caption(script, ai_voices)
    (folder / "caption.txt").write_text(text + "\n", encoding="utf-8")
    (folder / "scene.json").write_text(json.dumps(entry["script"], ensure_ascii=False,
                                                  indent=2), encoding="utf-8")
    (folder / "qa.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    # What the publisher and the item page read (the same keys as a written post).
    (folder / "draft.json").write_text(json.dumps({
        "topic": entry.get("topic") or script.title, "hook_lines": [script.title],
        "hashtags": script.hashtags[:5], "alternative_hooks": [], "alt_text": script.alt_text,
        "caption_hi": script.caption_hi, "source": script.source,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "template.txt").write_text("scene\n", encoding="utf-8")
    status = ItemStatus.NEEDS_REVIEW if report.passed else ItemStatus.BLOCKED
    item = Item(id=item_id, format=SCENE_REEL, pillar=entry["pillar"], folder=str(folder),
                status=status, hook=script.title, caption=text,
                # You read and approved the script, its source and every line.
                fact_check="pass", for_date=for_date.isoformat(),
                duration_s=round(motion.duration, 2) if report.passed else None,
                qa_summary="; ".join(f"{c.name}: {c.detail}" for c in report.checks
                                     if not c.passed),
                features={"pillar": entry["pillar"], "template": "scene",
                          "voices": "ai" if ai_voices else "none"},
                music=music.rel(track) if track is not None and report.passed else None)
    s.add(item)
    s.flush()
    audit(s, actor, "created", item.id, status=item.status, scene=scene_id)
    scenes.set_status(scene_id, "made" if report.passed else "approved", item_id=item.id,
                      error=None if report.passed else item.qa_summary)
    return item
