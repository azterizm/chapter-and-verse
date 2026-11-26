"""Answer-level evaluation runner for the legal battery.

Records answers via the local recorder in ``eval.legal_gate`` with ``DATABASE_URL`` and
all Modal settings removed (Groq and Cloudflare only), scores them with the answer rubric,
and writes ``<out>/<UTC date>-<label>-<split>.json`` and ``.md``.

Usage:
    uv run python -m eval.legal_answers --split dev|test [--sealed-test]
        [--rows L01,L41] [--pace 20] [--label TEXT] [--out reports/legal-answers]
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import chapterverse.app
from chapterverse.contracts import (
    ClarifyEvent,
    ExtractiveEvent,
    NoticeEvent,
    RefusalEvent,
    SentenceEvent,
)
from eval.legal_gate import (
    DEFAULT_BATTERY,
    DEFAULT_SEAL,
    ROUTER_REPO,
    extract_provider_calls,
    load_battery,
    parse_row_ids,
    run_recorder,
    run_rubric,
    verify_seal,
)

__all__ = [
    "ANSWER_TEXT_LIMIT",
    "DEFAULT_OUT_DIR",
    "EXCLUDED_ENV_KEYS",
    "INCOMPLETE_NOTICE_CODES",
    "build_answers_env",
    "build_answers_report",
    "default_label",
    "main",
    "render_markdown_report",
    "run_legal_answers",
]

DEFAULT_OUT_DIR: Final = Path("reports/legal-answers")
ANSWER_TEXT_LIMIT: Final = 300
EXCLUDED_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "DATABASE_URL",
        "MODAL_URL",
        "MODAL_API_KEY",
        "MODAL_MASK_PROCESSOR",
        "MODAL_TOKENIZER_JSON",
    }
)
INCOMPLETE_NOTICE_CODES: Final[frozenset[str]] = frozenset(
    {
        "providers_unavailable",
        "provider_warming",
        "daily_cap",
        "global_budget",
        "quote_truncated",
        "no_text",
    }
)
_GROQ_CF_ONLY: Final = "groq,cloudflare"


def _filter_modal_provider(raw: str | None) -> str:
    if not raw or not raw.strip():
        return _GROQ_CF_ONLY
    kept = [p.strip().lower() for p in raw.split(",") if p.strip() and p.strip().lower() != "modal"]
    return ",".join(kept) if kept else _GROQ_CF_ONLY


def build_answers_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a clean environment without DATABASE_URL or Modal settings (Groq and Cloudflare)."""
    source = os.environ if environ is None else environ
    env = {
        k: v for k, v in source.items() if k not in EXCLUDED_ENV_KEYS and not k.startswith("MODAL_")
    }
    env["GEN_PROVIDERS"] = _filter_modal_provider(env.get("GEN_PROVIDERS"))
    env["JUDGE_PROVIDERS"] = _filter_modal_provider(env.get("JUDGE_PROVIDERS"))
    if (
        "DAILY_ASK_CAP" not in env
        and len(env.get("CAP_SALT", "").strip()) < chapterverse.app.MIN_CAP_SALT_LEN
    ):
        env["DAILY_ASK_CAP"] = "0"
    if "LEGAL_DATA_ROOT" not in env and (ROUTER_REPO / "data").is_dir():
        env["LEGAL_DATA_ROOT"] = str(ROUTER_REPO / "data")
    if "STORE_DIR" not in env and Path("artefacts/store").is_dir():
        env["STORE_DIR"] = "artefacts/store"
    if "DENSE_DIR" not in env and Path("artefacts/dense/index").is_dir():
        env["DENSE_DIR"] = "artefacts/dense/index"
    if "DENSE_MODEL_DIR" not in env and Path("artefacts/models").is_dir():
        env["DENSE_MODEL_DIR"] = "artefacts/models"
    return env


def default_label(git_dir: Path = Path(".git")) -> str:
    """Read the short commit hash from .git/HEAD without running git."""
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        return "unknown"
    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_rel = head[5:].strip()
        ref_file = git_dir / ref_rel
        if ref_file.is_file():
            sha = ref_file.read_text(encoding="utf-8").strip()
            return sha[:7] or "unknown"
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                line_s = line.strip()
                if not line_s or line_s.startswith(("#", "^")):
                    continue
                sha, *rest = line_s.split(" ", 1)
                if rest and rest[0].strip() == ref_rel:
                    return sha.strip()[:7] or "unknown"
        return "unknown"
    return head[:7] if head else "unknown"


