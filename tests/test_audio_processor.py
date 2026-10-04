"""Tests for audio processing."""

import numpy as np
import pytest

from whisper_aloud.audio import AudioProcessor


def test_normalize():
    """Test audio normalization."""
    audio = np.array([0.5, -0.5, 0.25], dtype=np.float32)
    normalized = AudioProcessor.normalize(audio, target_level=0.95)

    assert np.max(np.abs(normalized)) == pytest.approx(0.95, abs=0.01)


def test_normalize_empty():
    """Test normalization with empty array."""
    audio = np.array([], dtype=np.float32)
    normalized = AudioProcessor.normalize(audio)
    assert len(normalized) == 0


def test_stereo_to_mono():
    """Test stereo to mono conversion."""
    stereo = np.array([[0.5, 0.3], [0.2, 0.4]], dtype=np.float32)
    mono = AudioProcessor.stereo_to_mono(stereo)

    assert mono.ndim == 1
    assert len(mono) == 2
    assert mono[0] == pytest.approx(0.4, abs=0.01)  # Average of 0.5 and 0.3


def test_stereo_to_mono_already_mono():
    """Test stereo to mono with already mono audio."""
    mono = np.array([0.5, 0.3, 0.2], dtype=np.float32)
    result = AudioProcessor.stereo_to_mono(mono)

    assert np.array_equal(result, mono)


def test_resample():
    """Test audio resampling."""
    audio = np.sin(2 * np.pi * 440 * np.linspace(0, 1, 44100)).astype(np.float32)
    resampled = AudioProcessor.resample(audio, 44100, 16000)

    expected_length = int(len(audio) * 16000 / 44100)
    assert len(resampled) == expected_length


def test_resample_same_rate():
    """Test resampling with same input/output rate."""
    audio = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    resampled = AudioProcessor.resample(audio, 16000, 16000)

    assert np.array_equal(resampled, audio)


def test_detect_voice_activity():
    """Test VAD."""
    # Silent audio
    silent = np.zeros(16000, dtype=np.float32)
    activity = AudioProcessor.detect_voice_activity(silent, threshold=0.02)
    assert not activity.any()

    # Loud audio
    loud = np.ones(16000, dtype=np.float32) * 0.5
    activity = AudioProcessor.detect_voice_activity(loud, threshold=0.02)
    assert activity.any()


def test_detect_voice_activity_empty():
    """Test VAD with empty audio."""
    empty = np.array([], dtype=np.float32)
    activity = AudioProcessor.detect_voice_activity(empty)
    assert len(activity) == 0


def test_trim_silence():
    """Test silence trimming."""
    # Create audio with silence at start and end
    silence = np.zeros(8000, dtype=np.float32)  # 0.5s silence
    voice = np.ones(8000, dtype=np.float32) * 0.1  # 0.5s voice
    audio = np.concatenate([silence, voice, silence])

    trimmed, start, end = AudioProcessor.trim_silence(audio, 16000)

    # Should trim most silence, keep some padding
    assert len(trimmed) < len(audio)
    assert start >= 0
    assert end <= len(audio)


def test_trim_silence_no_voice():
    """Test trimming when no voice is detected."""
    silent = np.zeros(16000, dtype=np.float32)
    trimmed, start, end = AudioProcessor.trim_silence(silent, 16000)

    # Should return original with start=0, end=len
    assert len(trimmed) == len(silent)
    assert start == 0
    assert end == len(silent)


def test_process_recording():
    """Test complete audio processing pipeline."""
    # Create stereo audio at 44.1kHz (samples x channels)
    audio = np.random.randn(44100, 2).astype(np.float32) * 0.1

    processed = AudioProcessor.process_recording(
        audio,
        sample_rate=44100,
        target_rate=16000,
        normalize=True,
        trim_silence_enabled=False
    )

    # Should be mono, resampled, normalized
    assert processed.ndim == 1
    assert len(processed) < 44100  # Resampled down from 44100 samples
    assert np.max(np.abs(processed)) <= 1.0


def test_process_recording_empty():
    """Test processing pipeline with empty audio."""
    empty = np.array([], dtype=np.float32)
    processed = AudioProcessor.process_recording(empty, 16000)

    assert len(processed) == 0


