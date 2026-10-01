"""Audio processing utilities."""

import logging
from math import gcd
from typing import Tuple

import numpy as np
from scipy import signal

from ..config import AudioProcessingConfig
from ..exceptions import AudioProcessingError

logger = logging.getLogger(__name__)


class NoiseGate:
    """Noise gate with smooth attack/release, hysteresis, and hold time."""

    def __init__(
        self,
        threshold_db: float = -40.0,
        attack_ms: float = 5.0,
        release_ms: float = 50.0,
        hold_ms: float = 60.0,
        hysteresis_db: float = 6.0,
    ):
        """Initialise the noise gate.

        Args:
            threshold_db: Gate open threshold in dBFS. Audio whose RMS level
                exceeds this value causes the gate to open.
            attack_ms: Time in milliseconds for the gain to ramp linearly from
                0 to 1 once the signal crosses the open threshold.
            release_ms: RC time constant (1/e decay) in milliseconds for the
                gain to fall after hold expires and signal is below close threshold.
            hold_ms: Time in milliseconds the gate stays open after signal drops
                below the close threshold (prevents chatter on brief dips).
            hysteresis_db: Separation in dB between open and close thresholds.
                Close threshold = threshold_db - hysteresis_db. Prevents rapid
                toggling near the threshold.
        """
        self.threshold_db = threshold_db
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.hold_ms = hold_ms
        self.hysteresis_db = hysteresis_db
        self._envelope = 0.0
        self._gate_open = False
        self._hold_remaining = 0  # samples left in hold period

    def reset(self) -> None:
        """Clear envelope/open/hold state, as if freshly constructed."""
        self._envelope = 0.0
        self._gate_open = False
        self._hold_remaining = 0

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply noise gate to audio chunk.

        Uses a 25ms RMS window for level detection, hysteresis between open/close
        thresholds, and a hold timer to prevent gate chatter.
        """
        if audio.size == 0:
            return audio

        open_threshold = 10 ** (self.threshold_db / 20.0)
        close_threshold = 10 ** ((self.threshold_db - self.hysteresis_db) / 20.0)
        hold_samples = int(self.hold_ms * sample_rate / 1000.0)
        release_samples = max(1.0, self.release_ms * sample_rate / 1000.0)
        release_coeff = np.exp(-1.0 / release_samples)

        n = len(audio)

        # Step 1: Compute per-sample RMS level using a 25ms sliding window
        # to smooth over waveform zero-crossings (was 2ms — too short for speech).
        win = min(max(2, int(sample_rate * 0.025)), n)
        sq = audio.astype(np.float64) ** 2
        cumsum = np.cumsum(sq)
        rms = np.empty(n, dtype=np.float64)
        if n <= win:
            rms[:] = np.sqrt(cumsum / np.arange(1, n + 1))
        else:
            rms[:win] = np.sqrt(cumsum[:win] / np.arange(1, win + 1))
            rms[win:] = np.sqrt((cumsum[win:] - cumsum[:-win]) / win)

        # Step 2: Compute gate state with hysteresis + hold in 10ms blocks.
        # This avoids a per-sample Python loop while handling stateful hold logic.
        block_size = max(1, int(sample_rate * 0.010))
        gate_state = np.empty(n, dtype=bool)
        gate_open = self._gate_open
        hold_remaining = self._hold_remaining

        for start in range(0, n, block_size):
            end = min(start + block_size, n)
            block_rms = float(rms[start:end].mean())

            if block_rms >= open_threshold:
                # Signal above open threshold: open gate and reset hold
                gate_open = True
                hold_remaining = hold_samples
            elif block_rms < close_threshold:
                # Signal below close threshold: count down hold, then close
                if hold_remaining > 0:
                    hold_remaining = max(0, hold_remaining - (end - start))
                else:
                    gate_open = False
            # else: between thresholds → maintain current state (hysteresis zone)

            gate_state[start:end] = gate_open

        self._gate_open = gate_open
        self._hold_remaining = hold_remaining

        # Step 3: Apply smooth attack/release envelope using vectorized segment processing.
        attack_step = 1.0 / max(1, int(self.attack_ms * sample_rate / 1000.0))
        envelope = self._envelope
        gain = np.empty(n, dtype=np.float64)

        changes = np.diff(gate_state.astype(np.int8))
        seg_starts = np.concatenate(([0], np.nonzero(changes)[0] + 1, [n]))

        for seg_idx in range(len(seg_starts) - 1):
            start = seg_starts[seg_idx]
            end = seg_starts[seg_idx + 1]
            seg_len = end - start

            if gate_state[start]:
                ramp = envelope + attack_step * np.arange(1, seg_len + 1)
                np.clip(ramp, 0.0, 1.0, out=ramp)
                gain[start:end] = ramp
                envelope = ramp[-1]
            else:
                decay_factors = release_coeff ** np.arange(1, seg_len + 1)
                gain[start:end] = envelope * decay_factors
                envelope = gain[end - 1]

        self._envelope = float(envelope)
        return (audio * gain).astype(audio.dtype)


class AGC:
    """Automatic Gain Control using sliding-window RMS.

    Args:
        target_db: Target output level in dBFS (default -18.0).
        max_gain_db: Maximum gain boost in dB (default 30.0).
        min_gain_db: Minimum gain (attenuation) in dB (default -10.0).
        attack_ms: Attack time constant in ms for gain reduction (default 10.0).
        release_ms: Release time constant in ms for gain increase (default 100.0).
        window_ms: RMS measurement window in ms (default 300.0).
    """

    def __init__(
        self,
        target_db: float = -18.0,
        max_gain_db: float = 20.0,
        min_gain_db: float = -10.0,
        attack_ms: float = 10.0,
        release_ms: float = 100.0,
        window_ms: float = 300.0,
    ):
        self.target_linear = 10 ** (target_db / 20.0)
        self.max_gain = 10 ** (max_gain_db / 20.0)
        self.min_gain = 10 ** (min_gain_db / 20.0)
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.window_ms = window_ms
        self._current_gain = 1.0
        # Rolling history of raw samples from the tail of the previous process()
        # call, used to seed the RMS window so chunked processing matches
        # one-shot processing (see WHISP-65 / WHISP-34 finding: AGC used to
        # recompute its RMS window from scratch on every call, with no memory
        # of prior audio, which made chunked output diverge sharply from
        # one-shot output). Starts empty, so the very first call (or a
        # single one-shot call) behaves exactly as before.
        self._history = np.empty(0, dtype=np.float64)

    def reset(self) -> None:
        """Clear gain and RMS history, as if freshly constructed."""
        self._current_gain = 1.0
        self._history = np.empty(0, dtype=np.float64)

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply AGC to audio chunk.

        Uses a sliding RMS window to estimate signal level, then smoothly
        adjusts gain toward the target level. Attack (gain decrease) is faster
        than release (gain increase) to prevent clipping on transients.

        The RMS window is seeded with up to ``window_samples - 1`` trailing
        samples from the previous call (``self._history``), so splitting a
        signal into chunks and calling ``process()`` repeatedly reproduces the
        same per-sample RMS values as a single one-shot call over the whole
        signal — this is what makes the pipeline streaming-safe (WHISP-65).
        """
        if audio.size == 0:
            return audio

        n = len(audio)
        window_samples = max(1, int(self.window_ms * sample_rate / 1000.0))
        attack_coeff = np.exp(-1.0 / max(1.0, self.attack_ms * sample_rate / 1000.0))
        release_coeff = np.exp(-1.0 / max(1.0, self.release_ms * sample_rate / 1000.0))

        # Prepend rolling history so the windowed RMS at the start of this
        # chunk sees the same trailing samples a one-shot call would have.
        history = self._history
        h = len(history)
        combined = np.concatenate([history, audio.astype(np.float64)])
        m = len(combined)

        # Precompute per-sample RMS over (history + chunk) using cumulative sum (O(m))
        sq = combined ** 2
        cumsum = np.cumsum(sq)
        rms_full = np.empty(m, dtype=np.float64)
        if m <= window_samples:
            rms_full[:] = np.sqrt(cumsum / np.arange(1, m + 1))
        else:
            rms_full[:window_samples] = np.sqrt(cumsum[:window_samples] / np.arange(1, window_samples + 1))
            rms_full[window_samples:] = np.sqrt(
                (cumsum[window_samples:] - cumsum[:-window_samples]) / window_samples
            )
        # Only the portion covering this call's own samples is relevant below.
        rms_arr = rms_full[h:]

        # Keep up to window_samples-1 trailing raw samples for the next call.
        keep = min(max(window_samples - 1, 0), m)
        self._history = combined[m - keep:] if keep > 0 else np.empty(0, dtype=np.float64)

        # Compute desired gain per sample (vectorized)
        desired_gain = np.where(
            rms_arr > 1e-8,
            np.clip(self.target_linear / np.maximum(rms_arr, 1e-8), self.min_gain, self.max_gain),
            self._current_gain,  # Hold for silence
        )

        # Apply smoothed gain tracking with attack/release coefficients.
        # Process in 10ms blocks rather than per-sample to avoid a Python
        # loop over every sample (which is slow on long recordings).
        # The per-block coefficient is scaled so the overall time constant
        # matches the per-sample IIR: coeff_block = exp(-B / tau_samples).
        block_size = max(1, int(sample_rate * 0.010))  # ~10ms
        tau_attack = max(1.0, self.attack_ms * sample_rate / 1000.0)
        tau_release = max(1.0, self.release_ms * sample_rate / 1000.0)
        block_attack_coeff = np.exp(-block_size / tau_attack)
        block_release_coeff = np.exp(-block_size / tau_release)
        gain_arr = np.empty(n, dtype=np.float64)
        gain = self._current_gain
        for start in range(0, n, block_size):
            end = min(start + block_size, n)
            block_target = float(desired_gain[start:end].mean())
            coeff = block_attack_coeff if block_target < gain else block_release_coeff
            gain = coeff * gain + (1 - coeff) * block_target
            gain_arr[start:end] = gain

        self._current_gain = float(gain)
        return (audio * gain_arr).astype(np.float32)


