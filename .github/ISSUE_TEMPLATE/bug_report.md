---
name: Bug report
about: Report something that doesn't work as expected
title: "[Bug] "
labels: bug
assignees: ''
---

## Description

A clear, concise description of what's wrong.

## Steps to reproduce

1. ...
2. ...
3. ...

## Expected result

What you expected to happen.

## Actual result

What actually happened. Include any error text verbatim.

## FV checklist id (optional)

If you found this while running the field-validation checklist
(`docs/validation/0.3.0-field-checklist.md`), paste its id here (e.g.
`FV-012`) so the result can be traced back to that checklist. Leave blank
otherwise.

## Environment

- WhisperAloud version/commit:
- Install method: native (`install.sh`) / dev checkout / Flatpak
- Distro + version:
- Display server: Wayland / X11
- Speech engine in use: faster-whisper / Parakeet / Nemotron streaming

## Logs

```
journalctl --user -u whisper-aloud -n 200 --no-pager
```

(Paste relevant output above. For a Flatpak install, also run
`flatpak run --verbose org.fede.whisperaloud` and include anything relevant
from the portal/sandbox trace.)
