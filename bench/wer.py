"""Word Error Rate with normalization identical across all engines."""
import jiwer

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
