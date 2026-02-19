"""Audio processing utilities."""

import logging
from typing import Tuple

import numpy as np
from scipy import signal

from ..exceptions import AudioProcessingError

logger = logging.getLogger(__name__)


class NoiseGate:
    """Noise gate with smooth attack/release."""

    def __init__(self, threshold_db: float = -40.0, attack_ms: float = 5.0, release_ms: float = 50.0):
        self.threshold_db = threshold_db
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self._envelope = 0.0

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply noise gate to audio chunk.

        Uses RMS-based level detection to determine gate open/close state,
        then applies smooth exponential attack/release to the gain envelope.
        """
        if audio.size == 0:
            return audio

        threshold_linear = 10 ** (self.threshold_db / 20.0)
        release_samples = max(1.0, self.release_ms * sample_rate / 1000.0)
        release_coeff = np.exp(-1.0 / release_samples)

        n = len(audio)

        # Step 1: Compute per-sample RMS level using a short sliding window
        # to smooth over waveform zero-crossings
        win = max(2, int(sample_rate * 0.002))  # ~2ms window
        sq = audio.astype(np.float64) ** 2
        cumsum = np.cumsum(sq)
        rms = np.empty(n, dtype=np.float64)
        rms[:win] = np.sqrt(cumsum[:win] / np.arange(1, win + 1))
        rms[win:] = np.sqrt((cumsum[win:] - cumsum[:-win]) / win)

        # Step 2: Determine gate target (1.0 = open, 0.0 = closed)
        gate_open = rms > threshold_linear

        # Step 3: Apply smooth attack/release envelope
        # Attack: fast ramp using linear interpolation over attack window
        # Release: exponential decay for smooth fade-out
        attack_samples = max(1, int(self.attack_ms * sample_rate / 1000.0))
        envelope = self._envelope
        gain = np.empty(n, dtype=np.float64)

        for i in range(n):
            if gate_open[i]:
                # Linear ramp toward 1.0 over attack_samples
                envelope = min(1.0, envelope + 1.0 / attack_samples)
            else:
                envelope = release_coeff * envelope
            gain[i] = envelope

        self._envelope = envelope
        return (audio * gain).astype(audio.dtype)


class AudioProcessor:
    """Audio processing operations."""

    @staticmethod
    def normalize(audio: np.ndarray, target_level: float = 0.95) -> np.ndarray:
        """
        Normalize audio to target peak level.

        Args:
            audio: Input audio array
            target_level: Target peak level (0.0 to 1.0)

        Returns:
            Normalized audio array
        """
        if audio.size == 0:
            return audio

        peak = np.max(np.abs(audio))
        if peak > 0:
            return audio * (target_level / peak)
        return audio

    @staticmethod
    def stereo_to_mono(audio: np.ndarray) -> np.ndarray:
        """
        Convert stereo audio to mono.

        Args:
            audio: Stereo audio array (2D)

        Returns:
            Mono audio array (1D)
        """
        if audio.ndim == 1:
            return audio  # Already mono
        elif audio.ndim == 2:
            # Average channels
            return np.mean(audio, axis=1).astype(audio.dtype)
        else:
            raise AudioProcessingError(f"Invalid audio shape: {audio.shape}")

    @staticmethod
    def resample(audio: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
        """
        Resample audio to target sample rate.

        Args:
            audio: Input audio array
            original_rate: Original sample rate
            target_rate: Target sample rate

        Returns:
            Resampled audio array
        """
        if original_rate == target_rate:
            return audio

        if audio.size == 0:
            return audio

        try:
            num_samples = int(len(audio) * target_rate / original_rate)
            resampled = signal.resample(audio, num_samples)
            logger.debug(f"Resampled {original_rate}Hz -> {target_rate}Hz")
            return resampled.astype(np.float32)
        except Exception as e:
            raise AudioProcessingError(f"Resampling failed: {e}") from e

    @staticmethod
    def detect_voice_activity(audio: np.ndarray, threshold: float = 0.02) -> np.ndarray:
        """
        Detect voice activity (energy-based VAD).

        Args:
            audio: Input audio array
            threshold: RMS threshold for voice detection

        Returns:
            Boolean array (True = voice, False = silence)
        """
        if audio.size == 0:
            return np.array([], dtype=bool)

        # Calculate RMS in sliding windows
        window_size = 400  # ~25ms at 16kHz
        hop_size = 160     # ~10ms at 16kHz

        activity = np.zeros(len(audio), dtype=bool)

        for i in range(0, len(audio) - window_size, hop_size):
            window = audio[i:i + window_size]
            rms = np.sqrt(np.mean(np.square(window)))

            if rms > threshold:
                activity[i:i + window_size] = True

        return activity

    @staticmethod
    def trim_silence(
        audio: np.ndarray,
        sample_rate: int,
        threshold: float = 0.02,
        min_silence_duration: float = 0.3
    ) -> Tuple[np.ndarray, int, int]:
        """
        Trim silence from start and end of audio.

        Args:
            audio: Input audio array
            sample_rate: Sample rate in Hz
            threshold: RMS threshold for voice detection
            min_silence_duration: Minimum silence duration to trim (seconds)

        Returns:
            Tuple of (trimmed_audio, start_sample, end_sample)
        """
        if audio.size == 0:
            return audio, 0, 0

        # Detect voice activity
        activity = AudioProcessor.detect_voice_activity(audio, threshold)

        # Find first and last voice activity
        voice_indices = np.where(activity)[0]

        if len(voice_indices) == 0:
            # No voice detected, return empty or original
            logger.warning("No voice activity detected in audio")
            return audio, 0, len(audio)

        start_idx = voice_indices[0]
        end_idx = voice_indices[-1]

        # Add small padding
        padding_samples = int(0.1 * sample_rate)  # 100ms padding
        start_idx = max(0, start_idx - padding_samples)
        end_idx = min(len(audio), end_idx + padding_samples)

        trimmed = audio[start_idx:end_idx]
        logger.debug(f"Trimmed {start_idx / sample_rate:.2f}s from start, "
                    f"{(len(audio) - end_idx) / sample_rate:.2f}s from end")

        return trimmed, start_idx, end_idx

    @staticmethod
    def process_recording(
        audio: np.ndarray,
        sample_rate: int,
        target_rate: int = 16000,
        normalize: bool = True,
        trim_silence_enabled: bool = True,
        vad_threshold: float = 0.02
    ) -> np.ndarray:
        """
        Complete audio processing pipeline.

        Args:
            audio: Input audio array
            sample_rate: Original sample rate
            target_rate: Target sample rate (Whisper uses 16kHz)
            normalize: Whether to normalize levels
            trim_silence_enabled: Whether to trim silence
            vad_threshold: VAD threshold for silence trimming

        Returns:
            Processed audio array ready for transcription
        """
        if audio.size == 0:
            logger.warning("Empty audio provided to processor")
            return audio

        # Convert to mono if stereo
        if audio.ndim > 1:
            audio = AudioProcessor.stereo_to_mono(audio)

        # Resample if needed
        if sample_rate != target_rate:
            audio = AudioProcessor.resample(audio, sample_rate, target_rate)

        # Trim silence
        if trim_silence_enabled:
            audio, _, _ = AudioProcessor.trim_silence(audio, target_rate, vad_threshold)

        # Normalize
        if normalize and audio.size > 0:
            audio = AudioProcessor.normalize(audio)

        logger.info(f"Processed audio: {len(audio) / target_rate:.2f}s duration")
        return audio