class PeakLimiter:
    """Soft-knee peak limiter with passthrough below the knee.

    Signals below the knee point pass through unchanged.  Signals at or above
    the knee are smoothly saturated toward the ceiling using tanh — no hard
    plateau or discontinuity at the ceiling boundary.

    Args:
        ceiling_db: Asymptotic output ceiling in dBFS (default -1.0).
        knee_ratio: Knee starts at ``knee_ratio * ceiling`` (default 0.9).
            Below the knee the transfer is linear (unity gain).
    """

    def __init__(self, ceiling_db: float = -1.0, knee_ratio: float = 0.9):
        self.ceiling = 10 ** (ceiling_db / 20.0)
        self.ceiling_db = ceiling_db
        self.knee_ratio = knee_ratio

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Apply soft-knee limiting.

        Below ``knee_ratio * ceiling``: unity gain (passthrough).
        Above knee: tanh saturation anchored at the knee so the output
        asymptotically approaches the ceiling with no plateau.
        """
        if audio.size == 0:
            return audio

        knee = self.knee_ratio * self.ceiling
        scale = self.ceiling - knee  # room between knee and ceiling

        abs_audio = np.abs(audio).astype(np.float64)
        above_knee = abs_audio > knee

        out = audio.astype(np.float64)
        if np.any(above_knee):
            excess = abs_audio[above_knee] - knee
            out[above_knee] = np.sign(audio[above_knee]) * (knee + scale * np.tanh(excess / scale))

        return out.astype(np.float32)


class Denoiser:
    """Spectral denoising using noisereduce (optional dependency).

    Args:
        strength: Noise reduction strength from 0.0 (off) to 1.0 (maximum).
            Maps to noisereduce's prop_decrease parameter.
    """

    def __init__(self, strength: float = 0.5):
        self.strength = strength
        self._noisereduce = None
        try:
            import noisereduce
            self._noisereduce = noisereduce
        except ImportError:
            logger.info("noisereduce not installed, denoising disabled")

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply spectral denoising. Falls back to passthrough if noisereduce unavailable."""
        if self._noisereduce is None or audio.size == 0:
            return audio
        try:
            return self._noisereduce.reduce_noise(
                y=audio, sr=sample_rate,
                prop_decrease=self.strength,
                stationary=True,
            ).astype(np.float32)
        except Exception as e:
            logger.error(f"Denoising failed, returning original audio: {e}", exc_info=True)
            return audio


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
    def detect_voice_activity(
        audio: np.ndarray,
        threshold: float = 0.02,
        sample_rate: int = 16000,
    ) -> np.ndarray:
        """
        Detect voice activity (energy-based VAD).

        Args:
            audio: Input audio array
            threshold: RMS threshold for voice detection
            sample_rate: Sample rate in Hz — used to compute window/hop sizes
                so VAD behaves correctly at any sample rate (not only 16 kHz).

        Returns:
            Boolean array (True = voice, False = silence)
        """
        if audio.size == 0:
            return np.array([], dtype=bool)

        # Window/hop sizes derived from sample_rate (previously hardcoded for 16kHz)
        window_size = max(1, int(sample_rate * 0.025))  # 25ms
        hop_size = max(1, int(sample_rate * 0.010))     # 10ms

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

        # Detect voice activity (pass sample_rate so windows scale correctly)
        activity = AudioProcessor.detect_voice_activity(audio, threshold, sample_rate)

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


