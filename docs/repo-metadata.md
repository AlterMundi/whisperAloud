# GitHub repo metadata checklist (WHISP-85)

Ready-to-apply checklist for the repo's GitHub-side presentation (not
version-controlled content — settings on github.com). Source:
`docs/research/2026-10-03-github-presentation/notes.md` §3, §4, §5. Commands
are given for Fede to run himself; **this ticket does not run them** (no
`gh repo edit`/API writes were made by the implementer).

All commands assume `gh` is authenticated and the working directory is a
clone of `AlterMundi/whisperAloud`, or use `--repo AlterMundi/whisperAloud`
explicitly as shown.

## 1. Repository description

Current description is generic ("An offline voice dictation app for Linux
desktop"); research notes recommend leading with the differentiators
(offline/local, named engines, platform) the way OpenWhispr/Vocalinux do:

```bash
gh repo edit AlterMundi/whisperAloud \
  --description "Offline voice dictation for the Linux desktop — GTK4 + D-Bus daemon, Whisper & Parakeet engines, no cloud."
```

## 2. Topics

Repo currently has no topics set (verified in the research session). Suggested
set, modeled on OpenWhispr/Handy/Vocalinux's own topics:

```bash
gh repo edit AlterMundi/whisperAloud \
  --add-topic speech-to-text \
  --add-topic dictation \
  --add-topic voice-typing \
  --add-topic linux \
  --add-topic gtk4 \
  --add-topic gnome \
  --add-topic dbus \
  --add-topic whisper \
  --add-topic whisper-cpp \
  --add-topic parakeet \
  --add-topic offline-first \
  --add-topic privacy-first \
  --add-topic flatpak \
  --add-topic open-source \
  --add-topic accessibility
```

(`gh repo edit` only *adds* topics with `--add-topic`; there's no
`--remove-topic` needed since none exist today. Review the final list on
the repo page afterward — GitHub caps topics at 20 and lowercases/hyphenates
them automatically.)

## 3. Social preview image

GitHub's spec: 1280×640px (2:1), PNG/JPEG, under 1MB, keep text inside the
center ~1000×500px safe area, logo/name top-left, one line of value prop
plus a visual (screenshot or the architecture diagram).

No custom social preview is set today (default GitHub-generated preview).
Suggested source material once available: a polished (SVG→PNG) version of
the architecture diagram in `README.md`, or the "main window, recording"
screenshot (see `docs/assets/README.md`), combined with the WhisperAloud
wordmark and the tagline from the README hero.

This cannot be set via `gh` — it's a web-UI-only setting:

1. Go to `https://github.com/AlterMundi/whisperAloud/settings` →
   **Social preview** → **Edit** → upload the 1280×640 image.

## 4. Website link

Set once a project site or Flathub listing exists (currently neither does).
Placeholder command for when a URL is available:

```bash
gh repo edit AlterMundi/whisperAloud --homepage "https://flathub.org/apps/org.fede.whisperaloud"
```

## 5. Discussions

Competitors in this niche (OpenWhispr, Jan, Open WebUI) use Discussions for
support/community instead of routing everything through issues.

```bash
gh repo edit AlterMundi/whisperAloud --enable-discussions
```

## 6. Issue templates and PR template

Added in this ticket (repo files, not a GitHub setting):

- `.github/ISSUE_TEMPLATE/bug_report.md` (includes an "FV checklist id"
  field for defects found via `docs/validation/0.3.0-field-checklist.md`)
- `.github/ISSUE_TEMPLATE/feature_request.md`
- `.github/ISSUE_TEMPLATE/config.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`

Nothing further to run for these — they take effect automatically once
merged to the repo's default branch.

## 7. SECURITY.md

Added in this ticket (`SECURITY.md`) pointing at GitHub's private
vulnerability reporting flow. That flow needs to be turned on once (it is
off by default):

```bash
gh api -X PUT repos/AlterMundi/whisperAloud/private-vulnerability-reporting
```

Verify it's on:

```bash
gh api repos/AlterMundi/whisperAloud/private-vulnerability-reporting
```

## 8. Flathub AI-policy check (go/no-go, not cosmetic)

Before submitting to Flathub (see README roadmap and `docs/flatpak.md`),
re-read Flathub's **current, live** generative-AI policy at
`docs.flathub.org` — the research notes found it changed more than once in
2026 and a specific wording was not independently re-confirmed this
session. This is a compliance decision for Fede to make at submission time,
not something resolved by this ticket.

## 9. `primaprashant/awesome-voice-typing` listing

Not a setting on this repo, but a concrete low-effort external PR; draft
prepared at [`docs/outreach/awesome-voice-typing.md`](outreach/awesome-voice-typing.md)
for Fede to review before it's actually opened against that repo.

## Not recommended / out of scope here

Per the research notes, a star-history chart and an in-README comparison
table were found to **not** correlate with popularity among comparable
projects and are intentionally not part of this ticket's README changes.
