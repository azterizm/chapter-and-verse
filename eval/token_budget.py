"""S5.1a token budget measurement over the legal battery (no network, no LLM calls).

Captures the real first generate ``ChatRequest`` via :class:`CapturingRotation` over
:func:`eval.legal_gate._build_pipeline`, builds the real judge ``ChatRequest`` via
``legal_rag_verifier.judge`` and ``JudgedVerifier``, and reports per-call tokens,
ledger reservations, and answer shapes against Groq's 8,000 TPM minute limit.

Usage:
    uv run python -m eval.token_budget --split dev [--battery PATH] [--out DIR]
        [--judge-scope all|cited|section] [--dense-dir DIR]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal

from legal_rag_verifier.index import build_index
from legal_rag_verifier.judge import STRICT_FIGURES, Judge, JudgedVerifier
from legal_rag_verifier.premise import Passage, PassageKind, Premise

from chapterverse.audit.prompt import estimate_tokens as plain_estimate_tokens
from chapterverse.audit.prompt import render_passage
from chapterverse.audit.refs import check_refs
from chapterverse.contracts import (
    AllProvidersUnavailable,
    ChatRequest,
    ChatResult,
    Event,
    ProviderUnavailable,
    SourcesEvent,
    TemporalStatus,
    Window,
)
from chapterverse.pipeline import AskRequest, Pipeline
from chapterverse.providers.judge_chat import RotationJudgeChat
from chapterverse.providers.ledger import JUDGE_OUTPUT_RESERVE
from chapterverse.providers.ledger import estimate_tokens as ledger_estimate_tokens
from chapterverse.providers.presets import JUDGE_PRESET
from chapterverse.sections import section_coordinate
from eval.latency import percentile
from eval.legal_gate import (
    DEFAULT_BATTERY,
    DEFAULT_SEAL,
    ROUTER_REPO,
    _build_pipeline,
    load_battery,
    verify_seal,
)

__all__ = [
    "ANSWER_SHAPES",
    "EARLIER_SENTENCE_TOKENS",
    "GROQ_TPM_LIMIT",
    "REP_SENTENCE_WORDS",
    "CapturingRotation",
    "answers_per_minute",
    "build_judge_request",
    "default_out_dir",
    "main",
    "measure_row",
    "render_markdown",
    "shape_reservation",
    "summarize_measurements",
]

GROQ_TPM_LIMIT: Final = 8000
EARLIER_SENTENCE_TOKENS: Final = 40
REP_SENTENCE_WORDS: Final = 25
ANSWER_SHAPES: Final[tuple[tuple[str, int, int], ...]] = (
    ("A", 1, 2),
    ("B", 2, 3),
    ("C", 3, 6),
)
JudgeScope = Literal["all", "cited", "section"]


class CapturingRotation:
    """``Rotation`` that records every ``ChatRequest`` and fails like ``NoModelRotation``."""

    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def available(self) -> list[str]:
        return ["fake"]

    def complete(self, req: ChatRequest) -> ChatResult:
        self.requests.append(req)
        raise AllProvidersUnavailable([ProviderUnavailable("fake", "config", None, "fake")])


def shape_reservation(
    gen_reservation: int,
    judge_reservation: int,
    gen_calls: int,
    judge_calls: int,
    *,
    earlier_sentence_tokens: int = EARLIER_SENTENCE_TOKENS,
) -> int:
    """Total ledger reservation across ``gen_calls`` generate and ``judge_calls`` judge calls.

    Block k (0-indexed) resends the prompt plus k earlier accepted sentences estimated at
    ``earlier_sentence_tokens`` each.
    """
    gen_total = sum(gen_reservation + k * earlier_sentence_tokens for k in range(gen_calls))
    return gen_total + judge_calls * judge_reservation


def answers_per_minute(reservation: int, tpm_limit: int = GROQ_TPM_LIMIT) -> int:
    """Whole answers that fit in ``tpm_limit`` tokens per minute."""
    if reservation <= 0:
        return 0
    return tpm_limit // reservation


def _representative_sentence(windows: Sequence[Window]) -> str:
    first = windows[0]
    words = first.text.split()[:REP_SENTENCE_WORDS]
    raw = f"{' '.join(words)} [{first.passage_id}]"
    passage_ids = frozenset(w.passage_id for w in windows)
    passage_texts = {w.passage_id: w.text for w in windows}
    passage_coords = {w.passage_id: w.coordinate for w in windows if w.coordinate}
    return check_refs(raw, passage_ids, passage_texts, passage_coords).text


def build_judge_request(
    windows: Sequence[Window],
    *,
    scope: JudgeScope,
    query: str | None = None,
) -> tuple[str, ChatRequest]:
    """Build the real judge ``ChatRequest`` for a representative sentence over ``windows``."""
    if not windows:
        raise ValueError("build_judge_request requires at least one window")
    rep_sentence = _representative_sentence(windows)
    if scope == "all":
        scope_windows: Sequence[Window] = windows
    elif scope == "cited":
        scope_windows = (windows[0],)
    elif scope == "section":
        target_section = section_coordinate(windows[0].coordinate) if windows[0].coordinate else ""
        scope_windows = (
            tuple(
                w
                for w in windows
                if w.coordinate and section_coordinate(w.coordinate) == target_section
            )
            if target_section
            else (windows[0],)
        )
    else:
        raise ValueError(f"unknown judge scope {scope!r}")
    passages = tuple(
        Passage(w.text, w.coordinate, PassageKind.PROVISION, w.heading) for w in scope_windows
    )
    scope_premise = Premise(passages)

    judge_rot = CapturingRotation()
    judge = Judge(RotationJudgeChat(judge_rot), reply=JUDGE_PRESET.reply)  # type: ignore[arg-type]
    verifier = JudgedVerifier(judge, STRICT_FIGURES)
    with contextlib.suppress(AllProvidersUnavailable):
        verifier.check_sentence(scope_premise, rep_sentence, query=query)

    if not judge_rot.requests:
        candidates = [w.nli_text for w in build_index(scope_premise).windows]
        with contextlib.suppress(AllProvidersUnavailable):
            judge.score(candidates, rep_sentence)

    if not judge_rot.requests:
        raise RuntimeError("failed to capture a judge ChatRequest")
    return rep_sentence, judge_rot.requests[0]


def _request_metrics(
    req: ChatRequest,
    *,
    output_reserve: int,
    premise_chars: int | None = None,
) -> dict[str, Any]:
    system_chars = sum(len(m["content"]) for m in req.messages if m["role"] == "system")
    user_chars = sum(len(m["content"]) for m in req.messages if m["role"] == "user")
    total_chars = sum(len(m["content"]) for m in req.messages)
    ledger_tokens = ledger_estimate_tokens(req.messages, req.prefill)
    plain_tokens = plain_estimate_tokens("".join(m["content"] for m in req.messages))
    metrics: dict[str, Any] = {
        "system_chars": system_chars,
        "user_chars": user_chars,
        "total_chars": total_chars,
        "ledger_tokens": ledger_tokens,
        "plain_tokens": plain_tokens,
        "max_tokens": req.max_tokens,
        "output_reserve": output_reserve,
        "ledger_reservation": ledger_tokens + output_reserve,
    }
    if premise_chars is not None:
        metrics["premise_chars"] = premise_chars
        metrics["premise_share"] = round(premise_chars / total_chars, 4) if total_chars > 0 else 0.0
    return metrics


def measure_row(
    pipeline: Pipeline,
    rotation: CapturingRotation,
    row: Mapping[str, Any],
    *,
    judge_scope: JudgeScope,
) -> dict[str, Any]:
    """Run one legal battery row through ``pipeline`` and measure generate and judge budgets."""
    rotation.requests.clear()
    events: list[Event] = []
    question = str(row["question"])
    pipeline.run(AskRequest(query=question), events.append)

    sources_ev = next((e for e in events if isinstance(e, SourcesEvent)), None)
    windows = sources_ev.windows if sources_ev is not None else ()
    model_windows = tuple(w for w in windows if w.status is TemporalStatus.IN_FORCE)
    gen_req = next((r for r in rotation.requests if r.purpose == "generate"), None)

    gen_metrics: dict[str, Any] | None = None
    judge_metrics: dict[str, Any] | None = None
    rep_sentence: str | None = None

    if gen_req is not None and model_windows:
        rendered_passages = "\n\n".join(render_passage(w) for w in model_windows)
        gen_metrics = _request_metrics(
            gen_req,
            output_reserve=gen_req.max_tokens,
            premise_chars=len(rendered_passages),
        )
        rep_sentence, judge_req = build_judge_request(
            model_windows,
            scope=judge_scope,
            query=question,
        )
        judge_metrics = _request_metrics(judge_req, output_reserve=JUDGE_OUTPUT_RESERVE)

    return {
        "id": str(row.get("id", "")),
        "question": question,
        "windows_sent": {
            "count": len(windows),
            "total_chars": sum(len(w.text) for w in windows),
        },
        "in_force_windows": len(model_windows),
        "generate": gen_metrics,
        "judge": judge_metrics,
        "representative_sentence": rep_sentence,
    }


def _stat_pair(values: Sequence[float]) -> dict[str, float]:
    return {
        "median": round(percentile(values, 50.0), 1),
        "p90": round(percentile(values, 90.0), 1),
    }


def _purpose_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not records:
        return {}
    fields = (
        "system_chars",
        "user_chars",
        "total_chars",
        "ledger_tokens",
        "plain_tokens",
        "max_tokens",
        "output_reserve",
        "ledger_reservation",
    )
    out: dict[str, Any] = {f: _stat_pair([float(r[f]) for r in records]) for f in fields}
    if "premise_share" in records[0]:
        out["premise_share"] = {
            "median": round(percentile([float(r["premise_share"]) for r in records], 50.0), 4),
            "p90": round(percentile([float(r["premise_share"]) for r in records], 90.0), 4),
        }
    return out


def summarize_measurements(
    rows: Sequence[Mapping[str, Any]],
    *,
    judge_scope: JudgeScope,
) -> dict[str, Any]:
    """Compute per-purpose median/p90 and answer-shape capacity in 8,000 TPM."""
    gen_records = [r["generate"] for r in rows if r.get("generate") is not None]
    judge_records = [r["judge"] for r in rows if r.get("judge") is not None]
    gen_summary = _purpose_summary(gen_records)
    judge_summary = _purpose_summary(judge_records)

    shapes: list[dict[str, Any]] = []
    if gen_summary and judge_summary:
        gen_med = round(gen_summary["ledger_reservation"]["median"])
        judge_med = round(judge_summary["ledger_reservation"]["median"])
        gen_p90 = round(gen_summary["ledger_reservation"]["p90"])
        judge_p90 = round(judge_summary["ledger_reservation"]["p90"])
        for name, gen_calls, judge_calls in ANSWER_SHAPES:
            res_med = shape_reservation(gen_med, judge_med, gen_calls, judge_calls)
            res_p90 = shape_reservation(gen_p90, judge_p90, gen_calls, judge_calls)
            shapes.append(
                {
                    "shape": name,
                    "generate_calls": gen_calls,
                    "judge_calls": judge_calls,
                    "median_reservation": res_med,
                    "median_answers_per_minute": answers_per_minute(res_med),
                    "p90_reservation": res_p90,
                    "p90_answers_per_minute": answers_per_minute(res_p90),
                }
            )

    return {
        "judge_scope": judge_scope,
        "tpm_limit": GROQ_TPM_LIMIT,
        "earlier_sentence_tokens": EARLIER_SENTENCE_TOKENS,
        "rows_total": len(rows),
        "rows_measured": len(gen_records),
        "rows_without_generate": len(rows) - len(gen_records),
        "by_purpose": {
            "generate": gen_summary,
            "judge": judge_summary,
        },
        "answer_shapes": shapes,
    }


def _ledger_mechanics_lines() -> list[str]:
    return [
        "## Ledger and rotation mechanics",
        "",
        (
            "- Reservation vs actual usage: Before a call, `OpenAICompatProvider._attempt` "
            "(providers/openai_compat.py:790-791) calls `ledger.reserve(name, est, "
            "max_output_tokens=max_out)`, which adds `est + max_out` to `g.pending_tokens` "
            "(providers/ledger.py:303, 492-496). Immediately after the call returns or fails, "
            "`record()` (providers/openai_compat.py:800, 808, 815, 825, 838-847) releases that "
            "pending hold via `_hold(..., sign=-1)` (providers/ledger.py:333, 495-496) and "
            "replaces it in `g.minute` with `(prompt_tokens or 0) + (completion_tokens or 0)` "
            "(falling back to `est_tokens` only when both are None, providers/ledger.py:538-543). "
            "When Groq response headers are present, `g.hdr_tokens` and `self._groq_tpm` are "
            "also updated from `x-ratelimit-remaining-tokens` and `x-ratelimit-limit-tokens` "
            "(providers/ledger.py:553-560)."
        ),
        (
            "- How the 1.5x margin and max_tokens count: `TOKENS_PER_CHAR = 1.5 / 4` "
            "(providers/ledger.py:73) inflates `estimate_tokens()` by 1.5x over 4 chars/token "
            "(providers/ledger.py:77-80). `ProviderRotation.complete` computes `est` with that "
            "function and sets `max_out = JUDGE_OUTPUT_RESERVE` (64, providers/ledger.py:57) for "
            "judge calls or `req.max_tokens` for generate calls (providers/rotation.py:436-437). "
            "Before admitting each call, `ProviderRotation._try_provider` checks "
            "`ledger.window_wait(name, est, max_output_tokens=max_out)` "
            "(providers/rotation.py:378-388), which requires `sum(g.minute) + g.pending_tokens + "
            "(est + max_out) <= self._groq_tpm` and, while headers are fresh, `g.hdr_tokens - "
            "g.pending_tokens >= (est + max_out)` (providers/ledger.py:388, 517-532). Thus every "
            "new call must fit the 1.5x prompt estimate plus output reserve in remaining headroom."
        ),
        (
            "- How long a reservation holds the window: The in-flight hold `g.pending_tokens` "
            "lasts only for the duration of the HTTP request until `record()` runs. Once recorded, "
            "actual usage stays in `g.minute` for 60.0 s (`_prune_groq` drops entries where "
            "`ts <= now - 60.0`, providers/ledger.py:505-508). Header headroom `g.hdr_tokens` "
            "applies until `now + (h.reset_tokens_s or 60.0)` (providers/ledger.py:560). If wait "
            "exceeds `WINDOW_WAIT_MAX_S` (8.0 s) or `REQUEST_WAIT_BUDGET_S` (20.0 s), rotation "
            "stops waiting and spills to Cloudflare (providers/rotation.py:68-69, 352-391)."
        ),
        "",
    ]


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render cold, plain markdown summary including totals, shapes, R5 notes, and rows."""
    summary = report["summary"]
    by_purpose = summary["by_purpose"]
    lines: list[str] = [
        f"# Token Budget ({report['split']}, judge-scope={summary['judge_scope']})",
        "",
        (
            f"Rows measured: {summary['rows_measured']}/{summary['rows_total']} "
            f"(without generate: {summary['rows_without_generate']})"
        ),
        "",
        "## Per-call tokens by purpose",
        "",
        (
            "| Purpose | Plain p50 | Plain p90 | Ledger p50 | Ledger p90 | "
            "Reserve p50 | Reserve p90 | Premise share p50 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for purpose in ("generate", "judge"):
        s = by_purpose.get(purpose, {})
        if not s:
            continue
        share = f"{s['premise_share']['median']:.1%}" if "premise_share" in s else "-"
        lines.append(
            f"| {purpose} | {s['plain_tokens']['median']:.1f} | {s['plain_tokens']['p90']:.1f} | "
            f"{s['ledger_tokens']['median']:.1f} | {s['ledger_tokens']['p90']:.1f} | "
            f"{s['ledger_reservation']['median']:.1f} | {s['ledger_reservation']['p90']:.1f} | "
            f"{share} |"
        )

    lines.extend(
        [
            "",
            "## Answer shapes in 8,000 TPM",
            "",
            "| Shape | Calls (gen+judge) | Med reserve | Med ans/min | p90 reserve | p90 ans/min |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for sh in summary["answer_shapes"]:
        calls = f"{sh['generate_calls']}+{sh['judge_calls']}"
        lines.append(
            f"| {sh['shape']} | {calls} | {sh['median_reservation']} | "
            f"{sh['median_answers_per_minute']} | {sh['p90_reservation']} | "
            f"{sh['p90_answers_per_minute']} |"
        )

    lines.append("")
    lines.extend(_ledger_mechanics_lines())
    lines.extend(
        [
            "## Per-row breakdown",
            "",
            (
                "| ID | Win | Win chars | Gen plain | Gen ledger | Gen reserve | "
                "Premise share | Judge plain | Judge ledger | Judge reserve |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for r in report["rows"]:
        w = r["windows_sent"]
        g = r["generate"]
        j = r["judge"]
        if g is None or j is None:
            lines.append(
                f"| {r['id']} | {w['count']} | {w['total_chars']} | - | - | - | - | - | - | - |"
            )
        else:
            lines.append(
                f"| {r['id']} | {w['count']} | {w['total_chars']} | {g['plain_tokens']} | "
                f"{g['ledger_tokens']} | {g['ledger_reservation']} | {g['premise_share']:.1%} | "
                f"{j['plain_tokens']} | {j['ledger_tokens']} | {j['ledger_reservation']} |"
            )
    lines.append("")
    return "\n".join(lines)


def format_console_table(summary: Mapping[str, Any]) -> str:
    """Short plain-text table for stdout."""
    by_purpose = summary["by_purpose"]
    lines = [
        (
            f"Token budget summary (judge-scope={summary['judge_scope']}, "
            f"rows={summary['rows_measured']}/{summary['rows_total']}, TPM={summary['tpm_limit']})"
        ),
        "Purpose   Plain(p50/p90)    Ledger(p50/p90)   Reserve(p50/p90)  PremiseShare",
    ]
    for purpose in ("generate", "judge"):
        s = by_purpose.get(purpose, {})
        if not s:
            continue
        plain = f"{s['plain_tokens']['median']:.0f}/{s['plain_tokens']['p90']:.0f}"
        ledger = f"{s['ledger_tokens']['median']:.0f}/{s['ledger_tokens']['p90']:.0f}"
        res = f"{s['ledger_reservation']['median']:.0f}/{s['ledger_reservation']['p90']:.0f}"
        share = f"{s['premise_share']['median']:.1%}" if "premise_share" in s else "-"
        lines.append(f"{purpose:<9} {plain:<17} {ledger:<17} {res:<17} {share}")
    lines.append("")
    lines.append("Shape  Calls       MedReserve  MedAns/min  p90Reserve  p90Ans/min")
    for sh in summary["answer_shapes"]:
        calls = f"{sh['generate_calls']}g+{sh['judge_calls']}j"
        lines.append(
            f"{sh['shape']:<6} {calls:<11} {sh['median_reservation']:<11} "
            f"{sh['median_answers_per_minute']:<11} {sh['p90_reservation']:<11} "
            f"{sh['p90_answers_per_minute']}"
        )
    return "\n".join(lines)


def default_out_dir() -> Path:
    scratch = os.environ.get("CV_SCRATCH", "scratch")
    return Path(scratch) / "s5-token-budget" / "out"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--sealed-test", action="store_true", help="allow test split after seal")
    parser.add_argument("--battery", type=Path, default=DEFAULT_BATTERY)
    parser.add_argument("--seal", type=Path, default=DEFAULT_SEAL)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--judge-scope", choices=("all", "cited", "section"), default="all")
    parser.add_argument("--dense-dir", type=Path, default=None)
    parser.add_argument("--models-dir", type=Path, default=Path("artefacts/models"))
    parser.add_argument("--router-repo", type=Path, default=ROUTER_REPO)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    battery_path = args.battery.resolve()
    seal_path = args.seal.resolve()

    if args.split == "test":
        if not args.sealed_test:
            print("REFUSED: --split test requires --sealed-test")
            return 1
        ok, note = verify_seal(battery_path, seal_path)
        if not ok:
            print(f"REFUSED: {note}")
            return 1
        print(f"seal OK: {note}")

    out_dir: Path = args.out.resolve() if args.out is not None else default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    pipeline = _build_pipeline(
        args.router_repo.resolve(),
        dense_dir=args.dense_dir.resolve() if args.dense_dir else None,
        models_dir=args.models_dir.resolve(),
    )
    rotation = CapturingRotation()
    pipeline.rotation = rotation

    rows = [r for r in load_battery(battery_path, split=args.split) if r.get("must_reach")]
    measured = [measure_row(pipeline, rotation, r, judge_scope=args.judge_scope) for r in rows]
    summary = summarize_measurements(measured, judge_scope=args.judge_scope)

    report = {
        "split": args.split,
        "battery": str(args.battery),
        "summary": summary,
        "rows": measured,
    }
    (out_dir / "token-budget.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "token-budget.md").write_text(render_markdown(report), encoding="utf-8")

    print(format_console_table(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
