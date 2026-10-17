"""Original, copyright-free ambience: a soft tanpura-like drone plus a breath of wind.

The buffer is built to be exactly periodic over the video length, so the reel loops
without an audible seam.
"""

import wave
from pathlib import Path

import numpy as np

SR = 44100
SA = 185.0  # roughly F#3, the middle Sa


def _pluck(n: int, f0: float, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    tone = np.zeros(n, np.float32)
    for h in range(1, 14):
        # "Jawari" buzz: upper harmonics swell a little after the attack.
        swell = 1 + 0.6 * (1 - np.exp(-t * 3)) * (h / 13)
        tone += (np.sin(2 * np.pi * f0 * h * t + rng.uniform(0, 6.28)) / h ** 0.9 * swell)
    env = (1 - np.exp(-t / 0.012)) * np.exp(-t / 2.8)
    return (tone * env).astype(np.float32)


def _periodic_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """Pink-ish noise that is periodic over n samples (built in the frequency domain)."""
    freqs = np.fft.rfftfreq(n, 1 / SR)
    spectrum = rng.normal(size=freqs.size) + 1j * rng.normal(size=freqs.size)
    spectrum /= np.maximum(freqs, 20) ** 0.75
    spectrum[freqs > 2500] *= 0.15
    noise = np.fft.irfft(spectrum, n)
    return (noise / np.abs(noise).max()).astype(np.float32)


def ambience(duration: float, seed: int = 0) -> np.ndarray:
    """Stereo float32 array, shape (n, 2), peak about 0.5."""
    rng = np.random.default_rng(seed)
    n = int(round(duration * SR))
    out = np.zeros((n, 2), np.float32)
    # Classic tuning: low Pa, Sa, Sa, low Sa (kharaj).
    strings = [SA * 0.75, SA, SA, SA * 0.5]
    cycles = max(1, round(duration / 3.2))
    cycle = n / cycles
    for c in range(cycles):
        for i, f0 in enumerate(strings):
            start = int(c * cycle + i * cycle / 4)
            note = _pluck(n, f0 * (1 + rng.normal(0, 0.0007)), rng) * (0.8 if i == 3 else 0.55)
            idx = (start + np.arange(n)) % n  # wrap the tail so the loop is seamless
            pan = 0.35 + 0.1 * i
            out[idx, 0] += note * (1 - pan)
            out[idx, 1] += note * pan
    wind = _periodic_noise(n, rng)
    out[:, 0] += wind * 0.05
    out[:, 1] += np.roll(wind, n // 3) * 0.05
    return out * (0.5 / np.abs(out).max())


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
