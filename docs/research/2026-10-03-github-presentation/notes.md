# WHISP-85 Research Notes: GitHub Presentation Patterns for AI/ML Open-Source Products (2026)

Research date: 2026-10-03. Method: web search + fetch only (no repo changes, app not run).
All findings below are cited with a URL. Items that could not be independently confirmed
(e.g. via a second source) are explicitly flagged **[UNVERIFIED]**.

---

## 1. Who is actually popular right now (local-first / desktop / dictation-adjacent)

Star counts below are as reported by search snapshots in 2026; treat as approximate —
GitHub stars move daily and different sources disagree by a few thousand. Cross-checked
against at least one source per repo.

| Project | Repo | ~Stars (2026) | Category | Notes |
|---|---|---|---|---|
| whisper.cpp | [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) | ~54.1k | STT engine | The de facto offline Whisper runtime; almost every dictation app above it depends on it or whisper.cpp bindings. [Source](https://github.com/ggml-org/whisper.cpp) |
| Jan | [menloresearch/jan](https://github.com/menloresearch/jan) | ~41.9k (Mar 2026) | Local-first LLM desktop app | Apache-2.0, Tauri-based, "ChatGPT alternative that runs 100% offline." [Source](https://sinhaabhinav.in/blog/jan-ai-review-2026) |
| Open WebUI | [open-webui/open-webui](https://github.com/open-webui/open-webui) | ~153.9k | Self-hosted AI platform | Far and away the largest in this set; MCP support listed as a GitHub topic. [Source: live repo page] |
| Handy | [cjpais/Handy](https://github.com/cjpais/Handy) | ~32.7k | Local dictation (desktop, Tauri/Rust) | Cross-platform (Mac/Win/Linux), MIT, funded via GitHub Sponsors, no telemetry. [Source](https://www.openassistivetech.org/handy-the-dictation-app-that-actually-respects-you/), [repo](https://github.com/cjpais/Handy) |
| OpenWhispr | [OpenWhispr/openwhispr](https://github.com/OpenWhispr/openwhispr) | ~9.0k | Dictation + agents, local/cloud hybrid | Electron app, BYOK cloud + local Parakeet/Whisper/sherpa-onnx. [Source: live repo page] |
| Ollama | [ollama/ollama](https://github.com/ollama/ollama) | 100k+ (widely reported, not re-verified this session) **[UNVERIFIED exact count]** | Local LLM runtime | README is almost entirely a "Community Integrations" directory — see §2. |
| Speed of Sound | [zugaldia/speedofsound](https://github.com/zugaldia/speedofsound) | not captured this session **[UNVERIFIED]** | Linux-only voice typing | Closest direct competitor to WhisperAloud: Linux desktop, Whisper/Parakeet/Canary engines, Flathub + Snap distribution, X11+Wayland via portals. [Source](https://github.com/zugaldia/speedofsound) |
| Vocalinux | [TeamADAPT/vocalinux](https://github.com/TeamADAPT/vocalinux) (also forked under VocaHQ) | ~899 | Linux offline dictation | whisper.cpp/Whisper/VOSK/Parakeet engines, AGPL-3.0, X11+Wayland. Topics: `accessibility, dictation, gpu-acceleration, linux, offline-first, privacy-first, python, speech-recognition, speech-to-text, voice, voice-typing, vosk, wayland, whisper, whisper-cpp`. [Source](https://www.blog.brightcoding.dev/2026/04/26/vocalinux-revolutionary-offline-voice-dictation-for-linux) |
| VOXD | [jakovius/voxd](https://github.com/jakovius/voxd) | ~298 | Linux dictation (GUI+tray+CLI) | Topics: `ai-transcription, dictation, linux, open-source, user-friendly, voice-to-text, voice-transcription, voice-typing`. [Source: search snapshot] |
| Whispering / Epicenter | [EpicenterHQ/epicenter](https://github.com/epicenter-so/epicenter) (renamed/moved from [braden-w/whispering](https://github.com/braden-w/whispering)) | original repo ~291 stars before the move **[UNVERIFIED current combined count]** | Local-first dictation, now a broader "local-first apps" platform | HN "Show HN: Whispering" post: 591 points / 152 comments (2025-08-18) — one of the best-performing dictation launches found. [Source](https://news.ycombinator.com/item?id=44942731) |

**Hacker News "Show HN" performance for dictation apps (front-page hits found this session):**
- Whispering — 591 pts / 152 comments, 2025-08-18. [HN](https://news.ycombinator.com/item?id=44942731)
- Ghost Pepper (macOS hold-to-talk STT) — 467 pts / 200 comments, 2026-04-06. [HN](https://news.ycombinator.com/item?id=... see search result title "Show HN: Ghost Pepper")
- Handy — 247 pts / 110 comments, 2026-01-15.
- Yap (on-device macOS dictation, no model download) — 104 pts / 44 comments, 2026-07-27. [HN](https://news.ycombinator.com/item?id=49073834)
- TongueType (local Whisper dictation, macOS) — [HN](https://news.ycombinator.com/item?id=48148106)
- All four found via one search snapshot; exact point/comment counts **[UNVERIFIED against live HN page — could have grown since]**.

**Takeaway:** the pattern that correlates with high stars in this niche is (a) true zero-cloud/zero-telemetry offline operation stated as the first sentence, (b) cross-platform reach (Mac/Win/Linux, or at minimum a slick single-OS experience), (c) a trivially fast install (`brew`/`winget`/`apt` one-liners, or Flathub), and (d) a tiny, extremely legible README focused on *why* + *quickstart*, not exhaustive docs (Handy and Vocalinux keep deep material in separate docs/wiki pages). WhisperAloud today is Linux-only and daemon/D-Bus-architecture-first, which is a smaller addressable audience than Handy/OpenWhispr's cross-platform approach — closer in spirit to Speed of Sound, Vocalinux, and VOXD.

Sources for this section also include: [Handy GitHub topics/description — live repo page], [OpenWhispr GitHub topics/description — live repo page], [awesome-voice-typing list](https://github.com/primaprashant/awesome-voice-typing).

---

## 2. README structure patterns observed (fetched and outlined directly from each repo's raw README)

### Handy ([raw README](https://raw.githubusercontent.com/cjpais/Handy/main/README.md))
Header badge (Discord) → **Why Handy?** (4 bullets: Free/Open Source/Private/Simple) → **How It Works** (3 steps + tech detail) → **Quick Start** (brew/winget/apt one-liners) → **Sponsors** (logos) → **Integrations** (Raycast) → Debug Mode → CLI Parameters → Known Issues → Verify Release Signatures (minisign) → Troubleshooting → How to Contribute → Related Projects → License → Acknowledgments.
Notably **no** demo GIF, no comparison table, no architecture diagram, no roadmap, no star-history chart, no AI-development disclosure — despite being the highest-starred pure dictation app found.

### OpenWhispr ([raw README](https://raw.githubusercontent.com/OpenWhispr/openwhispr/main/README.md))
Logo + badges (license, platforms, release version, download count, **GitHub stars badge**) → tagline ("the open-source and free alternative to WisprFlow and Granola") → nav links (website/docs/downloads/API/changelog) → intro paragraph → **download table** (per-OS formats) → **Features** (long bullet list incl. AI agents, meeting transcription, diarization, BYOK) → Quick Start → Documentation links → Tech Stack → **Star History chart** → Sponsors (Neon) → Contributing → License → Acknowledgments (credits whisper.cpp, Parakeet, etc.).

### Speed of Sound ([raw README](https://raw.githubusercontent.com/zugaldia/speedofsound/main/README.md))
Logo/hero → badges (version, build, MIT, Linux) → feature bullets → **animated GIF demo** → Getting Started (**Flathub + Snap Store badges/links** as primary install path) → Contributing (linked doc) → Built With (credits Sherpa ONNX, Whisper, etc.) → Support/Contributions.
This is the single closest analog to WhisperAloud (Linux-only, Whisper/Parakeet engines, Flathub-first) and is proof that a modest, demo-GIF-led, Flathub-first README works for this exact niche.

### Jan ([raw README](https://raw.githubusercontent.com/menloresearch/jan/main/README.md))
Banner image → GitHub metric badges (commit activity, contributors, closed issues, **Discord member count**) → nav links → value prop ("full control" + "privacy") → **install badges** (Microsoft Store, Flathub) + per-OS links → Features (6 bullets incl. **MCP support**) → Build from Source → System Requirements → Troubleshooting (docs/Discord/logs) → Contributing (commit-signing requirement) → Links → Contact (per-purpose emails) → License (Apache-2.0) → Acknowledgements.

### Open WebUI ([raw README](https://raw.githubusercontent.com/open-webui/open-webui/main/README.md))
Hero + badges → value prop ("extensible, feature-rich, user-friendly, offline-capable") → **30+ feature bullets** → **Ecosystem** section (companion apps: Computer, Terminal, Desktop App) → Install (pip/Docker variants incl. GPU) → What's Next (roadmap pointer) → License → Support (Discord) → **Security** (responsible-disclosure link) → **Star History chart** → Attribution.

### whisper.cpp ([raw README](https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/README.md))
Logo + badges (MIT, release, CI, package managers) → feature list (no deps, Apple Silicon/AVX/quantization/GPU) → platform support list → implementation note → Quick Start (clone/build/run) → Model management → **per-accelerator subsections** (CoreML, CUDA, Vulkan, ROCm, OpenVINO, etc.) → FFmpeg/Docker → Conan packaging → advanced features → benchmarking → model format doc → **language bindings table** → XCFramework/Swift → VAD → **examples showcase table** (CLI/server/mobile/plugins with links) → Community (GitHub Discussions).
No roadmap, no contributing section, no star history, no sponsors — this README optimizes for developer/integrator trust (breadth of hardware support, bindings) rather than consumer appeal.

### Ollama ([raw README](https://raw.githubusercontent.com/ollama/ollama/main/README.md))
Title → Download (per-OS + Docker + library SDKs) → Get started → REST API examples (curl/Python/JS) → Supported backends → Documentation links → **massive categorized "Community Integrations" directory** (chat UIs, mobile, editors, libraries, agent frameworks, RAG, bots, CLI tools, observability, infra). No MCP/AGENTS.md section found despite Ollama's own ecosystem including agent frameworks — the ecosystem-directory approach itself (not a named "MCP" badge) is what signals breadth and activity.

### Epicenter / ex-Whispering ([raw README](https://raw.githubusercontent.com/epicenter-so/epicenter/main/README.md))
Logo/tagline → badges (stars, AGPL-3.0, Discord) → nav → deep technical section on CRDT/Yjs sync architecture → Status/Trust Boundaries **table** (what data leaves the device, per scenario — a strong privacy-communication device) → Repo Map (apps/packages tables) → Development (Bun) → Design Notes (ADR links) → Contributing → License → footer. No feature bullets, no install guide, no demo, no AI disclosure — this project pivoted from a single dictation app toward a platform/monorepo, so its README is now developer-facing rather than end-user-facing.

### Cross-project correlation
- **Star-history chart** appears in OpenWhispr and Open WebUI (the two with cloud+hybrid ambitions and VC/sponsor backing) but **not** in Handy, Speed of Sound, whisper.cpp, Vocalinux, or Jan. It correlates with "funded startup" positioning more than with raw popularity — Handy (32.7k stars) has no star-history chart at all. **Recommendation: optional, low priority for WhisperAloud.**
- **Demo GIF/video above the fold** appears consistently in the smaller, single-purpose Linux tools (Speed of Sound) and is the single most-cited "best practice" element across generic README-advice sources, yet the two single biggest star-count winners in this table (whisper.cpp, Handy) ship **no demo media at all** in the main README — they rely on being infrastructure/utility-grade and let docs sites or Homebrew/winget one-liners carry trust instead. For a brand-new, not-yet-famous desktop app like WhisperAloud, a demo GIF still matters more because there is no reputation yet to substitute for it.
- **Comparison tables against alternatives**: not found in any of the seven READMEs fetched directly (Handy, OpenWhispr, Speed of Sound, Jan, Open WebUI, whisper.cpp, Epicenter). Comparison tables exist instead on *third-party* review/SEO sites (openwhispr.com/compare/handy, getvoibe.com/resources/*) rather than in the GitHub README itself. **Recommendation: skip an in-README comparison table; it's not actually the pattern leaders use.**
- **Roadmap section**: only Open WebUI has one ("What's Next"), and it's a one-line pointer to an external doc, not an in-README table. Low priority.
- **Sponsors section**: Handy (logo showcase) and OpenWhispr (Neon) both have one; it's tied to funding, not to popularity mechanics. Not essential for WhisperAloud now, but a GitHub Sponsors button is cheap to add.
- **llms.txt / AGENTS.md / MCP mentions**: AGENTS.md itself was donated to the Agentic AI Foundation in Dec 2025 and is read natively by Claude Code, Codex, Cursor, Devin, Copilot, Gemini CLI, Windsurf, and Amazon Q; "more than 60,000 repositories" use it, but a 10,000-repo survey found only ~5% of repos have adopted *any* context-file format. [Source](https://windowsforum.com/threads/agents-md-in-2026-turning-agent-prompts-into-reviewable-repo-policy.430224/) — this is a real but still-minority trend. None of the seven dictation/local-AI READMEs fetched this session mention AGENTS.md or llms.txt in their user-facing README (Jan and Open WebUI mention **MCP** as a product feature, which is a different thing — MCP is "we speak Model Context Protocol," not "an AI agent should follow this file to work on our code"). WhisperAloud already has a CLAUDE.md (developer-facing); an AGENTS.md pointing the same content at other agent CLIs would be low-cost and on-trend, but is not something README visitors expect to see called out — it belongs as a repo file, not a README section.

---

## 3. GitHub repository metadata patterns

Findings per repo (fetched live repo pages this session):

| Repo | Topics | Description | Discussions | Issue templates | Security policy |
|---|---|---|---|---|---|
| OpenWhispr | `ai, anthropic, cross-platform, gemini, groq, linux, macos, nvidia, open-source, openai, parakeet, speech-to-text, transcribe, whisper, windows` | "Voice-to-text dictation app with local (Nvidia Parakeet/Whisper) and cloud models (BYOK). Privacy-first and available cross-platform." | Yes | Yes | Yes |
| Handy | `accessibility, cross-platform, speech-to-text, tauri-v2` | "A free, open source, and extensible speech-to-text application that works completely offline." | Yes | Yes | **No** (even at 32.7k stars) |
| Open WebUI | `ai, llm, llm-ui, llm-webui, llms, mcp, ollama, ollama-webui, open-webui, openai, openapi, rag, self-hosted, ui, webui` | "User-friendly AI Interface (Supports Ollama, OpenAI API, ...)" | Yes | Yes (inferred from scale) | Yes, with a responsible-disclosure program |
| Vocalinux | `accessibility, dictation, gpu-acceleration, linux, offline-first, privacy-first, python, speech-recognition, speech-to-text, voice, voice-typing, vosk, wayland, whisper, whisper-cpp` | "Free, open-source, 100% offline voice dictation for Linux. Speak and type anywhere via whisper.cpp, Whisper & VOSK engines, GPU-accelerated, works on X11 + Wayland!" | not checked | not checked | not checked |
| **WhisperAloud (current)** | **none set** | "An offline voice dictation app for Linux desktop" | not checked this session | not checked | not checked |

**WhisperAloud currently has no GitHub topics set** — this was directly verified by fetching the live repo page, and it's a clear, free, immediate fix: topics drive GitHub's own topic-browse pages (e.g. `github.com/topics/speech-to-text`, `github.com/topics/dictation`) which is organic discovery surface none of the competitors are leaving unused.

GitHub social preview image guidance (generic, from GitHub's own docs and SEO write-ups, not dictation-specific): 1280×640px (2:1), PNG/JPEG, under 1MB, keep text inside the center ~1000×500px safe area, put logo/name top-left, show one line of value prop plus a visual (screenshot/terminal/diagram). [Source](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview). WhisperAloud's repo page currently shows no custom social preview (default GitHub-generated code-snippet preview) — **[flag: not independently re-verified against GitHub's raw OG-image endpoint, but no custom image was visible when the repo page was fetched]**.

Flathub listing-quality requirements (directly relevant since WhisperAloud ships Flatpak and competitors Speed of Sound/Vocalinux lean on Flathub as primary distribution): apps need 1+ screenshot in MetaInfo to appear in Flathub search; recommended range is 3–6 screenshots for a medium-complexity app, best screenshot first (it's used in featured banners), screenshot URLs must point to a tagged/pinned commit (not a moving branch) so they don't silently break, and screenshots should carry `xml:lang` tags. [Source](https://docs.flathub.org/docs/for-app-authors/metainfo-guidelines/quality-guidelines). Meeting these isn't required for basic acceptance but is required to get into Flathub's "prominent places" (featured/curated spots). [Source](https://docs.flathub.org/docs/for-app-authors/requirements)

---

## 4. AI-assisted development disclosure — what respected projects do

This is the most consequential and most unsettled area, and directly matters for WhisperAloud because (a) this project is developed with heavy AI assistance and (b) it targets Flathub distribution.

- **Flathub's generative-AI policy went through real flux in 2026**: it tightened in May 2026 to explicitly bar AI-generated/AI-assisted code, documentation, manifests, patches, build scripts, *and* bar the submission PR itself from being AI-generated or AI-opened, with submitters told not to request AI review on submission PRs. [Source](https://linuxiac.com/flathub-now-rejects-ai-assisted-apps-and-submissions/), [Source](https://www.gamingonlinux.com/2026/05/flathub-moves-to-ban-nearly-all-apps-and-submissions-made-with-generative-ai/). In September 2026 the policy reportedly flipped twice — from a blanket ban to a disclosure-based approach, then back to a ban, restored 2026-09-21, explicitly stating "disclosure does not exempt manifests from this restriction." [Source](https://www.webanditnews.com/2026/06/02/gnome-circle-draws-a-line-against-ai-generated-code/ mentions the GNOME side; Flathub-specific September flip-flop reported in the search snapshot but **[UNVERIFIED — could not independently load the primary Flathub policy page / changelog this session to confirm exact dates and wording; treat the "flipped twice in September" claim as reported-but-unconfirmed]**). Exceptions may be granted for "mature, well-maintained projects" per the same reporting **[UNVERIFIED against Flathub's own docs]**.
  - **Action implication for WhisperAloud**: before relying on Flathub as a distribution channel, someone must read Flathub's current, live AI policy text at submission time (it is moving fast) and decide whether WhisperAloud's AI-assisted development process is compatible with submission as currently worded. This notes file flags it; it does not resolve it.
- **GNOME Circle** updated its criteria in 2026 to reject submissions showing "clear signs of low-effort machine-generated output" (unnecessary code volume, inconsistent style, imaginary/hallucinated API usage, comments that look like leftover LLM prompts), but explicitly states AI as a *learning aid or development tool* (e.g., code completion) is not prohibited — the bar is code quality and the developer's ability to explain/justify what was submitted, not the presence of AI tooling per se. [Source](https://www.webanditnews.com/2026/06/02/gnome-circle-draws-a-line-against-ai-generated-code/)
- **Disclosure-in-README is an emerging but minority norm**, and reads as a positive trust signal rather than a negative one where seen: one project (OpenVIBES) added a note near the top of its README stating it's built with Claude Code and Codex; community discussion frames stating AI involvement as low-cost and helpful because it tells contributors up front "whether using LLM tools is acceptable" in that project, and at least one maintainer reported inferring AI use anyway and said explicit disclosure would have been the deciding factor in choosing to contribute. [Source](https://github.com/openvibes-project/openvibes-protocol/pull/22), [general discussion source from search snapshot — specific outlet not re-verified, treat framing as directionally correct but **[UNVERIFIED single-source]**]. A dedicated Claude Code plugin exists purely to help projects add a human/AI attribution section to READMEs, which itself signals this is now a recognized need in the ecosystem. [Source](https://github.com/danielrosehill/Claude-AI-Attribution-Plugin)
- None of the seven high-star READMEs fetched directly this session (Handy, OpenWhispr, Speed of Sound, Jan, Open WebUI, whisper.cpp, Epicenter) contain any AI-development disclosure section, despite several of them (Electron/Tauri consumer apps with fast 2025-2026 release cadences) being very plausible candidates for heavy AI-assisted development. **This suggests silence is still the default even among AI-adjacent products**, and that disclosure is something done out of principle/transparency rather than because audiences expect or demand it in the README.
- **Recommendation for WhisperAloud**: add a short, factual "Development process" or "About this project" note (a few sentences, not a badge) stating the project is built with heavy AI pair-programming (naming tools used, e.g. Claude Code) and that all changes go through tests/review — mirroring the OpenVIBES pattern — placed low in the README (near Contributing/Acknowledgments), not in the hero section. Treat the Flathub manifest/submission-specific AI-use question as a separate, higher-stakes compliance decision to make at submission time, not something this README note resolves on its own.

---

## 5. Distribution channels

- **Flathub**: direct distribution channel for this exact niche — Speed of Sound and Vocalinux both lead their "Getting Started" with Flathub (and Snap for Speed of Sound). Flathub quality guidelines (screenshots, metainfo) are detailed in §3 above. The live AI-policy risk in §4 is the main blocker to evaluate before/while submitting WhisperAloud.
- **GNOME Circle**: relevant since WhisperAloud is GTK4/GNOME-first. Requires an OSI-approved license (WhisperAloud is MIT — compatible) and "uses or extends the GNOME platform" plus quality/accessibility review (keyboard nav, screen reader, contrast). [Source](https://dev.to/revisto/journey-to-gnome-circle-community-app-ideas-and-getting-started-4k2), [Source](https://wiki.gnome.org/Foundation/SoftwarePolicy). The 2026 AI-code-quality criterion (clear signs of low-effort machine output get rejected) applies here too — same implication as Flathub: code quality/explainability matters more than the mere presence of AI tooling. Benefits include promotion and GNOME Foundation membership eligibility for contributors. [Source](https://www.webanditnews.com/2026/06/02/gnome-circle-draws-a-line-against-ai-generated-code/)
- **Hacker News "Show HN"**: proven channel for exactly this app category — Whispering (591 pts), Ghost Pepper (467 pts), Handy (247 pts), Yap (104 pts) all launched this way in 2025-2026 (see §1 table; exact live counts **[UNVERIFIED against current HN page — see caveat above]**). Common thread in the titles/framing: lead with "open-source," "local," "offline," "on-device, no model download" as the hook, not the underlying ML architecture.
- **Reddit**: r/selfhosted called out specifically as effective for this category of software. [Source](https://dev.to/tooljet/how-tooljet-gained-20000-github-stars-and-400-contributors-4ee0) (ToolJet's growth write-up, not dictation-specific but a concrete, citable playbook: Reddit r/selfhosted engagement, Product Hunt launches timed to releases, regular dev.to content, "good first issue" curation, monthly community calls, conference presence). r/linux and r/opensource specific launch-pattern data could not be found this session **[flag: searched, no concrete citable example surfaced; do not claim a pattern here without further research]**.
- **Awesome-lists**: [`primaprashant/awesome-voice-typing`](https://github.com/primaprashant/awesome-voice-typing) is a directly relevant, actively maintained curated list (entries: name, platforms, local/hybrid/cloud mode, engine, one-line summary) that already lists Handy, OpenWhispr, Speed of Sound, Vocalinux, VOXD, nerd-dictation, Elograf, and others — but **does not list WhisperAloud**. This is a concrete, low-effort PR opportunity: submit WhisperAloud with the same entry format (name, Linux, Local, Whisper/Parakeet, one-line summary).
- **Product Hunt + GitHub Trending**: cited as a real traffic driver for the open-source investment tracker Wealthfolio (front page of HN + GitHub Trending + Homebrew feature all reinforced each other). [Source](https://dev.to/afadil/how-i-built-an-open-source-app-that-went-viral-160p) The lesson for WhisperAloud: coordinate a release (ideally a Flathub-available one) with a single Show HN post and a Product Hunt listing on the same day, rather than spreading launches out.

---

## Recommended README outline for WhisperAloud

Based on the above, weighted toward what the single closest analog (Speed of Sound) and the highest-star pure-dictation app (Handy) actually do, not toward every element generic "awesome README" advice lists:

1. **Hero**: logo/wordmark, one-line value prop ("Offline voice dictation for the Linux desktop — GTK4 app + D-Bus daemon, Whisper & Parakeet engines, nothing leaves your machine"), 3-5 badges (license, Flathub version/downloads if/when listed, latest release, CI status if public). Keep this to ~5 lines, no paragraph.
2. **Demo GIF or short video**, immediately below the hero, above any text — showing: hotkey press → speaking → live partial transcript appearing → text pasted into a real app. This is the single highest-leverage missing element for a not-yet-famous app (see §2 cross-project correlation).
3. **Why WhisperAloud** (4-5 bullets, Handy-style): fully offline/local, daemon-first/stable for long sessions, GTK4/GNOME-native, streaming partials, Flatpak-ready.
4. **Install** — lead with the easiest path first (Flathub badge once listed; until then, `./install.sh` one-liner), then link out to `INSTALL.md`/`DEPENDENCIES.md` for manual/distro-specific steps (mirrors Handy's brew/winget/apt pattern and Speed of Sound's Flathub-first pattern).
5. **Quickstart** — 3-step GUI launch + CLI daemon control, essentially what's already in the current README; keep it.
6. **Core features** — bullet list (already largely present in the current README; keep, trim to scan in under 10 seconds).
7. **Architecture at a glance** — keep the existing ASCII diagram; it's a real differentiator (none of the seven READMEs fetched this session had an architecture diagram at all, so this is already ahead of the pattern and should stay).
8. **Privacy / offline note** — one short, explicit paragraph: no network calls during transcription, where models/history are stored, no telemetry. Every successful project in §1 states this in the first few lines; WhisperAloud currently buries it inside bullet 1 of "Why WhisperAloud" — promote it to its own short, bolded line near the top.
9. **Development process / AI-assisted disclosure** — 2-3 sentences, OpenVIBES-style, placed near Contributing (see §4). Not in the hero.
10. **Contributing** — short, link to CONTRIBUTING.md if one exists; Jan's "commit-signing requirement" style one-liner is a good model for brevity.
11. **Related projects / acknowledgments** — credit faster-whisper, Whisper, Parakeet/NeMo, GTK/libadwaita, etc. (every README fetched this session has this section; it's near-universal and costs nothing).
12. **License** — one line, current MIT statement is fine.

Explicitly **not recommended** based on evidence: an in-README comparison table against alternatives (not actually used by any leader found), a roadmap table (only one of seven projects has one, and it's a one-line pointer not a table), and a star-history chart (correlates with VC-style project positioning, not with raw popularity — Handy has 32.7k stars with none).

---

## Repo-metadata change checklist

- [ ] **Set GitHub topics** (currently empty — verified live). Suggested set, modeled on OpenWhispr/Handy/Vocalinux: `speech-to-text`, `dictation`, `voice-typing`, `linux`, `gtk4`, `gnome`, `dbus`, `whisper`, `whisper-cpp`, `parakeet`, `offline-first`, `privacy-first`, `flatpak`, `open-source`, `accessibility`.
- [ ] **Tighten the one-line repo description** to lead with the differentiators competitors use in theirs (offline/local, engine names, platform): e.g. "Offline voice dictation for the Linux desktop — GTK4 + D-Bus daemon, Whisper & Parakeet engines, no cloud." (Current description is accurate but generic compared to OpenWhispr's and Vocalinux's descriptions, which both name engines and privacy stance in the first sentence.)
- [ ] **Add a custom social preview image** (repo currently shows no custom preview — verified live): 1280×640px, logo top-left, one-line value prop, a terminal/app screenshot or the architecture diagram as the visual. See §3 for GitHub's own spec.
- [ ] **Add a website link** in the repo sidebar if/when a project site or Flathub listing exists (Handy, OpenWhispr, Speed of Sound, Vocalinux all have one; WhisperAloud currently does not).
- [ ] **Confirm/add a SECURITY.md** — mixed signal here: Handy (32.7k stars) has none, but OpenWhispr and Open WebUI both do, and Open WebUI's includes a responsible-disclosure program. Not urgent but cheap and strictly positive; add if time allows.
- [ ] **Check/enable GitHub Discussions** if not already on (OpenWhispr, Jan, Open WebUI all use it for support/community instead of issues).
- [ ] **Check for issue templates**; add at minimum a bug-report and feature-request template (present on every repo checked this session except raw confirmation was skipped for Vocalinux).
- [ ] **Decide the Flathub AI-policy question before submitting** (see §4) — this is a go/no-go compliance decision, not a cosmetic one, and the policy text may have changed again since this research was done; re-check the live Flathub docs at submission time.
- [ ] **Submit WhisperAloud to `primaprashant/awesome-voice-typing`** (§5) — concrete, low-effort PR using the list's existing entry format.

---

## Assets list — screenshots and GIFs to record

1. **Hero demo GIF/video (highest priority)**: hotkey press → speak a sentence → live partial transcript visible in the GTK4 main window → final text appears pasted into a real target app (e.g. a text editor or browser field). This is the single element most of the research in §2 flags as high-value-but-currently-missing from WhisperAloud, and it's the one Speed of Sound (the closest direct analog) leads with.
2. **Main GTK4 window, idle state** — clean shot of the app's primary UI, used as the first Flathub screenshot (Flathub explicitly uses screenshot #1 in featured banners, §3).
3. **Main window, actively recording** — showing the audio level meter / live partial transcript mid-sentence, to visually communicate "streaming partials" as a differentiator.
4. **Model picker / in-app model download** — showing the license-consent + progress UI for downloading a Whisper/Parakeet model, since in-app model management is a real feature competitors (OpenWhispr, whisper.cpp users doing manual downloads) don't all offer as smoothly.
5. **History panel** — showing SQLite-backed transcript history with search, to differentiate from apps that don't persist history.
6. **System tray / hotkey indicator** — small shot showing the tray icon and a global-hotkey-triggered recording state, useful for both the README and as a Flathub screenshot (daemon-first model is otherwise invisible to a user looking at screenshots).
7. **Settings panel** — engine/model selection (Whisper vs Parakeet vs Nemotron streaming), relevant because engine choice is a named differentiator in the project description.
8. **Terminal/CLI shot** — `whisper-aloud transcribe` producing SRT/VTT/JSON output, for the architecture-minded/developer audience (mirrors whisper.cpp's and Ollama's developer-trust approach via visible CLI/API usage).
9. **Architecture diagram, polished graphic version** of the existing ASCII diagram in the README — worth turning into an actual image (SVG/PNG) for the social preview image and for use in any GNOME Circle or Flathub "detailed description" sections, since none of the competitor READMEs have an architecture diagram at all and this is a genuine current strength to showcase more visually.
10. **Social preview image** (1280×640px) combining #2 or #3 with the wordmark/logo and a one-line value prop, per GitHub's spec in §3.

---

## Flags — things that need a second look before acting on them

- Exact current GitHub star counts for Ollama and Speed of Sound were not captured this session; re-check before citing a specific number externally.
- The claim that Flathub's AI policy "flipped twice in September 2026" is from a single search-snapshot source and was not confirmed against Flathub's own live policy page/changelog — re-verify directly at `docs.flathub.org` before making any submission decision.
- HN point/comment counts for Whispering, Ghost Pepper, Handy, and Yap are snapshot figures from search results, not re-checked against the live HN items; treat as approximate.
- No concrete r/linux- or r/opensource-specific (as opposed to r/selfhosted) launch-pattern case study was found this session despite searching; don't assert a specific r/linux playbook without further research.
- "Exceptions may be granted for mature, well-maintained projects" (Flathub AI policy) is reported secondhand and not confirmed against Flathub's primary documentation.
