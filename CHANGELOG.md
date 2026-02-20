# Changelog

All notable user-facing changes to this project are documented here.

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
