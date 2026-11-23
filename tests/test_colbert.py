"""Tests for ColBERT neural reranker (Slice 3, requirements R1, R2, R6).

Tests:
- MaxSim against a hand computed example with tiny vectors
- Window splitting on fixture sections (never split inside (a)/(b) paragraphs)
- ColBERT load fails on a tampered or missing model file
- Matched span maps to correct characters on fixture text
- Encode query and docs properties (normalization, skiplist filtering)
- Best window selection across multi-window candidate sections
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from chapterverse.dense.colbert import (
    ColbertScorer,
    compute_maxsim,
    split_section_windows,
)
from chapterverse.dense.embed import ModelLoadError


def test_maxsim_hand_computed_tiny_vectors() -> None:
    # Query: 2 tokens in R^2
    # q0 = [1.0, 0.0]
    # q1 = [0.0, 1.0]
    q = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)

    # Document: 3 tokens in R^2
    # d0 = [1.0, 0.0]   (matches q0 with cosine 1.0, q1 with 0.0), offset (0, 5)
    # d1 = [0.6, 0.8]   (matches q0 with cosine 0.6, q1 with 0.8), offset (6, 11)
    # d2 = [0.0, 1.0]   (matches q0 with cosine 0.0, q1 with 1.0), offset (12, 15)
    d = np.array([[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]], dtype=np.float32)
    offsets = [(0, 5), (6, 11), (12, 15)]

    # MaxSim calculation:
    # For q0: max is d0 (1.0)
    # For q1: max is d2 (1.0)
    # Sum: 2.0
    # Winning doc tokens: d0 won 1 query token, d2 won 1 query token.
    # Tie for max count: tokens d0 and d2.
    # Span: min(0, 12) to max(5, 15) = (0, 15).
    score, span = compute_maxsim(q, d, offsets)
    assert pytest.approx(score, rel=1e-5) == 2.0
    assert span == (0, 15)


def test_maxsim_dominant_token_span() -> None:
    # Query: 3 tokens
    # q0 = [1, 0], q1 = [1, 0], q2 = [0, 1]
    q = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)

    # Doc: 2 tokens
    # d0 = [1, 0], offset (10, 20) -> wins q0 and q1 (count = 2)
    # d1 = [0, 1], offset (30, 40) -> wins q2 (count = 1)
    d = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    offsets = [(10, 20), (30, 40)]

    score, span = compute_maxsim(q, d, offsets)
    assert pytest.approx(score, rel=1e-5) == 3.0
    # d0 won the most query tokens (2 vs 1), so span should cover d0 only
    assert span == (10, 20)


def test_maxsim_empty_inputs() -> None:
    q = np.zeros((0, 2), dtype=np.float32)
    d = np.array([[1.0, 0.0]], dtype=np.float32)
    score, span = compute_maxsim(q, d, [(0, 5)])
    assert score == 0.0
    assert span == (0, 0)

    q2 = np.array([[1.0, 0.0]], dtype=np.float32)
    d2 = np.zeros((0, 2), dtype=np.float32)
    score2, span2 = compute_maxsim(q2, d2, [])
    assert score2 == 0.0
    assert span2 == (0, 0)


def test_window_splitting_fixture_no_split_inside_paragraphs() -> None:
    text = (
        "General. "
        "(1) In determining for the purposes of this Part whether the dismissal of an employee "
        "is fair or unfair, it is for the employer to show— "
        "(a) the reason (or, if more than one, the principal reason) for the dismissal, and "
        "(b) that it is either a reason falling within subsection (2) "
        "or some other substantial reason. "
        "(2) A reason falls within this subsection if it— "
        "(a) relates to the capability or qualifications of the employee, or "
        "(b) relates to the conduct of the employee. "
        "(3) In subsection (2)(a)— "
        "(a) “capability”, in relation to an employee, "
        "means his capability assessed by reference to health, and "
        "(b) “qualifications”, in relation to an employee, "
        "means any degree, diploma or other academic credential."
    )
    heading = "General."
    windows = split_section_windows(text, heading=heading)

    assert len(windows) == 3
    # Check window 1 starts with heading and subsection (1), containing (a) and (b)
    assert windows[0].startswith("General. (1) In determining")
    assert "(a) the reason" in windows[0]
    assert "(b) that it is either" in windows[0]
    assert "(2) A reason falls" not in windows[0]
    assert windows[0].endswith("substantial reason.")

    # Check window 2 starts with heading and subsection (2), containing (a) and (b)
    assert windows[1].startswith("General. (2) A reason falls")
    assert "(a) relates to the capability" in windows[1]
    assert "(b) relates to the conduct" in windows[1]
    assert "(3) In subsection" not in windows[1]
    assert windows[1].endswith("conduct of the employee.")

    # Check window 3 starts with heading and subsection (3), containing (a) and (b)
    assert windows[2].startswith("General. (3) In subsection (2)(a)")
    assert "(a) “capability”" in windows[2]
    assert "(b) “qualifications”" in windows[2]


def test_window_splitting_cross_references_not_split() -> None:
    # A section with references like 'section 117(1)' or 'subsection (2)'
    text = (
        "Limit of compensatory award etc. "
        "(1) The amount of any compensation awarded to a person under section 117(1) and (2) "
        "shall not exceed the limit. "
        "(2) Subsection (1) shall not apply in cases of health and safety."
    )
    heading = "Limit of compensatory award etc."
    windows = split_section_windows(text, heading=heading)

    assert len(windows) == 2
    assert windows[0].startswith("Limit of compensatory award etc. (1) The amount")
    assert "section 117(1) and (2)" in windows[0]
    assert windows[1].startswith("Limit of compensatory award etc. (2) Subsection (1)")


def test_window_splitting_section_without_subsections() -> None:
    text = "Short title. This Act may be cited as the Employment Rights Act 1996."
    heading = "Short title."
    windows = split_section_windows(text, heading=heading)

    assert len(windows) == 1
    assert windows[0] == "Short title. This Act may be cited as the Employment Rights Act 1996."


def test_colbert_load_fail_closed_tampered_model(tmp_path: Path) -> None:
    # Create a dummy model directory with a corrupted file
    model_dir = tmp_path / "answerai-colbert-small-v1"
    model_dir.mkdir(parents=True)
    (model_dir / "model.onnx").write_bytes(b"dummy onnx content")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

    manifest = {
        "answerai-colbert-small-v1": {
            "files": {
                "model.onnx": {
                    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                    "size": 18,
                }
            }
        }
    }
    manifest_path = tmp_path / "models-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Should fail with ModelLoadError because sha256 does not match
    with pytest.raises(ModelLoadError, match="sha256 mismatch"):
        ColbertScorer.load(model_dir, manifest_path)


def test_colbert_load_fail_closed_missing_file(tmp_path: Path) -> None:
    model_dir = tmp_path / "answerai-colbert-small-v1"
    model_dir.mkdir(parents=True)

    manifest = {
        "answerai-colbert-small-v1": {
            "files": {
                "model.onnx": {
                    "sha256": "bd4bee8b055ed7a8e8b6e401792ae6e9137d9712c35eaa16f915ba69cae6d633",
                    "size": 133298178,
                }
            }
        }
    }
    manifest_path = tmp_path / "models-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ModelLoadError, match="missing model file"):
        ColbertScorer.load(model_dir, manifest_path)


def test_colbert_real_model_matched_span_and_rerank() -> None:
    model_dir = Path("artefacts/models/answerai-colbert-small-v1")
    manifest_path = Path("artefacts/models/models-manifest.json")
    if not (model_dir / "model.onnx").is_file() or not manifest_path.is_file():
        pytest.skip("ColBERT model files not present")

    scorer = ColbertScorer.load(model_dir, manifest_path, threads=4)

    # Test encode_query
    q_vecs = scorer.encode_query("unfair dismissal compensatory award cap")
    assert q_vecs.ndim == 2
    assert q_vecs.shape[1] == 96
    assert len(q_vecs) <= 32
    # Verify L2 normalization
    norms = np.linalg.norm(q_vecs, axis=-1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)

    # Test candidate sections
    # Doc 1: s124 (very relevant to compensatory award cap)
    s124_text = (
        "Limit of compensatory award etc. "
        "(1) The amount of any compensation awarded to a person under section 117(1) and (2) "
        "shall not exceed £123,543. "
        "(2) Subsection (1) shall not apply in cases of whistleblowing."
    )
    # Doc 2: s1 (employment particulars, irrelevant)
    s1_text = (
        "Statement of initial employment particulars. "
        "(1) Where a worker begins employment, the employer shall give the worker a statement. "
        "(2) The statement shall contain particulars of the names of employer and employee."
    )

    matches = scorer.score("unfair dismissal compensatory award cap", [s124_text, s1_text])
    assert len(matches) == 2

    match_124 = matches[0]
    match_1 = matches[1]

    # s124 must score substantially higher than s1
    assert match_124.score > match_1.score

    # s124 has 2 windows: window 0 (compensation amount £123,543) should be best window
    assert match_124.best_window == 0

    # Span must map to valid character range in window 0
    w0 = split_section_windows(s124_text, heading="Limit of compensatory award etc.")[0]
    start, end = match_124.span
    assert 0 <= start < end <= len(w0)
    matched_chars = w0[start:end]
    # The matched span should capture relevant terms
    assert any(
        term in matched_chars.lower() or matched_chars.lower() in term
        for term in ("compensat", "award", "limit", "amount")
    )
