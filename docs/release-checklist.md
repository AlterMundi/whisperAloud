# Release Checklist

## Scope

Use this checklist before tagging any public release branch from `master`.

## 1. Pre-merge Gate

- `git status --short` is clean.
- Fast CI profile passes (`./scripts/test_ci.sh -q`).
- Planned release version is set in `pyproject.toml`.
- `CHANGELOG.md` contains a dated section for the target version.

## 2. Manual Smoke (Desktop Session)

- Daemon starts: `whisper-aloud-daemon` or `systemctl --user start whisper-aloud`.
- GUI connects and shows `Ready`.
- Hotkey toggles recording start/stop.
- Tray indicator updates state transitions (idle/recording/transcribing).
- History list receives new transcription entry.
- Clipboard behavior matches config (`auto_copy`/`auto_paste`).
- OSD notifications fire for start/stop/error when enabled.
- `whisper-aloud status` and `whisper-aloud reload` return expected responses.

## 3. Packaging Smoke

- Install editable package in clean venv: `pip install -e .`.
- `whisper-aloud --help` works.
- `whisper-aloud-transcribe --help` works.
- Console scripts exist in PATH:
  - `whisper-aloud`
  - `whisper-aloud-transcribe`
  - `whisper-aloud-gui`
  - `whisper-aloud-daemon`

## 4. Cut Release Branch + Tag

```bash
git checkout master
git pull --ff-only origin master
git checkout -b release/0.2.0
git push -u origin release/0.2.0
git tag -a v0.2.0 -m "WhisperAloud 0.2.0"
git push origin v0.2.0
```

## 5. Post-release

- Publish GitHub release notes from `CHANGELOG.md`.
- Include migration note: `release-0.1.0` is the frozen pre-refactor snapshot.
