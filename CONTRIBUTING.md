# Contributing to WhisperAloud

Thanks for considering a contribution. This document covers the dev
environment, test commands, and repository layout; see
[`README.md`](README.md) for the end-user overview and
[`CLAUDE.md`](CLAUDE.md) for the conventions this repository uses with AI
coding agents (Claude Code in particular).

## Workflow

1. Create a feature branch from `master`.
2. Keep changes scoped and tested.
3. Run `./scripts/test_ci.sh` before opening a PR.
4. Include rationale for user-facing behavior changes (what changed and
   why, not just what).
5. Use [Conventional Commits](https://www.conventionalcommits.org/) style
   messages (`feat(scope): ...`, `fix(scope): ...`, `test(scope): ...`,
   `docs(scope): ...`, `refactor(scope): ...`).

## Development setup

```bash
# IMPORTANT: keep system GTK bindings visible in the venv
python3 -m venv ~/.venvs/whisper_aloud --system-site-packages
source ~/.venvs/whisper_aloud/bin/activate

pip install -e ".[dev]"
python scripts/check_dependencies.py
```

Quality checks:

```bash
python -m ruff check src tests
python -m mypy src
python -m black src tests   # line length 100, see pyproject.toml
```

## Testing

Several of these wrap the real `pytest` invocation instead of calling it
directly because the daemon, D-Bus, and GTK touch the real desktop by
default (the session bus, the display, the clipboard); the wrappers give
each suite its own disposable bus/display/HOME so a test run can never
reach your session.

```bash
source ~/.venvs/whisper_aloud/bin/activate
pip install -e ".[dev]"

# Unit tests: fast, no display/audio/dbus (same subset CI's `fast` job runs)
./scripts/test_ci.sh

# e2e: real daemon subprocess + fake audio/STT, on a private dbus-run-session bus
./scripts/test_e2e.sh

# GUI smoke tests: real GTK4/libadwaita widgets under a private Xvfb display
# and D-Bus session, with a disposable HOME/XDG tree
./scripts/test_gui.sh

# Any test marked dbus_private (e.g. the hotkey/portal flow), on a private bus
./scripts/test_private_bus.sh -m dbus_private tests/test_hotkey_portal_flow.py
```

`test_e2e.sh` and `test_private_bus.sh` honor a `PYTHON` env var (defaulting
to `python`, i.e. whatever is first on `PATH`) to pick the interpreter, and
`test_gui.sh` honors `WHISPER_ALOUD_VENV_PYTHON` (defaulting to
`~/.venvs/whisper_aloud/bin/python`) for the same purpose — CI uses these to
point at a `--system-site-packages` venv built from the runner's own
`python3`.

Never run tests against your real session bus, config (`~/.config/whisper_aloud`),
or data (`~/.local/share/whisper_aloud`) — always go through the scripts
above, which isolate HOME/XDG/the bus.

## Repository layout

```text
src/whisper_aloud/
  audio/         # recording + processing
  clipboard/     # copy/paste integration
  persistence/   # SQLite history + archive
  service/       # daemon, dbus client, hotkey, tray
  ui/            # GTK4 client
  __main__.py    # CLI entrypoint

data/            # systemd/dbus/desktop metadata
scripts/         # validation and utility scripts
tests/           # unit + integration tests
docs/            # reference docs (D-Bus API, config, CLI, Flatpak notes, ...)
```

## AI-assisted development

This project is developed with heavy use of AI pair-programming (Claude
Code); see [`CLAUDE.md`](CLAUDE.md) for the per-ticket workflow, branch, and
safety conventions used when an agent implements a change. Human-authored
or AI-assisted, every change goes through the same review and test gate
described above.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`. If you're filing a
defect found via the field-validation checklist
([`docs/validation/0.3.0-field-checklist.md`](docs/validation/0.3.0-field-checklist.md)),
include its `FV-0NN` id in the "FV checklist id" field so the result can be
traced back to that checklist.
