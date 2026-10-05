# Screenshots after HIG-07 (Preferences)

Rendered from branch `feat/hig07-preferences` with the audit's capture tool (isolated Xvfb, private
D-Bus session, fake engine), GTK 4.18 and libadwaita 1.7. Only the Preferences files are kept here; the
rest of the UI is unchanged from `../screenshots-after-hig01/`.

```
docs/design/hig-audit/capture/run_capture.sh "$PWD" OUT shipped
docs/design/hig-audit/capture/run_capture.sh "$PWD" OUT shipped-dark
```

| File | State |
|---|---|
| `30-preferences-general*.png` | General page (viewport, full length, dark) |
| `31-preferences-audio*.png`, `32-preferences-history*.png` | Audio and History pages, ported row for row (redesign: HIG-09) |
| `33-preferences-general-expanded-full.png` | Model Advanced, Paste Into Active Window and Notifications expanded |
| `34-preferences-model-catalog.png` | Full catalog window, kept until the HIG-08 subpage |
| `35-preferences-invalid-shortcut.png` | Inline validation: error-styled entry row and a toast; nothing sent |
| `36-preferences-live-update.png` | Another client calls `SetConfig(paste_backend=clipboard)`; the Typing Method row follows |

Notes on the pictures:

- The capture daemon runs with the e2e harness overrides (fake engine, `auto_paste` and notifications
  off). For `33` and `36` the capture tool turns the two expanders on *visually only* (no `SetConfig`
  is sent), so the paste options can be seen.
- The Model row shows the test-only "Fake" engine, hence "0 MB" and the licence note.
- There is no Save/Cancel, no unsaved-changes guard and no "Settings saved" alert any more, so the old
  `35-settings-discard-confirm` and `36-settings-saved-alert` have no counterpart.
