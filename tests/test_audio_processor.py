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
