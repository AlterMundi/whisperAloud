#!/usr/bin/env python3
"""Regenerate the WHISP-77 "quiet remote speaker" test fixtures.

Not run by the test suite itself -- the generated WAVs are committed under
`tests/fixtures/vad_quiet_speech/` so tests don't depend on this script's
input being available. Only re-run this if the fixture needs to change.

Input: `bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/test_wavs/en.wav`,
a real (TTS) English speech sample used elsewhere in this repo for STT
benchmarking (see bench/README.md). That directory is not tracked by git
(see bench/.gitignore) and is only present on dev machines that have run the
benchmark suite -- this script is a record of how the fixture was produced,
not something CI or a fresh checkout can run.

Fixture design: `en.wav`'s first sentence ("Ask not what your country can do
for you.") stands in for a loud, local meeting participant (speaker A). Its
second sentence ("Ask what you can do for your country.") -- same speaker,
same language, different content/timing -- stands in for a quiet remote
participant (speaker B): run through `_room_coloration` (a synthetic room
reverb + 300-3400Hz band-limiting, standing in for "picked up by a laptop
mic from across the room, through speakers" rather than a close mic), then
attenuated and mixed with a noise floor. Using the same underlying speaker
for both avoids introducing an unrelated voice as a confound; what WHISP-77
is about is loudness/coloration, not speaker identity.

The first version of this fixture (plain attenuation + white noise, no
reverb/band-limiting) was too easy: it didn't catch that the first
`normalize_loudness` implementation (AGC-based) made Silero VAD retention
*worse* than doing nothing on a real recording (see stt/normalize.py's
docstring). The reverb+band-limit coloration reproduces that failure mode
in a small, fast, deterministic fixture.
"""
import json
import os
import wave

import numpy as np
import scipy.signal as ss

BENCH_WAV = (
    "/home/fede/REPOS/whisperAloud/bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/"
    "test_wavs/en.wav"
)
OUT_DIR = os.path.join(os.path.dirname(__file__), "vad_quiet_speech")

SAMPLE_RATE = 16000
SPLIT_SECONDS = 2.1  # boundary between the two sentences in en.wav
GAP_SECONDS = 2.5  # true silence between "speakers" -- long enough that Silero
                    # VAD (min_silence_duration_ms=2000 default) treats them as
                    # separate speech regions rather than bridging them.
ATTEN_DB = -15.0  # speaker B's attenuation relative to speaker A
NOISE_DB = -40.0  # background noise floor added under speaker B
RNG_SEED = 99
COLORATION_SEED = 123  # separate RNG stream for the synthetic room/speaker impulse response

# A second fixture isolating Silero VAD's own contribution to the bug (see
# quiet_speech_component.wav below), independent of any leading-speaker
# context: the same room-reverb + band-limiting coloration as speaker B
# above, applied standalone (no preceding loud speaker A in the file) and
# attenuated further, since without that preceding loud context Silero
# tolerates somewhat more attenuation before its default threshold drops it.
COMPONENT_ATTEN_DB = -15.0
COMPONENT_NOISE_DB = -45.0
COMPONENT_RNG_SEED = 2024


