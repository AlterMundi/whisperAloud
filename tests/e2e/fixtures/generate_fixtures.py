"""Generate the synthetic audio fixtures for tests/e2e. Run once and commit
the resulting .wav/.txt files -- CI should not regenerate binary fixtures on
every run, and diffs stay reviewable.

    python tests/e2e/fixtures/generate_fixtures.py
"""
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).parent / "audio"


def main() -> None:
    HERE.mkdir(exist_ok=True)

    sf.write(HERE / "silence.wav", np.zeros(16000 // 2, dtype=np.float32), 16000)
    (HERE / "silence.txt").write_text("")

    t = np.linspace(0, 1.0, 16000, endpoint=False)
    tone = (0.2 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    sf.write(HERE / "tone.wav", tone, 16000)
    (HERE / "tone.txt").write_text("the quick brown fox")

    # Distinct word, for SearchHistory tests that must match only one entry.
    t2 = np.linspace(0, 1.0, 16000, endpoint=False)
    tone2 = (0.2 * np.sin(2 * np.pi * 523.25 * t2)).astype(np.float32)
    sf.write(HERE / "umbrella.wav", tone2, 16000)
    (HERE / "umbrella.txt").write_text("the umbrella in the hallway")

    # Accented text, for WHISP-47's FTS accent-folding behaviour test.
    t3 = np.linspace(0, 1.0, 16000, endpoint=False)
    tone3 = (0.2 * np.sin(2 * np.pi * 659.25 * t3)).astype(np.float32)
    sf.write(HERE / "cancion.wav", tone3, 16000)
    (HERE / "cancion.txt").write_text("una canción bonita")


if __name__ == "__main__":
    main()
