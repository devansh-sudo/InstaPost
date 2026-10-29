"""Hindi voices for scene reels: Gemini text-to-speech, one call per line, so each line's
exact length is known (the subtitles and the speaker highlight follow it).

Uses the same GEMINI_API_KEY as the pictures. About $0.0003 per spoken second, so a scene
costs well under a cent in voice. Without a key (offline runs) there is no voice: the reel
then shows the lines as subtitles only, timed for reading.
"""

import base64

import numpy as np

from instapost import budget
from instapost.config import get_settings, require
from instapost.studio.images import post_with_retry

SR = 24000            # Gemini TTS returns 24 kHz, 16-bit, mono PCM
USD_PER_SECOND = 0.0003


class VoiceUnavailable(RuntimeError):
    pass


def available() -> bool:
    s = get_settings()
    return bool(s.gemini_api_key) and s.scene_voices


def speak(text: str, voice: str, delivery: str) -> np.ndarray:
    """Mono float32 samples at SR for one line."""
    s = get_settings()
    require("gemini_api_key")
    seconds = max(1.5, len(text.split()) / 2.4)
    budget.ensure_room(seconds * USD_PER_SECOND)
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{s.gemini_tts_model}:generateContent")
    body = {
        # The delivery note steers the voice and is not read out.
        "contents": [{"parts": [{"text": f"Say {delivery}, in Hindi: {text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    r = post_with_retry("gemini", url, json=body, headers={"x-goog-api-key": s.gemini_api_key},
                        timeout=120)
    try:
        part = next(p for p in r.json()["candidates"][0]["content"]["parts"]
                    if "inlineData" in p)
    except (KeyError, IndexError, StopIteration):
        raise VoiceUnavailable("Gemini returned no audio for a line") from None
    pcm = np.frombuffer(base64.b64decode(part["inlineData"]["data"]), "<i2")
    samples = pcm.astype(np.float32) / 32768
    budget.record("voice", s.gemini_tts_model, len(samples) / SR * USD_PER_SECOND,
                  seconds=round(len(samples) / SR, 2))
    return trim(samples)


def trim(samples: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    """Cut silence at both ends, keeping 60 ms so words are not clipped."""
    loud = np.flatnonzero(np.abs(samples) > threshold)
    if loud.size == 0:
        return samples
    pad = int(0.06 * SR)
    return samples[max(0, loud[0] - pad):loud[-1] + pad]
