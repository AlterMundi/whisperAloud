# Changelog

All notable user-facing changes to this project are documented here.

## [Unreleased]

### Changed
- Nemotron 3.5 ASR Streaming's OpenMDW-1.1 license review (open since 0.3.0)
  is complete: the engine is now a normal, visible choice in Settings and
  `whisper-aloud models`/`transcribe`/`download-model`, with no
  `--accept-license` flag required. Its license is still non-standard for
  this project, so downloading it still shows an explicit consent step (a
  dialog in Settings, a one-line notice on the CLI) -- see
  `docs/THIRD_PARTY_MODELS.md`.

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