def _sanitize_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", label.strip()).strip("-")
    return cleaned or "unknown"


def _extract_incomplete_notices(events: Sequence[Any]) -> list[str]:
    reasons: list[str] = []
    for ev in events:
        if isinstance(ev, NoticeEvent):
            if ev.code in INCOMPLETE_NOTICE_CODES:
                reasons.append(ev.code)
        elif isinstance(ev, dict) and ev.get("type") == "notice":
            code = str(ev.get("code", ""))
            if code in INCOMPLETE_NOTICE_CODES:
                reasons.append(code)
    return reasons


def _dict_event_text(ev: Mapping[str, Any]) -> list[str]:
    ev_type = ev.get("type")
    if ev_type == "sentence":
        s_obj = ev.get("sentence")
        return [str(s_obj.get("text", ""))] if isinstance(s_obj, Mapping) else []
    if ev_type == "refusal":
        return [str(ev.get("message", ""))]
    if ev_type == "clarify":
        return [str(ev.get("question", ""))]
    if ev_type == "extractive":
        return [str(w.get("text", "")) for w in ev.get("windows") or () if isinstance(w, Mapping)]
    return []


def _extract_answer_text(events: Sequence[Any]) -> str:
    parts: list[str] = []
    for ev in events:
        if isinstance(ev, SentenceEvent):
            parts.append(ev.sentence.text)
        elif isinstance(ev, RefusalEvent):
            parts.append(ev.message)
        elif isinstance(ev, ClarifyEvent):
            parts.append(ev.question)
        elif isinstance(ev, ExtractiveEvent):
            parts.extend(w.text for w in ev.windows)
        elif isinstance(ev, Mapping):
            parts.extend(_dict_event_text(ev))
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return text[:ANSWER_TEXT_LIMIT]


def build_answers_report(
    rubric_res: Mapping[str, Any],
    answers: Sequence[Mapping[str, Any]],
    *,
    split: str,
    label: str,
    utc_date: str,
) -> dict[str, Any]:
    """Combine rubric scoring with per-call providers, incomplete notices, and answer snippets."""
    answers_by_id = {str(a.get("id", "")): a for a in answers}
    notice_counts: Counter[str] = Counter()
    rows_out: list[dict[str, Any]] = []

    for res in rubric_res.get("results", []):
        rid = str(res["id"])
        ans = answers_by_id.get(rid, {})
        events = ans.get("events", [])
        calls = ans.get("provider_calls")
        if not calls:
            calls = extract_provider_calls(events)
        providers_used = [str(c.get("provider", "")) for c in calls if isinstance(c, Mapping)]
        incomplete_reasons = _extract_incomplete_notices(events)
        for reason in incomplete_reasons:
            notice_counts[reason] += 1
        incomplete_sent = bool(incomplete_reasons)
        reason_str = (
            incomplete_reasons[0]
            if len(incomplete_reasons) == 1
            else (", ".join(dict.fromkeys(incomplete_reasons)) if incomplete_reasons else None)
        )
        rows_out.append(
            {
                "id": rid,
                "passed": bool(res["passed"]),
                "failed_checks": list(res.get("failure_reasons", [])),
                "elapsed_ms": ans.get("elapsed_ms"),
                "provider_calls": list(calls),
                "providers_used": providers_used,
                "incomplete_notice": incomplete_sent,
                "incomplete_reason": reason_str,
                "answer_text": _extract_answer_text(events),
            }
        )

    passed_count = int(rubric_res.get("passed", 0))
    failed_count = int(rubric_res.get("failed", 0))
    total_count = int(rubric_res.get("total_evaluated", len(rows_out)))

    return {
        "utc_date": utc_date,
        "label": label,
        "split": split,
        "totals": {
            "rows_total": total_count,
            "rows_passed": passed_count,
            "rows_failed": failed_count,
            "incomplete_notices_by_reason": dict(sorted(notice_counts.items())),
        },
        "rows": rows_out,
    }


