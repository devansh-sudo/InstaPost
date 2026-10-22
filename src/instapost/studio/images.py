"""Image providers. The image model never draws text; all text is rendered by our code.

Two images per piece: a background scene (portrait) and a pencil-sketch illustration.
`PlaceholderProvider` draws simple procedural images so the pipeline runs offline for free.
"""

import base64
import io
import math
import random
import re
import time
from typing import Protocol

import httpx
from PIL import Image, ImageDraw, ImageFilter

from instapost import budget
from instapost.config import get_settings

NO_TEXT = ("Absolutely no text, letters, numbers, captions, watermarks or logos anywhere "
           "in the image.")


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, portrait: bool) -> Image.Image: ...


class QuotaError(RuntimeError):
    """The image API refused because of quota or billing; retrying will not help."""


RETRY_STATUSES = {429, 500, 502, 503}
RETRY_WAITS = (10, 30, 60)  # seconds, used when the API does not say how long to wait
MAX_WAIT = 90


def _api_message(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
    except ValueError:
        return r.text[:300]
    msg = err.get("message") if isinstance(err, dict) else str(err)
    return " ".join(str(msg or r.text[:300]).split())[:500]


def _retry_after(r: httpx.Response) -> float | None:
    """Wait the API asks for: Retry-After header or Google's RetryInfo ("17s")."""
    if (h := r.headers.get("retry-after", "")).isdigit():
        return float(h)
    try:
        for d in r.json().get("error", {}).get("details", []):
            if m := re.fullmatch(r"([\d.]+)s", str(d.get("retryDelay", ""))):
                return float(m.group(1))
    except (ValueError, AttributeError):
        pass
    return None


def _hopeless(provider: str, r: httpx.Response, msg: str) -> str | None:
    """Explain a quota/billing refusal that waiting will not fix, else None."""
    low = msg.lower()
    if r.status_code == 429 and ("limit: 0" in low or "free_tier" in low or "per day" in low
                                 or "billing" in low):
        if provider == "gemini":
            return ("Gemini refused: this API key has no (or no remaining) image quota. "
                    "Image models need a paid key: aistudio.google.com -> API keys -> "
                    "set up billing for the key's project. Google says: " + msg)
        return f"{provider} refused for quota or billing. It says: {msg}"
    if provider == "openai" and r.status_code == 429 and "insufficient_quota" in r.text:
        return "OpenAI refused: no credit left (platform.openai.com -> Billing). " + msg
    return None


def post_with_retry(provider: str, url: str, *, sleep=time.sleep, **kw) -> httpx.Response:
    """POST with a few patient retries for rate limits and server hiccups.

    Errors carry the API's own explanation (never the key, which is sent in a header).
    """
    for attempt in range(len(RETRY_WAITS) + 1):
        r = httpx.post(url, **kw)
        if r.is_success:
            return r
        msg = _api_message(r)
        if reason := _hopeless(provider, r, msg):
            raise QuotaError(reason)
        if r.status_code not in RETRY_STATUSES or attempt == len(RETRY_WAITS):
            raise RuntimeError(f"{provider} image API error {r.status_code}: {msg}")
        sleep(min(_retry_after(r) or RETRY_WAITS[attempt], MAX_WAIT))
    raise AssertionError("unreachable")


def scene_prompt(scene: str) -> str:
    return (f"Photorealistic, softly out-of-focus background photo: {scene}. "
            f"Shallow depth of field, cinematic warm color grade, calm mood. {NO_TEXT}")


def illustration_prompt(subject: str, style: str) -> str:
    return f"{subject}. Style: {style}. Pure white background. {NO_TEXT}"


class GeminiProvider:
    name = "gemini"

    def generate(self, prompt: str, portrait: bool) -> Image.Image:
        s = get_settings()
        if not s.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        budget.ensure_room(budget.IMAGE_PRICES[self.name])
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{s.gemini_image_model}:generateContent")
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "9:16" if portrait else "4:3"},
            },
        }
        r = post_with_retry(self.name, url, json=body,
                            headers={"x-goog-api-key": s.gemini_api_key}, timeout=120)
        budget.record("image", s.gemini_image_model, budget.IMAGE_PRICES[self.name])
        for part in r.json()["candidates"][0]["content"]["parts"]:
            if "inlineData" in part:
                return Image.open(io.BytesIO(base64.b64decode(part["inlineData"]["data"])))
        raise RuntimeError("Gemini returned no image (possibly blocked by its safety filter)")


