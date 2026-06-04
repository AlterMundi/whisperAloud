"""Word Error Rate with normalization identical across all engines."""
import jiwer

# Normalization lowercases and strips punctuation but does NOT strip Spanish
# accents/diacritics. Both reference and hypothesis go through the same transform,
# so the comparison is fair; however, engines that drop diacritics will show a
# slightly higher WER floor. Kept identical to the WHISP-33 baseline for comparability.
_norm = jiwer.Compose([
    jiwer.ToLowerCase(),
    jiwer.RemovePunctuation(),
    jiwer.RemoveMultipleSpaces(),
    jiwer.Strip(),
    jiwer.ReduceToListOfListOfWords(),
])


def wer(reference: str, hypothesis: str) -> float:
    """Normalized WER for a single ref/hyp pair (lowercase, no punctuation)."""
    return jiwer.wer(
        reference=[reference], hypothesis=[hypothesis],
        reference_transform=_norm, hypothesis_transform=_norm,
    )
