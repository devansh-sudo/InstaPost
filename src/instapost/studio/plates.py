"""Book photos ("plates") for reels: where they come from, which ones are good, which is next.

Sources:
- your own photos: dashboard -> Backgrounds -> Upload (saved in state/plates/own/), or files
  in data/plates/ (a `name.json` next to `name.jpg` with {"quad": [[x,y] x4]} sets the
  page corners by hand: top-left, top-right, bottom-right, bottom-left);
- Unsplash: searched automatically when fewer than MIN_READY photos are ready.

Every photo is checked (page found, page blank, page big enough to read once cropped to
9:16). Photos the checker is sure about are used straight away; the others wait on the
Backgrounds page, where you approve them or click the four page corners yourself.
"""

import io
import json
import logging
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from PIL import Image

from instapost.config import get_settings
from instapost.render import plate as plate_mod
from instapost.studio.free_images import UNSPLASH_API, _mark_used

log = logging.getLogger("instapost.plates")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_READY = 8          # search Unsplash for more when fewer photos than this are ready
FETCH_EVERY = timedelta(hours=6)
MAX_DOWNLOADS = 12     # per search, to stay far inside Unsplash's 50 requests an hour
CONFIDENT_FILL = 0.93  # auto-found pages this clean are used without review
QUERIES = [
    "hands holding open book blank pages",
    "open book blank page cozy",
    "open blank notebook hands",
    "reading open book pov",
    "open journal blank pages table",
    "blank open book warm light",
]


def _dir() -> Path:
    d = get_settings().state_dir / "plates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_file() -> Path:
    return _dir() / "index.json"


def load_index() -> dict:
    try:
        data = json.loads(_index_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("plates", {})
    data.setdefault("skipped", [])
    data.setdefault("meta", {})
    return data


def save_index(data: dict) -> None:
    tmp = _index_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_index_file())


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _entry(file: Path, source: str, credit: str = "") -> dict:
    """Check a photo and describe it for the index."""
    with Image.open(file) as img:
        photo = img.convert("RGB")
    entry = {"file": str(file), "source": source, "credit": credit, "added": _now(),
             "size": list(photo.size)}
    sidecar = file.with_suffix(".json")
    if sidecar.exists():
        quad = json.loads(sidecar.read_text(encoding="utf-8")).get("quad")
        entry.update(quad=quad, auto=False)
    else:
        det = plate_mod.detect_page(photo)
        if det is None:
            entry.update(quad=None, auto=True, status="off", reason="no page found")
            return entry
        entry.update(quad=[list(p) for p in det.quad], auto=True,
                     detail={"fill": det.fill, "blank": det.blank, "aspect": det.aspect,
                             "spine_cut": det.spine_cut})
        if not det.ok:
            entry.update(status="off", reason=det.reason)
            return entry
    if plate_mod.fit_to_frame(photo, [tuple(p) for p in entry["quad"]]) is None:
        entry.update(status="off", reason="page too small or too close to the edge for 9:16")
        return entry
    detail = entry.get("detail", {})
    # Sure enough to use without asking: corners given by hand, or one clean page found
    # without having to cut off a facing page (a wrong cut would look believable but off).
    confident = not entry["auto"] or (detail.get("fill", 0) >= CONFIDENT_FILL
                                      and not detail.get("spine_cut"))
    entry.update(status="on" if confident else "review", reason="" if confident
                 else "check the page outline")
    return entry


def sync_library() -> dict:
    """Add new photos from data/plates/ and state/plates/own/ to the index."""
    data = load_index()
    known = {p["file"] for p in data["plates"].values()}
    folders = [(get_settings().data_dir / "plates", "library"), (_dir() / "own", "own")]
    for folder, source in folders:
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() not in IMAGE_EXTENSIONS or str(f) in known:
                continue
            try:
                data["plates"][f"{source}-{f.stem}"] = _entry(f, source)
            except OSError as e:
                log.warning("cannot read %s: %s", f, e)
    save_index(data)
    return data


def set_status(plate_id: str, status: str, quad: list | None = None) -> dict:
    data = load_index()
    entry = data["plates"][plate_id]
    if quad is not None:
        entry.update(quad=quad, auto=False, reason="")
        _sample_file(plate_id).unlink(missing_ok=True)
    entry["status"] = status
    if status == "on":
        entry["reason"] = ""
    save_index(data)
    return entry


def flag(plate_id: str, reason: str) -> None:
    """Send a photo back for a check (the image check found its page outline wrong)."""
    data = load_index()
    if plate_id in data["plates"]:
        data["plates"][plate_id].update(status="review", reason=f"image check: {reason}"[:200])
        save_index(data)


def ready(data: dict | None = None) -> list[tuple[str, dict]]:
    data = data or load_index()
    return [(k, v) for k, v in data["plates"].items() if v.get("status") == "on"]


def load(plate_id: str, entry: dict, seed: int = 0) -> plate_mod.Plate | None:
    try:
        with Image.open(entry["file"]) as img:
            photo = img.convert("RGB")
    except OSError:
        return None
    return plate_mod.prepare(plate_id, photo, [tuple(p) for p in entry["quad"]],
                             entry.get("credit", ""), seed)


