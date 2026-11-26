"""Slice 4 legal battery retrieval gate, answer rubric, and recorder.

Provides:
- Battery loading and seal verification (R1, R2).
- Retrieval gate over real data (R3).
- Answer rubric and recorder with live / local target options (R4).

Local recording builds the runtime with ``chapterverse.app.build_runtime`` with
``DATABASE_URL`` removed from the passed environment, so local recording never writes
receipts or quota rows to Postgres.

Usage:
    uv run python -m eval.legal_gate --split dev [--out DIR]
    uv run python -m eval.legal_gate --answers FILE.jsonl [--rows L01,L07] [--out DIR]
    uv run python -m eval.legal_gate --record --target local [--rows L01,L07] [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal

import httpx
from legal_rag_router import Router

import chapterverse.app
from chapterverse.audit.policy import build_policy
from chapterverse.config import Settings
from chapterverse.contracts import (
    ClarifyEvent,
    ErrorEvent,
    Event,
    ExtractiveEvent,
    ReceiptEvent,
    RefusalEvent,
    RouteEvent,
    SentenceEvent,
    SourcesEvent,
    canonical_json,
    to_jsonable,
)
from chapterverse.dense.embed import load_model
from chapterverse.dense.retriever import DenseRetriever
from chapterverse.dense.store import BinaryInt8Store
from chapterverse.pipeline import AskRequest, Pipeline
from chapterverse.providers.presets import JUDGE_PRESET_NAME
from chapterverse.providers.rotation import ProviderRotation
from chapterverse.store import open_store
from eval.probes_gate import load_access_headers, parse_sse_to_events
from eval.run import NoModelRotation

__all__ = [
    "DEFAULT_BATTERY",
    "DEFAULT_FORBIDDEN_PHRASES",
    "DEFAULT_SEAL",
    "HTTP_OK",
    "evaluate_answer",
    "extract_provider_calls",
    "is_descendant_or_equal",
    "load_battery",
    "main",
    "parse_row_ids",
    "run_recorder",
    "run_retrieval_gate",
    "run_rubric",
    "verify_seal",
]

DEFAULT_BATTERY: Final = Path("batteries/legal/legal.jsonl")
DEFAULT_SEAL: Final = Path("batteries/legal/SEAL.json")
HTTP_OK: Final = 200
DEFAULT_FORBIDDEN_PHRASES: Final[tuple[str, ...]] = (
    "provided passages",
    "the passages",
    "passages provided",
    "provisions provided",
    "the text provided",
    "the excerpt",
)
ROUTER_REPO: Final = Path(
    os.environ.get("LRR_ROOT", str(Path.home() / "Code" / "legal-rag-router"))
)


def is_descendant_or_equal(coord: str, target: str) -> bool:
    """True if coord is equal to target or is a hierarchical descendant of target."""
    c = coord.strip().rstrip("/")
    t = target.strip().rstrip("/")
    return c == t or c.startswith(f"{t}/")


def verify_seal(battery_path: Path, seal_path: Path) -> tuple[bool, str]:
    """Verify test rows against the canonical sha256 seal and row counts."""
    if not seal_path.is_file():
        return False, f"seal file {seal_path} not found"
    if not battery_path.is_file():
        return False, f"battery file {battery_path} not found"
    try:
        seal_data = json.loads(seal_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"cannot read seal: {exc}"

    rows: list[dict[str, Any]] = []
    with battery_path.open("r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                rows.append(json.loads(line_str))

    test_rows = [r for r in rows if r.get("split") == "test"]
    test_rows.sort(key=lambda r: str(r.get("id", "")))
    canonical_bytes = canonical_json(test_rows)
    calc_sha = hashlib.sha256(canonical_bytes).hexdigest()

    expected_sha = seal_data.get("sha256")
    if calc_sha != expected_sha:
        return False, f"seal sha256 mismatch: expected {expected_sha}, computed {calc_sha}"

    expected_counts = seal_data.get("row_counts") or seal_data.get("splits")
    if expected_counts:
        actual_dev = sum(1 for r in rows if r.get("split") == "dev")
        actual_test = sum(1 for r in rows if r.get("split") == "test")
        exp_dev = expected_counts.get("dev")
        exp_test = expected_counts.get("test")
        if actual_dev != exp_dev or actual_test != exp_test:
            msg = (
                f"row counts mismatch: expected {expected_counts}, "
                f"got dev={actual_dev}, test={actual_test}"
            )
            return False, msg

    return True, calc_sha


def load_battery(battery_path: Path, split: str | None = None) -> list[dict[str, Any]]:
    """Load rows from legal.jsonl, optionally filtered by split."""
    rows: list[dict[str, Any]] = []
    with battery_path.open("r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                row = json.loads(line_str)
                if split is None or row.get("split") == split:
                    rows.append(row)
    return rows


def _prepare_settings(repo: Path) -> Settings:
    env_dict = dict(os.environ)
    if "LEGAL_DATA_ROOT" not in env_dict and (repo / "data").is_dir():
        env_dict["LEGAL_DATA_ROOT"] = str(repo / "data")
    if "STORE_DIR" not in env_dict and Path("artefacts/store").is_dir():
        env_dict["STORE_DIR"] = "artefacts/store"
    return Settings.from_env(env_dict)


def _build_pipeline(
    router_repo: Path,
    dense_dir: Path | None = None,
    models_dir: Path = Path("artefacts/models"),
) -> Pipeline:
    settings = _prepare_settings(router_repo)
    store = open_store(settings.store_dir)
    router = Router.from_path(settings.router_index, concepts=settings.concepts_index)

    dense = None
    if dense_dir is not None:
        dense_store = BinaryInt8Store.open(dense_dir, corpus_id=store.corpus_id)
        dense_embedder = load_model("bge-small-en-v1.5", models_dir)
        concepts = getattr(router, "concepts", None) or getattr(router, "_concepts", None)
        dense = DenseRetriever(dense_embedder, dense_store, concepts)

    return Pipeline(
        router=router,
        store=store,
        rotation=NoModelRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
        dense=dense,
    )


def run_retrieval_gate(
    rows: list[dict[str, Any]],
    pipeline: Pipeline,
) -> dict[str, Any]:
    """Run retrieval gate over rows and return detailed results and metrics."""
    per_row: list[dict[str, Any]] = []
    must_items_total = 0
    must_items_reached = 0
    should_items_total = 0
    should_items_reached = 0
    rows_passed = 0

    for row in rows:
        events: list[Event] = []
        pipeline.run(AskRequest(query=row["question"]), events.append)

        route_ev = next((e for e in events if isinstance(e, RouteEvent)), None)
        sources_ev = next((e for e in events if isinstance(e, SourcesEvent)), None)

        route_status = route_ev.status if route_ev is not None else "NO_ROUTE_EVENT"
        bound_coords = list(route_ev.coordinates) if route_ev is not None else []
        windows_sent = [w.coordinate for w in sources_ev.windows] if sources_ev is not None else []

        row_instruments = [
            inst
            for inst in (row.get("instrument"), *(row.get("other_instruments") or []))
            if isinstance(inst, str) and inst.strip()
        ]
        bound_yes = bool(row_instruments) and any(
            is_descendant_or_equal(b, inst) for b in bound_coords for inst in row_instruments
        )
        refused_or_clarified = any(
            isinstance(e, (RefusalEvent, ClarifyEvent)) for e in events
        ) or route_status not in ("ROUTE_BOUNDED", "NO_ROUTE_EVENT")

        must_reach: list[str] = row.get("must_reach", [])
        should_reach: list[str] = row.get("should_reach", [])

        missing_must = [
            m for m in must_reach if not any(is_descendant_or_equal(w, m) for w in windows_sent)
        ]
        should_hits = [
            s for s in should_reach if any(is_descendant_or_equal(w, s) for w in windows_sent)
        ]
        should_missing = [s for s in should_reach if s not in should_hits]

        row_passed = (len(missing_must) == 0) if must_reach else (bound_yes or refused_or_clarified)
        if row_passed:
            rows_passed += 1

        must_items_total += len(must_reach)
        must_items_reached += len(must_reach) - len(missing_must)
        should_items_total += len(should_reach)
        should_items_reached += len(should_hits)

        per_row.append(
            {
                "id": row["id"],
                "question": row["question"],
                "route_status": route_status,
                "bound": "yes" if bound_yes else "no",
                "bound_coordinates": bound_coords,
                "windows_sent": windows_sent,
                "passed": row_passed,
                "missing_must_reach": missing_must,
                "should_reach_hits": should_hits,
                "should_reach_missing": should_missing,
                "note": row.get("note", ""),
            }
        )

    must_recall = round(must_items_reached / must_items_total, 3) if must_items_total > 0 else 1.0
    should_recall = (
        round(should_items_reached / should_items_total, 3) if should_items_total > 0 else 1.0
    )

    totals = {
        "rows_total": len(rows),
        "rows_passed": rows_passed,
        "rows_failed": len(rows) - rows_passed,
        "must_reach_items_total": must_items_total,
        "must_reach_items_reached": must_items_reached,
        "must_reach_recall": must_recall,
        "should_reach_items_total": should_items_total,
        "should_reach_items_reached": should_items_reached,
        "should_reach_recall": should_recall,
    }

    return {
        "totals": totals,
        "rows": per_row,
    }


def _extract_sentences_and_citations(
    events: Sequence[Any],
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Extract (sentence_text, citation_coords) pairs and all citation coords."""
    sentences_out: list[tuple[str, list[str]]] = []
    all_citations: list[str] = []

    for ev in events:
        if isinstance(ev, SentenceEvent):
            s_text = ev.sentence.text
            coords = [c.coordinate for c in ev.citations]
            sentences_out.append((s_text, coords))
            all_citations.extend(coords)
        elif isinstance(ev, ExtractiveEvent):
            all_citations.extend(w.coordinate for w in ev.windows)
        elif isinstance(ev, dict):
            ev_type = ev.get("type")
            if ev_type == "sentence":
                s_obj = ev.get("sentence", {})
                s_text = str(s_obj.get("text", ""))
                cits = ev.get("citations", [])
                coords = [
                    str(c["coordinate"]) for c in cits if isinstance(c, dict) and "coordinate" in c
                ]
                sentences_out.append((s_text, coords))
                all_citations.extend(coords)
            elif ev_type == "extractive":
                windows = ev.get("windows") or []
                all_citations.extend(
                    str(w["coordinate"])
                    for w in windows
                    if isinstance(w, dict) and "coordinate" in w
                )

    return sentences_out, all_citations


