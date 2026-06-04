from bench.wer import wer


def test_identical_is_zero():
    assert wer("Hola, mundo.", "hola mundo") == 0.0


def test_one_substitution():
    # 2 reference words, 1 wrong -> 0.5
    assert wer("hola mundo", "hola tierra") == 0.5


def test_punctuation_and_case_ignored():
    assert wer("El gato.", "el GATO") == 0.0
