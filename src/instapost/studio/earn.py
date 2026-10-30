"""Earning: affiliate links in captions and descriptions, and the sutra e-book's buy link.

Links you add on the dashboard (Earn) are matched to each post by its theme and added where a
link can be clicked: the YouTube description and the Facebook caption. Instagram captions
cannot hold clickable links, so Instagram posts point to your public link page (/links, put
its address in your Instagram bio). Every block carries a short disclosure, as the Advertising
Standards Council of India and Amazon's associate rules require.

Saved in state/earn/earn.json.
"""

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from instapost.config import get_settings

PILLARS = ("shiv_dhyan", "gita", "kabir", "jeevan_sutra", "life_lesson")
DISCLOSURE = ("(एफ़िलिएट लिंक: इससे ख़रीदने पर हमें छोटा कमीशन मिलता है, आपके लिए कीमत वही "
              "रहती है।)")
AMAZON_NOTE = "As an Amazon Associate I earn from qualifying purchases."
URL = re.compile(r"^https://[^\s<>\"']{4,500}$")


def _file() -> Path:
    d = get_settings().state_dir / "earn"
    d.mkdir(parents=True, exist_ok=True)
    return d / "earn.json"


def load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("links", [])
    data.setdefault("links_on", True)
    data.setdefault("ebook_url", "")
    data.setdefault("used", {})
    return data


def save(data: dict) -> None:
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_file())


def check_url(url: str) -> str:
    url = url.strip()
    if not URL.match(url):
        raise ValueError("the link must start with https:// and have no spaces")
    return url


def add_link(title: str, url: str, pillars: list[str], tags: list[str]) -> dict:
    title = " ".join(title.split())[:80]
    if not title:
        raise ValueError("give the link a short name, e.g. श्रीमद्भगवद्गीता (गीता प्रेस)")
    link = {"id": secrets.token_hex(3), "title": title, "url": check_url(url),
            "pillars": [p for p in pillars if p in PILLARS],
            "tags": sorted({t.strip().lower() for t in tags if t.strip()})[:12], "on": True,
            "added": datetime.now(UTC).isoformat()}
    data = load()
    data["links"].append(link)
    save(data)
    return link


def update(link_id: str, **fields) -> None:
    data = load()
    for link in data["links"]:
        if link["id"] == link_id:
            link.update(fields)
    save(data)


def remove(link_id: str) -> None:
    data = load()
    data["links"] = [x for x in data["links"] if x["id"] != link_id]
    save(data)


def set_options(links_on: bool | None = None, ebook_url: str | None = None) -> dict:
    data = load()
    if links_on is not None:
        data["links_on"] = links_on
    if ebook_url is not None:
        data["ebook_url"] = check_url(ebook_url) if ebook_url.strip() else ""
    save(data)
    return data


def public_page() -> str | None:
    """The address of the public link page (for the Instagram bio), if the dashboard is
    reachable from the internet."""
    base = get_settings().public_base_url
    return f"{str(base).rstrip('/')}/links" if base else None


def pick(pillar: str, text: str, data: dict | None = None) -> dict | None:
    """The link that fits a post best: same theme first, then shared words, then the one
    used least recently (so the same link is not in every post)."""
    data = data or load()
    words = set(re.findall(r"\w+", text.lower()))
    best, best_key = None, None
    for link in data["links"]:
        if not link.get("on"):
            continue
        fits = pillar in link["pillars"] or not link["pillars"]
        score = (2 if pillar in link["pillars"] else 0) + len(words & set(link["tags"]))
        if not fits and score == 0:
            continue
        key = (score, -_age(data["used"].get(link["id"])))
        if best_key is None or key > best_key:
            best, best_key = link, key
    return best


def _age(ts: str | None) -> float:
    return datetime.fromisoformat(ts).timestamp() if ts else 0.0


def block(platform: str, pillar: str, text: str, mark_used: bool = True) -> str:
    """What to add under a caption or description on this platform ("" = nothing)."""
    data = load()
    if not data["links_on"]:
        return ""
    if platform == "instagram":
        page = public_page()
        return ("\n\n📿 किताबें, पूजा सामग्री और ई-बुक: bio के लिंक में"
                if page and (data["links"] or data["ebook_url"]) else "")
    lines = []
    link = pick(pillar, text, data)
    if link is not None:
        lines.append(f"📿 {link['title']}: {link['url']}")
        if mark_used:
            data["used"][link["id"]] = datetime.now(UTC).isoformat()
            save(data)
    if data["ebook_url"]:
        lines.append(f"📖 जीवन सूत्र ई-बुक: {data['ebook_url']}")
    if link is None:
        return "\n\n" + "\n".join(lines) if lines else ""
    lines.append(DISCLOSURE)
    if "amazon." in link["url"] or "amzn." in link["url"]:
        lines.append(AMAZON_NOTE)
    return "\n\n" + "\n".join(lines)
