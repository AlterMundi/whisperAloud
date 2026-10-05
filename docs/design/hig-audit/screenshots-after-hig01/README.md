# Screenshots after HIG-01

Rendered from branch `feat/hig01-libadwaita` with the audit's capture tool (isolated Xvfb, private
D-Bus session, fake engine), GTK 4.18 and libadwaita 1.7:

```
docs/design/hig-audit/capture/run_capture.sh "$PWD" "$PWD/docs/design/hig-audit/screenshots-after-hig01" shipped
docs/design/hig-audit/capture/run_capture.sh "$PWD" "$PWD/docs/design/hig-audit/screenshots-after-hig01" shipped-dark
```

File names match `../screenshots/` (before HIG-01). Since HIG-01 the app is an `Adw.Application`, so
`shipped` *is* the libadwaita look. For an Adw app, `shipped-dark` forces dark style through
`Adw.StyleManager` (libadwaita ignores the GtkSettings dark-theme property), and it now also captures the
narrow-width states (`40`, `41`).

## Visible differences from `../screenshots/`

- Header bar: `Adw.HeaderBar` inside an `Adw.ToolbarView`. It has flat icon buttons (no frames), round
  window controls and no gradient. The controls and their order are unchanged.
- Main content: the custom bordered and shadowed "surfaces" around the record panel and the transcript are
  gone. The transcript sits in an Adwaita `card`, the history list in a `card`, and the record/cancel
  buttons are real Adwaita pills.
- Stop and Cancel buttons use the Adwaita destructive style, which is a red tint with red text and no
  longer a solid red fill. Changing that style is HIG-05.
- The level meter track, ticks, border and peak marker follow the theme foreground, so the track is light
  in light style. Tentative partial text is dimmed from the foreground and is no longer a literal grey.
- The status bar is the toolbar view's bottom bar. Its numbers use `numeric` (tabular figures).
- The history rows' titles use regular weight. The custom row radius, margins and minimum height are gone.
- Settings and Keyboard Shortcuts get Adwaita header bars with bold titles and Adwaita boxed lists and
  rows (the `Adw.ComboRow`/`ExpanderRow` rows now have proper height and padding). The pill tab switcher
  becomes Adwaita's linked switcher. Nothing else changes structurally (HIG-07).
- Dark style follows the desktop through `Adw.StyleManager`, and in dark the transcript card has no inset
  view background.
- Minimum window size (see `metrics.json`) went from 785×490 to 721×391 with history shown, because the
  custom padding is gone. The width with history hidden is unchanged at 577. The 360 px target is HIG-12.
