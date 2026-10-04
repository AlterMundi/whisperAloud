# Changelog

All notable user-facing changes to this project are documented here.

## [Unreleased]

### Added
- Input normalization is now the first stage of live audio processing. A
  digital, speech-level-driven gain lifts quiet microphones to a known
  level (speech toward -20 dBFS by default, less when background noise would be lifted above -50 dBFS) before trimming, the noise gate and the
  rest of the existing chain run. It only amplifies, never clips, caps how
  far it raises background noise, and never changes the device volume.
  This fixes incomplete transcripts from quiet mics, especially under
  Flatpak, where the old wpctl gain raise can't run. Configure it with the
  new `input_gain` config section. `GetStatus()` reports it under
  `"input_gain"` (D-Bus API 1.5.0).
- The speech model now loads in the background as soon as the daemon starts,
  instead of on the first transcription (which made the first dictation
  after every start wait ~9 s). Model readiness is visible: the GUI status
  bar shows "Loading model…", "Model ready", "Model missing — Download" or
  the load error, `whisper-aloud status` shows it next to the model, and
  D-Bus clients get `GetStatus()["model_state"]`/`["model_error"]` and the
  new `ModelStateChanged` signal (D-Bus API 1.6.0). A recording made while
  the model is still loading is transcribed as soon as it is ready; a model
  that is not downloaded is never fetched automatically and fails with a
  clear "Model not available" error instead.

### Changed
- Streaming engines (Nemotron) refuse to start recording until the model is
  loaded (`Error("model_not_ready")`) instead of loading it synchronously
  and freezing the daemon, and a model change in Settings now loads in the
  background instead of blocking the daemon.
- Nemotron 3.5 ASR Streaming's OpenMDW-1.1 license (pending since 0.3.0) was
  reviewed and accepted by the maintainer on 2026-10-03 (not via a formal
  legal review): the engine is now a normal, visible choice in Settings and
  `whisper-aloud models`/`transcribe`/`download-model`, with no
  `--accept-license` flag required. Its license is still non-standard for
  this project, so downloading it still shows an explicit consent step (a
  dialog in Settings, a one-line notice on the CLI) -- see
  `docs/THIRD_PARTY_MODELS.md`.

### Fixed
- In-app/CLI download of the Nemotron engine (`DownloadModel` over D-Bus)
  always failed with "Downloading is not supported for engine
  'nemotron-streaming'", even though it has a catalog download URL like
  Parakeet -- the daemon's download-backend routing is now derived from
  that URL instead of a hardcoded engine-name check.

## [0.3.0] - 2026-10-02

### Added
- Catalog-driven speech engine selection: pick a model from Settings and the
  daemon resolves the right engine (`faster-whisper`, Parakeet, Nemotron)
  automatically, instead of being hard-wired to Whisper.
- Parakeet (sherpa-onnx) as a selectable production transcription engine,
  offering much faster CPU throughput than `faster-whisper` for long files.
- Nemotron streaming engine with live partial transcripts: the main window
  now shows words as they are recognized during a recording, via a new
  `PartialTranscript` D-Bus signal, instead of only the final text.
- In-app model download from Settings, with progress, cancel, and an
  explicit license-consent step before any model is fetched.
- `whisper-aloud transcribe` CLI gains `--engine` selection, chunked
  transcription of long recordings, `srt`/`vtt`/`json` output formats, and
  direct `m4a` (and other non-WAV) input via `ffmpeg`/PyAV decoding.
- `whisper-aloud history list`, `history favorites`, and `history export`
  CLI subcommands, backed by a new daemon `ExportHistory` D-Bus method
  (D-Bus API 1.4.0).
- D-Bus activation for `org.fede.whisperaloud`: launching any client now
  starts the daemon on demand (via the systemd user unit, with a direct
  `Exec=` fallback for non-systemd sessions), and the GUI shows its window
  immediately instead of waiting on a slow first connection.
- Portal-based paste support when running WhisperAloud as a Flatpak.

### Changed
- Only one mechanism now starts the daemon per session (systemd unit, D-Bus
  activation, or XDG autostart no longer race each other), and the global
  shortcut is no longer re-prompted for on every startup.
- Config reload (`ReloadConfig`) now fails loudly on a malformed config
  file instead of silently resetting to defaults, and config writes/reads
  are hardened against concurrent access.
- Stale transcription results from a cancelled or superseded job are now
  dropped instead of being delivered late; a model reload requested while
  the daemon is busy is deferred until it is safe to apply (D-Bus API
  1.1.1).

### Developer / Testing
- New end-to-end (`tests/e2e/`), GUI (`tests/gui/`), and private-D-Bus-bus
  test harnesses, plus fake audio/engine backends, so the full
  record-to-history pipeline and GTK4 UI can be exercised without a real
  microphone, display, or session bus.
- Desktop/session isolation for tests (private bus, temp `HOME`/XDG dirs)
  so the suite never touches a developer's real WhisperAloud session,
  config, or portal dialogs.
- New CI jobs covering the e2e and GUI test suites alongside the existing
  fast unit-test profile.
- Versioned D-Bus API reference (`docs/dbus-api.md`) and an `ApiVersion`
  daemon property, now at `1.4.0`.

## [0.2.0] - 2026-02-20

### Added
- Daemon-first operational model as the primary runtime path.
- D-Bus command set for lifecycle and recording control (`start`, `stop`, `toggle`, `status`, `cancel`, `reload`, `quit`).
- Daemon history D-Bus operations for search/favorites/delete/toggle favorite used by GUI history panel.
- CI installer-matrix validation step (`scripts/validate_install_matrix.sh`) in packaging smoke job.
- Updated repository documentation map and release process references.

### Changed
- `master` now tracks the refactored migration baseline.
- Installation and troubleshooting docs aligned with current script and command paths.
- CI profile formalized around deterministic fast checks and release-readiness validation.
- Clipboard test suite made headless-deterministic and promoted to fast/full test profiles.
- Reconnection flow now synchronizes GUI state from daemon status to avoid zombie `idle` assumptions.
- History panel grouping/query decision logic extracted into GTK-free module with direct unit coverage.

### Migration
- Pre-refactor snapshot preserved under tag `release-0.1.0`.

## [0.1.0] - 2026-02-19

### Added
- Initial public packaging baseline.
