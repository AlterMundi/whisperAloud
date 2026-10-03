# README assets (pending)

This directory holds the demo GIFs and screenshots referenced from the main
[`README.md`](../../README.md). None of these files exist yet — they are
placeholders to be filled in from Fede's field test
([`docs/validation/0.3.0-field-checklist.md`](../validation/0.3.0-field-checklist.md)).
Until then the README links to this file instead of a missing image, so the
link stays valid.

## Expected files

| File | Used in | Content |
|---|---|---|
| `demo-dictation.gif` | README hero | Hotkey press → speak a sentence → live partial transcript → text pasted into a real app |
| `demo-main-window.gif` | README | Main GTK4 window, idle state |
| `demo-recording.gif` | README | Main window while actively recording (level meter / live partial transcript) |

## Additional shots worth capturing (not yet referenced from the README)

These are useful for a Flathub listing (MetaInfo `<screenshots>`) and/or a
polished architecture graphic, per
[`docs/research/2026-10-03-github-presentation/notes.md`](../research/2026-10-03-github-presentation/notes.md)
§"Assets list":

- Model picker / in-app model download (license consent + progress)
- History panel (search over the SQLite/FTS5 store)
- System tray / hotkey indicator
- Settings panel (engine/model selection)
- Terminal/CLI shot (`whisper-aloud transcribe` producing SRT/VTT/JSON)
- A polished (SVG/PNG) version of the architecture diagram, for use as the
  GitHub social preview image (see [`docs/repo-metadata.md`](../repo-metadata.md))

Once a file lands here, update the corresponding `README.md` placeholder to
reference it as a normal Markdown image.
