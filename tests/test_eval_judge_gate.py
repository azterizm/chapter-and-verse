"""Offline tests of the judge gate runner (eval/judge_gate.py): counting, cache, pacing.

No network: the provider is a fake. The replay tests need the verifier repo at ``LRV_ROOT``
(default ``~/Code/legal-rag-verifier``) and skip without it; they read it and write nothing there.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from legal_rag_verifier.judge import STRICT_FIGURES, Judge, JudgedVerifier

from chapterverse.contracts import AllProvidersUnavailable, ProviderCall, ProviderUnavailable

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    name = "judge_gate_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / "judge_gate.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


jg = _load()

# ---------------------------------------------------------------------------- statistics


def test_clopper_pearson_matches_beta_quantiles() -> None:
    # scipy.stats.beta.ppf(0.025, 62, 6), beta.ppf(0.975, 63, 5)
    lo, hi = jg.clopper_pearson(62, 67)
    assert lo == pytest.approx(0.8343738, abs=1e-6)
    assert hi == pytest.approx(0.9753253, abs=1e-6)
    lo, hi = jg.clopper_pearson(8, 84)
    assert lo == pytest.approx(0.0420204, abs=1e-6)
    assert hi == pytest.approx(0.1790597, abs=1e-6)


def test_clopper_pearson_edges() -> None:
    lo, hi = jg.clopper_pearson(0, 10)
    assert lo == 0.0
    assert hi == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-6)
    lo, hi = jg.clopper_pearson(10, 10)
    assert hi == 1.0
    assert lo == pytest.approx(0.025 ** (1 / 10), abs=1e-6)
    with pytest.raises(ValueError, match="need"):
        jg.clopper_pearson(3, 2)
    with pytest.raises(ValueError, match="need"):
        jg.clopper_pearson(0, 0)


def test_rates_split_a_cell_into_right_and_wrong() -> None:
    out = jg.rates({"units": 151, "correct": 138, "false_rollbacks": 8, "caught": 62})
    assert (out["right"], out["wrong"]) == (84, 67)
    assert out["caught"]["pct"] == 92.5
    assert out["false_rollback"]["pct"] == 9.5
    assert out["accuracy"]["ci95"] == [85.7, 95.3]


def test_rates_with_an_empty_side() -> None:
    out = jg.rates({"units": 2, "correct": 2, "false_rollbacks": 0, "caught": 0})
    assert out["wrong"] == 0
    assert out["caught"]["pct"] is None


# ---------------------------------------------------------------------------- the chat


class FakeInner:
    """A RotationJudgeChat stand-in: replies from a script of results or exceptions."""

    model = "fake/qwen"

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
                provider="fake",
                model_id=self.model,
                quantisation=None,
                purpose="judge",
                prompt_tokens=100,
                completion_tokens=7,
                latency_ms=12.0,
            )
        )
        return {"content": item, "ms": 12.0, "tokens": 7, "finish": "stop"}


def _unavailable(kind: Any, detail: str = "", retry: float | None = None) -> Exception:
    return AllProvidersUnavailable([ProviderUnavailable("fake", kind, retry, detail)])


def _chat(tmp_path: Path, inner: FakeInner, **kw: Any) -> Any:
    slept: list[float] = []
    chat = jg.GateChat(
        provider="fake",
        inner=inner,
        body=lambda m: {"messages": m, "temperature": 0.7},
        out=tmp_path,
        sleep=slept.append,
        **kw,
    )
    chat.slept = slept
    return chat


MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
SUPPORTED = '{"label": "supported", "passage": 1}'


def test_cache_hit_sends_nothing_and_survives_a_restart(tmp_path: Path) -> None:
    inner = FakeInner([SUPPORTED])
    chat = _chat(tmp_path, inner)
    first = chat(MSGS)
    assert chat(MSGS) == first
    assert inner.sent == 1
    assert chat.hits == 1
    again = _chat(tmp_path, FakeInner([]))
    assert again(MSGS) == first
    log = [json.loads(x) for x in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert [r["outcome"] for r in log] == ["ok"]
    assert log[0]["prompt_tokens"] == 100


def test_cache_key_depends_on_the_body(tmp_path: Path) -> None:
    inner = FakeInner([SUPPORTED, SUPPORTED])
    chat = _chat(tmp_path, inner)
    chat(MSGS)
    chat([MSGS[0], {"role": "user", "content": "other"}])
    assert inner.sent == 2


def test_rate_limit_waits_and_resends(tmp_path: Path) -> None:
    inner = FakeInner([_unavailable("rate_limit", "HTTP 429", 7.0), SUPPORTED])
    chat = _chat(tmp_path, inner)
    assert chat(MSGS)["content"] == SUPPORTED
    assert chat.slept == [7.0]
    kinds = [
        json.loads(x)["outcome"] for x in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    assert kinds == ["rate_limit", "ok"]


def test_local_ledger_refusal_polls_without_logging_a_request(tmp_path: Path) -> None:
    inner = FakeInner([_unavailable("quota", "local quota ledger refused"), SUPPORTED])
    chat = _chat(tmp_path, inner)
    chat(MSGS)
    assert chat.slept == [jg.POLL_S]
    log = (tmp_path / "requests.jsonl").read_text().splitlines()
    assert len(log) == 1


def test_open_circuit_waits_its_retry(tmp_path: Path) -> None:
    inner = FakeInner([_unavailable("server", "circuit open", 30.0), SUPPORTED])
    chat = _chat(tmp_path, inner)
    chat(MSGS)
    assert chat.slept == [30.0]


def test_daily_quota_halts(tmp_path: Path) -> None:
    inner = FakeInner([_unavailable("quota", "local quota ledger refused")])
    chat = _chat(tmp_path, inner, daily_spent=lambda: True)
    with pytest.raises(jg.RunHalted, match="daily quota"):
        chat(MSGS)


def test_long_waits_halt(tmp_path: Path) -> None:
    inner = FakeInner([_unavailable("rate_limit", "HTTP 429", 600.0)] * 2)
    chat = _chat(tmp_path, inner, max_wait_s=900.0)
    with pytest.raises(jg.RunHalted, match="waited"):
        chat(MSGS)


@pytest.mark.parametrize("kind", ["config", "model_changed"])
def test_config_and_model_changes_halt(tmp_path: Path, kind: str) -> None:
    chat = _chat(tmp_path, FakeInner([_unavailable(kind, "HTTP 401")]))
    with pytest.raises(jg.RunHalted, match=kind):
        chat(MSGS)


def test_request_cap_halts_before_sending(tmp_path: Path) -> None:
    inner = FakeInner([SUPPORTED])
    chat = _chat(tmp_path, inner, requests_left=lambda: 0)
    with pytest.raises(jg.RunHalted, match="cap"):
        chat(MSGS)
    assert inner.sent == 0


def test_server_failure_is_a_failed_attempt_for_judge(tmp_path: Path) -> None:
    """Judge retries an OSError and labels the sentence error after 3 attempts."""
    inner = FakeInner([_unavailable("server", "HTTP 500")] * 3)
    judge = Judge(_chat(tmp_path, inner), reply="short")
    judge.score(["The limit is 5."], "The limit is 5.")
    assert judge.calls[-1]["label"] == "error"
    assert judge.calls[-1]["attempts"] == 3
    assert inner.sent == 3


def test_halt_escapes_judge(tmp_path: Path) -> None:
    judge = Judge(_chat(tmp_path, FakeInner([_unavailable("config", "HTTP 401")])))
    with pytest.raises(jg.RunHalted):
        judge.score(["p"], "s")


def test_judge_counts_a_good_reply(tmp_path: Path) -> None:
    judge = Judge(_chat(tmp_path, FakeInner([SUPPORTED])), reply="short")
    verifier = JudgedVerifier(judge, STRICT_FIGURES)
    assert verifier.config == STRICT_FIGURES
    judge.score(["The limit is 5."], "The limit is 5.")
    assert judge.calls[-1]["label"] == "supported"
    assert judge.calls[-1]["attempts"] == 1


# ---------------------------------------------------------------------------- replay


@pytest.fixture(scope="module")
def verifier() -> Any:
    root = jg.VERIFIER_ROOT
    if not (root / "scripts" / "live_check.py").is_file() or not (root / jg.REFERENCE).is_file():
        pytest.skip("legal-rag-verifier repo with stop 78 results not present")
    return jg.load_verifier(root)


def test_replay_reproduces_stop_78(verifier: Any) -> None:
    calls78 = [
        json.loads(x) for x in (verifier.root / jg.REFERENCE_CALLS).read_text().splitlines() if x
    ]
    chat = jg.ReplayChat(calls78, verifier.judge_mod.prompt_key)
    keys = jg.answer_keys(verifier)
    verdicts, calls = jg.run_answers(verifier, chat, keys)
    ref = json.loads((verifier.root / jg.REFERENCE).read_text())
    assert chat.missing == 0
    assert len(calls) == ref["calls_total"] == 141
    assert jg.score(verifier, verdicts, keys) == ref["cells"]


def test_subsample_cells_add_up_to_the_whole(verifier: Any) -> None:
    calls78 = [
        json.loads(x) for x in (verifier.root / jg.REFERENCE_CALLS).read_text().splitlines() if x
    ]
    keys = jg.answer_keys(verifier)
    verdicts, _ = jg.run_answers(
        verifier, jg.ReplayChat(calls78, verifier.judge_mod.prompt_key), keys
    )
    half = set(keys[::2])
    a = jg.score(verifier, verdicts, sorted(half))["total"]
    b = jg.score(verifier, verdicts, sorted(set(keys) - half))["total"]
    whole = jg.score(verifier, verdicts, keys)["total"]
    assert {k: a[k] + b[k] for k in whole} == whole


def test_scrub_removes_org_ids() -> None:
    assert (
        jg.scrub("in organization `org_01abcXYZ` tier") == "in organization `org_<redacted>` tier"
    )


# ---------------------------------------------------------------------------- budget


def _log(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_groq_ledger_holds_the_day_but_not_the_minute(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    log = tmp_path / "a" / "requests.jsonl"
    _log(
        log,
        [
            {
                "provider": "groq",
                "outcome": "ok",
                "t": now - 600,
                "prompt_tokens": 1000,
                "completion_tokens": 20,
            },
            {
                "provider": "groq",
                "outcome": "ok",
                "t": now - 90000,
                "prompt_tokens": 5000,
                "completion_tokens": 20,
            },
            {"provider": "groq", "outcome": "rate_limit", "t": now - 30},
            {"provider": "cloudflare", "outcome": "ok", "t": now - 10, "prompt_tokens": 9},
        ],
    )
    ledger = jg.groq_ledger([log, tmp_path / "missing.jsonl"], now=now)
    g = ledger.snapshot()["groq"]
    assert g["tokens_day"] == 1020
    assert g["requests_day"] == 1
    assert g["tokens_minute"] == 0
    assert ledger.available("groq")


def test_groq_day_ceiling_counts_as_spent(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    log = tmp_path / "requests.jsonl"
    _log(
        log,
        [
            {
                "provider": "groq",
                "outcome": "ok",
                "t": now - 100 - i,
                "prompt_tokens": 10_000,
                "completion_tokens": 0,
            }
            for i in range(20)
        ],
    )
    ledger = jg.groq_ledger([log], now=now)
    assert jg._groq_daily_spent(ledger)


def test_groq_sent_today_counts_every_logged_request(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _log(a, [{"provider": "groq", "day": "2026-11-19", "outcome": o} for o in ("ping", "ok")])
    _log(
        b,
        [
            {"provider": "groq", "day": "2026-11-19", "outcome": "rate_limit"},
            {"provider": "groq", "day": "2026-11-18", "outcome": "ok"},
            {"provider": "cloudflare", "day": "2026-11-19", "outcome": "ok"},
        ],
    )
    assert jg.groq_sent_today([a, b], "2026-11-19") == 3


# ---------------------------------------------------------------------------- option 2 routing


def _prompt(chars: int) -> list[dict[str, str]]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": "x" * chars}]


def test_route_is_fixed_by_the_prompt_estimate() -> None:
    # estimate = ceil(chars * 1.5 / 4) + 8; Cloudflare once estimate + 1024 > 8000.
    assert jg.heldout_route(_prompt(18_000)) == "groq"
    assert jg.heldout_route(_prompt(19_000)) == "cloudflare"
    retry = [
        *_prompt(18_000),
        {"role": "assistant", "content": "y" * 5000},
        {"role": "user", "content": "again"},
    ]
    assert jg.heldout_route(retry) == "groq"


def test_router_sends_each_call_to_its_provider_and_records_it() -> None:
    got: list[str] = []

    def chat(name: str) -> Any:
        def call(messages: list[dict[str, str]]) -> dict[str, Any]:
            got.append(name)
            return {"content": SUPPORTED, "ms": 1.0, "tokens": 1, "finish": "stop"}

        return call

    router = jg.SizeRouter({"groq": chat("groq"), "cloudflare": chat("cloudflare")}, len)
    router(_prompt(100))
    router(_prompt(30_000))
    assert got == ["groq", "cloudflare"]
    assert router.used == {100: "groq", 30_000: "cloudflare"}


def test_prefetch_pass_skips_rows_for_the_other_provider() -> None:
    router = jg.SizeRouter({"groq": FakeInner([SUPPORTED])}, len, only="groq")
    with pytest.raises(jg.SkipRow):
        router(_prompt(30_000))
    judge = Judge(router, reply="short")
    with pytest.raises(jg.SkipRow):
        judge.score(["p"], "x" * 30_000)


# ---------------------------------------------------------------------------- R3: judge scope


def test_fresh2_run_and_replay_reject_cited_and_section_scopes(tmp_path: Path) -> None:
    for scope in ("cited", "section"):
        with pytest.raises(SystemExit, match="fresh2"):
            jg.main(["--out", str(tmp_path), "run", "--provider", "groq", "--judge-scope", scope])
        with pytest.raises(SystemExit, match="fresh2"):
            jg.main(["--out", str(tmp_path), "--judge-scope", scope, "replay"])


def test_cited_windows_rule_matches_equal_ancestor_and_descendant_only() -> None:
    from legal_rag_verifier.premise import Passage, PassageKind, Premise  # noqa: PLC0415

    premise = Premise(
        passages=(
            Passage("Section stem", coordinate="uk/ukpga/2006/46/s465", kind=PassageKind.PROVISION),
            Passage("Sub 3", coordinate="uk/ukpga/2006/46/s465/3", kind=PassageKind.PROVISION),
            Passage("Sub 3a", coordinate="uk/ukpga/2006/46/s465/3/a", kind=PassageKind.PROVISION),
            Passage("Sub 30", coordinate="uk/ukpga/2006/46/s465/30", kind=PassageKind.PROVISION),
            Passage("Other s46", coordinate="uk/ukpga/2006/46/s46", kind=PassageKind.PROVISION),
            Passage("Fact", coordinate=None, kind=PassageKind.FACT),
        )
    )
    assert jg.cited_windows_for_coordinate(premise, "uk/ukpga/2006/46/s465/3") == (0, 1, 2)
    assert jg.cited_windows_for_coordinate(premise, "uk/ukpga/2006/46/s999") == ()
    assert jg.cited_windows_for_coordinate(premise, None) == ()


def test_section_windows_rule_matches_entire_section_only() -> None:
    from legal_rag_verifier.premise import Passage, PassageKind, Premise  # noqa: PLC0415

    premise = Premise(
        passages=(
            Passage("Section stem", coordinate="uk/ukpga/2006/46/s465", kind=PassageKind.PROVISION),
            Passage("Sub 3", coordinate="uk/ukpga/2006/46/s465/3", kind=PassageKind.PROVISION),
            Passage("Sub 30", coordinate="uk/ukpga/2006/46/s465/30", kind=PassageKind.PROVISION),
            Passage("Other s46", coordinate="uk/ukpga/2006/46/s46", kind=PassageKind.PROVISION),
            Passage("Fact", coordinate=None, kind=PassageKind.FACT),
        )
    )
    assert jg.section_windows_for_coordinate(premise, "uk/ukpga/2006/46/s465/3") == (0, 1, 2)
    assert jg.section_windows_for_coordinate(premise, "uk/ukpga/2006/46/s999") == ()
    assert jg.section_windows_for_coordinate(premise, None) == ()


def test_run_heldout_cited_scope_and_fallback(tmp_path: Path) -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    from legal_rag_verifier import judge as jm  # noqa: PLC0415
    from legal_rag_verifier.premise import Passage, PassageKind, Premise  # noqa: PLC0415

    p_anc = Passage(
        "Section 1", coordinate="uk/ukpga/2000/1/s1", kind=PassageKind.PROVISION, heading="Act, s.1"
    )
    p_sub = Passage(
        "Subsection (2) limit is 5.",
        coordinate="uk/ukpga/2000/1/s1/2",
        kind=PassageKind.PROVISION,
        heading="Act, s.1(2)",
    )
    p_desc = Passage(
        "Paragraph (a) extra.",
        coordinate="uk/ukpga/2000/1/s1/2/a",
        kind=PassageKind.PROVISION,
        heading="Act, s.1(2)(a)",
    )
    p_other = Passage(
        "Subsection (3) unrelated.",
        coordinate="uk/ukpga/2000/1/s1/3",
        kind=PassageKind.PROVISION,
        heading="Act, s.1(3)",
    )
    premise = Premise(
        passages=(p_anc, p_sub, p_desc, p_other),
        enrichment=jg.HELDOUT_ENRICHMENT_ID,
    )
    rows = [
        {
            "id": "r-match",
            "class": "grounded_paraphrase",
            "expected": "EMIT",
            "query": "What is the limit?",
            "sentence": "The limit is five.",
            "excerpt_coordinate": "uk/ukpga/2000/1/s1/2",
            "premise": ["uk/ukpga/2000/1/s1"],
        },
        {
            "id": "r-miss",
            "class": "grounded_paraphrase",
            "expected": "EMIT",
            "query": "What is the limit?",
            "sentence": "The limit is five.",
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

    class SpyChat:
        model = "spy"

        def __init__(self) -> None:
            self.prompts: list[str] = []

        def __call__(self, messages: list[dict[str, str]], /) -> dict[str, Any]:
            self.prompts.append(messages[1]["content"])
            return {"content": SUPPORTED, "ms": 1.0, "tokens": 1, "finish": "stop"}

    spy_all = SpyChat()
    stats_all: dict[str, int] = {}
    jg.run_heldout(fake_v, fake_rb, spy_all, judge_scope="all", stats=stats_all)
    assert stats_all["cited_fallback"] == 0
    assert "[4]" in spy_all.prompts[0]

    spy_cited = SpyChat()
    stats_cited: dict[str, int] = {}
    jg.run_heldout(fake_v, fake_rb, spy_cited, judge_scope="cited", stats=stats_cited)
    assert stats_cited["cited_fallback"] == 1
    # First row matched ancestor (0), equal (1), descendant (2) -> 3 passages, no [4] / s.1(3)
    assert "[3]" in spy_cited.prompts[0]
    assert "[4]" not in spy_cited.prompts[0]
    assert "unrelated" not in spy_cited.prompts[0]
    # Second row matched nothing -> fallback to all 4 passages
    assert "[4]" in spy_cited.prompts[1]
    assert "unrelated" in spy_cited.prompts[1]

    spy_section = SpyChat()
    stats_section: dict[str, int] = {}
    jg.run_heldout(fake_v, fake_rb, spy_section, judge_scope="section", stats=stats_section)
    assert stats_section["cited_fallback"] == 1
    # First row matches the whole s1 section (all 4 passages, including sibling s.1(3))
    assert "[4]" in spy_section.prompts[0]
    assert "unrelated" in spy_section.prompts[0]
    # Second row matched nothing -> fallback to all 4 passages
    assert "[4]" in spy_section.prompts[1]
