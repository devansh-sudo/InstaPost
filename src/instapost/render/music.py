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
    Pillar.SHIV_DHYAN: {"shiv", "shiva", "om", "mahadev", "shankar", "mahakal", "damru",
                        "mantra", "rudra", "chant", "temple"},
    Pillar.GITA: {"krishna", "flute", "bansuri", "gita", "govinda", "radha"},
    Pillar.KABIR: {"kabir", "sitar", "ektara", "folk", "bhajan", "doha", "santoor"},
    Pillar.JEEVAN_SUTRA: {"calm", "peace", "piano", "meditation", "flute", "morning", "soft"},
    Pillar.LIFE_LESSON: {"calm", "peace", "piano", "meditation", "hope", "soft", "morning"},
}


# Folder names that put every track inside them on one pillar (data/music/shiv/...).
FOLDER_PILLARS = {
    Pillar.SHIV_DHYAN: {"shiv", "shiva", "mahadev", "shankar", "bholenath", "mahakal", "rudra",
                        "shiv_dhyan", "om"},
    Pillar.GITA: {"krishna", "kanha", "gita", "radha", "radhe", "govinda", "bansuri"},
    Pillar.KABIR: {"kabir", "bhajan", "doha", "folk"},
    Pillar.JEEVAN_SUTRA: {"jeevan", "jeevan_sutra", "calm", "peace", "meditation", "life"},
    Pillar.LIFE_LESSON: {"life", "life_lesson", "lesson", "calm", "peace", "piano", "hope"},
}


def library_dir() -> Path:
    return get_settings().data_dir / "music"


def tracks() -> list[Path]:
    folder = library_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*")
                  if p.suffix.lower() in AUDIO_EXTENSIONS
                  and not any(part.startswith(".") for part in p.relative_to(folder).parts))


def rel(path: Path) -> str:
    """The track's name inside the library, e.g. "shiv/om-chant.mp3"."""
    try:
        return path.relative_to(library_dir()).as_posix()
    except ValueError:
        return path.name


def start_offset(path: Path) -> float:
    m = re.search(r"@(\d+(?:\.\d+)?)$", path.stem)
    return float(m.group(1)) if m else 0.0


def folder_pillars(path: Path) -> set[Pillar]:
    """Pillars named by the track's folders (empty when it sits loose in data/music)."""
    names = {part.lower().replace("-", "_").replace(" ", "_")
             for part in Path(rel(path)).parts[:-1]}
    return {p for p, words in FOLDER_PILLARS.items() if names & words}


def fit(path: Path, pillar: Pillar) -> int:
    """How well a track suits a pillar: its folder counts most, then words in the name."""
    if folder_pillars(path):
        return 10 if pillar in folder_pillars(path) else 0
    words = set(keywords(re.sub(r"@[\d.]+$", "", path.stem)))
    return len(PILLAR_WORDS.get(pillar, set()) & words)


def pillars_of(path: Path) -> list[Pillar]:
    return [p for p in Pillar if fit(path, p)]


def _usage_key(path: Path) -> str:
    return f"music:{rel(path)}"


def last_used(path: Path) -> str:
    return _load_usage().get(_usage_key(path), "")


def pick(pillar: Pillar | str) -> Path | None:
    """A track that fits the pillar (any track if none fits), least recently used first."""
    files = tracks()
    if not files or get_settings().music_mode != "library":
        return None
    pillar = Pillar(pillar)
    usage = _load_usage()
    candidates = [p for p in files if fit(p, pillar)] or files
    best = min(candidates, key=lambda p: (usage.get(_usage_key(p), ""), -fit(p, pillar), p.name))
    _mark_used(_usage_key(best))
    return best


def duration(path: Path) -> float | None:
    """Length in seconds (ffprobe), cached by file size and time."""
    import json
    import subprocess as sp

    cache_file = get_settings().state_dir / "music_durations.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    st = path.stat()
    key = f"{rel(path)}|{st.st_size}|{int(st.st_mtime)}"
    if key not in cache:
        out = sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
        try:
            cache[key] = round(float(out.stdout.strip()), 1)
        except ValueError:
            cache[key] = None
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    return cache[key]


SAFE_NAME = re.compile(r"[^A-Za-z0-9@._-]+")


def safe_name(name: str) -> str:
    """A file or folder name that is safe on disk: letters, digits, @ . _ - only."""
    clean = SAFE_NAME.sub("-", Path(name).name).strip("-.")
    return clean[:80] or "track"


def resolve(name: str) -> Path | None:
    """The library file for a name from the dashboard, never anything outside the library."""
    root = library_dir().resolve()
    path = (root / name).resolve()
    if root not in path.parents or path.suffix.lower() not in AUDIO_EXTENSIONS \
            or not path.is_file():
        return None
    return path


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
