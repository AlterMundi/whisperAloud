"""D-Bus daemon service for WhisperAloud."""

import logging
import signal as signal_module
import uuid
from pydbus.generic import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydbus import SessionBus

from ..audio.recorder import AudioRecorder, RecordingState
from ..config import WhisperAloudConfig
from ..exceptions import WhisperAloudError
from ..transcriber import Transcriber
from ..gnome_integration import NotificationManager
from ..persistence import HistoryManager

logger = logging.getLogger(__name__)

class WhisperAloudService:
    """
    <node>
      <interface name="org.fede.whisperAloud.Control">
        <method name="StartRecording"/>
        <method name="StopRecording">
          <arg type="s" direction="out"/>
        </method>
        <method name="ToggleRecording">
          <arg type="s" direction="out"/>
        </method>
        <method name="GetStatus">
          <arg type="s" direction="out"/>
        </method>
        <method name="Quit"/>
        <signal name="StatusChanged">
          <arg type="s"/>
        </signal>
        <signal name="TranscriptionCompleted">
           <arg type="s"/>
           <arg type="i"/>
         </signal>
         <signal name="HistoryUpdated">
           <arg type="i"/>
         </signal>
         <method name="ReloadConfig">
           <arg type="s" direction="out"/>
         </method>
         <signal name="ConfigReloaded"/>
         <signal name="ErrorOccurred">
           <arg type="s"/>
         </signal>
      </interface>
    </node>
    """

    def __init__(self, config: Optional[WhisperAloudConfig] = None):
        """Initialize the service."""
        self.config = config or WhisperAloudConfig.load()

        # Core components
        self.recorder: Optional[AudioRecorder] = None
        self.transcriber: Optional[Transcriber] = None

        # Threading
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="whisper-aloud")

        # State
        self._shutdown = False
        self._transcribing = False

        # GNOME integration
        self.notifications: Optional[NotificationManager] = None

        # Initialize components
        self._init_components()
        
        # Initialize notifications
        try:
            self.notifications = NotificationManager(self.config)
        except Exception as e:
            logger.warning(f"Failed to initialize notifications: {e}")

        # Initialize history manager for persistence
        self.history_manager = HistoryManager(self.config.persistence)
        self.session_id = str(uuid.uuid4())
        logger.info(f"Daemon session ID: {self.session_id}")

        logger.info("WhisperAloudService initialized")

    def _init_components(self) -> None:
        """Initialize recorder and transcriber."""
        try:
            self.recorder = AudioRecorder(self.config.audio)
            self.transcriber = Transcriber(self.config)
            self.transcriber.load_model()
            logger.info("Components initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            raise

    def run(self) -> None:
        """Run the D-Bus service."""
        try:
            # Publish service on D-Bus
            bus = SessionBus()
            bus.publish("org.fede.whisperAloud", self)
            logger.info("D-Bus service published")

            # Keep the service running
            def signal_handler(signum, frame):
                logger.info("Received signal, shutting down")
                self.Quit()

            signal_module.signal(signal_module.SIGTERM, signal_handler)
            signal_module.signal(signal_module.SIGINT, signal_handler)
            
            # Use GLib main loop if available, otherwise pause
            try:
                from gi.repository import GLib
                loop = GLib.MainLoop()
                self._loop = loop
                loop.run()
            except ImportError:
                logger.warning("GLib not available, using signal.pause()")
                signal_module.pause()

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Service failed to start: {e}")
            raise
        finally:
            self._cleanup()

    def StartRecording(self) -> None:
        """Start audio recording."""
        if not self.recorder:
            raise WhisperAloudError("Recorder not initialized")

        try:
            self.recorder.start()
            self.StatusChanged(self.recorder.state.value)
            if self.notifications:
                self.notifications.show_recording_started()
            logger.info("Recording started via D-Bus")
        except Exception as e:
            self.ErrorOccurred(str(e))
            raise

    def StopRecording(self) -> str:
        """Stop recording and start async transcription."""
        if not self.recorder or not self.transcriber:
            raise WhisperAloudError("Components not initialized")

        try:
            # Stop recording
            audio_data = self.recorder.stop()
            self.StatusChanged(self.recorder.state.value)

            # Start transcription in background thread (non-blocking)
            self._transcribing = True
            self.StatusChanged("transcribing")
            self.executor.submit(self._transcribe_and_emit, audio_data)

            # Return immediately - result will be emitted via signal
            return "transcribing"

        except Exception as e:
            self.ErrorOccurred(str(e))
            raise

    def ToggleRecording(self) -> str:
        """Toggle recording state."""
        if not self.recorder:
            raise WhisperAloudError("Recorder not initialized")

        if self.recorder.is_recording:
            return self.StopRecording()
        else:
            self.StartRecording()
            return "recording"

    def GetStatus(self) -> str:
        """Get current status."""
        if not self.recorder:
            return "error"
        if self._transcribing:
            return "transcribing"
        return self.recorder.state.value

    def ReloadConfig(self) -> str:
        """Reload configuration from file and apply changes."""
        try:
            logger.info("Reloading configuration...")
            new_config = WhisperAloudConfig.load()

            # Check if model config changed
            if (new_config.model.name != self.config.model.name or
                new_config.model.device != self.config.model.device):
                logger.info("Model config changed, reloading...")
                self.transcriber = Transcriber(new_config)
                self.transcriber.load_model()

            # Check if audio config changed
            if new_config.audio != self.config.audio:
                logger.info("Audio config changed, recreating recorder...")
                self.recorder = AudioRecorder(new_config.audio)

            self.config = new_config
            self.ConfigReloaded()
            logger.info("Configuration reloaded successfully")
            return "OK"

        except Exception as e:
            logger.error(f"Failed to reload config: {e}")
            return f"ERROR: {e}"

    def Quit(self) -> None:
        """Quit the service."""
        logger.info("Quit requested via D-Bus")
        self._shutdown = True
        
        if hasattr(self, '_loop'):
            self._loop.quit()
        else:
            import os
            os._exit(0)

    StatusChanged = signal()
    TranscriptionCompleted = signal()
    HistoryUpdated = signal()
    ConfigReloaded = signal()
    ErrorOccurred = signal()

    def _transcribe_audio(self, audio_data):
        """Transcribe audio data (runs in thread)."""
        return self.transcriber.transcribe_numpy(audio_data)

    def _transcribe_and_emit(self, audio_data) -> None:
        """Transcribe audio and emit completion signal (runs in thread)."""
        try:
            result = self._transcribe_audio(audio_data)

            # Save to history database
            try:
                entry_id = self.history_manager.add_transcription(
                    result=result,
                    audio=audio_data if self.config.persistence.save_audio else None,
                    sample_rate=self.config.audio.sample_rate,
                    session_id=self.session_id
                )
                logger.info(f"Transcription saved to database: ID {entry_id}")

                # Emit signals with entry ID
                self._transcribing = False
                self.StatusChanged("idle")
                self.TranscriptionCompleted(result.text, entry_id)
                self.HistoryUpdated(entry_id)  # Nueva señal para sincronización
                if self.notifications:
                    self.notifications.show_transcription_completed(result.text)
                logger.info("Transcription completed and signals emitted")

            except Exception as e:
                logger.error(f"Failed to save history: {e}")
                # Continue with signal emission even if history save fails
                self._transcribing = False
                self.StatusChanged("idle")
                self.TranscriptionCompleted(result.text, -1)  # -1 indicates save failed
                if self.notifications:
                    self.notifications.show_transcription_completed(result.text)

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            self._transcribing = False
            self.StatusChanged("idle")
            self.ErrorOccurred(str(e))
            if self.notifications:
                self.notifications.show_error(str(e))

    def _cleanup(self) -> None:
        """Clean up resources."""
        logger.info("Cleaning up service resources")

        # Shutdown executor
        self.executor.shutdown(wait=True)

        # Cleanup components
        if self.recorder:
            self.recorder.cancel()
        if self.transcriber:
            self.transcriber.unload_model()

        logger.info("Service cleanup complete")