def _check_expect_abstention(
    row: Mapping[str, Any],
    sentences: Sequence[tuple[str, list[str]]],
    refusal_stripped: str,
    abstention_count: int,
    failure_reasons: list[str],
) -> None:
    if abstention_count < 1:
        failure_reasons.append("expected abstention sentence, none found")
    row_instruments = [
        inst
        for inst in (row.get("instrument"), *(row.get("other_instruments") or []))
        if isinstance(inst, str) and inst.strip()
    ]
    bad_cits = [
        c
        for stext, cits in sentences
        if stext.strip() != refusal_stripped
        for c in cits
        if any(is_descendant_or_equal(c, inst) for inst in row_instruments)
    ]
    if bad_cits:
        failure_reasons.append(f"non-abstention sentence cites row instrument: {bad_cits}")


def _check_required_any_phrases(
    req_any: Sequence[str],
    sentence_texts: Sequence[str],
    refusal_stripped: str,
    failure_reasons: list[str],
) -> None:
    if not req_any:
        return
    supported_text = " ".join(
        stext for stext in sentence_texts if stext.strip() != refusal_stripped
    ).lower()
    if not any(phrase.lower() in supported_text for phrase in req_any):
        failure_reasons.append(f"missing required phrase (any of {list(req_any)})")


def evaluate_answer(  # noqa: PLR0912
    row: dict[str, Any],
    events: Sequence[Any],
    policy_refusal_text: str | None = None,
) -> dict[str, Any]:
    """Check recorded answer against rubric in row['answer_checks']."""
    checks = row.get("answer_checks")
    if not checks:
        return {
            "id": row.get("id", ""),
            "passed": True,
            "failure_reasons": [],
            "note": "no answer_checks",
        }

    if policy_refusal_text is None:
        policy_refusal_text = build_policy(JUDGE_PRESET_NAME).refusal_text

    failure_reasons: list[str] = []
    sentences, all_citations = _extract_sentences_and_citations(events)
    sentence_texts = [s[0] for s in sentences]

    # 1. required_citations
    req_cits: list[str] = checks.get("required_citations", [])
    missing_req = [
        rc for rc in req_cits if not any(is_descendant_or_equal(ac, rc) for ac in all_citations)
    ]
    if missing_req:
        failure_reasons.append(f"missing required citations: {missing_req}")

    # 2. credit_citations (reported, not scored)
    credit_cits: list[str] = checks.get("credit_citations", [])
    credit_hits = [
        cc for cc in credit_cits if any(is_descendant_or_equal(ac, cc) for ac in all_citations)
    ]

    # 3. forbidden_phrases (row phrases add to defaults)
    row_forbidden = checks.get("forbidden_phrases") or []
    forbidden = list(dict.fromkeys((*DEFAULT_FORBIDDEN_PHRASES, *row_forbidden)))
    found_forbidden: list[str] = []
    for stext in sentence_texts:
        for phrase in forbidden:
            if phrase.lower() in stext.lower() and phrase not in found_forbidden:
                found_forbidden.append(phrase)
    if found_forbidden:
        failure_reasons.append(f"forbidden phrases found: {found_forbidden}")

    # 4. max_abstentions
    max_abstentions: int = checks.get("max_abstentions", 1)
    refusal_stripped = policy_refusal_text.strip()
    abstention_count = sum(1 for stext in sentence_texts if stext.strip() == refusal_stripped)
    if abstention_count > max_abstentions:
        failure_reasons.append(f"too many abstentions: {abstention_count} (max {max_abstentions})")

    # 5. no_citations_on_abstention
    no_cits_abstention: bool = checks.get("no_citations_on_abstention", True)
    if no_cits_abstention:
        for stext, cits in sentences:
            if stext.strip() == refusal_stripped and cits:
                failure_reasons.append(f"abstention sentence has citations: {cits}")

    # 6. no_duplicate_sentences
    no_dup: bool = checks.get("no_duplicate_sentences", True)
    duplicate_sentences: list[str] = []
    if no_dup:
        seen: set[str] = set()
        for stext in sentence_texts:
            norm = stext.strip()
            if norm in seen and norm not in duplicate_sentences:
                duplicate_sentences.append(norm)
            seen.add(norm)
        if duplicate_sentences:
            failure_reasons.append(f"duplicate sentences found: {duplicate_sentences}")

    # 7. expect_abstention
    if checks.get("expect_abstention", False):
        _check_expect_abstention(
            row, sentences, refusal_stripped, abstention_count, failure_reasons
        )

    # 8. required_any_phrases (at least one phrase must appear in non-abstention sentences)
    _check_required_any_phrases(
        checks.get("required_any_phrases") or [],
        sentence_texts,
        refusal_stripped,
        failure_reasons,
    )

    passed = len(failure_reasons) == 0
    return {
        "id": row.get("id", ""),
        "passed": passed,
        "failure_reasons": failure_reasons,
        "missing_required": missing_req,
        "credit_hits": credit_hits,
        "abstention_count": abstention_count,
        "duplicate_sentences": duplicate_sentences,
    }


