# Flathub submission checklist (WHISP-61)

Status: **not submitted, and not submittable as-is.** This document maps each
current Flathub requirement to what the WhisperAloud Flatpak does, and lists the
blockers honestly. Nothing here has been sent to Flathub, and no PR has been
opened. Decisions about a submission belong to Fede.

Sources, read 2026-10-03:

- Requirements: <https://docs.flathub.org/docs/for-app-authors/requirements>
- Linter reference: <https://docs.flathub.org/docs/for-app-authors/linter>
- Generative-AI policy coverage:
  [linuxiac](https://linuxiac.com/flathub-now-rejects-ai-assisted-apps-and-submissions/),
  [OMG! Ubuntu](https://www.omgubuntu.co.uk/2026/06/flathub-bans-ai-coded-apps)
- Vocalinux precedent: flathub/flathub#9368, closed on policy grounds on
  2026-07-23. Vocalinux now ships `.flatpak` bundles on GitHub Releases
  instead ([VocaHQ/vocalinux#784](https://github.com/VocaHQ/vocalinux/issues/784)).

## Blockers

### 1. Generative-AI policy (hard blocker)

Flathub's requirements now say:

- submitters must disclose any AI-generated code, documentation or packaging;
- "Flathub manifests must not contain AI-generated or AI-assisted content";
- AI tools or agents must not open or automate submission PRs, write their
  commit messages, descriptions or review replies, and must not be used for
  linter-exception requests;
- per the May 2026 rewording, applications that contain AI-generated or
  AI-assisted code are not accepted at all, except that exceptions "may be
  granted for mature, well-maintained projects". There is no published process
  for getting one.

What this means for WhisperAloud:

- Large parts of the application code, tests and docs were written with AI
  assistance. Under the current wording the app itself is very likely
  ineligible, whatever the manifest looks like.
- `flatpak/org.fede.whisperaloud.yml`, `scripts/build_flatpak.sh`, the
  metainfo and this document were all AI-generated (WHISP-61). **They must
  not be submitted to Flathub.** A Flathub manifest would have to be written
  by hand by Fede. The files here can serve only as a description of what
  the app needs, not as the submission text.
- Fede takes authorship and responsibility for any submission, and any
  submission must disclose the AI assistance. Taking responsibility does
  not lift the ban on AI-assisted manifests, and it does not make an
  AI-assisted app eligible.
- Realistic alternatives, without the Flathub policy constraint:
  1. attach `whisper-aloud-<version>.flatpak` bundles to GitHub Releases,
     which is what Vocalinux did after #9368;
  2. host our own OSTree repo (for example on GitHub Pages) with a
     `.flatpakrepo` file, so users get `flatpak update`;
  3. ask Flathub for an exception once the project has a longer public
     history (low odds).

### 2. App ID and domain control (blocker for Flathub only)

The Flathub rule is: "The domain must be directly related to the project …
and the author … must have control over the domain." `org.fede.whisperaloud`
implies control of `fede.org`, which we do not have. The code-hosting form
would be `io.github.AlterMundi.whisperAloud`, or a lowercase variant (at least
4 components; a dash is only allowed in the last one).

Changing the app ID is not a packaging-only change. Flatpak only lets an app
own D-Bus names at or below its ID, so the daemon bus name
`org.fede.whisperaloud` (public API, `docs/dbus-api.md`), the GtkApplication
ID `org.fede.whisperaloud.Gui`, the desktop file, the metainfo, the icon and
the portal registrations would all have to move. That would be a breaking
D-Bus API change, needing a major API version bump and a compatibility period
for scripts that call the old name. The local build keeps
`org.fede.whisperaloud`, which is consistent with every current identifier.

### 3. "Built entirely from source" and prebuilt wheels (policy risk)

Requirement: "All source available submissions must be built entirely from
source code", and no binaries may be committed in the submission PR.

The manifest installs manylinux wheels from PyPI (downloaded at build time
with sha256 pins; none are committed) for:

- ctranslate2;
- av (PyAV, which bundles FFmpeg);
- numpy and scipy;
- onnxruntime;
- sherpa-onnx and sherpa-onnx-core (which bundles its own onnxruntime);
- tokenizers;
- hf-xet;
- psutil, pyyaml and cffi.

Flathub has historically tolerated PyPI wheels for some hard-to-build
scientific packages, but a reviewer can ask for source builds. In the order
reviewers would most likely object:

1. ctranslate2, onnxruntime and sherpa-onnx (large C++ builds with vendored
   dependencies);
2. av, because it bundles FFmpeg while the runtime already provides FFmpeg;
3. numpy and scipy (buildable with meson and the SDK's OpenBLAS, but slow);
4. the small ones (psutil, pyyaml, cffi, tokenizers, hf-xet).

Building all of them from source is a significant packaging project in
itself: ctranslate2 needs oneDNN or OpenBLAS, onnxruntime has a large CMake
tree, and sherpa-onnx needs both onnxruntime and kaldi-native-fbank. The
manifest also ships two copies of onnxruntime: faster-whisper's Silero VAD
dependency and sherpa-onnx-core's bundled copy.

## Requirement-by-requirement

`flatpak-builder-lint` run on the local build:

- `lint manifest` reports one error, `appid-url-not-reachable` (fede.org;
  see blocker 2);
- `lint repo` additionally reports `appstream-missing-screenshots` and
  `appstream-screenshots-not-mirrored-in-ostree` (placeholder screenshots).

There are no finish-args, desktop-file or icon findings.

| Requirement | How the manifest meets it | Status |
|---|---|---|
| Inclusion: real functional scope, not a thin wrapper | Full GTK4/libadwaita app with recording, transcription, history, settings and model management | OK |
| Not tray-only | `command: whisper-aloud-gui`. Launching opens the window. The tray is optional and the app works fully without a StatusNotifierWatcher | OK |
| Window-first startup | The desktop entry starts the GUI. The GUI's first D-Bus call auto-starts the daemon through the exported activation file `share/dbus-1/services/org.fede.whisperaloud.service`, which Flatpak rewrites to `flatpak run --command=whisper-aloud org.fede.whisperaloud --daemon`. There is no systemd unit, and `SystemdService=` is removed: Flatpak's exporter strips it anyway (`allowed_dbus_service_keys` in flatpak-dir.c) | OK |
| No host-dependent setup / portals mandatory where they exist | Paste uses the RemoteDesktop portal (WHISP-59), the hotkey uses the GlobalShortcuts portal (WHISP-64) and notifications use the Notification portal through libnotify. Inside the sandbox, ydotool, xdotool, keybinder, gsettings probing and wpctl are never used (`sandbox.is_flatpak_sandbox()` gates). `wl-copy` is built into the app (wl-clipboard module), not taken from the host | OK |
| Minimal static permissions | See the finish-args in the manifest; each one has a comment. No `home`/`host` filesystem, no session/system bus socket, no `--own-name`, no portal talk-names | OK, with two justifications below |
| `--share=network` | Only for user-initiated model downloads (Hugging Face Hub, sherpa-onnx GitHub releases), behind a license-consent step. Transcription is offline. No model is bundled: models are large and have their own licenses | Needs a reviewer note |
| `--talk-name=org.mpris.MediaPlayer2.*` | The "pause media while recording" feature, on by default. The linter does not flag it, but a reviewer may ask for it to be dropped. Without it the feature silently does nothing | Possible reviewer pushback |
| `--talk-name=org.kde.StatusNotifierWatcher` | Optional tray icon. Common on Flathub | OK |
| Runtime is the latest stable and not EOL | `org.gnome.Platform//51` | OK |
| No network during the build | All sources are pinned (`git` tag+commit, or wheel URL + sha256) | OK |
| License files in `share/licenses/$FLATPAK_ID` | flatpak-builder records them automatically for whisper-aloud, portaudio, wl-clipboard, libayatana-appindicator, libdbusmenu and ayatana-ido (verified in the build). The pip modules' licenses stay only in each wheel's `*.dist-info/`, not in `share/licenses/` | A reviewer may want the pip licenses copied too |
| Metainfo present and valid | `data/org.fede.whisperaloud.metainfo.xml` passes `appstreamcli validate --no-net --pedantic` | OK |
| Screenshots exist, are PNG and are mirrored | Placeholder URLs under `docs/screenshots/` that do not exist yet. Flathub builds mirror them with `--mirror-screenshots-url` | **TODO**: capture real screenshots |
| OARS content rating | `oars-1.1` with no flagged content: offline tool, no user-to-user interaction, no ads, no data collection | OK |
| Desktop file valid, Icon equals the app ID | `desktop-file-validate` passes. The `Exec=/usr/bin/…` prefixes are rewritten at build time. Icon `org.fede.whisperaloud` | OK |
| Icon: SVG or ≥256 px PNG | `share/icons/hicolor/scalable/apps/org.fede.whisperaloud.svg` | OK, but the artwork is a placeholder |
| English localisation complete | The UI, desktop file and metainfo are in English | OK |
| Manifest at the top level, named after the app ID, 2-space YAML | In this repo it is at `flatpak/org.fede.whisperaloud.yml`. A Flathub repo would have a hand-written copy at its top level (see blocker 1) | N/A while blocker 1 stands |
| Shared modules as a submodule | `flatpak/shared-modules/` is a vendored copy of flathub/shared-modules at `cb9ec60`. Flathub expects the git submodule | Change on submission |
| App source as a release tag | The local build uses `type: dir`. A submission would use `type: git` + `tag` + `commit` | Change on submission |
| Trademark | "Whisper" is OpenAI's model name. WhisperAloud is not affiliated with OpenAI, and the icon uses no OpenAI artwork. A reviewer might still ask about the name | Low risk |
| Development history / maintenance | Public repo with tagged releases since 0.1.0 (February 2026) | Reviewer judgement |

## Known gaps in the Flatpak build (not policy)

- Sound feedback is disabled. GSound and libcanberra and a sound theme are not
  in the GNOME runtime, and `ui/sound_feedback.py` degrades to silence. Adding
  libcanberra (a shared module), gsound and sound-theme-freedesktop would
  restore it.
- No GPU inference. CUDA libraries are not available in the sandbox, so
  ctranslate2 and onnxruntime run on the CPU.
- No autostart. The daemon starts on demand when the window opens, or from a
  desktop action, the CLI or the hotkey, but only once the daemon is running.
  To have the global shortcut work right after login, the app needs a
  "Start in background at login" switch that calls the Background portal's
  `RequestBackground(autostart=true, commandline=["whisper-aloud", "--daemon"])`.
  That is a feature (UI, consent flow, tests) and is left as a follow-up
  ticket. Until then, `docs/flatpak.md` documents a manual autostart entry.
- Under X11 (fallback-x11), the daemon has no clipboard tool (no xclip in the
  sandbox), so `ClipboardManager` uses its file fallback. Wayland sessions are
  unaffected.