# ── H1/H2: AGC vectorization tests ───────────────────────────────────────────

def test_agc_no_per_sample_loop():
    """AGC.process must not iterate sample-by-sample (for i in range(n))."""
    import inspect
    from whisper_aloud.audio.audio_processor import AGC
    src = inspect.getsource(AGC.process)
    assert 'for i in range' not in src, "Per-sample Python loop detected in AGC.process"


def test_agc_amplifies_quiet_signal():
    """AGC should boost a signal that is below target."""
    import numpy as np
    from whisper_aloud.audio.audio_processor import AGC
    sr = 16000
    agc = AGC(target_db=-18.0, max_gain_db=20.0)
    audio = np.ones(sr, dtype=np.float32) * 0.01  # very quiet
    result = agc.process(audio, sr)
    assert np.abs(result).mean() > np.abs(audio).mean(), "AGC should boost quiet signal"
    assert np.all(np.isfinite(result)), "AGC output must be finite"


# ── M1: NoiseGate hysteresis + hold + 25ms RMS window ────────────────────────

def test_noise_gate_rms_window_25ms():
    """NoiseGate RMS window must be at least 25ms (no more 2ms constant)."""
    import inspect
    from whisper_aloud.audio.audio_processor import NoiseGate
    src = inspect.getsource(NoiseGate.process)
    assert "0.002" not in src, "Old 2ms RMS window constant still present"


