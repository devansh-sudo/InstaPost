"""Background music from your own royalty-free library (data/music/).

Put licence-free tracks there (Pixabay Music, YouTube Audio Library "no attribution"...),
named by what they are, e.g. `krishna-bansuri-calm.mp3`, `shiv-om-chant@12.mp3` (the `@12`
means: start 12 seconds in). Each post gets a track that fits its pillar, least recently used
first; the reel's own tanpura ambience stays underneath, very quietly.

Commercial songs (film bhajans, label releases) must not go here: Instagram and Facebook mute
or remove such reels and YouTube gives the earnings to the label. Trending songs are added in
the Instagram app instead (the "Trending song" button in Telegram).
"""

import re
import subprocess
from pathlib import Path

from instapost.config import get_settings
from instapost.models import Pillar
from instapost.studio.free_images import _load_usage, _mark_used, keywords

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}

# Words in file names that suit each pillar (file names are matched with these).
PILLAR_WORDS = {
    Pillar.SHIV_DHYAN: {"shiva", "om", "mahadev", "damru", "mantra", "rudra", "chant", "temple"},
    Pillar.GITA: {"krishna", "flute", "bansuri", "gita", "govinda", "radha"},
    Pillar.KABIR: {"kabir", "sitar", "ektara", "folk", "bhajan", "doha", "santoor"},
    Pillar.JEEVAN_SUTRA: {"calm", "peace", "piano", "meditation", "flute", "morning", "soft"},
    Pillar.LIFE_LESSON: {"calm", "peace", "piano", "meditation", "hope", "soft", "morning"},
}


def library_dir() -> Path:
    return get_settings().data_dir / "music"


def tracks() -> list[Path]:
    folder = library_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*")
                  if p.suffix.lower() in AUDIO_EXTENSIONS and not p.name.startswith("."))


def start_offset(path: Path) -> float:
    m = re.search(r"@(\d+(?:\.\d+)?)$", path.stem)
    return float(m.group(1)) if m else 0.0


def pick(pillar: Pillar | str) -> Path | None:
    """A track that fits the pillar (any track if none fits), least recently used first."""
    files = tracks()
    if not files or get_settings().music_mode != "library":
        return None
    wanted = PILLAR_WORDS.get(Pillar(pillar), set())
    usage = _load_usage()

    def fit(p: Path) -> int:
        return len(wanted & set(keywords(re.sub(r"@[\d.]+$", "", p.stem))))

    candidates = [p for p in files if fit(p)] or files
    best = min(candidates, key=lambda p: (usage.get(f"music:{p}", ""), -fit(p), p.name))
    _mark_used(f"music:{best}")
    return best


def mix(ambience_wav: Path, track: Path, duration: float, out_wav: Path) -> None:
    """Track (faded, loudness-levelled) over the quiet ambience, exactly `duration` long."""
    fade_out = min(1.2, duration / 4)
    graph = (
        f"[1:a]atrim=start={start_offset(track)}:duration={duration},asetpts=N/SR/TB,"
        f"aformat=sample_rates=44100:channel_layouts=stereo,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"afade=t=in:d=0.4,afade=t=out:st={duration - fade_out:.2f}:d={fade_out:.2f}[m];"
        f"[0:a]volume=0.22[a];"
        # The ambience sets the length, so the very end is never silent.
        f"[a][m]amix=inputs=2:duration=first:normalize=0,atrim=duration={duration}[out]"
    )
    # -stream_loop: a track shorter than the reel repeats instead of stopping.
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(ambience_wav),
           "-stream_loop", "-1", "-i", str(track),
           "-filter_complex", graph, "-map", "[out]", "-ar", "44100", "-ac", "2", str(out_wav)]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        raise RuntimeError(f"could not mix the music track {track.name}")


# --- trending sounds (posted in the Instagram app; never downloaded) ------------------------

TRENDING_FILE = "trending.yaml"

# Search words that find fitting sounds in Instagram's audio library, per pillar.
SEARCH_HINTS = {
    Pillar.SHIV_DHYAN: ["Shiv", "Om Namah Shivaya", "Mahadev", "Har Har Mahadev"],
    Pillar.GITA: ["Krishna flute", "Radhe Krishna", "Hare Krishna", "bansuri"],
    Pillar.KABIR: ["bhajan", "Kabir", "sufi", "ektara"],
    Pillar.JEEVAN_SUTRA: ["peaceful flute", "meditation", "spiritual"],
    Pillar.LIFE_LESSON: ["peaceful", "soft piano", "emotional flute"],
}


def trending_list() -> list[dict]:
    """Sounds you noted as trending (data/music/trending.yaml; see trending.example.yaml)."""
    import yaml

    path = library_dir() / TRENDING_FILE
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [t for t in doc.get("sounds", []) if t.get("name")]


def suggest_trending(pillar: Pillar | str, mood: str | None = None, n: int = 3) -> list[dict]:
    """Best matching trending sounds for a reel: same pillar first, then mood words."""
    pillar = Pillar(pillar)
    mood_words = set(keywords(mood or ""))

    def rank(t: dict) -> tuple:
        pillars = {str(p) for p in t.get("pillars") or []}
        words = set(keywords(" ".join([t.get("mood", ""), t.get("name", "")])))
        return (pillar.value in pillars or not pillars, len(mood_words & words))

    return sorted(trending_list(), key=rank, reverse=True)[:n]
