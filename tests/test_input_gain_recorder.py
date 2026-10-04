"""AudioRecorder wiring of the input normalization stage.

Order on the batch path must be: resample -> InputNormalizer -> trim_silence
-> AudioPipeline (unchanged). The streaming path normalizes before the
streaming pipeline. The PortAudio callback only copies frames.
No real audio device is used: sounddevice.InputStream and device
validation are mocked, frames are injected.
"""

import logging
import time
from unittest.mock import Mock, patch

import numpy as np
import pytest

from tests.test_input_gain import _rms_db, chunks, utterance, voiced_mask
from whisper_aloud.audio.audio_processor import AudioPipeline, AudioProcessor
from whisper_aloud.audio.recorder import AudioRecorder
from whisper_aloud.config import AudioConfig, AudioProcessingConfig, InputGainConfig

# Chain fully off so assertions see only what normalization (and trim) did.
CHAIN_OFF = AudioProcessingConfig(
    noise_gate_enabled=False,
    agc_enabled=False,
    denoising_enabled=False,
    limiter_enabled=False,
    highpass_hz=0,
)


@pytest.fixture
def no_device():
    with (
        patch("whisper_aloud.audio.device_manager.DeviceManager.validate_device") as validate,
        patch("sounddevice.InputStream") as stream,
    ):
        validate.return_value = Mock()
        stream.return_value = Mock()
        yield


def _recorder(input_gain=None, vad=False, hook=None, processing=CHAIN_OFF):
    return AudioRecorder(
        AudioConfig(sample_rate=16000, vad_enabled=vad),
        processing_config=processing,
        input_gain_config=input_gain,
        on_processed_chunk=hook,
    )


def _feed(recorder, audio):
    """Drive the real callback chunk by chunk without overrunning the bounded
    hook queue: wait for the worker to drain it every few chunks."""
    session = recorder._chunk_session
    for i, c in enumerate(chunks(audio)):
        recorder._audio_callback(c.reshape(-1, 1), len(c), None, None)
        if session is not None and i % 20 == 19:
            deadline = time.monotonic() + 5.0
            while not session.queue.empty() and time.monotonic() < deadline:
                time.sleep(0.005)


def _record(recorder, audio):
    recorder.start()
    recorder._frames = [audio.copy()]
    return recorder.stop()


def test_enabled_by_default(no_device):
    rec = AudioRecorder(AudioConfig())
    assert rec.input_gain_config.enabled is True
    assert rec.input_gain_config.hardware_control is False


def test_batch_output_is_normalized(no_device):
    raw = utterance()
    out = _record(_recorder(), raw)
    mask = voiced_mask(len(out))
    assert _rms_db(raw[mask]) == pytest.approx(-47.0, abs=1.5)
    assert _rms_db(out[mask]) == pytest.approx(-20.0, abs=3.0)


def test_disabled_is_passthrough(no_device):
    raw = utterance()
    out = _record(_recorder(input_gain=InputGainConfig(enabled=False)), raw)
    np.testing.assert_allclose(out, raw, atol=1e-7)


def test_normalization_runs_before_trim_and_pipeline(no_device):
    """trim_silence and the pipeline must both receive the normalized signal."""
    order = []
    real_trim = AudioProcessor.trim_silence
    real_process = AudioPipeline.process

    def spy_trim(audio, sample_rate, threshold=0.02, min_silence_duration=0.3):
        order.append(("trim", _rms_db(audio[voiced_mask(len(audio))])))
        return real_trim(audio, sample_rate, threshold, min_silence_duration)

    def spy_process(self, audio, sample_rate):
        order.append(("pipeline", _rms_db(audio)))
        return real_process(self, audio, sample_rate)

    with (
        patch.object(AudioProcessor, "trim_silence", staticmethod(spy_trim)),
        patch.object(AudioPipeline, "process", spy_process),
    ):
        _record(_recorder(vad=True), utterance())

    assert [name for name, _ in order] == ["trim", "pipeline"]
    trim_level = order[0][1]
    assert trim_level == pytest.approx(-20.0, abs=3.0), "trim_silence saw un-normalized audio"


def test_callback_only_copies_frames(no_device):
    """The PortAudio callback stores raw samples; normalization happens later."""
    rec = _recorder()
    rec.start()
    raw = utterance()[:1600]
    rec._audio_callback(raw.reshape(-1, 1), len(raw), None, None)
    np.testing.assert_array_equal(rec._frames[0], raw)
    rec.stop()


def test_batch_logs_summary_and_keeps_stats(no_device, caplog):
    rec = _recorder()
    with caplog.at_level(logging.INFO, logger="whisper_aloud.audio.recorder"):
        _record(rec, utterance())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Input normalization (batch)" in text
    assert "speech_before=" in text and "speech_after=" in text
    assert rec.last_input_gain_stats is not None
    assert rec.last_input_gain_stats.final_gain_db > 20.0


def test_streaming_chunks_are_normalized(no_device):
    received = []
    rec = _recorder(hook=lambda c: received.append(c.copy()))
    raw = np.concatenate([utterance(words=8) for _ in range(2)])
    rec.start()
    _feed(rec, raw)
    rec._frames = [raw.copy()]
    rec.stop()
    streamed = np.concatenate(received)
    assert len(streamed) == len(raw)
    tail = slice(len(raw) // 2, None)  # after convergence
    assert _rms_db(streamed[tail]) > _rms_db(raw[tail]) + 18.0


def test_streaming_session_seeded_from_last_batch_gain(no_device):
    rec = _recorder(hook=lambda c: None)
    _record(rec, utterance())
    learned = rec.last_input_gain_stats.target_gain_db
    assert learned > 20.0

    rec.start()
    assert rec._chunk_session.normalizer is not None
    assert rec._chunk_session.normalizer._gain_db == pytest.approx(learned)
    rec._frames = [utterance()]
    rec.stop()


def test_streaming_disabled_has_no_normalizer(no_device):
    rec = _recorder(input_gain=InputGainConfig(enabled=False), hook=lambda c: None)
    rec.start()
    assert rec._chunk_session.normalizer is None
    rec._frames = [np.zeros(1600, np.float32)]
    rec.stop()


def test_streaming_session_logs_summary(no_device, caplog):
    rec = _recorder(hook=lambda c: None)
    rec.start()
    _feed(rec, utterance())
    rec._frames = [utterance()]
    with caplog.at_level(logging.INFO, logger="whisper_aloud.audio.recorder"):
        rec.stop()
    assert any("Input normalization (stream)" in r.getMessage() for r in caplog.records)


def test_resampled_device_rate_still_normalized(no_device):
    """48 kHz capture: normalization runs at 16 kHz after resampling."""
    rec = AudioRecorder(
        AudioConfig(sample_rate=48000, vad_enabled=False), processing_config=CHAIN_OFF
    )
    raw16 = utterance()
    raw48 = np.repeat(raw16, 3).astype(np.float32)
    out = _record(rec, raw48)
    assert len(out) == pytest.approx(len(raw16), abs=3)
    assert rec.last_input_gain_stats.speech_after_dbfs == pytest.approx(-20.0, abs=3.0)
