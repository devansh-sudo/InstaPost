"""Ink drawings for the book page: one library, one style.

Where drawings come from, in the order a post looks for one:
1. the library, when a drawing's tags match the post's subject (least recently used first):
   data/drawings/ (the starter set and your own files), uploads on the dashboard
   (state/drawings/own/) and drawings made earlier by Gemini (state/drawings/made/);
2. a new drawing from Gemini in the fixed engraving style, when GEMINI_API_KEY works
   (about $0.04); it is saved to the library, so the next post on that subject is free;
3. any library drawing that suits the pillar;
4. the old way (an Unsplash photo turned into a pencil sketch, or the placeholder).

Every drawing is cleaned to the same look before use: black ink, white background, trimmed.
Tags come from folder names, file names and an optional `name.json` ({"tags": [...]}).
"""

import json
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from instapost.config import get_settings
from instapost.models import Pillar
from instapost.studio.free_images import _load_usage, _mark_used, keywords

log = logging.getLogger("instapost.drawings")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# One style for every generated drawing, so the whole page looks like one book.
INK_STYLE = ("Black ink illustration in the style of a vintage book engraving: fine pen lines "
             "and cross-hatching for shading, no solid fills, no colour, no grey wash. Pure "
             "white background, the subject centred with a wide empty margin, no frame, no "
             "border, no ground plane beyond a few short strokes. Respectful, traditional "
             "Indian depiction.")

# Words in a post's illustration subject that stand for a pillar's usual drawings.
PILLAR_TAGS = {
    Pillar.SHIV_DHYAN: {"shiv_dhyan", "shiva", "shiv"},
    Pillar.GITA: {"gita", "krishna"},
    Pillar.KABIR: {"kabir"},
    Pillar.JEEVAN_SUTRA: {"jeevan_sutra"},
    Pillar.LIFE_LESSON: {"life_lesson"},
}
SYNONYMS = {"shiv": "shiva", "mahadev": "shiva", "shankar": "shiva", "bholenath": "shiva",
            "kanha": "krishna", "govinda": "krishna", "lamp": "diya", "deepak": "diya",
            "mountains": "mountain", "himalayas": "himalaya", "flowers": "flower",
            "feathers": "feather", "beads": "mala", "rosary": "mala", "pot": "matka"}


@dataclass
class Drawing:
    id: str
    path: Path
    tags: set[str]
    source: str  # starter / library / own / made


def _words(text: str) -> set[str]:
    out = set()
    for w in keywords(text.replace("_", " ")) + re.findall(r"[a-z]+_[a-z]+", text.lower()):
        out.add(SYNONYMS.get(w, w))
    return out


def folders() -> list[tuple[Path, str]]:
    st = get_settings()
    return [(st.data_dir / "drawings", "library"), (st.state_dir / "drawings" / "own", "own"),
            (st.state_dir / "drawings" / "made", "made")]


def disabled_file() -> Path:
    return get_settings().state_dir / "drawings" / "disabled.json"


