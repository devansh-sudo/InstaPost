"""Free image sources, used when the paid image API is unavailable (or chosen on purpose).

- `library`: your own pictures in data/images/ (for example photos downloaded from Unsplash).
    data/images/scenes/    portrait backgrounds (diya, temple, window light, mountains ...)
    data/images/subjects/  what the book-page drawing shows (shiva, krishna, kabir, lotus ...)
  Files are picked by matching their names to the post (name them like
  `shiva-meditating-himalaya.jpg`), least recently used first.
- `unsplash`: searches Unsplash with a free access key (UNSPLASH_ACCESS_KEY). Photographer credit
  is saved next to the post (image_credits.txt), as Unsplash's API guidelines ask.

Photos for the drawing are turned into a pencil sketch, so they match the book-page style.
"""

import io
import json
import random
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageFilter, ImageOps

from instapost.config import get_settings, require

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
UNSPLASH_API = "https://api.unsplash.com"

STOPWORDS = set("""a an the and or of in on at to with for from by under over into near its his her
their this that is are was were be soft softly warm cinematic photo photorealistic background
shallow depth field color grade calm mood style pure white simple pencil sketch drawing
illustration line art minimal detailed light lit out focus image scene view one two""".split())

# Different spellings people use, mapped to one word for matching file names.
SYNONYMS = {
    "shiv": "shiva", "mahadev": "shiva", "shankar": "shiva", "bholenath": "shiva",
    "shivling": "shivling", "shivalinga": "shivling", "lingam": "shivling",
    "krishn": "krishna", "kanha": "krishna", "govind": "krishna", "flute": "flute",
    "bansuri": "flute", "diyas": "diya", "lamp": "diya", "lamps": "diya", "candle": "diya",
    "temples": "temple", "mandir": "temple", "mountains": "mountain", "himalayas": "himalaya",
    "lotuses": "lotus", "rivers": "river", "ganga": "river", "ganges": "river",
}


class ImageUnavailable(RuntimeError):
    """This source has nothing suitable; the next source in the chain is tried."""


def subject_of(prompt: str) -> str:
    """The subject inside a prompt built by images.scene_prompt / illustration_prompt."""
    if "background photo:" in prompt:
        prompt = prompt.split("background photo:", 1)[1]
    return re.split(r"\.\s+(?:Style:|Shallow)", prompt, maxsplit=1)[0].strip(" .")


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", text.lower())
    out = []
    for w in words:
        w = SYNONYMS.get(w, w)
        if len(w) >= 3 and w not in STOPWORDS and w not in out:
            out.append(w)
    return out


def pencil_sketch(img: Image.Image) -> Image.Image:
    """Photo -> graphite sketch on white (colour dodge of the inverted, blurred grey image)."""
    gray_img = ImageOps.exif_transpose(img).convert("L")
    gray_img.thumbnail((1400, 1400))
    gray = np.asarray(gray_img, np.float32)
    radius = max(gray_img.size) / 90
    blur = np.asarray(ImageOps.invert(gray_img).filter(ImageFilter.GaussianBlur(radius)),
                      np.float32)
    dodge = np.clip(gray * 255.0 / np.maximum(255.0 - blur, 1.0), 0, 255)
    # Darken the strokes a little so they survive the multiply onto paper.
    lines = np.clip(255.0 - (255.0 - dodge) * 1.7, 0, 255).astype(np.uint8)
    return ImageOps.autocontrast(Image.fromarray(lines), cutoff=1).convert("RGB")


def looks_like_drawing(img: Image.Image) -> bool:
    """Mostly white and colourless: already a sketch, use as it is."""
    small = img.convert("RGB").resize((64, 64))
    hsv = np.asarray(small.convert("HSV"), np.float32)
    # A pencil drawing on (even beige) paper: light overall, almost no colour.
    return float(np.asarray(small.convert("L")).mean()) > 140 and float(hsv[..., 1].mean()) < 40


# --- remembering what was used, so the same picture doesn't repeat ----------------------------

def _usage_file() -> Path:
    return get_settings().state_dir / "image_usage.json"


