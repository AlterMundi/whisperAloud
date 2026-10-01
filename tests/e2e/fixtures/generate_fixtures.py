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


if __name__ == "__main__":
    main()