def test_noise_gate_hysteresis_prevents_chatter():
    """Gate must stay open when signal drops to mid-range (between close and open thresholds)."""
    import numpy as np
    from whisper_aloud.audio.audio_processor import NoiseGate
    sr = 16000
    # open_threshold = 10^(-30/20) = 0.0316; close_threshold (6dB below) = 0.0158
    gate = NoiseGate(threshold_db=-30.0, hysteresis_db=6.0)
    loud = np.ones(sr // 8, dtype=np.float32) * 0.1    # well above open threshold
    mid = np.ones(sr // 4, dtype=np.float32) * 0.022   # above close (0.0158) but below open (0.0316)
    audio = np.concatenate([loud, mid])
    result = gate.process(audio, sr)
    # Gate should stay OPEN during mid section — output ≥ 50% of input
    mid_start = sr // 8
    mid_end = mid_start + sr // 4
    assert np.abs(result[mid_start:mid_end]).mean() > np.abs(mid).mean() * 0.5, (
        "Gate closed during mid section (chatter): hysteresis not working"
    )


def test_noise_gate_hold_keeps_gate_open():
    """Gate must stay open for hold_ms after signal drops below close threshold."""
    import numpy as np
    from whisper_aloud.audio.audio_processor import NoiseGate
    sr = 16000
    hold_ms = 80.0
    gate = NoiseGate(threshold_db=-30.0, hold_ms=hold_ms, hysteresis_db=0.0)
    loud = np.ones(sr // 8, dtype=np.float32) * 0.1   # above threshold
    silent = np.zeros(sr // 2, dtype=np.float32)        # below threshold
    audio = np.concatenate([loud, silent])
    result = gate.process(audio, sr)
    # First hold_ms of silence: gate open → output ≈ input (zero = zero, but gate is open)
    # After hold: gate releases → output fades toward zero
    # Since silent input = 0, both open and closed give ~0 output.
    # Instead verify: end of audio (long after hold) has same or less amplitude than beginning of silence
    loud_end = sr // 8
    hold_samples = int(sr * hold_ms / 1000.0)
    after_hold_start = loud_end + hold_samples + int(sr * 0.1)  # 100ms after hold ends
    if after_hold_start < len(result):
        # After hold, envelope should be decaying (near zero for silent input)
        assert result[after_hold_start:].max() < 0.01


# ── M2: AGC default max_gain_db ──────────────────────────────────────────────

def test_agc_default_max_gain_is_20db():
    """AGC default max_gain must correspond to 20dB (was 30dB = 31.62x)."""
    import numpy as np
    from whisper_aloud.audio.audio_processor import AGC
    # 20dB = 10.0x linear; 30dB = 31.62x — verify we're at the lower cap
    assert AGC().max_gain == pytest.approx(10 ** (20.0 / 20.0), rel=1e-3)


def test_processing_config_default_max_gain_is_20db():
    """AudioProcessingConfig default agc_max_gain_db must be 20.0."""
    from whisper_aloud.config import AudioProcessingConfig
    assert AudioProcessingConfig().agc_max_gain_db == 20.0


# ── M4: PeakLimiter soft-knee ─────────────────────────────────────────────────

def test_peak_limiter_ceiling_not_exceeded():
    """Output must never exceed ceiling (−1 dBFS ≈ 0.891 linear)."""
    import numpy as np
    from whisper_aloud.audio.audio_processor import PeakLimiter
    limiter = PeakLimiter(ceiling_db=-1.0)
    audio = np.ones(1000, dtype=np.float32) * 2.0  # 2x over ceiling
    result = limiter.process(audio)
    ceiling_linear = 10 ** (-1.0 / 20.0)
    assert np.max(np.abs(result)) <= ceiling_linear + 1e-4


def test_peak_limiter_soft_knee_no_plateau():
    """Soft-knee limiter must not produce a plateau (hard-clip signature)."""
    import numpy as np
    from whisper_aloud.audio.audio_processor import PeakLimiter
    limiter = PeakLimiter(ceiling_db=-1.0)
    # Linearly increasing signal through and above ceiling
    audio = np.linspace(0.7, 1.2, 2000, dtype=np.float32)
    result = limiter.process(audio)
    # Hard clip: diff == 0 in plateau; soft knee: diff always > 0
    diffs = np.diff(result.astype(np.float64))
    assert not np.any(diffs == 0.0), "Hard-clipping plateau detected in limiter output"
    assert np.all(diffs >= 0.0), "Monotonicity violated"


# ── M5: VAD sample-rate-aware windows ────────────────────────────────────────

def test_vad_accepts_sample_rate_param():
    """detect_voice_activity must accept a sample_rate parameter."""
    import inspect
    from whisper_aloud.audio.audio_processor import AudioProcessor
    sig = inspect.signature(AudioProcessor.detect_voice_activity)
    assert "sample_rate" in sig.parameters


def test_vad_respects_sample_rate():
    """trim_silence must work correctly at sample rates other than 16kHz."""
    import numpy as np
    from whisper_aloud.audio.audio_processor import AudioProcessor
    sr = 48000
    # 2 seconds: 0.5s silence, 1s speech, 0.5s silence
    silence = np.zeros(int(sr * 0.5), dtype=np.float32)
    speech = np.ones(int(sr * 1.0), dtype=np.float32) * 0.1
    audio = np.concatenate([silence, speech, silence])
    trimmed, start, end = AudioProcessor.trim_silence(audio, sr)
    # Bounds must be within the audio
    assert 0 <= start <= end <= len(audio)
    # Speech portion must be included
    speech_start = int(sr * 0.5)
    speech_end = speech_start + int(sr * 1.0)
    assert start <= speech_start
    assert end >= speech_end


# ── L1: Denoiser exception promotion ─────────────────────────────────────────

def test_denoiser_exception_logged_as_error(caplog):
    """Denoiser failures must be logged at ERROR level (not WARNING)."""
    import logging
    import numpy as np
    from unittest.mock import patch
    from whisper_aloud.audio.audio_processor import Denoiser

    denoiser = Denoiser(strength=0.5)
    denoiser._noisereduce = object()  # non-None so process() tries to call it

    with patch.object(
        denoiser,
        '_noisereduce',
        new_callable=lambda: type('FakeNR', (), {
            'reduce_noise': staticmethod(lambda **kw: (_ for _ in ()).throw(RuntimeError("oom")))
        })
    ):
        with caplog.at_level(logging.WARNING, logger='whisper_aloud.audio.audio_processor'):
            audio = np.zeros(1000, dtype=np.float32)
            result = denoiser.process(audio, 16000)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) > 0, "No ERROR log emitted on denoiser failure"
    assert result is not None, "process() must still return audio on failure"


# ── WHISP-93: adaptive (relative) trim_silence ───────────────────────────────
#
# Field bug: under Flatpak, GainController's hardware-gain-raise step is a
# structural no-op (WHISP-61), so a quiet recording's RMS never cleared the
# old fixed absolute vad_threshold (0.02) and trim_silence discarded
# everything but the single loudest clause. See
# docs/audits/2026-10-04-runtime-assumptions-pass2.md's WHISP-93 section.


def _quiet_speech_like(sr=16000, duration_s=6.4, seed=0, speech_rms=0.008, floor_rms=0.0006):
    """Amplitude-modulated noise bursts simulating quiet, under-gained speech:
    RMS ~0.005-0.01 during "talking" bursts, a much lower noise floor in the
    gaps between them -- well under the old fixed 0.02 absolute threshold,
    but clearly voice relative to its own noise floor (WHISP-93)."""
    rng = np.random.default_rng(seed)
    n = int(sr * duration_s)
    t = np.arange(n) / sr
    burst_period = 0.28
    duty = 0.7
    phase = (t % burst_period) / burst_period
    envelope = np.where(phase < duty, speech_rms, floor_rms)
    noise = rng.standard_normal(n)
    return (noise * envelope).astype(np.float32)


def _old_trim_silence_absolute(audio, sample_rate, threshold):
    """Reproduces the pre-fix trim_silence algorithm (absolute RMS gate via
    detect_voice_activity) for before/after comparison in the regression
    test below. Not production code -- trim_silence itself no longer works
    this way."""
    from whisper_aloud.audio.audio_processor import AudioProcessor

    activity = AudioProcessor.detect_voice_activity(audio, threshold, sample_rate)
    voice_indices = np.where(activity)[0]
    if len(voice_indices) == 0:
        return audio, 0, len(audio)
    start_idx = voice_indices[0]
    end_idx = voice_indices[-1]
    padding_samples = int(0.1 * sample_rate)
    start_idx = max(0, start_idx - padding_samples)
    end_idx = min(len(audio), end_idx + padding_samples)
    return audio[start_idx:end_idx], start_idx, end_idx


def test_trim_silence_quiet_speech_not_collapsed_to_a_fraction():
    """A quiet-but-real recording (RMS ~0.005-0.01, well under the old fixed
    0.02 threshold) must not be trimmed down to a small fraction of its
    duration -- this is the core WHISP-93 fix."""
    sr = 16000
    lead_silence = np.zeros(int(sr * 0.3), dtype=np.float32)
    speech = _quiet_speech_like(sr=sr, duration_s=6.4)
    trail_silence = np.zeros(int(sr * 0.3), dtype=np.float32)
    audio = np.concatenate([lead_silence, speech, trail_silence])
    raw_duration = len(audio) / sr

    trimmed, start, end = AudioProcessor.trim_silence(audio, sr)
    kept_duration = (end - start) / sr

    assert kept_duration / raw_duration > 0.8, (
        f"kept only {kept_duration:.2f}s of {raw_duration:.2f}s "
        f"({100 * kept_duration / raw_duration:.0f}%) -- quiet speech was "
        "collapsed to a small fraction, reproducing the WHISP-93 bug"
    )


def test_trim_silence_still_trims_leading_and_trailing_silence():
    """The adaptive algorithm must still trim real leading/trailing silence
    around a loud, unambiguous speech segment."""
    sr = 16000
    silence = np.zeros(int(sr * 1.0), dtype=np.float32)
    voice = (np.ones(int(sr * 1.0), dtype=np.float32) * 0.3)
    audio = np.concatenate([silence, voice, silence])

    trimmed, start, end = AudioProcessor.trim_silence(audio, sr)

    assert start > 0, "Leading silence was not trimmed"
    assert end < len(audio), "Trailing silence was not trimmed"
    assert len(trimmed) < len(audio)


def test_trim_silence_pure_silence_returns_original():
    """Pure digital silence must be handled the same as before: original
    audio returned unchanged, not an empty array."""
    sr = 16000
    silent = np.zeros(sr, dtype=np.float32)
    trimmed, start, end = AudioProcessor.trim_silence(silent, sr)

    assert len(trimmed) == len(silent)
    assert start == 0
    assert end == len(silent)


def test_trim_silence_loud_recording_unchanged_behavior():
    """A loud recording (well above both the old and new thresholds) must
    still get sensible leading/trailing trim, matching pre-fix behavior."""
    sr = 16000
    silence = np.zeros(8000, dtype=np.float32)
    voice = np.ones(8000, dtype=np.float32) * 0.5
    audio = np.concatenate([silence, voice, silence])

    trimmed, start, end = AudioProcessor.trim_silence(audio, sr)

    assert len(trimmed) < len(audio)
    assert 0 <= start <= end <= len(audio)
    # The loud voice segment (indices 8000:16000) must be fully retained.
    assert start <= 8000
    assert end >= 16000


def test_trim_silence_regression_field_ratio_fixed():
    """Reproduces the exact WHISP-93 field signature: a mostly-quiet 6.4s
    recording with one short loud clause. The old absolute-threshold
    algorithm collapsed this to ~1.45s (22.7%); the new adaptive algorithm
    must keep the great majority of the recording."""
    sr = 16000
    quiet = _quiet_speech_like(sr=sr, duration_s=6.4, seed=1)
    raw_duration = len(quiet) / sr

    # Inject a short loud clause (RMS ~0.03, above the old 0.02 threshold)
    # into the middle of the quiet recording, exactly the kind of "one loud
    # moment" the field logs' "What?" / 41.97%-confidence fragments suggest.
    loud_start = int(sr * 3.0)
    loud_len = int(sr * 1.1)
    rng = np.random.default_rng(2)
    quiet[loud_start : loud_start + loud_len] = rng.standard_normal(loud_len) * 0.03

    old_trimmed, old_start, old_end = _old_trim_silence_absolute(quiet, sr, threshold=0.02)
    old_ratio = (old_end - old_start) / len(quiet)

    new_trimmed, new_start, new_end = AudioProcessor.trim_silence(quiet, sr, threshold=0.02)
    new_ratio = (new_end - new_start) / len(quiet)

    # Reproduce the bug with the old algorithm (sanity check on the fixture).
    assert old_ratio < 0.4, (
        f"fixture did not reproduce the field collapse: old algorithm kept "
        f"{100 * old_ratio:.0f}% of {raw_duration:.2f}s"
    )
    # The new algorithm must keep the great majority of the recording.
    assert new_ratio > 0.8, (
        f"new algorithm only kept {100 * new_ratio:.0f}% of {raw_duration:.2f}s "
        f"(old algorithm kept {100 * old_ratio:.0f}%) -- WHISP-93 not fixed"
    )


def test_trim_silence_conservative_backstop_logs_warning(caplog):
    """If the adaptive decision would keep only a small fraction of a
    recording whose overall RMS is clearly not near-silent, trim_silence
    must skip the trim and log a WARNING rather than silently discarding
    most of the audio."""
    import logging
    from unittest.mock import patch

    sr = 16000
    lead_silence = np.zeros(int(sr * 0.3), dtype=np.float32)
    speech = _quiet_speech_like(sr=sr, duration_s=4.0, seed=3)
    loud_start, loud_len = int(sr * 2.0), int(sr * 0.5)
    rng = np.random.default_rng(5)
    speech[loud_start : loud_start + loud_len] = rng.standard_normal(loud_len) * 0.03
    trail_silence = np.zeros(int(sr * 0.3), dtype=np.float32)
    audio = np.concatenate([lead_silence, speech, trail_silence])

    # Push the adaptive threshold pathologically close to the speech-level
    # estimate so only the short loud clause clears it -- exercising the
    # backstop (default _MIN_KEPT_FRACTION) independently of the main
    # algorithm's own (already well-behaved) margin.
    with patch("whisper_aloud.audio.audio_processor._RELATIVE_VOICE_MARGIN", 0.95):
        with caplog.at_level(logging.WARNING, logger="whisper_aloud.audio.audio_processor"):
            trimmed, start, end = AudioProcessor.trim_silence(audio, sr)

    assert len(trimmed) == len(audio)
    assert start == 0 and end == len(audio)
    assert any("safety net" in r.getMessage() for r in caplog.records)
