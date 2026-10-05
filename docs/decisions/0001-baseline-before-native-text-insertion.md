# 0001 — Baseline before replacing the auto-paste method

- **Status:** accepted, 2026-10-05
- **Follow-up (WHISP-119):** the new method has landed. Auto-paste uses the
  GNOME Shell extension's `org.fede.whisperaloud.TextInput.InsertText`
  (extension version 5; `contrib/gnome-shell-extension/README.md`), and the
  default `clipboard.paste_backend = "auto"` never opens a RemoteDesktop
  session. The baseline method is the opt-in switch
  `clipboard.paste_backend = "remote-desktop"` (see "Rollback" below and
  `docs/configuration.md`). Field validation: section 21 of
  `docs/validation/0.3.0-field-checklist.md`.
- **Baseline tag:** `baseline/0.3.0-remotedesktop-paste`
- **Baseline bundle:** `~/REPOS/scripts/flatpak-build/whisper-aloud-0.3.0-baseline-remotedesktop.flatpak`
  (built from `ae548e7`; later commits up to the tag are documentation only)

## Context

The 0.3.0 Flatpak was validated on the maintainer's desktop (GNOME 48.7,
Wayland). With the global shortcut (Ctrl+Space) and the window closed, the
daemon records, transcribes and pastes into the focused application. Auto-paste
uses the xdg RemoteDesktop portal: one consent dialog, restore token persisted,
session prewarmed at daemon start, paste ordered after `wl-copy` completes,
focus confirmed through the GNOME Shell extension.

## Known issues of the baseline (accepted)

- GNOME shows a permanent orange "remote access" indicator while the
  RemoteDesktop session is open. This is by design in gnome-shell
  (`remoteAccess.js`), not fixable from the portal.
- Ctrl+V is synthesized as a keysym, which can fail on non-Latin layouts.
- Mic levels are low on this machine; digital input normalization is limited
  by the noise cap (speech ends around -33 to -40 dBFS).
- An empty transcription produces no user notification.

## Decision

From this point we replace the paste method with native insertion from the
GNOME Shell extension (`Main.inputMethod.commit()`, virtual-keyboard paste
fallback), per `docs/research/2026-10-05-gnome-text-insertion/report.md`.
The new method is better grounded but not yet proven in the field. The
baseline above is the known-good fallback.

## Rollback

Code:

```sh
git log --oneline baseline/0.3.0-remotedesktop-paste..master   # what changed since
git revert -m 1 <merge-commit>                                  # undo one merged change
git checkout -b rollback/remotedesktop baseline/0.3.0-remotedesktop-paste   # or restart from the baseline
```

Installed app (keeps `~/.var/app/org.fede.whisperaloud` data and permissions):

```sh
flatpak install --user -y --reinstall --bundle \
    ~/REPOS/scripts/flatpak-build/whisper-aloud-0.3.0-baseline-remotedesktop.flatpak
```

The RemoteDesktop path will stay in the code as an opt-in backend, so a
configuration switch is the first, lightest rollback step once the new method
lands.

That switch now exists (WHISP-119). First, lightest rollback step, with no
reinstall: set it in `~/.var/app/org.fede.whisperaloud/config/whisper_aloud/config.json`
(Flatpak) or `~/.config/whisper_aloud/config.json` (native),

```json
{"clipboard": {"paste_backend": "remote-desktop"}}
```

then `whisper-aloud reload` (or restart the daemon). Setting it back to
`"auto"` closes the RemoteDesktop session, and its indicator, right away.