def _read_wav_mono16k(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        data = w.readframes(n)
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        arr = arr.reshape(-1, ch).mean(axis=1)
    if sr != SAMPLE_RATE:
        import scipy.signal as ss

        arr = ss.resample(arr, int(len(arr) * SAMPLE_RATE / sr)).astype(np.float32)
    return arr


def _room_coloration(signal: np.ndarray, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    """Cheap synthetic "captured over speakers in a room" coloration:
    convolve with an exponentially-decaying noise impulse response (fake
    room reverb, ~300ms RT60) and band-limit to roughly a small speaker's
    frequency response (300-3400 Hz, phone/speaker-ish)."""
    rt60_s = 0.15
    ir_len = int(sample_rate * rt60_s)
    decay = np.exp(-np.linspace(0, 6.0, ir_len))  # ~6 time constants over rt60
    impulse_response = (rng.normal(0, 1, size=ir_len) * decay).astype(np.float32)
    impulse_response /= np.max(np.abs(impulse_response)) + 1e-9
    reverberant = ss.fftconvolve(signal, impulse_response, mode="full")[: len(signal)]

    sos = ss.butter(4, [300, 3400], btype="bandpass", fs=sample_rate, output="sos")
    colored = ss.sosfiltfilt(sos, reverberant)

    peak = np.max(np.abs(colored))
    if peak > 0:
        colored = colored * (np.max(np.abs(signal)) / peak)  # restore original peak level
    return colored.astype(np.float32)


def _save_wav(audio: np.ndarray, path: str) -> None:
    audio = np.clip(audio, -1.0, 1.0)
    audio_int16 = (audio * 32767).astype(np.int16)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio_int16.tobytes())


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    full = _read_wav_mono16k(BENCH_WAV)
    split_sample = int(SPLIT_SECONDS * SAMPLE_RATE)
    a = full[:split_sample]
    b = full[split_sample:]

    # Plain attenuation + noise, no room coloration: this is the fixture used
    # by the full Transcriber.transcribe_file() content-recovery test, where
    # the actual *words* decoded matter, not just VAD coverage. Heavy
    # reverb/band-limiting (see quiet_speech_component.wav below) distorts
    # speaker B enough that even a correctly-selected VAD segment decodes to
    # garbled/hallucinated text, which would make that test about decoder
    # intelligibility rather than about the VAD-retention regression WHISP-77
    # is actually about.
    rng = np.random.default_rng(RNG_SEED)
    atten = 10 ** (ATTEN_DB / 20.0)
    b_quiet = b * atten
    noise = rng.normal(0, 1, size=len(b_quiet)).astype(np.float32) * (10 ** (NOISE_DB / 20.0))
    b_noisy = (b_quiet + noise).astype(np.float32)

    gap = np.zeros(int(SAMPLE_RATE * GAP_SECONDS), dtype=np.float32)
    mix = np.concatenate([a, gap, b_noisy]).astype(np.float32)

    # A faint, continuous room-tone noise floor under the *whole* recording
    # (real recordings are never pure digital silence between speakers).
    # Without this, faster-whisper's VAD-mode decoding -- which stitches
    # separate detected speech chunks together for one decode pass -- treats
    # the true-zero gap as a hard break and (independently of VAD retention)
    # fails to decode the second chunk; see stt/normalize.py's docstring and
    # test_vad_quiet_speech.py for the underlying faster-whisper behaviour.
    room_tone_rng = np.random.default_rng(RNG_SEED + 1)
    mix = mix + room_tone_rng.normal(0, 1, size=len(mix)).astype(np.float32) * (10 ** (-55.0 / 20.0))
    mix = np.clip(mix, -1.0, 1.0).astype(np.float32)

    a_start, a_end = 0, len(a)
    b_start, b_end = a_end + len(gap), a_end + len(gap) + len(b_noisy)

    _save_wav(mix, os.path.join(OUT_DIR, "two_speaker.wav"))

    # Isolates Silero VAD's own thresholding behaviour on real, room/speaker
    # colored speech, with no preceding loud speaker in the file at all (see
    # module docstring / test_vad_quiet_speech.py for why the attenuation
    # differs from two_speaker.wav's speaker B).
    rng_c = np.random.default_rng(COMPONENT_RNG_SEED)
    coloration_rng_c = np.random.default_rng(COLORATION_SEED + 1)
    b_colored_standalone = _room_coloration(b, SAMPLE_RATE, coloration_rng_c)
    comp_atten = 10 ** (COMPONENT_ATTEN_DB / 20.0)
    comp_quiet = b_colored_standalone * comp_atten
    comp_noise = rng_c.normal(0, 1, size=len(comp_quiet)).astype(np.float32) * (
        10 ** (COMPONENT_NOISE_DB / 20.0)
    )
    comp = (comp_quiet + comp_noise).astype(np.float32)
    _save_wav(comp, os.path.join(OUT_DIR, "quiet_speech_component.wav"))

    # Near-silence fixture: low-level noise only, no speech at all. Used to
    # check that the fix (normalization + more permissive VAD) does not
    # hallucinate speech out of noise.
    rng2 = np.random.default_rng(7)
    sil = rng2.normal(0, 1, size=int(SAMPLE_RATE * 3.0)).astype(np.float32) * (10 ** (-50.0 / 20.0))
    _save_wav(sil, os.path.join(OUT_DIR, "silence_only.wav"))

    meta = {
        "sample_rate": SAMPLE_RATE,
        "a_start": a_start,
        "a_end": a_end,
        "b_start": b_start,
        "b_end": b_end,
        "a_text": "Ask not what your country can do for you.",
        "b_text": "Ask what you can do for your country.",
        "b_attenuation_db": ATTEN_DB,
        "b_noise_floor_db": NOISE_DB,
        "quiet_speech_component_attenuation_db": COMPONENT_ATTEN_DB,
        "quiet_speech_component_noise_floor_db": COMPONENT_NOISE_DB,
        "quiet_speech_component_duration_s": len(b) / SAMPLE_RATE,
        "source": (
            "bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/test_wavs/en.wav, "
            f"split at {SPLIT_SECONDS}s: first half is speaker A at normal level, "
            "second half (speaker B) is plain-attenuated + noised for "
            "two_speaker.wav (used for full-pipeline content-recovery testing), "
            "or additionally run through a synthetic room-reverb + 300-3400Hz "
            "band-limiting (_room_coloration) for quiet_speech_component.wav "
            "(used for VAD-retention-only testing) -- see module docstring "
            "(WHISP-77)."
        ),
    }
    with open(os.path.join(OUT_DIR, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"wrote fixtures to {OUT_DIR}")


if __name__ == "__main__":
    main()