def disabled() -> set[str]:
    try:
        return set(json.loads(disabled_file().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def set_enabled(drawing_id: str, on: bool) -> None:
    off = disabled()
    off.discard(drawing_id) if on else off.add(drawing_id)
    disabled_file().parent.mkdir(parents=True, exist_ok=True)
    disabled_file().write_text(json.dumps(sorted(off)), encoding="utf-8")


def library(include_disabled: bool = False) -> list[Drawing]:
    off = set() if include_disabled else disabled()
    out = []
    for root, source in folders():
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if f.suffix.lower() not in IMAGE_EXTENSIONS or f.name.startswith("."):
                continue
            rel = f.relative_to(root)
            did = f"{source}:{rel.with_suffix('').as_posix()}"
            if did in off:
                continue
            tags = _words(" ".join(rel.with_suffix("").parts)) - {"starter"}
            side = f.with_suffix(".json")
            if side.exists():
                try:
                    tags |= {SYNONYMS.get(t, t) for t in
                             json.loads(side.read_text(encoding="utf-8")).get("tags", [])}
                except ValueError:
                    pass
            kind = "starter" if rel.parts[0] == "starter" else source
            out.append(Drawing(did, f, tags, kind))
    return out


def match(subject: str, pillar: Pillar | str | None = None,
          drawings: list[Drawing] | None = None) -> list[tuple[int, Drawing]]:
    """Drawings with their score for a post: shared subject words count most, the pillar a
    little; best and least recently used first."""
    wanted = _words(subject)
    ptags = PILLAR_TAGS.get(Pillar(pillar), set()) if pillar else set()
    usage = _load_usage()
    scored = []
    for d in drawings if drawings is not None else library():
        score = 3 * len(wanted & d.tags) + (1 if ptags & d.tags else 0)
        scored.append((score, d))
    scored.sort(key=lambda sd: (-sd[0], usage.get(f"drawing:{sd[1].id}", ""), sd[1].id))
    return scored


def ink(img: Image.Image) -> Image.Image:
    """Any drawing to the house look: grey-free black ink on white, trimmed with a margin."""
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(white, rgba)
    g = np.asarray(img.convert("L"), np.float32)
    paper = float(np.percentile(g, 90))
    darkest = float(np.percentile(g, 0.5))
    span = max(paper - darkest, 1.0)
    a = np.clip((g - darkest) / span, 0, 1)
    a = a ** 0.85                        # keep fine hatching visible
    a = np.where(a > 0.93, 1.0, a)       # paper tone and stains -> pure white
    out = Image.fromarray((a * 255).astype(np.uint8))
    box = ImageOps.invert(out).getbbox()
    if box:
        m = int(0.04 * max(out.size))
        out = out.crop((max(0, box[0] - m), max(0, box[1] - m),
                        min(out.width, box[2] + m), min(out.height, box[3] + m)))
    return out


def is_drawing(img: Image.Image) -> bool:
    """A line drawing on light paper, not a photo: mostly white, some ink, little colour."""
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        img = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba)
    small = img.convert("RGB").resize((400, max(1, round(400 * img.height / img.width))))
    hsv = np.asarray(small.convert("HSV"), np.float32)
    v, s = hsv[..., 2], hsv[..., 1]
    light = (v > 200).mean()
    inked = (v < 160).mean()  # fine lines turn grey when reduced, so count grey as ink
    return light > 0.5 and 0.004 < inked < 0.45 and float(np.median(s)) < 45


def mark_used(d: Drawing) -> None:
    _mark_used(f"drawing:{d.id}")


def _save_made(img: Image.Image, subject: str) -> Drawing:
    folder = get_settings().state_dir / "drawings" / "made"
    folder.mkdir(parents=True, exist_ok=True)
    words = sorted(_words(subject))[:6]
    stem = "-".join(words[:4]) or "drawing"
    path = folder / f"{stem}-{secrets.token_hex(2)}.png"
    img.save(path, optimize=True)
    path.with_suffix(".json").write_text(json.dumps({"tags": words, "subject": subject},
                                                    ensure_ascii=False), encoding="utf-8")
    rel = path.relative_to(folder).with_suffix("").as_posix()
    return Drawing(f"made:{rel}", path, set(words), "made")


def illustration(subject: str, pillar: Pillar | str,
                 fallback: Callable[[], Image.Image] | None = None,
                 generate: bool | None = None) -> tuple[Image.Image, str]:
    """The drawing for one post and where it came from (a library id, or "fallback").
    `fallback` makes a picture the old way (Unsplash sketch, placeholder)."""
    from instapost.studio import images

    st = get_settings()
    lib = library()
    ranked = match(subject, pillar, lib)
    if ranked and ranked[0][0] >= 3:  # a real subject match
        d = ranked[0][1]
        mark_used(d)
        with Image.open(d.path) as img:
            return ink(img), d.id
    generate = st.drawings_generate if generate is None else generate
    if generate and st.gemini_api_key and not images.recent_refusal("gemini", 1):
        try:
            raw = images.GeminiProvider().generate(full_prompt(subject), portrait=False)
            if is_drawing(raw):
                d = _save_made(ink(raw), subject)
                mark_used(d)
                return Image.open(d.path).convert("L"), d.id
            log.warning("Gemini drawing for %r was not a clean line drawing; not used", subject)
        except Exception as e:  # noqa: BLE001 - quota, billing, network: fall through
            log.info("Gemini drawing unavailable: %s", str(e)[:200])
    if ranked and ranked[0][0] >= 1:  # suits the pillar
        d = ranked[0][1]
        mark_used(d)
        with Image.open(d.path) as img:
            return ink(img), d.id
    if fallback is not None:
        try:
            return fallback(), "fallback"
        except Exception as e:  # noqa: BLE001 - then any drawing beats none
            if not ranked:
                raise
            log.info("fallback picture failed (%s); using a library drawing", str(e)[:200])
    if ranked:
        d = ranked[0][1]
        mark_used(d)
        with Image.open(d.path) as img:
            return ink(img), d.id
    raise LookupError("no drawing available")


# Subjects for a full library, one style. Make them in the Gemini or ChatGPT app (free, then
# upload on the dashboard) or with `instapost drawings pack` (Gemini API, about $0.04 each).
PROMPT_PACK: list[tuple[str, list[str]]] = [
    ("Lord Shiva meditating seen from behind, sitting on a rock, trishul beside him, crescent "
     "moon in his hair, Himalaya peaks and a still lake ahead", ["shiva", "meditation",
                                                                 "himalaya", "shiv_dhyan"]),
    ("Lord Shiva in meditation, front view, eyes closed, serene face, snake around the neck, "
     "Ganga flowing from his hair", ["shiva", "meditation", "ganga", "shiv_dhyan"]),
    ("Mount Kailash at dawn with a lone sadhu walking towards it", ["kailash", "shiva",
                                                                    "mountain", "journey"]),
    ("A Shivling with bel leaves, a brass lota pouring water over it", ["shivling", "shiva",
                                                                        "puja", "shiv_dhyan"]),
    ("Nandi the bull sitting calmly in front of a small temple", ["nandi", "shiva", "temple",
                                                                  "patience"]),
    ("A damru and rudraksha mala resting on a stone", ["damru", "mala", "shiva", "shiv_dhyan"]),
    ("Krishna playing the flute under a kadamba tree, a cow resting beside him",
     ["krishna", "flute", "gita", "peace"]),
    ("Krishna as charioteer guiding Arjuna on the battlefield of Kurukshetra, horses in motion",
     ["krishna", "arjuna", "chariot", "gita", "dharma"]),
    ("Arjuna kneeling with his bow lowered, Krishna's hand on his shoulder",
     ["arjuna", "krishna", "gita", "doubt", "courage"]),
    ("A peacock feather and a bansuri crossed on an open book", ["krishna", "flute",
                                                                 "peacock", "gita"]),
    ("Young Krishna with a pot of butter, playful smile", ["krishna", "butter", "joy", "gita"]),
    ("Radha and Krishna sitting by the Yamuna river at dusk", ["krishna", "radha", "love",
                                                               "gita", "river"]),
    ("Sant Kabir sitting at his handloom weaving, a simple hut behind him", ["kabir", "loom",
                                                                             "weaver", "work"]),
    ("A potter shaping clay on a wheel, pots drying in the sun", ["kabir", "potter", "clay",
                                                                  "matka", "patience"]),
    ("An ektara leaning against a tree trunk, a cloth bag beside it", ["kabir", "ektara",
                                                                       "music", "bhajan"]),
    ("A lone traveller with a staff on a long winding village road", ["journey", "kabir",
                                                                      "life_lesson", "path"]),
    ("A banyan tree with aerial roots and a small shrine at its base", ["tree", "banyan",
                                                                        "roots", "patience"]),
    ("A diya burning steadily on a window sill on a windy night", ["diya", "light", "hope",
                                                                   "jeevan_sutra"]),
    ("A row of diyas on temple steps by a river", ["diya", "diwali", "river", "ghat"]),
    ("Varanasi ghats at sunrise with boats and temple spires", ["ganga", "varanasi", "ghat",
                                                                "river", "morning"]),
    ("A lotus blooming in a muddy pond", ["lotus", "purity", "jeevan_sutra", "growth"]),
    ("Hands folded in namaste holding a single marigold", ["namaste", "gratitude", "flower",
                                                           "jeevan_sutra"]),
    ("A mother lighting a lamp in a courtyard tulsi plant", ["mother", "tulsi", "diya",
                                                             "family", "life_lesson"]),
    ("An old man and a child sitting on a bench under a tree", ["family", "wisdom", "elders",
                                                                "life_lesson"]),
    ("A small boat on a calm lake at dawn, one oar resting", ["calm", "lake", "boat",
                                                              "peace", "jeevan_sutra"]),
    ("A seedling growing through a crack in stone", ["growth", "patience", "hope",
                                                     "life_lesson"]),
    ("An hourglass with sand running, a dry leaf beside it", ["time", "patience",
                                                              "life_lesson"]),
    ("A temple bell hanging in a stone archway", ["temple", "bell", "prayer", "peace"]),
    ("A sadhu in meditation under a peepal tree", ["meditation", "sadhu", "peepal",
                                                   "jeevan_sutra"]),
    ("A cow and calf resting near a village well", ["cow", "village", "care", "life_lesson"]),
    ("Sunrise over hills with birds flying home", ["sun", "sunrise", "morning", "hope"]),
    ("A kalash with mango leaves and a coconut on a decorated stand", ["kalash", "puja",
                                                                       "auspicious"]),
]


def pack_status() -> list[tuple[str, list[str], bool]]:
    """Each pack subject and whether a drawing with its tags exists already."""
    made = library(include_disabled=True)
    out = []
    for subject, tags in PROMPT_PACK:
        have = any(d.source in ("made", "own") and len(set(tags) & d.tags) >= min(3, len(tags))
                   for d in made)
        out.append((subject, tags, have))
    return out


def full_prompt(subject: str) -> str:
    from instapost.studio.images import NO_TEXT

    return f"{subject}. {INK_STYLE} {NO_TEXT}"


def make_pack(limit: int = 40) -> tuple[int, list[str]]:
    """Make the missing prompt-pack drawings with Gemini. Returns (made, problems)."""
    from instapost.studio import images

    made, problems = 0, []
    for subject, tags, have in pack_status():
        if have or made >= limit:
            continue
        try:
            raw = images.GeminiProvider().generate(full_prompt(subject), portrait=False)
        except Exception as e:  # noqa: BLE001 - quota or billing: stop, report
            problems.append(f"stopped: {str(e)[:200]}")
            break
        if not is_drawing(raw):
            problems.append(f"not a clean line drawing: {subject[:60]}")
            continue
        _save_made(ink(raw), " ".join([subject, *tags]))
        made += 1
    return made, problems


def preview(d: Drawing) -> Path:
    """The drawing as it prints on the page's paper, small (for the dashboard)."""
    import hashlib

    folder = get_settings().state_dir / "drawings" / "previews"
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{d.path}{d.path.stat().st_mtime}".encode()).hexdigest()[:16]
    out = folder / f"{key}.jpg"
    if not out.exists():
        with Image.open(d.path) as img:
            line = ink(img)
        line.thumbnail((420, 300))
        paper = Image.new("RGB", line.size, (243, 234, 219))
        from PIL import ImageChops
        ImageChops.multiply(paper, line.convert("RGB")).save(out, quality=85)
    return out


def add_upload(name: str, data: bytes, tags: list[str]) -> tuple[Drawing | None, str]:
    """Save an uploaded drawing (cleaned to the house look) with its tags."""
    import io

    from instapost.render.music import safe_name

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001
        return None, "not a picture"
    if not is_drawing(img):
        return None, "looks like a photo, not a line drawing on white"
    folder = get_settings().state_dir / "drawings" / "own"
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(safe_name(name)).stem or "drawing"
    path = folder / f"{stem}.png"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}.png"
        n += 1
    ink(img).save(path, optimize=True)
    words = sorted({SYNONYMS.get(w, w) for w in tags if w} | _words(stem))
    path.with_suffix(".json").write_text(json.dumps({"tags": words}), encoding="utf-8")
    return Drawing(f"own:{path.stem}", path, set(words), "own"), ""