def run_rubric(
    rows: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    policy_refusal_text: str | None = None,
    *,
    row_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Score answer recordings against rows with answer_checks."""
    wanted = set(row_ids) if row_ids is not None else None
    rows_by_id = {r["id"]: r for r in rows}
    results: list[dict[str, Any]] = []
    passed_count = 0
    failed_count = 0

    for ans in answers:
        rid = ans.get("id")
        if wanted is not None and rid not in wanted:
            continue
        row = rows_by_id.get(rid)
        if row is None or not row.get("answer_checks"):
            continue
        events = ans.get("events", [])
        eval_res = evaluate_answer(row, events, policy_refusal_text=policy_refusal_text)
        results.append(eval_res)
        if eval_res["passed"]:
            passed_count += 1
        else:
            failed_count += 1

    return {
        "total_evaluated": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "results": results,
    }


def parse_row_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


_parse_row_ids = parse_row_ids


def _local_runtime_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    env = {k: v for k, v in source.items() if k != "DATABASE_URL"}
    if (
        "DAILY_ASK_CAP" not in env
        and len(env.get("CAP_SALT", "").strip()) < chapterverse.app.MIN_CAP_SALT_LEN
    ):
        env["DAILY_ASK_CAP"] = "0"
    return env


def _available_providers(rotation: Any, purpose: Literal["generate", "judge"]) -> list[str]:
    if rotation is None:
        return []
    if isinstance(rotation, ProviderRotation):
        return list(rotation.available(purpose))
    try:
        return [str(name) for name in rotation.available(purpose)]
    except TypeError:
        return [str(name) for name in rotation.available()]


def extract_provider_calls(events: Sequence[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for ev in events:
        if isinstance(ev, ReceiptEvent):
            calls.extend(
                {
                    "provider": c.provider,
                    "purpose": c.purpose,
                    "latency_ms": round(c.latency_ms, 1),
                }
                for c in ev.fact.calls
            )
        elif isinstance(ev, dict) and ev.get("type") == "receipt":
            fact = ev.get("fact")
            if isinstance(fact, dict):
                calls.extend(
                    {
                        "provider": str(c.get("provider", "")),
                        "purpose": str(c.get("purpose", "")),
                        "latency_ms": round(float(c.get("latency_ms", 0.0)), 1),
                    }
                    for c in fact.get("calls", [])
                    if isinstance(c, dict)
                )
    return calls


_extract_provider_calls = extract_provider_calls


def _build_record_line(
    row_id: str,
    events: Sequence[Any],
    elapsed_ms: float,
) -> dict[str, Any]:
    json_events = [to_jsonable(e) for e in events]
    record: dict[str, Any] = {
        "id": row_id,
        "events": json_events,
        "elapsed_ms": elapsed_ms,
    }
    provider_calls = _extract_provider_calls(events)
    if provider_calls:
        record["provider_calls"] = provider_calls
    return record


def _record_local(
    target_rows: Sequence[dict[str, Any]],
    out_path: Path,
    *,
    pace: float,
    pipeline: Any,
    environ: Mapping[str, str] | None = None,
) -> None:
    runtime = None
    if pipeline is None:
        runtime = chapterverse.app.build_runtime(_local_runtime_env(environ))
        active_pipeline: Any = runtime.pipeline
    else:
        active_pipeline = pipeline

    try:
        bound_ranker = getattr(active_pipeline, "bound_ranker", None)
        wait_fn = getattr(bound_ranker, "wait", None)
        if callable(wait_fn):
            wait_fn(timeout=15.0)
        rotation = getattr(active_pipeline, "rotation", None)
        gen_providers = _available_providers(rotation, "generate")
        judge_providers = _available_providers(rotation, "judge")
        print(
            f"Available providers: generate={', '.join(gen_providers) or 'none'}; "
            f"judge={', '.join(judge_providers) or 'none'}"
        )

        with out_path.open("w", encoding="utf-8") as fh:
            for idx, r in enumerate(target_rows):
                if idx > 0 and pace > 0:
                    time.sleep(pace)
                events: list[Event] = []
                t0 = time.perf_counter()
                active_pipeline.run(
                    AskRequest(query=r["question"], client_key=None, net_class="other"),
                    events.append,
                )
                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                record = _build_record_line(r["id"], events, elapsed_ms)
                fh.write(json.dumps(record) + "\n")
    finally:
        if runtime is not None and callable(getattr(runtime, "shutdown", None)):
            runtime.shutdown()


def _record_live(
    target_rows: Sequence[dict[str, Any]],
    out_path: Path,
    *,
    base_url: str,
    pace: float,
    transport: httpx.BaseTransport | None,
) -> None:
    headers = load_access_headers()
    url = f"{base_url.rstrip('/')}/api/ask"
    req_headers = {"Content-Type": "application/json", **headers}

    with (
        httpx.Client(timeout=60.0, transport=transport) as client,
        out_path.open("w", encoding="utf-8") as fh,
    ):
        for idx, r in enumerate(target_rows):
            if idx > 0 and pace > 0:
                time.sleep(pace)
            t0 = time.perf_counter()
            with client.stream(
                "POST", url, json={"query": r["question"]}, headers=req_headers
            ) as resp:
                if resp.status_code != HTTP_OK:
                    err_text = resp.read().decode("utf-8", "replace")
                    events: list[Event] = [
                        ErrorEvent(code=f"http_{resp.status_code}", message=err_text)
                    ]
                else:
                    events = parse_sse_to_events(resp.iter_lines())
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            record = _build_record_line(r["id"], events, elapsed_ms)
            fh.write(json.dumps(record) + "\n")


def run_recorder(
    rows: list[dict[str, Any]],
    out_path: Path,
    *,
    target: str = "local",
    base_url: str = "https://cv-origin.memonsystems.com",
    pace: float = 3.0,
    all_rows: bool = False,
    row_ids: Sequence[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    pipeline: Any = None,
    router_repo: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Record engine answers for rows with answer_checks (or selected/all) to out_path."""
    del router_repo
    if row_ids is not None:
        wanted = set(row_ids)
        target_rows = [r for r in rows if r.get("id") in wanted]
    else:
        target_rows = [r for r in rows if all_rows or r.get("answer_checks")]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if target == "local":
        _record_local(target_rows, out_path, pace=pace, pipeline=pipeline, environ=environ)
    elif target == "live":
        _record_live(
            target_rows,
            out_path,
            base_url=base_url,
            pace=pace,
            transport=transport,
        )

    return 0


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
    parser.add_argument("--router-repo", type=Path, default=ROUTER_REPO)
    parser.add_argument("--dense-dir", type=Path, default=None)
    parser.add_argument("--models-dir", type=Path, default=Path("artefacts/models"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--answers", type=Path, default=None, help="JSONL answers file for rubric")
    parser.add_argument("--record", action="store_true", help="run recorder to capture answers")
    parser.add_argument("--target", choices=("local", "live"), default="local")
    parser.add_argument("--base-url", default="https://cv-origin.memonsystems.com")
    parser.add_argument("--pace", type=float, default=3.0)
    parser.add_argument("--all", action="store_true", help="record all rows instead of rubric rows")
    parser.add_argument(
        "--rows",
        default=None,
        help="comma-separated row ids to select (e.g. L01,L07)",
    )
    return parser.parse_args(argv)


def default_out_dir() -> Path:
    scratch = os.environ.get(
        "CV_SCRATCH",
        "/private/tmp/claude-501/-Users-abdiel-Code-chapter-and-verse/5a8af36a-333e-4abd-aae1-107eda07f631/scratchpad",
    )
    return Path(scratch) / "s4-legal-battery" / f"run-{int(time.time())}"


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0912, PLR0915
    args = _parse_args(argv)
    selected_rows = _parse_row_ids(args.rows)

    out: Path = args.out.resolve() if args.out is not None else default_out_dir()
    out.mkdir(parents=True, exist_ok=True)

    battery_path = args.battery.resolve()
    seal_path = args.seal.resolve()

    # 1. Answer Rubric
    if args.answers is not None and not args.record:
        answers_file = args.answers.resolve()
        if not answers_file.is_file():
            print(f"REFUSED: answers file {answers_file} not found")
            return 1
        rows = load_battery(battery_path)
        answers: list[dict[str, Any]] = []
        with answers_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line_str = line.strip()
                if line_str:
                    answers.append(json.loads(line_str))
        rubric_res = run_rubric(rows, answers, row_ids=selected_rows)
        (out / "answers-rubric.json").write_text(
            json.dumps(rubric_res, indent=2) + "\n", encoding="utf-8"
        )
        for r in rubric_res["results"]:
            tag = "PASS" if r["passed"] else "FAIL"
            print(f"[{tag}] {r['id']}")
            for reason in r.get("failure_reasons", []):
                print(f"    FAILURE: {reason}")
        print(f"\nTotal evaluated: {rubric_res['total_evaluated']}")
        print(f"Passed: {rubric_res['passed']}")
        print(f"Failed: {rubric_res['failed']}")
        return 0 if rubric_res["failed"] == 0 else 1

    # 2. Recorder
    if args.record:
        record_out = args.answers.resolve() if args.answers is not None else (out / "answers.jsonl")
        rows = load_battery(battery_path)
        run_recorder(
            rows,
            record_out,
            target=args.target,
            base_url=args.base_url,
            pace=args.pace,
            all_rows=args.all,
            row_ids=selected_rows,
            router_repo=args.router_repo.resolve(),
        )
        print(f"Recorded answers to {record_out}")
        return 0

    # 3. Retrieval Gate
    if args.split == "test":
        if not args.sealed_test:
            print("REFUSED: --split test requires --sealed-test")
            return 1
        ok, note = verify_seal(battery_path, seal_path)
        if not ok:
            print(f"REFUSED: {note}")
            return 1
        print(f"seal OK: {note}")

    pipeline = _build_pipeline(
        args.router_repo.resolve(),
        dense_dir=args.dense_dir.resolve() if args.dense_dir else None,
        models_dir=args.models_dir.resolve(),
    )

    rows = load_battery(battery_path, split=args.split)
    if selected_rows is not None:
        wanted = set(selected_rows)
        rows = [r for r in rows if r.get("id") in wanted]
    report = run_retrieval_gate(rows, pipeline)

    # Console output
    for r in report["rows"]:
        tag = "PASS" if r["passed"] else "FAIL"
        print(
            f"[{tag}] {r['id']}: status={r['route_status']}; bound={r['bound']} "
            f"{r['bound_coordinates']}; windows={len(r['windows_sent'])}; "
            f"missing={r['missing_must_reach']}; should_hits={r['should_reach_hits']}"
        )

    totals = report["totals"]
    print("\nTotals:")
    print(f"Rows passed: {totals['rows_passed']}/{totals['rows_total']}")
    m_rec = totals["must_reach_recall"]
    m_reach = totals["must_reach_items_reached"]
    m_tot = totals["must_reach_items_total"]
    print(f"Must reach recall: {m_rec} ({m_reach}/{m_tot})")
    s_rec = totals["should_reach_recall"]
    s_reach = totals["should_reach_items_reached"]
    s_tot = totals["should_reach_items_total"]
    print(f"Should reach recall: {s_rec} ({s_reach}/{s_tot})")

    # Write legal-gate.json
    gate_json = {
        "split": args.split,
        "totals": totals,
        "rows": report["rows"],
    }
    (out / "legal-gate.json").write_text(json.dumps(gate_json, indent=2) + "\n", encoding="utf-8")

    # Write legal-gate.md (cold, plain, short)
    md_lines = [
        f"# Legal Gate ({args.split})",
        "",
        f"Split: {args.split}",
        f"Rows: {totals['rows_total']}",
        f"Rows passed: {totals['rows_passed']}",
        f"Rows failed: {totals['rows_failed']}",
        f"Must reach recall: {m_rec} ({m_reach}/{m_tot})",
        f"Should reach recall: {s_rec} ({s_reach}/{s_tot})",
        "",
        "## Failing rows",
        "",
    ]
    failing = [r for r in report["rows"] if not r["passed"]]
    if failing:
        for f_row in failing:
            if f_row["missing_must_reach"]:
                missing_str = ", ".join(f_row["missing_must_reach"])
                md_lines.append(f"- {f_row['id']}: missing {missing_str}")
            else:
                md_lines.append(
                    f"- {f_row['id']}: bound: {f_row['bound']}, status: {f_row['route_status']}"
                )
    else:
        md_lines.append("None")

    empty_must = [
        r
        for r in report["rows"]
        if not next((x.get("must_reach") for x in rows if x["id"] == r["id"]), None)
    ]
    if empty_must:
        md_lines.extend(["", "## Empty must_reach rows", ""])
        md_lines.extend(
            f"- {e_row['id']}: bound: {e_row['bound']}, status: {e_row['route_status']}, "
            f"passed: {'yes' if e_row['passed'] else 'no'}"
            for e_row in empty_must
        )
    md_lines.append("")

    (out / "legal-gate.md").write_text("\n".join(md_lines), encoding="utf-8")

    return 0 if totals["rows_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
