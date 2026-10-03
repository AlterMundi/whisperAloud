# Draft launch post (Show HN / r/linux)

**Status: draft only.** Not posted anywhere. Fede reviews, edits, and posts
this himself, ideally timed to a tagged release and (per
`docs/research/2026-10-03-github-presentation/notes.md` §5) coordinated
with any Flathub/Product-Hunt listing rather than spread out.

Per the research notes (§1, §5): the titles of successful "Show HN"
dictation-app launches lead with "open-source," "local," "offline," or
"on-device, no model download" rather than the underlying ML architecture,
and r/selfhosted is called out as an effective subreddit for this category
(r/linux-specific launch-pattern data was not found this session — treat
r/linux as a reasonable but less evidenced choice).

---

## Show HN title (draft)

```
Show HN: WhisperAloud – offline voice dictation for Linux (GTK4 + D-Bus daemon)
```

## Show HN / Reddit body (draft)

```markdown
Hi HN — I've been building WhisperAloud, an offline voice dictation app
for the Linux desktop.

It's daemon-first: a single D-Bus service owns the microphone, the
speech-to-text model (faster-whisper, or Parakeet via sherpa-onnx for
much faster CPU throughput), and a local SQLite+FTS5 history store. A
GTK4/libadwaita app, a CLI, the tray icon, and the global hotkey are all
thin clients of that daemon over a stable, versioned D-Bus API — so they
can restart independently of an in-progress recording.

What it does:
- Press a global hotkey anywhere, speak, and the transcript is copied
  (and, where supported, pasted automatically) into the focused window.
- Fully offline: no audio or transcript ever leaves the machine; models
  are downloaded once and cached locally.
- A streaming engine (Nemotron) shows live partial transcripts as you
  speak, not just after you stop.
- `whisper-aloud transcribe` handles longer recorded meetings too —
  chunked transcription, SRT/VTT/JSON output.
- Searchable transcript history with Markdown/JSON/CSV export.

It's Linux/GNOME-first today (GTK4, systemd user service, XDG Portal
hotkey); a Flatpak build is in progress. Source install instructions and
the full D-Bus API reference are in the repo.

Repo: https://github.com/AlterMundi/whisperAloud

Built with heavy use of AI pair-programming (Claude Code) under test and
review — happy to talk about that process if people are curious, and
happy to answer questions about the daemon/D-Bus architecture, the engine
catalog, or the audio pipeline.
```

## Notes for Fede before posting

- [ ] Fill in the demo GIF link once `docs/assets/demo-dictation.gif`
      exists (a visual is one of the strongest levers per the research
      notes — don't post without it if avoidable).
- [ ] Confirm whether to mention the AI-assisted development process in
      the public post at all, and if so keep it as brief as the README's
      own "Development process" section — this draft includes one
      sentence as an offer to discuss, not a headline claim.
- [ ] Time the post to a tagged release, and to the Flatpak/Flathub
      listing landing if that's realistic (research notes recommend
      coordinating HN + Product Hunt + GitHub Trending around one release
      rather than spreading launches out).
- [ ] Re-check title length/format against current HN/Reddit norms at
      post time — this draft was not validated against either platform's
      live submission form.
