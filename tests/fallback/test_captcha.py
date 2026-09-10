from pathlib import Path

import pytest

from fallback.captcha import looks_valid, solve

SAMPLES = sorted((Path(__file__).parent / "fixtures" / "captcha_samples").glob("*.png"))


@pytest.mark.parametrize("text,ok", [
    ("A3K7P", True), ("abcde", True), ("12345", True),
    ("A3K7", False), ("A3K7PP", False), ("A3K-7", False), ("", False),
])
def test_looks_valid(text, ok):
    assert looks_valid(text) is ok


def test_solve_returns_5_alnum_for_every_sample():
    assert SAMPLES, "no captcha fixtures committed"
    for p in SAMPLES:
        out = solve(p.read_bytes())
        assert looks_valid(out), f"{p.name}: solve returned {out!r}"


def test_solve_matches_known_answers_on_majority():
    # filenames are verified-correct labels; the model is deterministic, so this
    # should be near-100%. Threshold has margin for ddddocr version drift.
    hits = sum(1 for p in SAMPLES if solve(p.read_bytes()) == p.stem.upper())
    assert hits >= len(SAMPLES) * 0.6
