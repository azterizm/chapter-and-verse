"""Offline tests of the Modal held-out runner (eval/heldout_modal.py).

Verifies the R4 requirements:
1. Refuses without a method commit;
2. Caches replies by prompt sha under out / cache.jsonl;
3. Cost cap stops the run;
4. Counting matches the Groq runner's on the same fake verdicts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from legal_rag_verifier.judge import SYSTEM, prompt_key

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chapterverse.contracts import AllProvidersUnavailable, ProviderCall  # noqa: E402
from chapterverse.providers import InMemoryLedger, ModalLimits  # noqa: E402
from chapterverse.providers.openai_compat import ProviderWarming  # noqa: E402
from eval import heldout_modal as hm  # noqa: E402
from eval import judge_gate as jg  # noqa: E402

MSGS = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Hypothesis to check"}]
SUPPORTED = '{"label": "supported", "passage": 1}'
CONTRADICTED = '{"label": "contradicted", "passage": 2}'


class FakeInner:
    """A test double for the inner chat client: returns scripted replies."""

    model = "Vishva007/Qwen3.8-27B"

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[ProviderCall] = []
        self.sent = 0

    def __call__(self, messages: list[dict[str, str]], /) -> dict[str, Any]:
        self.sent += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        self.calls.append(
            ProviderCall(
                provider="modal",
                model_id=self.model,
                quantisation="GPTQ",
                purpose="judge",
                prompt_tokens=120,
                completion_tokens=8,
                latency_ms=580.0,
            )
        )
        return {"content": item, "ms": 580.0, "tokens": 8, "finish": "stop"}


def _gate_chat(tmp_path: Path, inner: FakeInner, **kw: Any) -> hm.ModalGateChat:
    slept: list[float] = []
    chat = hm.ModalGateChat(
        provider="modal",
        inner=inner,
        body=lambda m: {"messages": m, "temperature": 0.7},
        out=tmp_path,
        sleep=slept.append,
        **kw,
    )
    chat.slept = slept  # type: ignore[attr-defined]
    return chat


# ---------------------------------------------------------------------- 1. Refuses without commit


def test_refuses_without_method_commit(tmp_path: Path) -> None:
    args = argparse.Namespace(
        verifier=jg.VERIFIER_ROOT,
        out=tmp_path,
        layer_root=None,
        dry=False,
        method_commit="",
        max_usd=3.00,
        cmd=None,
    )
    with pytest.raises(SystemExit, match="method-commit"):
        hm.cmd_heldout(args)


# ---------------------------------------------------------------------- 2. Caches by prompt sha


def test_prompt_sha_matches_judge_prompt_key() -> None:
    user_text = "Some passage text for user prompt"
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_text}]
    assert hm.prompt_sha(msgs) == prompt_key(user_text)


def test_caches_by_prompt_sha_and_survives_restart(tmp_path: Path) -> None:
    inner = FakeInner([SUPPORTED])
    chat = _gate_chat(tmp_path, inner)
    expected_sha = hm.prompt_sha(MSGS)

    # First call: hits provider
    reply1 = chat(MSGS)
    assert reply1["content"] == SUPPORTED
    assert inner.sent == 1
    assert chat.hits == 0

    # Second call in same process: cache hit
    reply2 = chat(MSGS)
    assert reply2 == reply1
    assert inner.sent == 1
    assert chat.hits == 1

    # Verify cache file format on disk
    cache_lines = [
        json.loads(line) for line in (tmp_path / "cache.jsonl").read_text().splitlines() if line
    ]
    assert len(cache_lines) == 1
    assert cache_lines[0]["key"] == expected_sha
    assert cache_lines[0]["prompt_sha"] == expected_sha
    assert (tmp_path / "cache" / f"{expected_sha}.json").is_file()

    # Restart with empty script that would throw IndexError if called
    restart_chat = _gate_chat(tmp_path, FakeInner([]))
    assert expected_sha in restart_chat.cache
    reply3 = restart_chat(MSGS)
    assert reply3 == reply1
    assert restart_chat.hits == 1


# ---------------------------------------------------------------------- 3. Cost cap stops the run


def test_cost_cap_stops_run_before_sending(tmp_path: Path) -> None:
    inner = FakeInner([SUPPORTED])
    chat = _gate_chat(tmp_path, inner, max_usd=3.00, cost_spent=lambda: True)
    with pytest.raises(jg.RunHalted, match="dollar cap"):
        chat(MSGS)
    assert inner.sent == 0


def test_cost_cap_stops_run_after_budget_spent(tmp_path: Path) -> None:
    # 1 cold start = $0.3279, next tail = $0.0748. Cap of $0.40 allows 1 call but stops the 2nd.
    limits = ModalLimits(usd_per_session=0.40, usd_per_month=0.40)
    ledger = InMemoryLedger(modal=limits)
    inner = FakeInner([SUPPORTED, CONTRADICTED])
    chat = _gate_chat(tmp_path, inner, ledger=ledger, max_usd=0.40)

    # First call succeeds (fits within $0.40)
    chat(MSGS)
    assert inner.sent == 1

    # Second call fails because session spend plus tail exceeds $0.40
    other_msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "second"}]
    with pytest.raises(jg.RunHalted, match="dollar cap"):
        chat(other_msgs)
    assert inner.sent == 1


# -------------------------------------------------------- 4. Counting matches Groq runner


def test_counting_matches_groq_runner() -> None:
    rb = jg.load_run_battery(jg.VERIFIER_ROOT)
    fake_outcomes = [
        # Pass classes
        rb.Outcome("row-1", "grounded_paraphrase", "ROLLBACK", "PASS", ()),
        rb.Outcome("row-2", "grounded_paraphrase", "ROLLBACK", "ROLLBACK", ("ungrounded_figure",)),
        rb.Outcome("row-3", "connective", "ROLLBACK", "PASS", ()),
        rb.Outcome("row-4", "premise_correction", "ROLLBACK", "PASS", ()),
        # Planted failure classes
        rb.Outcome("row-5", "wrong_figure", "ROLLBACK", "ROLLBACK", ("ungrounded_figure",)),
        rb.Outcome("row-6", "value_swap", "ROLLBACK", "ROLLBACK", ("contradicted",)),
        rb.Outcome("row-7", "modal_shift", "ROLLBACK", "PASS", ()),
        rb.Outcome("row-8", "unsupported_plausible", "ROLLBACK", "ROLLBACK", ("not_stated",)),
    ]

    jg_counts = jg.heldout_counts(rb, fake_outcomes)
    modal_counts = hm.heldout_counts(rb, fake_outcomes)

    assert modal_counts == jg_counts
    assert modal_counts["rows"] == 8
    # 4 failures: 3 caught (recall 75%)
    assert modal_counts["failure_recall"]["k"] == 3
    assert modal_counts["failure_recall"]["n"] == 4
    assert modal_counts["failure_recall"]["pct"] == 75.0
    # 4 passes: 1 false rollback (25%)
    assert modal_counts["pass_false_rollback"]["k"] == 1
    assert modal_counts["pass_false_rollback"]["n"] == 4
    assert modal_counts["pass_false_rollback"]["pct"] == 25.0
    # Precision: 3 / (3 + 1) = 75%
    assert modal_counts["rollback_precision"]["pct"] == 75.0


# ---------------------------------------------------------------------- Additional helper tests


def test_estimate_modal_cost() -> None:
    # 328 calls: 406s cold start + 328 * 0.58s run + 120s tail = 716.24s
    # 716.24 * (0.000542 * 1.15) = 0.4464 USD
    est = hm.estimate_modal_cost(328)
    assert 0.40 <= est <= 0.50


def test_dynamic_modal_adapter_reads_env_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODAL_URL", raising=False)
    adapter = hm.DynamicModalAdapter()
    with pytest.raises(Exception, match="MODAL_URL"):
        _ = adapter.base_url()

    monkeypatch.setenv("MODAL_URL", "https://modal.example.com/endpoint")
    assert adapter.base_url() == "https://modal.example.com/endpoint/v1"
    assert adapter.health_url() == "https://modal.example.com/endpoint/health"


# ---------------------------------------------------------------------- R3: judge scope & dry run


def test_dry_run_reports_costs_and_chars_per_scope_and_separates_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    from legal_rag_verifier import judge as jm  # noqa: PLC0415
    from legal_rag_verifier.premise import Passage, PassageKind, Premise  # noqa: PLC0415

    p1 = Passage(
        "Alpha 10.",
        coordinate="uk/ukpga/2000/1/s1/1",
        kind=PassageKind.PROVISION,
        heading="Act, s.1(1)",
    )
    p2 = Passage(
        "Beta 20.",
        coordinate="uk/ukpga/2000/1/s1/2",
        kind=PassageKind.PROVISION,
        heading="Act, s.1(2)",
    )
    premise = Premise(passages=(p1, p2), enrichment=jg.HELDOUT_ENRICHMENT_ID)
    rows = [
        {
            "id": "r1",
            "class": "grounded_paraphrase",
            "expected": "EMIT",
            "query": "Q?",
            "sentence": "The figure is 10.",
            "excerpt_coordinate": "uk/ukpga/2000/1/s1/1",
            "premise": ["uk/ukpga/2000/1/s1"],
        },
        {
            "id": "r2",
            "class": "grounded_paraphrase",
            "expected": "EMIT",
            "query": "Q?",
            "sentence": "The figure is 20.",
            "excerpt_coordinate": "uk/ukpga/2000/1/s99/1",
            "premise": ["uk/ukpga/2000/1/s1"],
        },
    ]

    class FakeOutcome:
        def __init__(
            self, row_id: str, cls: str, expected: str, verdict: str, reasons: Any
        ) -> None:
            self.row_id = row_id
            self.cls = cls
            self.expected = expected
            self.verdict = verdict
            self.reasons = reasons

    fake_rb = SimpleNamespace(
        load_rows=lambda _p: rows,
        premises=SimpleNamespace(premise_key=lambda r: r["id"]),
        premises_for=lambda rs, _d, _e: {r["id"]: premise for r in rs},
        Outcome=FakeOutcome,
    )
    fake_v = SimpleNamespace(root=tmp_path, judge_mod=jm)
    monkeypatch.setattr(hm, "load_verifier", lambda _root: fake_v)
    monkeypatch.setattr(hm, "load_run_battery", lambda _root: fake_rb)
    monkeypatch.setattr(hm, "check_seal", lambda _root: {"seal": "s", "sha256": "h", "rows": 2})

    assert hm.main(["--out", str(tmp_path), "--dry", "--judge-scope", "all"]) == 0
    all_summary = json.loads(capsys.readouterr().out)
    assert hm.main(["--out", str(tmp_path), "--dry", "--judge-scope", "cited"]) == 0
    cited_summary = json.loads(capsys.readouterr().out)

    assert all_summary["judge_scope"] == "all"
    assert cited_summary["judge_scope"] == "cited"
    assert all_summary["judge_calls"] == cited_summary["judge_calls"] == 2
    assert all_summary["estimated_usd"] == cited_summary["estimated_usd"]
    assert cited_summary["judge_prompt_chars"] < all_summary["judge_prompt_chars"]
    assert all_summary["cited_fallback"] == 0
    assert cited_summary["cited_fallback"] == 1

    # Scope 'cited' writes under out/cited/, while default 'all' keeps out/ unchanged.
    seen_out: list[Path] = []
    monkeypatch.setattr(
        hm, "run_heldout", lambda *_a, **_kw: ([], [], [], {jg.HELDOUT_ENRICHMENT_ID}, [])
    )
    monkeypatch.setattr(
        hm,
        "DynamicModalAdapter",
        lambda: SimpleNamespace(base_url=lambda: "", health_url=lambda: ""),
    )
    monkeypatch.setattr(
        hm,
        "OpenAICompatProvider",
        lambda _a, ledger=None: SimpleNamespace(
            model_id="m",
            build_body=lambda _r: ({}, None),
        ),
    )
    monkeypatch.setattr(hm, "ProviderRotation", lambda *_a, **_kw: SimpleNamespace())
    monkeypatch.setattr(hm, "RotationJudgeChat", lambda *_a, **_kw: SimpleNamespace(model="m"))
    monkeypatch.setattr(hm, "settings_sent", lambda _p: {})
    monkeypatch.setattr(hm, "heldout_counts", lambda _rb, _o: {"summarise": {"headline": {}}})
    monkeypatch.setattr(hm, "recorded_outcomes", lambda *_a: [])

    def spy_gate_chat(**kw: Any) -> Any:
        seen_out.append(kw["out"])
        return SimpleNamespace(cache={}, waits=[])

    monkeypatch.setattr(hm, "ModalGateChat", spy_gate_chat)
    assert hm.main(["--out", str(tmp_path), "--method-commit", "abc"]) == 0
    assert (
        hm.main(["--out", str(tmp_path), "--method-commit", "abc", "--judge-scope", "cited"]) == 0
    )
    assert seen_out == [tmp_path, tmp_path / "cited"]


def test_warming_server_is_waited_for_not_a_failed_attempt(tmp_path: Path) -> None:
    warming = AllProvidersUnavailable(
        [ProviderWarming("modal", "warming: health probe did not answer")]
    )
    inner = FakeInner([warming, warming, SUPPORTED])
    chat = _gate_chat(tmp_path, inner)
    reply = chat(MSGS)
    assert reply["content"] == SUPPORTED
    assert inner.sent == 3
    assert chat.slept == [hm.WARM_POLL_S, hm.WARM_POLL_S]  # type: ignore[attr-defined]


def test_warming_past_max_wait_halts_the_run(tmp_path: Path) -> None:
    warming = AllProvidersUnavailable(
        [ProviderWarming("modal", "warming: health probe did not answer")]
    )
    inner = FakeInner([warming] * 5)
    chat = _gate_chat(tmp_path, inner, max_wait_s=2 * hm.WARM_POLL_S)
    with pytest.raises(jg.RunHalted):
        chat(MSGS)
