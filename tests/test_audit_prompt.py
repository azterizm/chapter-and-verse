"""The generation prompt and the policy (agent B4)."""

from __future__ import annotations

import re

import pytest
from legal_rag_verifier.judge import STRICT_FIGURES
from legal_rag_verifier.premise import Premise

from chapterverse.audit.policy import ABSTAIN_TEXT, build_policy, verifier_config
from chapterverse.audit.prompt import (
    _TEMPLATE_PARTS,
    CONTEXT_MARGIN_CHARS,
    ELLIPSIS,
    MODE_RULES,
    PROMPT_SHA256,
    PriorTurn,
    SessionState,
    build_prompt,
    estimate_tokens,
    render_source,
    trim_window_text,
    window_positions,
)
from chapterverse.contracts import (
    Policy,
    TemporalStatus,
    TextStore,
    Window,
    canonical_json,
    sha256_hex,
)

S124 = "uk/ukpga/1996/18/s124"
QUERY = "What is the maximum compensatory award for unfair dismissal?"


@pytest.fixture(scope="module")
def premise(fixture_text_store: TextStore) -> Premise:
    return Premise.from_records(fixture_text_store.get(S124))


@pytest.fixture(scope="module")
def windows(premise: Premise) -> tuple[Window, ...]:
    return tuple(
        Window(
            passage_id=f"P{i}",
            coordinate=str(p.coordinate),
            heading=p.heading or "",
            text=p.text,
            status=TemporalStatus.IN_FORCE,
            version_date="2026-09-28",
            source_url=f"https://www.legislation.gov.uk/ukpga/1996/18/section/124/{i}",
        )
        for i, p in enumerate(premise.passages, 1)
    )


def test_prompt_sha_is_the_hash_of_every_template_part() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", PROMPT_SHA256)
    assert PROMPT_SHA256 == "7264c2bee465b48512cec143f4dec31f59ac88fc8551c84cb80c67d53f625219"
    assert sha256_hex(canonical_json(_TEMPLATE_PARTS)) == PROMPT_SHA256


def test_s124_prompt_numbers_passages_and_states_the_rules(windows: tuple[Window, ...]) -> None:
    prompt = build_prompt(QUERY, windows)
    system, user = prompt.messages
    assert system["role"] == "system"
    assert user == {"role": "user", "content": QUERY}
    assert prompt.passage_ids == ("P1", "P2", "P3", "P4", "P5", "P6")
    content = system["content"]
    for window in windows:
        assert f"[{window.passage_id}] {window.coordinate}\n{window.heading}\n" in content
        assert window.text in content
    assert content.index("[P1] ") < content.index("[P2] ") < content.index("[P6] ")
    assert "using only the numbered passages" in content
    assert "[P1]" in content
    assert "You may name a section, subsection or paragraph only when the passage" in content
    assert "Never write an SI number" in content
    assert "Lead with the answer" in content
    assert 'never to "passages" or "the text provided"' in content
    assert "When the law shown does not settle the question" in content
    assert ABSTAIN_TEXT in content
    assert "do not answer the question, say so plainly" not in content
    assert MODE_RULES["short"] in content


def test_s124_prompt_is_lean(windows: tuple[Window, ...]) -> None:
    prompt = build_prompt(QUERY, windows)
    assert prompt.chars == 3596
    assert prompt.estimated_tokens == 899  # 3596 characters at 4 a token
    assert prompt.estimated_tokens < 1000


def test_synthesis_mode_changes_only_the_mode_rule(windows: tuple[Window, ...]) -> None:
    short = build_prompt(QUERY, windows, mode="short").messages[0]["content"]
    synthesis = build_prompt(QUERY, windows, mode="synthesis").messages[0]["content"]
    assert synthesis == short.replace(MODE_RULES["short"], MODE_RULES["synthesis"])


def test_highlight_trims_with_a_margin_and_marks_cuts() -> None:
    text = " ".join(f"word{i:03d}" for i in range(400))  # 3,199 characters
    start = text.index("word200")
    end = start + len("word200 word201")
    trimmed = trim_window_text(text, (start, end))
    assert trimmed.startswith(f"{ELLIPSIS} ")
    assert trimmed.endswith(f" {ELLIPSIS}")
    body = trimmed[2:-2]
    assert "word200 word201" in body
    assert body in text
    assert len(body) <= (end - start) + 2 * CONTEXT_MARGIN_CHARS
    assert len(body) >= (end - start) + 2 * (CONTEXT_MARGIN_CHARS - len("word000 "))
    assert re.fullmatch(r"(word\d{3} )*word\d{3}", body)  # no word cut in half


