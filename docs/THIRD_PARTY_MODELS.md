# Third-party speech models

WhisperAloud bundles no speech model weights. Every model in `stt/catalog.py` is
downloaded at runtime, on demand -- by `whisper-aloud download-model`, the
Settings "Download" button, or automatically the first time `transcribe`/the
daemon needs a model that isn't cached yet. This is true for every packaging
format, including the Flatpak: the Flatpak build contains only WhisperAloud's
own code, never a model file, so the same runtime-download behavior and the
same licenses below apply inside the sandbox as outside it.

Downloaded weights live under `$XDG_CACHE_HOME/whisper_aloud/models/`
(`faster-whisper`'s Whisper weights instead live under the standard
huggingface cache, `$XDG_CACHE_HOME/huggingface/`) -- never inside the
application's installed files.

This page lists, for each engine family, the upstream project, the license
that applies to its *weights* (not WhisperAloud's own code, which is
OpenMDW-1.1 -- see `LICENSE`), and anything a user or redistributor needs to
do to stay compliant.

## Whisper (`faster-whisper` engine)

- **Upstream**: OpenAI's Whisper, via the `faster-whisper`/CTranslate2 export.
- **License**: MIT.
- **Obligations**: standard MIT notice-preservation; no attribution is required
  in the application UI. No special handling needed.

## Parakeet (`parakeet` engine)

- **Upstream**: NVIDIA's Parakeet TDT, via the sherpa-onnx export used by this
  project's catalog (`parakeet-tdt-0.6b-v3`).
- **License**: CC-BY-4.0.
- **Obligations**: CC-BY-4.0 requires attribution to the original creator when
  the model (or output substantially derived from it) is redistributed.
  WhisperAloud's Settings dialog and CLI (`whisper-aloud models`) show the
  license for every catalog entry, including Parakeet's, so a user downloading
  it can see the attribution requirement; this document is the canonical
  reference for what that attribution should contain (model name, "NVIDIA",
  the CC-BY-4.0 license, and a link to the model card on Hugging Face/NGC).

## Nemotron 3.5 ASR Streaming (`nemotron-streaming` engine)

- **Upstream**: NVIDIA's Nemotron 3.5 ASR Streaming 0.6B (560 ms chunks), via
  the sherpa-onnx export used by this project's catalog
  (`nemotron-3.5-streaming-560ms`).
- **License**: OpenMDW-1.1 (Open Model and Weights License 1.1) -- a
  permissive license, but not one of the handful this project otherwise ships
  (MIT/Apache-2.0/CC-BY-4.0/BSD-3-Clause), and its terms include an
  attribution requirement.
- **Status**: WHISP-55 researched and documented OpenMDW-1.1's compatibility
  with this project's other STT dependencies (see
  `docs/superpowers/specs/2026-10-01-whisp55-nemotron-sherpa-findings.md`),
  and the license was reviewed and accepted by the maintainer (2026-10-03) --
  not a formal legal review. WHISP-86 then enabled the engine on that basis.
  It is not the recommended default for any engine/language
  (`recommended=False` in the catalog) -- Parakeet remains the recommended
  CPU model for es/en -- but it is no longer hidden behind a pending-review
  flag.
- **Obligations**: attribution is required, per OpenMDW-1.1's terms. Because
  the license is non-standard for this project, both the Settings UI and the
  CLI still require an explicit, affirmative step before a download starts:
  - **Settings UI**: clicking "Download" on this entry always shows a consent
    dialog naming the model, its license, and a link to the findings doc above,
    before `DownloadModel` is ever called
    (`ui/download_logic.requires_license_consent`, `ui/settings_dialog.py`).
  - **CLI**: `whisper-aloud download-model`/`transcribe` no longer require
    `--accept-license` for this entry (that flag now only gates a genuinely
    `needs_review` catalog entry, of which there are none today) -- instead
    they print a one-line, non-blocking license notice to stdout naming the
    license and pointing back to this document, so the user still sees it
    without the command being refused.

## Adding a new third-party model

When adding a catalog entry whose license isn't MIT/Apache-2.0/CC-BY-4.0/
BSD-3-Clause (`stt.catalog.ENGINES`'s standard allowlist, mirrored in
`ui.model_picker_logic._STANDARD_LICENSES`):

1. Set `needs_review=True` on the `ModelEntry` until a human has reviewed the
   license against this project's other dependencies (see WHISP-55/WHISP-56
   for the template this document and the findings doc follow).
2. Once reviewed and approved, flip `needs_review=False`, replace any
   "pending review" note with a neutral license summary (permissive/
   copyleft, attribution required or not, link to the findings doc), and add
   a section to this document following the pattern above.
3. The non-standard-license consent dialog (UI) and license notice (CLI) stay
   in place regardless of `needs_review` -- they're driven by
   `is_nonstandard_license()`, not by the review-pending gate.
