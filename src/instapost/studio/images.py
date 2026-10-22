"""Image providers. The image model never draws text; all text is rendered by our code.

Two images per piece: a background scene (portrait) and a pencil-sketch illustration.
`PlaceholderProvider` draws simple procedural images so the pipeline runs offline for free.
"""

import base64
import io
import json
import math
import random
import re
import sys
import time
from datetime import UTC, datetime
from typing import Protocol

import httpx
from PIL import Image, ImageDraw, ImageFilter

from instapost import budget
from instapost.config import get_settings, require

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
    if r.status_code in (401, 403) or (r.status_code == 400 and "api key" in low):
        return f"{provider} rejected the API key ({r.status_code}): {msg}"
    if r.status_code == 429 and ("limit: 0" in low or "free_tier" in low or "per day" in low
                                 or "billing" in low):
        if provider == "gemini":
            if "limit: 0" in low or "free_tier" in low:
                models = sorted(set(re.findall(r"model: ([\w.\-]+)", msg)))
                return ("Gemini refused: this key is on Google's free tier, which allows 0 "
                        f"images ({', '.join(models) or 'image model'}). Fix: aistudio.google.com"
                        " -> API keys -> Set up billing for the key's project.")
            return ("Gemini refused: the image quota is used up. Check billing and limits at "
                    "aistudio.google.com. Google says: " + msg[:200])
        return f"{provider} refused for quota or billing. It says: {msg}"
    if provider == "openai" and r.status_code == 429 and "insufficient_quota" in r.text:
        return "OpenAI refused: no credit left (platform.openai.com -> Billing). " + msg
    return None


def _status_file():
    return get_settings().state_dir / "image_sources.json"


def _load_status() -> dict:
    try:
        return json.loads(_status_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_refusal(provider: str, reason: str | None) -> None:
    """Remember (or clear) a quota/key refusal, so `check` and the dashboard can show it."""
    status = _load_status()
    if reason is None and provider not in status:
        return
    if reason is None:
        status.pop(provider)
    else:
        status[provider] = {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                            "reason": reason}
    try:
        path = _status_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def recent_refusal(provider: str, hours: float = 24) -> str | None:
    """The reason a source was refused within `hours`, else None."""
    entry = _load_status().get(provider)
    if not entry:
        return None
    age = datetime.now(UTC) - datetime.fromisoformat(entry["at"])
    return entry["reason"] if age.total_seconds() < hours * 3600 else None


def post_with_retry(provider: str, url: str, *, sleep=time.sleep, **kw) -> httpx.Response:
    """POST with a few patient retries for rate limits and server hiccups.

    Errors carry the API's own explanation (never the key, which is sent in a header).
    """
    for attempt in range(len(RETRY_WAITS) + 1):
        r = httpx.post(url, **kw)
        if r.is_success:
            record_refusal(provider, None)
            return r
        msg = _api_message(r)
        if reason := _hopeless(provider, r, msg):
            record_refusal(provider, reason)
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
        require("gemini_api_key")
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
        require("openai_api_key")
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


# After a quota/billing refusal a source is skipped for this long (the answer won't change soon).
SKIP_AFTER_QUOTA_S = 3600
_skip_until: dict[str, float] = {}


class FallbackProvider:
    """Tries each source in turn: e.g. gemini, then unsplash, then your own pictures."""

    def __init__(self, providers: list[ImageProvider], log=None):
        self.providers = providers
        self.name = providers[0].name
        self.used: list[str] = []  # which source made each picture, for the item's records
        self.log = log or (lambda msg: print(msg, file=sys.stderr))

    def generate(self, prompt: str, portrait: bool) -> Image.Image:
        from instapost.config import SetupError
        from instapost.studio.free_images import ImageUnavailable

        reasons = []
        for p in self.providers:
            if _skip_until.get(p.name, 0) > time.monotonic():
                reasons.append(f"{p.name}: skipped after a quota refusal")
                continue
            try:
                img = p.generate(prompt, portrait)
            except QuotaError as e:
                _skip_until[p.name] = time.monotonic() + SKIP_AFTER_QUOTA_S
                reasons.append(f"{p.name}: {e}")
            except (SetupError, ImageUnavailable, RuntimeError, httpx.HTTPError, OSError) as e:
                reasons.append(f"{p.name}: {e}")
            else:
                if p is not self.providers[0]:
                    self.log(f"images: used {p.name} ({'; '.join(r[:120] for r in reasons)})")
                self.used.append(p.name)
                return img
        raise QuotaError("No image source worked:\n  " + "\n  ".join(reasons))


PROVIDERS = ("gemini", "openai", "unsplash", "library", "placeholder")


def _single(name: str) -> ImageProvider:
    from instapost.studio.free_images import LibraryProvider, UnsplashProvider

    providers = {"gemini": GeminiProvider, "openai": OpenAIProvider,
                 "unsplash": UnsplashProvider, "library": LibraryProvider,
                 "placeholder": PlaceholderProvider}
    if name not in providers:
        raise ValueError(f"unknown image provider {name!r}; choose from {list(PROVIDERS)}")
    return providers[name]()


def chain_names(name: str | None = None) -> list[str]:
    """The main source plus IMAGE_FALLBACK, without repeats. Placeholder never falls back."""
    s = get_settings()
    name = name or s.image_provider
    if name == "placeholder":
        return [name]
    extra = [n.strip() for n in s.image_fallback.split(",") if n.strip() not in ("", "none")]
    return list(dict.fromkeys([name, *extra]))


def get_provider(name: str | None = None) -> ImageProvider:
    names = chain_names(name)
    if len(names) == 1:
        return _single(names[0])
    return FallbackProvider([_single(n) for n in names])