def _load_usage() -> dict[str, str]:
    try:
        return json.loads(_usage_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _mark_used(key: str) -> None:
    usage = _load_usage()
    usage[key] = datetime.now(UTC).isoformat()
    if len(usage) > 2000:  # keep the file small: drop the oldest entries
        usage = dict(sorted(usage.items(), key=lambda kv: kv[1])[-1500:])
    path = _usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(usage), encoding="utf-8")
    tmp.replace(path)


def _finish(img: Image.Image, portrait: bool, credit: str | None) -> Image.Image:
    img = ImageOps.exif_transpose(img).convert("RGB")
    if not portrait and not looks_like_drawing(img):
        img = pencil_sketch(img)
    if credit:
        img.info["credit"] = credit
    return img


# --- providers -------------------------------------------------------------------------------

class LibraryProvider:
    """Pictures you put in data/images/scenes and data/images/subjects."""

    name = "library"

    def __init__(self, root: Path | None = None):
        self.root = root or get_settings().data_dir / "images"

    def files(self, portrait: bool) -> list[Path]:
        folder = self.root / ("scenes" if portrait else "subjects")
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.rglob("*")
                      if p.suffix.lower() in IMAGE_EXTENSIONS and not p.name.startswith("."))

    def generate(self, prompt: str, portrait: bool) -> Image.Image:
        files = self.files(portrait)
        kind = "scenes" if portrait else "subjects"
        if not files:
            raise ImageUnavailable(f"no pictures in {self.root / kind}/")
        wanted = set(keywords(subject_of(prompt)))
        usage = _load_usage()
        rng = random.Random()

        def matches(p: Path) -> int:
            return len(wanted & set(keywords(" ".join(
                p.relative_to(self.root).with_suffix("").parts))))

        # Only pictures that fit the post (if any fit), then the least recently used of those,
        # so a good match doesn't become the background of every post.
        candidates = [p for p in files if matches(p)] or files
        best = min(candidates, key=lambda p: (usage.get(f"library:{p}", ""), -matches(p),
                                              rng.random()))
        _mark_used(f"library:{best}")
        with Image.open(best) as img:
            img.load()
            return _finish(img, portrait, None)


class UnsplashProvider:
    """Unsplash search (free key at unsplash.com/developers; 50 searches an hour in demo mode)."""

    name = "unsplash"

    def _get(self, url: str, **params) -> httpx.Response:
        key = get_settings().unsplash_access_key
        r = httpx.get(url, params=params, timeout=30,
                      headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"})
        if r.status_code in (401, 403):
            raise ImageUnavailable(f"Unsplash refused ({r.status_code}): check UNSPLASH_ACCESS_KEY "
                                   "or wait for the hourly limit to reset")
        r.raise_for_status()
        return r

    def generate(self, prompt: str, portrait: bool) -> Image.Image:
        require("unsplash_access_key")
        words = keywords(subject_of(prompt))
        if not words:
            raise ImageUnavailable("nothing to search for")
        usage = _load_usage()
        # Narrow first, then broader if nothing (new) is found.
        for query in dict.fromkeys([" ".join(words[:5]), " ".join(words[:3]), words[0]]):
            results = self._get(f"{UNSPLASH_API}/search/photos", query=query, per_page=20,
                                content_filter="high",
                                orientation="portrait" if portrait else "landscape"
                                ).json().get("results", [])
            fresh = [p for p in results if f"unsplash:{p.get('id')}" not in usage]
            if fresh:
                photo = fresh[0]
                break
        else:
            raise ImageUnavailable(f"Unsplash found nothing new for {' '.join(words[:5])!r}")

        # Unsplash asks apps to report each download (counts for the photographer).
        if loc := photo.get("links", {}).get("download_location"):
            try:
                self._get(loc)
            except (ImageUnavailable, httpx.HTTPError):
                pass
        size = {"w": 1080, "h": 1920, "fit": "crop"} if portrait else {"w": 1400}
        img_r = httpx.get(photo["urls"]["raw"], params={**size, "fm": "jpg", "q": 85},
                          timeout=60, follow_redirects=True)
        img_r.raise_for_status()
        _mark_used(f"unsplash:{photo['id']}")
        user = photo.get("user", {})
        credit = (f"Photo by {user.get('name', 'unknown')} on Unsplash: "
                  f"{photo.get('links', {}).get('html', '')}")
        return _finish(Image.open(io.BytesIO(img_r.content)), portrait, credit)
