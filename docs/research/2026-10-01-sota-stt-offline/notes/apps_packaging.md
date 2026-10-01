# Competing local/offline dictation apps on Linux, and how they package (as of 2026-10-01)

Research date: 2026-10-01. Scope: Linux desktop dictation and transcription apps, what they ship, how users pick an engine or model, how they package, and how they handle hotkeys and text insertion on Wayland. Notable changes since about March 2026 are flagged. A few claims come only from search-engine summaries and could not be checked against a primary page. These are marked **[unverified]**.

## Which Linux dictation apps exist and are active in 2026?

### Takeaway
The field is crowded and moving fast. Handy (Tauri/Rust, about 32k stars) and OpenWhispr (Electron) are the high-velocity cross-platform leaders, each shipping releases monthly or faster through September 2026. Voxtype is the Wayland-native CLI/daemon reference. Speech Note is the established Flathub app. Two developments matter most for WhisperAloud. First, **Speed of Sound** is a GTK/libadwaita app already on Flathub that uses portals and sherpa-onnx, so it is the closest direct competitor. Second, **Canonical's Myna** is a system-level dictation stack being prepared for Ubuntu 26.10.

### Cited Findings
- **Handy** (cjpais/Handy). MIT, about 32.5k stars, 866 commits on main. — [GitHub](https://github.com/cjpais/Handy)
  - Release cadence in 2026: v0.8.2 (2026-04-02) added Cohere Transcribe, Hebrew support, faster Parakeet, and VAD loaded in parallel with ASR. v0.8.3 (2026-04-28) added KDE overlay fixes and AWS Bedrock post-processing. **v0.9.0 (2026-07-01) was a major release that added streaming model support through a transcribe.cpp integration and "expanded model availability significantly".** v0.9.2 (2026-07-12) improved GPU detection and selection and added download cancellation in onboarding. v0.9.6 (2026-08-24) added portable model storage and restored the Wayland overlay. v0.9.7 (2026-09-18) added an experimental VAD, Linux clipboard improvements and an "auto push to talk mode". — [Handy releases](https://github.com/cjpais/Handy/releases)
- **OpenWhispr** (OpenWhispr/openwhispr). Cross-platform Electron app with local models (Parakeet/Whisper) plus cloud bring-your-own-key. Releases: 1.8.3 (2026-08-13), 1.9.0 (2026-08-24), 1.10.0 (2026-09-11), 1.10.2 (2026-09-15). — [OpenWhispr releases](https://github.com/OpenWhispr/openwhispr/releases)
- **Voxtype** (peteonrails/voxtype). Rust daemon built for Wayland that runs as a systemd user service. Push-to-talk, about 1.6k stars, 152 forks. — [GitHub](https://github.com/peteonrails/voxtype). Recent release v0.7.2 added "Streaming Dictation" with Parakeet and an evdev modifier-release guard. — [voxtype releases (via search)](https://github.com/peteonrails/voxtype/releases) **[release date unverified]**
- **Speech Note / dsnote** (mkiol). Long-standing Qt app on Flathub covering STT, TTS and translation. 4.9.1 Beta 2 was posted 2026-02-16 in flathub-beta. It moves to Qt6, adds accessibility for text insertion and clipboard reading, adds inline timestamps, bumps whisper.cpp to 1.9.1 ("10% transcription time reduction"), FasterWhisper to 1.2.1 with new AMD ROCm support, and CTranslate2 to 4.8.1. — [dsnote issue #374](https://github.com/mkiol/dsnote/issues/374). 4.9.0 shipped for Sailfish OS first. — [dsnote releases](https://github.com/mkiol/dsnote/releases). The stable 4.9.1 desktop date is **unverified**: a search summary claimed "September 20, 2026", but I could not confirm it.
- **Speed of Sound** (zugaldia/speedofsound). "Voice typing for the Linux desktop". Written in Java using Java-GI (GTK4/libadwaita/GStreamer), MIT, about 203 stars, 545 commits. On Flathub as `io.speedofsound.SpeedOfSound`, plus Snap, AppImage, deb and rpm. — [GitHub](https://github.com/zugaldia/speedofsound), [Flathub](https://flathub.org/en/apps/io.speedofsound.SpeedOfSound). Covered by press in April 2026. — [OMG! Ubuntu, 2026-04](https://www.omgubuntu.co.uk/2026/04/speed-of-sound-linux-voice-typing-app), [UbuntuHandbook 2026-04](https://ubuntuhandbook.org/index.php/2026/04/speed-of-sound-new-voice-typing-app-for-linux-desktop/)
- **Myna (Canonical/Ubuntu).** Local dictation intended for Ubuntu 26.10. It consists of a "Myna orchestrator" snap, model snaps on the Snap Store, and a Myna Settings app from a PPA. Ubuntu 26.10 preinstalls a GNOME Shell extension that shows a HUD; 26.04 uses notifications instead. Status as of September 2026: testable, but "no formal call for testing" yet. — [OMG! Ubuntu 2026-09](https://www.omgubuntu.co.uk/2026/09/ubuntu-myna-ai-install), [AlternativeTo 2026-06](https://alternativeto.net/news/2026/6/ubuntu-26-10-will-feature-myna-an-ai-powered-private-and-local-speech-to-text-solution/), [It's FOSS](https://itsfoss.com/news/myna-ai-speech-to-text-tool/). It has drawn criticism as "snap-only". — [GNOME Discourse](https://discourse.gnome.org/t/proposal-desktop-wide-offline-speech-to-text-dictation-support/35858)
- **Vocalinux** (upstream VocaHQ/vocalinux, AGPL-3.0). Version 0.17.0 added the Faster Whisper and Parakeet engines. — [GitHub fork mirror](https://github.com/dkchw/vocalinux), [vocalinux.com](https://vocalinux.com/)
- **Whispering / Epicenter** (moved from braden-w/whispering to EpicenterHQ/epicenter). v7.11.0 (reportedly June 2026) has three local engines through transcribe-rs (whisper, parakeet, moonshine) and ships Linux RPM and AppImage builds plus the AUR package `epicenter-whispering-bin`. — [Epicenter releases](https://github.com/EpicenterHQ/epicenter/releases), [AUR](https://aur.archlinux.org/packages/epicenter-whispering-bin) **[version/date from search summary only]**
- **GNOME Shell extension approach**: Blurt (whisper.cpp; uses the middle mouse button or Ctrl+V), Speech2Text (Whisper), Saytype, and GNOME Speaks (Azure cloud). — [Blurt on EGO](https://extensions.gnome.org/extension/6742/blurt/), [Speech2Text on EGO](https://extensions.gnome.org/extension/8238/gnome-speech2text/), [saytype](https://github.com/arifyaman/saytype)
- Other names in 2026 roundups: VOXD, nerd-dictation, LinuxWhispr, EasySpeak (billed as "GNOME- and Wayland-first"), ibus-speech-to-text. — [Weesper blog 2026-06-18](https://weesperneonflow.ai/en/blog/2026-06-18-voice-dictation-linux-open-source-tools-2026/) (vendor blog), [GNOME Discourse thread](https://discourse.gnome.org/t/proposal-desktop-wide-offline-speech-to-text-dictation-support/35858)

### Inferences
- The clear positioning gap is: a GNOME-native app on Flathub that uses portals and D-Bus, has searchable history, and supports multiple engines. Speed of Sound already holds part of it (Flathub, portals, sherpa-onnx, libadwaita) but has no history or FTS feature as far as I found. Myna will dominate on Ubuntu but is snap-based. WhisperAloud's differentiation (WHISP-40: D-Bus API, FTS5 history) should be measured mainly against Speed of Sound and Myna, not Handy.
- Since mid-2026, streaming dictation has become standard: Handy 0.9.0 and Voxtype 0.7.x both ship it.

### Gaps
- I did not check every individual app's activity, notably Buzz, Vibe, WhisperWriter, nerd-dictation and VOXD. I ran out of tool budget, so their 2026 status is unknown.
- I found no exact release date for Voxtype v0.7.2 or for the stable Speech Note 4.9.1 desktop release.
- A search summary claimed "Moondream released Parakeet Ultra and Redux" in September 2026. I could not verify this and it may be a hallucination by the summarizer, so it is excluded.

## Which engines and models does each app support, and how does the picker UX work?

### Takeaway
By 2026 the baseline set is Whisper (whisper.cpp or CTranslate2) plus Parakeet V2/V3, with Moonshine, **Cohere Transcribe** (added by Handy, OpenWhispr and Voxtype in April–August 2026) and Parakeet fine-tunes as differentiators. sherpa-onnx is the common runtime for non-Whisper models. Pickers converge on curated cards with size and accuracy hints, a recommended default, download on demand with cancel, and lately GPU detection.

### Cited Findings
- **Handy**:
  - Model cards list Whisper Small (487 MB), Medium (492 MB), Turbo (1.6 GB) and Large (1.1 GB) as GPU-accelerated. Parakeet V3 is described as "CPU-optimized model with excellent performance and automatic language detection". Models download on demand. Users can drop custom `.bin`/`.gguf` Whisper models into the models directory, where they are auto-discovered on restart. — [Handy README](https://github.com/cjpais/Handy)
  - Release history: Cohere Transcribe in v0.8.2; streaming models through transcribe.cpp in v0.9.0; GPU detection and selection plus cancel during onboarding download in v0.9.2; "model card decluttering" in v0.9.3; portable model storage in v0.9.6. — [Handy releases](https://github.com/cjpais/Handy/releases)
  - A third-party review lists Parakeet V2/V3 and Moonshine in the picker. — [Spokenly review](https://spokenly.app/blog/handy-review) (competitor's blog)
- **OpenWhispr**:
  - 1.10.0 (2026-09-11) introduced **"Orukeet, the new recommended local model"**, a multilingual Parakeet fine-tune running on sherpa-onnx, placed as "the first tab in the local model picker" for new installs. The same release made local models up to 3x faster on Apple silicon (an 11 s clip dropped from 605 ms to 173 ms).
  - 1.8.3 (2026-08-13) added Cohere Transcribe offline, "the most accurate offline model OpenWhispr ships, 5.42% average WER across 14 languages". — [OpenWhispr releases](https://github.com/OpenWhispr/openwhispr/releases)
- **Voxtype**:
  - Nine engines: whisper.cpp (default, 99 languages), and through ONNX Parakeet (English), Moonshine (English), SenseVoice (CJK), Paraformer, Dolphin (40+ languages), Omnilingual (1600+ languages), Cohere Transcribe (14 languages), and OpenVINO Whisper (Intel NPU).
  - Model selection is a TUI/CLI flow (`voxtype setup model`, `voxtype configure`) with automatic download. It recommends "base" as the balance point. Models load on demand and unload when idle. — [Voxtype README](https://github.com/peteonrails/voxtype)
- **Speed of Sound**: supports Whisper, Parakeet, Canary and other models through sherpa-onnx. It **bundles a small multilingual Whisper model by default**, with optional larger downloads. It offers switchable primary and secondary languages, plus optional LLM post-processing (Anthropic, Google, OpenAI, or self-hosted vLLM, Ollama or llama.cpp). — [GitHub](https://github.com/zugaldia/speedofsound)
- **Myna**: separate model snaps for Parakeet ("25 languages and punctuation"), Whisper and FunASR, each 1–2 GB. The Settings app chooses model size, language and hotkey. The first-run wizard did not work during testing. — [OMG! Ubuntu](https://www.omgubuntu.co.uk/2026/09/ubuntu-myna-ai-install)
- **Vocalinux**: whisper.cpp (default), OpenAI Whisper, Faster Whisper, VOSK, Parakeet, and an optional remote HTTP API. Settings has a simple "Speech Model" section with an expandable **"Advanced"** area for engine type, model size and specialization. — [Vocalinux](https://github.com/dkchw/vocalinux)
- **Speech Note**: engines are whisper.cpp (with Vulkan), FasterWhisper (CrisperWhisper and KBLab variants), Vosk, and others. It uses "simplified engine profiles" (4.7.0) and a "Rules" text-transformation feature. — [dsnote releases](https://github.com/mkiol/dsnote/releases)
- **Whispering**: local engines through transcribe-rs (whisper, parakeet, moonshine), or Speaches as a local server, or bring-your-own-key cloud. — [Epicenter releases (search summary)](https://github.com/EpicenterHQ/epicenter/releases)

### Inferences
- For WHISP-42, the dominant UX pattern is: (1) one recommended default, highlighted; (2) model cards that show size, speed and accuracy, plus a language-coverage tag (EN-only vs multilingual); (3) download with progress and cancel; (4) hardware-aware hints (GPU detected → suggest Whisper Turbo; CPU-only → suggest Parakeet V3); (5) an "Advanced" disclosure for engine, compute type and custom paths. Vocalinux's simple-plus-advanced split fits libadwaita (`AdwExpanderRow`).
- WhisperAloud's engine pair (faster-whisper plus Parakeet through sherpa-onnx) matches the 2026 baseline. Cohere Transcribe and Parakeet fine-tunes (Orukeet) are the next models competitors are adding. sherpa-onnx already gives access to several of them, which argues for an engine abstraction that is "runtime → model catalog" rather than hard-coded pairs.

### Gaps
- I found no source describing language-aware filtering in the pickers, such as hiding English-only models when a non-English language is selected. Myna and Speed of Sound let users choose a language, but how that interacts with the model list is not documented in what I read.

## Flatpak specifics: GPU runtimes, model storage, hotkeys, text insertion, Flathub policy

### Takeaway
Two things decide Flathub acceptance for dictation apps in 2026:
- **Use the portals.** That means GlobalShortcuts for hotkeys and RemoteDesktop for typing; ydotool/uinput is not acceptable.
- **Have a real app window.** Reviewers reject apps that live only in the tray.

Flathub also enforces its generative-AI policy against AI-written submissions. Speed of Sound shows that the portal-only route is viable on Flathub. Speech Note handles CUDA and ROCm through optional add-ons instead of bundling them in the main app.

### Cited Findings
- **Vocalinux Flathub submission #9368 was closed 2026-07-23** for three reasons:
  - 2026-07-17, @barthalion: "Shortcuts don't seem to work on Wayland. You should be using GlobalShortcuts portal for that."
  - 2026-07-18, @teamcons: conflict with Flathub's **generative AI policy**, because the manifest and CI were AI-assisted.
  - 2026-07-23, @CodedOre: "this is a tray-only application. Which are not permitted on Flathub". The submitter replied that the app has a full settings window.
  - — [flathub/flathub#9368](https://github.com/flathub/flathub/pull/9368)
- Vocalinux now distributes a manual Flatpak bundle, plus AppImage, AUR, PyPI and Snap (edge). Its text injection uses IBus, wtype, ydotool or clipboard. — [Vocalinux README](https://github.com/dkchw/vocalinux)
- **Speed of Sound on Flathub** uses XDG Desktop Portals **GlobalShortcuts and RemoteDesktop** to insert text into the focused app on X11 and Wayland. It also offers an in-app button and a tray. — [GitHub](https://github.com/zugaldia/speedofsound), [Flathub](https://flathub.org/en/apps/io.speedofsound.SpeedOfSound)
- **Speech Note on Flathub**: 4.9.1 updates CUDA for newer NVIDIA GPUs and adds ROCm, which requires installing "the AMD add-on". — [dsnote #374](https://github.com/mkiol/dsnote/issues/374). Earlier, 4.8.0 added Wayland support for text insertion and global shortcuts, and Vulkan for whisper.cpp. — [dsnote releases](https://github.com/mkiol/dsnote/releases)
- **Handy** ships deb and AppImage, and documents Fedora. I found no Flathub listing. On Wayland it needs wtype, ydotool or dotool for typing, and uses CLI flags, compositor keybindings or `pkill -USR2` signals for hotkeys. — [Handy README](https://github.com/cjpais/Handy)
- **Voxtype**:
  - Hotkeys use compositor-native bindings that call `voxtype record start/stop`, with key-release push-to-talk on Hyprland, Sway, River, KDE and GNOME. The fallback is evdev, which requires the user to be in the `input` group.
  - Typing uses wtype first, then dotool (which handles XKB non-US layouts), then ydotool, then clipboard.
  - Packaging: AUR, deb, rpm, Homebrew, AppImage (separate CPU/Vulkan and ONNX/CUDA variants) and Nix. There is no Flatpak.
  - — [Voxtype README](https://github.com/peteonrails/voxtype)
- **OpenWhispr** 1.9.0 (2026-08-24): "Push-to-talk on Wayland now works on Hyprland, KDE, and GNOME 48+", with auto-paste fixes for non-QWERTY layouts. It ships AppImage, deb and rpm. — [OpenWhispr releases](https://github.com/OpenWhispr/openwhispr/releases), [repo](https://github.com/OpenWhispr/openwhispr)
- Handy, in v0.9.6, restored the Wayland overlay and fixed paste-method detection. — [Handy releases](https://github.com/cjpais/Handy/releases)

### Inferences
- For WHISP-39 (Flatpak):
  - Use GlobalShortcuts for the hotkey. WhisperAloud already falls back from portal to keybinder, but the Flatpak build should be portal-only.
  - Use RemoteDesktop portal keyboard emulation, or clipboard plus a notification, instead of ydotool.
  - Make sure the GTK window is the main entry point, not the tray.
  - Ship CUDA as an optional extension or add-on, following the Speech Note model, rather than in the base app.
  - Be careful about AI-assisted manifests given the reviewer stance in #9368.
- Running the daemon as a systemd user service conflicts with Flatpak's model. Speed of Sound runs in a single process. A Flatpak WhisperAloud likely needs the daemon started on demand inside the sandbox through D-Bus activation and `--own-name=org.fede.whisperaloud`. This is my inference; I found no source on how others handle it.
- Bundling a small default model (Speed of Sound's approach) avoids a first-run network dependency and an empty state.

### Gaps
- I did not find Flathub manifests showing how sherpa-onnx or ONNX Runtime is bundled, for example Speed of Sound's manifest. Inspecting `flathub/io.speedofsound.SpeedOfSound` directly would close this gap.
- I found no explicit Flathub statement of policy on downloading models at runtime. Speech Note and Speed of Sound both do it, so it appears to be tolerated.

## Desktop-integration developments in 2026 (GNOME/KDE APIs, IBus/Fcitx, accessibility)

### Takeaway
Neither GNOME nor KDE has an official speech or dictation API as of October 2026. There is an open proposal on GNOME Discourse (June 2026) for a desktop-wide D-Bus dictation service with pluggable backends. That is almost exactly WhisperAloud's architecture, but no core developer has adopted it. Canonical's Myna, a snap plus a GNOME Shell HUD extension in Ubuntu 26.10, is the only vendor-backed effort. Input-method approaches (ibus-speech-to-text, Vocalinux's IBus injection) exist but are niche.

### Cited Findings
- On 2026-06-26, "Haydn" proposed on GNOME Discourse a D-Bus service with pluggable backends (Whisper, Faster-Whisper, Vosk), with a shortcut, an on-screen recording indicator, desktop-wide text insertion, engine selection, and offline-only operation. The proposal describes it as "something the desktop itself could provide".
- Replies pointed instead to Myna, Speed of Sound (by its author Antonio Zugaldia), ibus-speech-to-text (Adrian Vovk), and EasySpeak (by its developer Peter Bittner, 2026-06-30). I found no official GNOME adoption in the thread.
- — [GNOME Discourse](https://discourse.gnome.org/t/proposal-desktop-wide-offline-speech-to-text-dictation-support/35858)
- Myna in Ubuntu 26.10 has a preinstalled GNOME Shell extension HUD with several styles (default, vumeter, ribbon, bar). The default hotkey is Super+J or Super+T. — [OMG! Ubuntu](https://www.omgubuntu.co.uk/2026/09/ubuntu-myna-ai-install)
- Speech Note 4.9.1 improves accessibility of text insertion and clipboard reading. It also exposes D-Bus actions such as `start-reading-url` and `transcribe-file`, the latter taking an output-file parameter. — [dsnote #374](https://github.com/mkiol/dsnote/issues/374) (actions per search summary of the same issue). Speech Note has had a D-Bus API since 4.7.0. — [dsnote releases](https://github.com/mkiol/dsnote/releases)
- Vocalinux supports Wayland on GNOME, KDE, Sway and Hyprland through IBus integration. — [vocalinux.com/wayland](https://vocalinux.com/wayland/)

### Inferences
- WhisperAloud's D-Bus daemon matches what the GNOME proposal asks for. Publishing a stable, documented `org.fede.whisperaloud.Control` interface and joining that Discourse thread is a concrete WHISP-40 move. Speech Note is the main prior art for a dictation app with a D-Bus API.
- An IBus engine would give the most "native" insertion on GNOME Wayland, avoiding RemoteDesktop permission prompts, but it cannot run inside Flatpak. It would be a separate system-package component.

### Gaps
- I found nothing on KDE-specific dictation work in 2026, such as a Plasma speech API or KDE accessibility initiatives. The searches did not reach KDE blogs within the tool budget.
- I did not confirm what Myna uses to insert text (IBus, portal, or something else).