def test_highlight_at_the_start_keeps_the_start_unmarked() -> None:
    text = "alpha beta gamma " * 100
    trimmed = trim_window_text(text, (0, 5))
    assert trimmed.startswith("alpha beta")
    assert trimmed.endswith(ELLIPSIS)
    assert trim_window_text("short text", (0, 5)) == "short text"
    assert trim_window_text("short text", None) == "short text"
    assert trim_window_text("short text", (5, 5)) == "short text"


def test_window_with_highlight_is_trimmed_in_the_prompt(windows: tuple[Window, ...]) -> None:
    long_text = "Start. " + "x " * 600 + "The cap is £123,543. " + "y " * 600 + "End."
    span = (long_text.index("The cap"), long_text.index("The cap") + len("The cap is £123,543."))
    window = Window("P1", windows[1].coordinate, "Heading", long_text, TemporalStatus.IN_FORCE,
                    None, "https://example.org", span)  # fmt: skip
    content = build_prompt(QUERY, [window]).messages[0]["content"]
    assert "The cap is £123,543." in content
    assert "Start." not in content
    assert "End." not in content


def test_session_and_turns_are_rendered_as_coordinates(windows: tuple[Window, ...]) -> None:
    plain = build_prompt(QUERY, windows).messages[0]["content"]
    assert "Earlier in this conversation" not in plain
    session = SessionState(instrument_focus="uk/ukpga/1996/18", as_at="2026-01-01")
    history = (PriorTurn(("uk/ukpga/1996/18/s98",), "Is redundancy fair?"),
               PriorTurn(("uk/ukpga/1996/18/s94",)))  # fmt: skip
    content = build_prompt(QUERY, windows, session=session, history=history).messages[0]["content"]
    assert "it is not evidence" in content
    assert "Is redundancy fair? (bound to uk/ukpga/1996/18/s98)" in content
    assert "bound to uk/ukpga/1996/18/s94" in content
    assert "instrument uk/ukpga/1996/18; as at 2026-01-01" in content
    assert content.index("Earlier in this conversation") < content.index("Passages")


@pytest.mark.parametrize("ids", [("P1", "P1"), ("P0",), ("p1",), ("P1", "Q2")])
def test_bad_passage_ids_are_rejected(windows: tuple[Window, ...], ids: tuple[str, ...]) -> None:
    bad = [Window(pid, w.coordinate, w.heading, w.text, w.status, None, w.source_url)
           for pid, w in zip(ids, windows, strict=False)]  # fmt: skip
    with pytest.raises(ValueError, match="passage ids"):
        build_prompt(QUERY, bad)


def test_no_windows_or_unknown_mode_is_rejected(windows: tuple[Window, ...]) -> None:
    with pytest.raises(ValueError, match="at least one window"):
        build_prompt(QUERY, [])
    with pytest.raises(ValueError, match="unknown mode"):
        build_prompt(QUERY, windows, mode="long")  # type: ignore[arg-type]


def test_window_positions_and_source_rendering(
    premise: Premise, windows: tuple[Window, ...]
) -> None:
    positions = window_positions(premise, windows[:3])
    assert {i: w.passage_id for i, w in positions.items()} == {0: "P1", 1: "P2", 2: "P3"}
    source = render_source([windows[1], windows[2]])
    assert source == f"[P2] {windows[1].text}\n\n[P3] {windows[2].text}"


def test_estimate_tokens_rounds_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_policy_carries_the_prompt_hash_and_the_contract_defaults() -> None:
    policy = build_policy("qwen27-ar4")
    assert policy.prompt_sha256 == PROMPT_SHA256
    assert policy.judge_preset == "qwen27-ar4"
    assert (policy.max_rollbacks_per_sentence, policy.max_total_rollbacks) == (3, 8)
    assert policy.refusal_text == ABSTAIN_TEXT
    assert policy.steering == "inject"
    assert verifier_config(policy) is STRICT_FIGURES
    assert build_policy("qwen27-ar4").policy_hash == policy.policy_hash


def test_policy_rejects_bad_names() -> None:
    with pytest.raises(ValueError, match="judge preset"):
        build_policy("  ")
    policy = Policy(judge_preset="x", prompt_sha256=PROMPT_SHA256, verifier_config_name="LOOSE")
    with pytest.raises(ValueError, match="unknown verifier config"):
        verifier_config(policy)