class OpenAIProvider:
    name = "openai"

    def generate(self, prompt: str, portrait: bool) -> Image.Image:
        s = get_settings()
        if not s.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        budget.ensure_room(budget.IMAGE_PRICES[self.name])
        r = post_with_retry(
            self.name, "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {s.openai_api_key}"},
            json={"model": s.openai_image_model, "prompt": prompt, "quality": "medium",
                  "size": "1024x1536" if portrait else "1536x1024"},
            timeout=180,
        )
        budget.record("image", s.openai_image_model, budget.IMAGE_PRICES[self.name])
        return Image.open(io.BytesIO(base64.b64decode(r.json()["data"][0]["b64_json"])))


class PlaceholderProvider:
    """Free offline stand-in: warm bokeh background and a simple ink drawing."""

    name = "placeholder"

    def generate(self, prompt: str, portrait: bool) -> Image.Image:
        rng = random.Random(prompt)
        if portrait:
            return _bokeh_scene(rng)
        return _sketch(rng)


def _bokeh_scene(rng: random.Random) -> Image.Image:
    w, h = 1080, 1920
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        px.line([(0, y), (w, y)], fill=(int(70 + 60 * (1 - t)), int(45 + 30 * (1 - t)),
                                         int(30 + 10 * t)))
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    for _ in range(40):
        x, y, r = rng.randint(0, w), rng.randint(0, h), rng.randint(20, 90)
        c = rng.choice([(255, 190, 110), (255, 160, 80), (240, 220, 170)])
        g.ellipse([x - r, y - r, x + r, y + r], fill=c)
    glow = glow.filter(ImageFilter.GaussianBlur(28))
    return Image.blend(img, glow, 0.35)


def _sketch(rng: random.Random) -> Image.Image:
    w, h = 1200, 900
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    ink = (60, 55, 50)
    # Mountains
    for base, peak_h in ((560, 260), (600, 200)):
        pts = [(0, base)]
        for x in range(0, w + 1, 120):
            pts.append((x, base - rng.randint(peak_h // 3, peak_h)))
        pts.append((w, base))
        d.line(pts, fill=ink, width=3)
    # Rock and a seated figure silhouette
    d.ellipse([480, 610, 760, 720], outline=ink, width=4)
    d.ellipse([585, 380, 655, 450], outline=ink, width=4)
    d.polygon([(560, 450), (680, 450), (720, 620), (520, 620)], outline=ink, width=4)
    # Moon
    d.ellipse([930, 120, 1010, 200], outline=ink, width=3)
    # Hatching
    for i in range(0, w, 14):
        d.line([(i, 760 + (i % 28)), (i + 40, 790 + (i % 28))], fill=(120, 115, 110), width=1)
    for a in range(0, 360, 30):
        x = 970 + 70 * math.cos(math.radians(a))
        y = 160 + 70 * math.sin(math.radians(a))
        d.point((x, y), fill=ink)
    return img


def get_provider(name: str | None = None) -> ImageProvider:
    name = name or get_settings().image_provider
    providers = {"gemini": GeminiProvider, "openai": OpenAIProvider,
                 "placeholder": PlaceholderProvider}
    if name not in providers:
        raise ValueError(f"unknown image provider {name!r}; choose from {sorted(providers)}")
    return providers[name]()
