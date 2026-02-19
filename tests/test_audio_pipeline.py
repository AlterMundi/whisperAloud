import numpy as np
import pytest


class TestNoiseGate:
    """Tests for noise gate processing stage."""

    def test_gate_silences_below_threshold(self):
        """Audio below threshold should be silenced."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        # Quiet noise at ~-60 dBFS
        quiet = np.random.randn(1600).astype(np.float32) * 0.001
        result = gate.process(quiet, sample_rate=16000)
        rms = np.sqrt(np.mean(result ** 2))
        assert rms < 0.0005, f"Gate should silence quiet audio, got RMS {rms}"

    def test_gate_passes_above_threshold(self):
        """Audio above threshold should pass through after attack settles."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        sr = 16000
        # Loud 440 Hz sine at ~-10 dBFS, long enough for gate to open
        t = np.arange(4800, dtype=np.float32) / sr
        loud = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)
        result = gate.process(loud, sample_rate=sr)
        # After attack settles (~first 10ms), output should match input
        settled = int(0.01 * sr)  # 10ms warmup
        np.testing.assert_allclose(result[settled:], loud[settled:], atol=0.05)

    def test_gate_smooth_attack_release(self):
        """Gate should not produce clicks (smooth transitions)."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        sr = 16000
        # Silence then a proper 440 Hz tone (not aliased)
        silence = np.zeros(800, dtype=np.float32)
        t = np.arange(800, dtype=np.float32) / sr
        loud = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)
        audio = np.concatenate([silence, loud])
        result = gate.process(audio, sample_rate=sr)
        # Check for no sudden jumps > 0.1 between consecutive samples
        diffs = np.abs(np.diff(result))
        assert np.max(diffs) < 0.15, f"Click detected: max diff {np.max(diffs)}"