def candidates(seed: int = 0, exclude: set[str] | None = None, search: bool = True):
    """Ready plates, best first (learner) with the most recently used last; searches
    Unsplash first when too few are ready."""
    data = sync_library()
    if search and len(ready(data)) < MIN_READY:
        try:
            fetch_unsplash()
        except Exception as e:  # noqa: BLE001 - a failed search must not stop a post
            log.warning("Unsplash book photos: %s", e)
        data = load_index()
    from instapost.learn import assets

    entries = dict(ready(data))
    # Photos that did well come up more often; the last few used go to the back.
    for plate_id in assets.order("photo", sorted(entries), random.Random(seed)):
        entry = entries[plate_id]
        if exclude and plate_id in exclude:
            continue
        p = load(plate_id, entry, seed)
        if p is None:
            set_status(plate_id, "off")
            continue
        yield p


def mark_used(plate_id: str) -> None:
    _mark_used(f"plate:{plate_id}")


# --- Unsplash ---------------------------------------------------------------------------------

def fetch_unsplash(force: bool = False) -> int:
    """One search on Unsplash for book photos; returns how many new usable ones were added."""
    key = get_settings().unsplash_access_key
    if not key:
        return 0
    data = load_index()
    meta = data["meta"]
    last = meta.get("last_fetch")
    if not force and last and datetime.fromisoformat(last) > datetime.now(UTC) - FETCH_EVERY:
        return 0
    n = int(meta.get("query_n", 0))
    query, page = QUERIES[n % len(QUERIES)], 1 + n // len(QUERIES)
    meta.update(last_fetch=_now(), query_n=n + 1)
    save_index(data)

    headers = {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}
    r = httpx.get(f"{UNSPLASH_API}/search/photos", headers=headers, timeout=30,
                  params={"query": query, "per_page": 20, "page": page,
                          "orientation": "portrait", "content_filter": "high"})
    r.raise_for_status()
    added = downloads = 0
    seen = set(data["skipped"]) | {k.removeprefix("unsplash-") for k in data["plates"]}
    for photo in r.json().get("results", []):
        pid = photo.get("id")
        if not pid or pid in seen or downloads >= MAX_DOWNLOADS:
            continue
        downloads += 1
        img_r = httpx.get(photo["urls"]["raw"], params={"w": 1600, "fm": "jpg", "q": 88},
                          timeout=60, follow_redirects=True)
        if img_r.status_code != 200:
            continue
        file = _dir() / f"unsplash-{pid}.jpg"
        Image.open(io.BytesIO(img_r.content)).convert("RGB").save(file, quality=92)
        user = photo.get("user", {})
        credit = (f"Photo by {user.get('name', 'unknown')} on Unsplash: "
                  f"{photo.get('links', {}).get('html', '')}")
        entry = _entry(file, "unsplash", credit)
        data = load_index()
        if entry.get("status") == "off":
            data["skipped"].append(pid)  # never downloaded again
            file.unlink(missing_ok=True)
        else:
            data["plates"][f"unsplash-{pid}"] = entry
            added += 1
            # Unsplash asks apps to report each photo they use (counts for the photographer).
            if loc := photo.get("links", {}).get("download_location"):
                try:
                    httpx.get(loc, headers=headers, timeout=15)
                except httpx.HTTPError:
                    pass
        save_index(data)
    log.info("Unsplash book photos: %r page %s -> %s new", query, page, added)
    return added


# --- previews for the dashboard ---------------------------------------------------------------

def _sample_file(plate_id: str) -> Path:
    d = _dir() / "samples"
    d.mkdir(exist_ok=True)
    return d / f"{plate_id}.jpg"


def preview(plate_id: str) -> Path | None:
    """The photo, small, with the found page outlined (for checking the outline)."""
    from PIL import ImageDraw

    entry = load_index()["plates"].get(plate_id)
    if not entry:
        return None
    out = _dir() / "previews" / f"{plate_id}.jpg"
    if out.exists() and out.stat().st_mtime >= _index_file().stat().st_mtime:
        return out
    out.parent.mkdir(exist_ok=True)
    with Image.open(entry["file"]) as img:
        photo = img.convert("RGB")
    k = 540 / photo.width
    small = photo.resize((540, round(photo.height * k)))
    if entry.get("quad"):
        quad = [(x * k, y * k) for x, y in entry["quad"]]
        ImageDraw.Draw(small).line([*quad, quad[0]], fill=(255, 90, 40), width=4)
    small.save(out, quality=85)
    return out


def sample(plate_id: str) -> Path | None:
    """What a post looks like on this photo (a fixed example page), made once."""
    out = _sample_file(plate_id)
    if out.exists():
        return out
    entry = load_index()["plates"].get(plate_id)
    if not entry or not entry.get("quad"):
        return None
    p = load(plate_id, entry, seed=7)
    if p is None:
        return None
    from instapost.brand import load_brand
    from instapost.render.page import PageContent, render_page

    brand = load_brand()
    page = render_page(brand, PageContent(
        header=brand.header_text, page_number=108,
        hook_lines=["जिसे बदल नहीं सकते,", "उसे स्वीकार करना ही शांति है।"],
        body="जो हाथ में है उसे पूरे मन से करें, बाकी को शांत मन से स्वीकार करें।",
        reference_label="श्रीमद्भगवद्गीता 2.47",
        reference_text="कर्मण्येवाधिकारस्ते मा फलेषु कदाचन।",
        footer=brand.printed_handle, illustration=None), 7)
    comp = plate_mod.compose_on_plate(brand, p, page, 7)
    comp.still.resize((540, 960)).save(out, quality=86)
    return out