class AudioPipeline:
    """Full audio processing pipeline: gate -> AGC -> denoising -> limiter.

    Each stage is toggleable via config. The pipeline is stateful ---
    call process() repeatedly with audio chunks for streaming use.

    Args:
        config: AudioProcessingConfig controlling which stages are active.
        streaming: When True, the pipeline is in streaming mode: the
            whole-buffer ``Denoiser`` (noisereduce) is never instantiated,
            regardless of ``config.denoising_enabled``. Per WHISP-34's
            findings, ``Denoiser`` estimates its noise profile from — and
            spectrally gates — the *entire* array passed to a single call,
            with no state kept between calls, so it is structurally
            incompatible with per-chunk processing (it would need a
            genuinely streaming/causal noise suppressor instead, which is
            out of scope here). ``NoiseGate``, ``AGC``, and ``PeakLimiter``
            are all causal/stateful and safe to call per-chunk, so they are
            unaffected by this flag. Defaults to False, which preserves
            today's one-shot behaviour used by ``AudioRecorder.stop()``
            unchanged.
    """

    def __init__(self, config: AudioProcessingConfig, streaming: bool = False):
        self.config = config
        self.streaming = streaming
        self._gate = NoiseGate(threshold_db=config.noise_gate_threshold_db) if config.noise_gate_enabled else None
        self._agc = AGC(target_db=config.agc_target_db, max_gain_db=config.agc_max_gain_db) if config.agc_enabled else None
        self._limiter = PeakLimiter(ceiling_db=config.limiter_ceiling_db) if config.limiter_enabled else None
        self._denoiser = (
            None if streaming else (Denoiser(strength=config.denoising_strength) if config.denoising_enabled else None)
        )

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Process audio through the pipeline (gate -> AGC -> denoise -> limiter).

        This is the one-shot entry point used by ``AudioRecorder.stop()`` on
        the full recorded buffer; its behaviour is unchanged by WHISP-65.
        """
        result = audio
        if self._gate:
            result = self._gate.process(result, sample_rate)
        if self._agc:
            result = self._agc.process(result, sample_rate)
        if self._denoiser:
            result = self._denoiser.process(result, sample_rate)
        if self._limiter:
            result = self._limiter.process(result)
        return result.astype(np.float32)

    def process_chunk(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Process one streaming chunk (gate -> AGC -> limiter, no denoiser).

        Requires the pipeline to have been constructed with ``streaming=True``
        — this is a thin, explicit wrapper around ``process()`` (which already
        skips the denoiser stage when ``self._denoiser`` is None) that exists
        to make the streaming contract obvious at call sites and to fail
        loudly if a caller forgets to opt into streaming mode.
        """
        if not self.streaming:
            raise ValueError(
                "process_chunk() requires AudioPipeline(..., streaming=True); "
                "use process() for one-shot (non-streaming) processing."
            )
        return self.process(audio, sample_rate)

    def reset(self) -> None:
        """Clear all stateful history (gate envelope/hold, AGC gain/RMS
        history) so this pipeline behaves like a freshly constructed one on
        the next process()/process_chunk() call.

        This exists because AudioRecorder keeps a single AudioPipeline
        instance (``self._pipeline``) alive across many recording sessions
        (the daemon reuses one AudioRecorder for its whole lifetime) --
        without resetting it between recordings, AGC's rolling RMS history
        and NoiseGate's envelope/hold state from recording N would leak into
        recording N+1's one-shot process() call (WHISP-65 audit finding).
        PeakLimiter and Denoiser are stateless, so there is nothing to reset
        for them.
        """
        if self._gate is not None:
            self._gate.reset()
        if self._agc is not None:
            self._agc.reset()


