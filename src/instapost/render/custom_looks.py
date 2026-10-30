"""Your own looks, made on the dashboard (Templates -> Create your own look).

A look is a background plus colours, saved once and then used for free on every post:
- gradient: two colours you pick, with an optional pattern (mandala, lights or rays);
- picture:  a picture you upload, or one Gemini paints once from your description (~$0.04).
Pictures are cropped a little differently for every post and darkened (or lightened) so the
text always reads.

Saved in state/looks/<id>.json (+ <id>.jpg for a picture). Ids start with "custom-".
"""

import io
import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from instapost.brand import Brand
from instapost.config import get_settings

KINDS = ("reel", "carousel", "story")
PATTERNS = ("none", "mandala", "lights", "rays")
MOTIONS = ("dust", "embers", "petals")
LIGHT_TEXT = {"ink": "#FFF4E4", "ink_soft": "#EBD9C2"}
DARK_TEXT = {"ink": "#2B2420", "ink_soft": "#5A4E45"}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _dir() -> Path:
    d = get_settings().state_dir / "looks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check_id(look_id: str) -> str:
    if not re.fullmatch(r"custom-[0-9a-f]{6}", look_id):
        raise KeyError(look_id)
    return look_id


def get(look_id: str) -> dict:
    try:
        return json.loads((_dir() / f"{_check_id(look_id)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError(look_id) from None


def all_looks() -> list[dict]:
    out = []
    for f in sorted(_dir().glob("custom-*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _colour(value: str, default: str) -> str:
    value = (value or "").strip()
    return value if HEX.match(value) else default


def create(name: str, kinds: list[str], *, base: str = "gradient", top: str = "#2A1B3D",
           bottom: str = "#C8743A", pattern: str = "none", text: str = "light",
           highlight: str = "#A4581B", dim: float = 0.35, motion: str = "dust",
           picture: Image.Image | None = None, prompt: str = "") -> dict:
    """Save a new look; returns it. `picture` is required when base is "picture"."""
    if base == "picture" and picture is None:
        raise ValueError("choose a picture: upload one, or describe one for the AI to paint")
    look_id = f"custom-{secrets.token_hex(3)}"
    cfg = {
        "id": look_id, "name": (name or "My look").strip()[:40],
        "kinds": [k for k in kinds if k in KINDS] or list(KINDS),
        "base": "picture" if base == "picture" else "gradient",
        "top": _colour(top, "#2A1B3D"), "bottom": _colour(bottom, "#C8743A"),
        "pattern": pattern if pattern in PATTERNS else "none",
        "text": "dark" if text == "dark" else "light",
        "highlight": _colour(highlight, "#A4581B"),
        "dim": float(min(0.75, max(0.0, dim))),
        "motion": motion if motion in MOTIONS else "dust",
        "prompt": prompt.strip()[:400], "created": datetime.now(UTC).isoformat(),
    }
    if picture is not None:
        img = ImageOps.exif_transpose(picture).convert("RGB")
        img.thumbnail((2000, 2000))
        img.save(_dir() / f"{look_id}.jpg", quality=90)
    (_dir() / f"{look_id}.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    return cfg


def delete(look_id: str) -> None:
    for f in (_dir() / f"{_check_id(look_id)}.json", _dir() / f"{look_id}.jpg"):
        f.unlink(missing_ok=True)


def picture_from_upload(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001 - any unreadable upload gets the same answer
        raise ValueError("that file is not a picture") from e
    if min(img.size) < 400:
        raise ValueError("the picture is too small (at least 400 px on each side)")
    return img


def paint(description: str) -> Image.Image:
    """One background from Gemini (about $0.04), kept for every post in this look."""
    from instapost.studio import images

    prompt = (f"{description.strip()}. Vertical 9:16 background for a calm Hindi spiritual "
              "quote: soft, uncluttered, gentle light, a quiet open area in the middle where "
              "text will go, painterly and respectful. No text, no letters, no words, no "
              "watermark, no logo, no frame.")
    return images.GeminiProvider().generate(prompt, portrait=True)


def brand_for(brand: Brand, look_id: str) -> Brand:
    cfg = get(look_id)
    text = LIGHT_TEXT if cfg["text"] == "light" else DARK_TEXT
    paper = cfg["top"] if cfg["base"] == "gradient" else ("#1A1410" if cfg["text"] == "light"
                                                          else "#F3EADB")
    colors = brand.colors.model_copy(update={
        **text, "paper": paper, "highlight": cfg["highlight"],
        "highlight_alpha": 0.72 if cfg["text"] == "light" else 0.6})
    return brand.model_copy(update={"colors": colors})


def backdrop(look_id: str, w: int, h: int, seed: int = 0) -> Image.Image:
    from instapost.render.backdrops import _bokeh, _grain, _mandala, _to_image
    from instapost.render.page import hex_rgb

    cfg = get(look_id)
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx / w - 0.5) * 1.3) ** 2 + (yy / h - 0.5) ** 2)
    if cfg["base"] == "picture" and (_dir() / f"{look_id}.jpg").exists():
        with Image.open(_dir() / f"{look_id}.jpg") as src:
            pic = src.convert("RGB")
        # A slightly different crop for every post, so the same picture never looks stale.
        z = rng.uniform(1.0, 1.12)
        pic = ImageOps.fit(pic, (int(w * z), int(h * z)), Image.Resampling.LANCZOS,
                           centering=(rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)))
        pic = ImageOps.fit(pic, (w, h), Image.Resampling.LANCZOS)
        arr = np.asarray(pic, np.float32)
    else:
        top, bottom = (np.array(hex_rgb(c), np.float32) for c in (cfg["top"], cfg["bottom"]))
        arr = top + (bottom - top) * (yy / h)[..., None]
        if cfg["pattern"] == "lights":
            arr += _bokeh(w, h, rng, 16, (255, 200, 130), (0.0, 1.0), (0.01, 0.035), (0.1, 0.3))
        elif cfg["pattern"] == "rays":
            ang = np.arctan2(yy + h * 0.05, xx - w / 2)
            rays = 0.5 + 0.5 * np.sin(ang * 20 + rng.uniform(0, 6.28))
            arr += (rays * np.clip(1 - yy / h * 1.4, 0, 1) * 22)[..., None]
        elif cfg["pattern"] == "mandala":
            size = int(min(w, h) * 1.15)
            ink = hex_rgb((LIGHT_TEXT if cfg["text"] == "light" else DARK_TEXT)["ink_soft"])
            m = _mandala(size, ink, seed)
            a = np.asarray(m.getchannel("A"), np.float32)[..., None] / 255 * 0.14
            x0, y0 = (w - size) // 2, int(h * 0.38 - size / 2)
            sx0, sy0 = max(0, -x0), max(0, -y0)
            region = arr[max(0, y0):y0 + size, max(0, x0):x0 + size]
            sub = a[sy0:sy0 + region.shape[0], sx0:sx0 + region.shape[1]]
            region += (np.array(ink, np.float32) - region) * sub
    # Darken (light text) or lighten (dark text) so the words always read, more in the middle.
    target = 0.0 if cfg["text"] == "light" else 255.0
    k = cfg["dim"] * (1.0 - 0.25 * np.clip(r * 1.6, 0, 1))
    arr = arr + (target - arr) * k[..., None]
    arr *= np.clip(1.06 - r ** 2 * 0.5, 0.0, 1.0)[..., None]
    return _to_image(_grain(arr, rng, 2.0))


def motion_of(look_id: str) -> str | None:
    try:
        m = get(look_id)["motion"]
    except KeyError:
        return None
    return None if m == "dust" else m
