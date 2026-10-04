"""Field-failure regression on real speech (docs/design/input-gain-loop.md).

Real speech (two_speaker.wav, speaker A, the loud and clean half) rescaled to
the field recording's level (-47 dBFS speech over a -66 dBFS white-noise
floor), run through the UNCHANGED NoiseGate (default -40 dBFS open / -46
close). Without input normalization the gate mutes essentially all of it.
That's the WHISP-93 field report. With normalization in front, the speech
passes and the gate still closes in the noise-only lead-in and tail.
"""

from pathlib import Path

import numpy as np
import pytest

from whisper_aloud.audio.audio_processor import NoiseGate
from whisper_aloud.audio.input_gain import InputNormalizer
from whisper_aloud.config import AudioProcessingConfig, InputGainConfig

FIXTURE = Path(__file__).parent / "fixtures" / "vad_quiet_speech" / "two_speaker.wav"
SR = 16000
FRAME = 160


def _db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-20)


def _frame_rms(y):
    n = len(y) // FRAME * FRAME
    return np.sqrt(np.mean(np.asarray(y[:n], dtype=np.float64).reshape(-1, FRAME) ** 2, axis=1))


@pytest.fixture(scope="module")
def field_case():
    sf = pytest.importorskip("soundfile")
    audio, rate = sf.read(str(FIXTURE), dtype="float32")
    assert rate == SR
    speech = audio[0:33600].astype(np.float64)  # speaker A (see metadata.json)
    speech *= 10 ** ((-47.0 - _db(speech)) / 20)
    rng = np.random.default_rng(0)
    pad = SR // 2
    mix = rng.standard_normal(len(speech) + 2 * pad) * 10 ** (-66.0 / 20)
    mix[pad : pad + len(speech)] += speech
    clean = np.zeros(len(mix))
    clean[pad : pad + len(speech)] = speech
    level = 20 * np.log10(_frame_rms(clean) + 1e-20)
    active = level > _db(speech) - 20  # frames carrying real speech energy
    noise_only = np.zeros(len(level), dtype=bool)
    edge = pad // FRAME - 15  # stay clear of the gate's hold/release around speech
    noise_only[:edge] = True
    noise_only[-edge:] = True
    return mix.astype(np.float32), active, noise_only


def _pass_ratio(gated, before, frames):
    ratio = _frame_rms(gated) / np.maximum(_frame_rms(before), 1e-20)
    return float(np.mean(ratio[frames] > 0.5))


def _gate():
    cfg = AudioProcessingConfig()  # the chain's own, unchanged default
    return NoiseGate(threshold_db=cfg.noise_gate_threshold_db)


def test_without_normalization_the_gate_mutes_the_speech(field_case):
    mix, active, _ = field_case
    gated = _gate().process(mix, SR)
    assert _pass_ratio(gated, mix, active) < 0.1


def test_with_normalization_speech_passes_and_gate_still_closes(field_case):
    mix, active, noise_only = field_case
    norm = InputNormalizer(InputGainConfig())
    normalized = norm.normalize_buffer(mix)
    gated = _gate().process(normalized, SR)
    assert _pass_ratio(gated, normalized, active) >= 0.95
    assert _pass_ratio(gated, normalized, noise_only) <= 0.05
    assert norm.stats.limited_by == "noise"