class StreamingResampler:
    """Causal, chunk-wise resampler for streaming audio.

    ``AudioProcessor.resample()`` uses ``scipy.signal.resample``, which is
    FFT-based over the *entire* input array — every output sample depends on
    every input sample, including ones that haven't been recorded yet in a
    streaming context (WHISP-34 finding: ``test_resample_has_lookahead_xfail``).
    That makes it unsuitable for per-chunk use.

    This class wraps ``scipy.signal.resample_poly`` (a local, FIR/polyphase
    operation) and carries a short rolling history of raw input samples
    across calls, so that splitting a signal into chunks and resampling each
    one in sequence converges to the same result as resampling the whole
    signal at once — see ``tests/test_audio_pipeline_streaming.py``'s
    resampling-continuity test.
    """

    def __init__(self, orig_rate: int, target_rate: int):
        if orig_rate <= 0 or target_rate <= 0:
            raise ValueError("sample rates must be positive")
        g = gcd(orig_rate, target_rate)
        self.orig_rate = orig_rate
        self.target_rate = target_rate
        self.up = target_rate // g
        self.down = orig_rate // g
        self._history = np.empty(0, dtype=np.float32)
        # scipy's default Kaiser FIR kernel for resample_poly spans roughly
        # 2 * 10 * max(up, down) + 1 taps; keeping that many trailing raw
        # samples as history is enough for the filter to "see" the same
        # context a one-shot call would have at the start of each chunk.
        self._history_len = 10 * max(self.up, self.down)

    def reset(self) -> None:
        """Clear carried-over history (start a fresh streaming session)."""
        self._history = np.empty(0, dtype=np.float32)

    def process(self, chunk: np.ndarray) -> np.ndarray:
        """Resample one chunk, continuing from the previous call's history."""
        if chunk.size == 0:
            return chunk.astype(np.float32)

        history = self._history
        h = len(history)
        combined = np.concatenate([history, chunk.astype(np.float32)])

        resampled_combined = signal.resample_poly(combined, self.up, self.down)

        # Samples of resampled_combined attributable to the history prefix,
        # using the same length scaling resample_poly itself applies.
        h_out = int(round(h * self.up / self.down))
        out = resampled_combined[h_out:]

        keep = min(self._history_len, len(combined))
        self._history = combined[len(combined) - keep:] if keep > 0 else np.empty(0, dtype=np.float32)

        return out.astype(np.float32)
