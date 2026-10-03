# Security Policy

## Supported versions

WhisperAloud does not yet have a long-term-support release line; security
fixes are made against the latest `0.x` release on `master`. See
[`CHANGELOG.md`](CHANGELOG.md) for the current version.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security vulnerability.

Instead, use GitHub's private vulnerability reporting for this repository:
[Security → Report a vulnerability](https://github.com/AlterMundi/whisperAloud/security/advisories/new)
(this requires the maintainer to have enabled private vulnerability
reporting on the repo — see
[`docs/repo-metadata.md`](docs/repo-metadata.md) for the setup step). If
that is not available, open a regular issue asking for a private contact
channel and avoid including exploit details in it.

Please include:

- A description of the issue and its potential impact.
- Steps to reproduce (a minimal repro is very helpful).
- The WhisperAloud version/commit, OS, and whether you're running the
  native install, a dev checkout, or (once available) the Flatpak.

## Scope notes specific to this project

WhisperAloud is a local, daemon-first desktop app: the D-Bus daemon
(`org.fede.whisperaloud`) is only reachable on the user's own session bus,
and transcription/history/config stay on-device — there is no server
component. Security reports most relevant to this project tend to involve:

- D-Bus interface exposure (`org.fede.whisperaloud.Control`) — see
  [`docs/dbus-api.md`](docs/dbus-api.md) for the intended trust boundary
  (any process on the user's own session bus can call it, by design).
- Flatpak sandbox/portal permission scope (`docs/flatpak.md`) once the
  Flatpak build is published.
- Local file handling — config (`~/.config/whisper_aloud/`), history/audio
  archive (`~/.local/share/whisper_aloud/`), and cached models
  (`~/.cache/huggingface/`).

## Disclosure

We aim to acknowledge reports within a reasonable timeframe and will credit
reporters (unless they ask not to be) once a fix ships.
