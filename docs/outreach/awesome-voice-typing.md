# Draft PR to `primaprashant/awesome-voice-typing`

**Status: draft only.** Not opened. Fede reviews and opens it himself.

Source: `docs/research/2026-10-03-github-presentation/notes.md` §5, which
identifies [`primaprashant/awesome-voice-typing`](https://github.com/primaprashant/awesome-voice-typing)
as an actively maintained curated list (entries: name, platforms,
local/hybrid/cloud mode, engine, one-line summary) that already lists
Handy, OpenWhispr, Speed of Sound, Vocalinux, VOXD, nerd-dictation, and
Elograf, but not WhisperAloud.

## Entry to add

Matching that list's existing format as closely as possible (verify the
exact column headers/ordering in the live `README.md` before opening the
PR — they were not re-fetched for this draft):

| Name | Platform | Mode | Engine | Description |
|---|---|---|---|---|
| [WhisperAloud](https://github.com/AlterMundi/whisperAloud) | Linux | Local | Whisper (faster-whisper) / Parakeet | Daemon-first offline dictation for Linux/GNOME — GTK4 app + D-Bus daemon, global hotkey, streaming partials, searchable history. |

## PR title

```
Add WhisperAloud (Linux, local Whisper/Parakeet dictation)
```

## PR description draft

```markdown
Adds WhisperAloud, a Linux/GNOME dictation app with a daemon-first
architecture: a D-Bus daemon (`org.fede.whisperaloud`) owns recording,
transcription, and history, while a GTK4/libadwaita app and a CLI act as
thin clients.

- Fully offline speech-to-text via faster-whisper, with Parakeet
  (sherpa-onnx) as a faster alternative engine and a streaming
  (Nemotron) engine for live partial transcripts.
- Global hotkey (XDG Portal / libkeybinder3), system tray, searchable
  SQLite+FTS5 history with Markdown/JSON/CSV export.
- Source install today; Flatpak packaging in progress
  (https://github.com/AlterMundi/whisperAloud/blob/master/docs/flatpak.md).
- MIT licensed.

Repo: https://github.com/AlterMundi/whisperAloud
```

## Before opening this PR

- [ ] Re-fetch the live `awesome-voice-typing` README and confirm the exact
      table columns/format haven't changed since this research session.
- [ ] Decide whether to wait for the Flatpak listing (closer to how most
      other entries describe their primary install path) or submit now
      with "source install" noted.
- [ ] Have Fede open the PR from his own GitHub account — this draft is
      content only, not an authorization to open it automatically.