def render_markdown_report(report: Mapping[str, Any]) -> str:
    """Render plain ASCII markdown summary for the answer-level run."""
    totals = report["totals"]
    notices_map = totals["incomplete_notices_by_reason"]
    notices_str = ", ".join(f"{k}: {v}" for k, v in notices_map.items()) if notices_map else "none"
    lines = [
        f"# Legal Answers ({report['split']})",
        "",
        f"Date (UTC): {report['utc_date']}",
        f"Label: {report['label']}",
        f"Split: {report['split']}",
        f"Rows passed: {totals['rows_passed']}/{totals['rows_total']}",
        f"Rows failed: {totals['rows_failed']}",
        f"Incomplete notices by reason: {notices_str}",
        "",
        "## Rows",
        "",
    ]
    for r in report["rows"]:
        tag = "PASS" if r["passed"] else "FAIL"
        providers = ", ".join(r["providers_used"]) if r["providers_used"] else "none"
        elapsed = f"{r['elapsed_ms']} ms" if r["elapsed_ms"] is not None else "n/a"
        inc = f"yes ({r['incomplete_reason']})" if r["incomplete_notice"] else "no"
        failed = "; ".join(r["failed_checks"]) if r["failed_checks"] else "none"
        snippet = " ".join(str(r["answer_text"]).split())
        lines.append(
            f"- [{tag}] {r['id']} | elapsed: {elapsed} | providers: {providers} | "
            f"incomplete: {inc} | failed_checks: {failed} | answer: {snippet}"
        )
    lines.append("")
    return "\n".join(lines)


def _call_recorder(
    recorder: Callable[..., int],
    rows: list[dict[str, Any]],
    out_path: Path,
    *,
    pace: float,
    row_ids: Sequence[str] | None,
    environ: Mapping[str, str],
) -> int:
    sig = inspect.signature(recorder)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    kwargs: dict[str, Any] = {
        "target": "local",
        "pace": pace,
        "row_ids": row_ids,
    }
    if has_var_kw or "environ" in sig.parameters:
        kwargs["environ"] = environ
    return int(recorder(rows, out_path, **kwargs))


def run_legal_answers(
    *,
    split: str,
    sealed_test: bool = False,
    battery_path: Path = DEFAULT_BATTERY,
    seal_path: Path = DEFAULT_SEAL,
    row_ids: Sequence[str] | None = None,
    pace: float = 20.0,
    label: str | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
    recorder: Callable[..., int] = run_recorder,
    utc_date: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Run answer-level recording and rubric scoring, writing JSON and MD reports."""
    if split == "test":
        if not sealed_test:
            print("REFUSED: --split test requires --sealed-test")
            return 1, None
        ok, note = verify_seal(battery_path, seal_path)
        if not ok:
            print(f"REFUSED: {note}")
            return 1, None
        print(f"seal OK: {note}")

    split_rows = load_battery(battery_path, split=split)
    rubric_rows = [r for r in split_rows if r.get("answer_checks")]
    if row_ids is not None:
        wanted = set(row_ids)
        rubric_rows = [r for r in rubric_rows if r.get("id") in wanted]

    date_str = utc_date or datetime.now(UTC).strftime("%Y-%m-%d")
    resolved_label = _sanitize_label(label if label is not None else default_label())
    stem = f"{date_str}-{resolved_label}-{split}"

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{stem}.jsonl"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"

    clean_env = build_answers_env(environ)
    _call_recorder(
        recorder,
        rubric_rows,
        raw_path,
        pace=pace,
        row_ids=row_ids,
        environ=clean_env,
    )

    recorded: list[dict[str, Any]] = []
    if raw_path.is_file():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                recorded.append(json.loads(stripped))

    rubric_res = run_rubric(rubric_rows, recorded, row_ids=row_ids)
    report = build_answers_report(
        rubric_res,
        recorded,
        split=split,
        label=resolved_label,
        utc_date=date_str,
    )

    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    totals = report["totals"]
    print(f"Wrote {json_path} and {md_path}")
    print(
        f"Rows passed: {totals['rows_passed']}/{totals['rows_total']}; "
        f"incomplete_notices={totals['incomplete_notices_by_reason']}"
    )
    return (0 if totals["rows_failed"] == 0 else 1), report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument(
        "--sealed-test",
        nargs="?",
        const=True,
        default=False,
        help="allow test split execution when seal verifies",
    )
    parser.add_argument("--battery", type=Path, default=DEFAULT_BATTERY)
    parser.add_argument("--seal", type=Path, default=DEFAULT_SEAL)
    parser.add_argument("--rows", default=None, help="comma-separated row ids (e.g. L01,L41)")
    parser.add_argument("--pace", type=float, default=20.0)
    parser.add_argument("--label", default=None, help="run label (defaults to .git/HEAD commit)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rc, _ = run_legal_answers(
        split=args.split,
        sealed_test=bool(args.sealed_test),
        battery_path=args.battery.resolve(),
        seal_path=args.seal.resolve(),
        row_ids=parse_row_ids(args.rows),
        pace=args.pace,
        label=args.label,
        out_dir=args.out.resolve(),
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
