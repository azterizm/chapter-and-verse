"""Slice 1 latency and boot gate runner (R1, R3).

Measures boot time, memory usage (RSS), and per-question latency/quality metrics
over the 20 hand-written questions in batteries/slice1_questions.jsonl.

Usage:
    uv run python -m eval.latency [--port 8765] [--scratch PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any, Final

import httpx

from chapterverse.contracts import AnswerFactObject, validate_order

__all__ = [
    "DEFAULT_SCRATCH",
    "BootMetrics",
    "GateSummary",
    "LatencyMetrics",
    "QuestionResult",
    "get_rss_mb",
    "main",
    "percentile",
    "run_gate",
]

DEFAULT_SCRATCH: Final = Path(os.environ.get("CV_SCRATCH", "scratch"))
HTTP_OK: Final = 200
HTTP_PAYLOAD_TOO_LARGE: Final = 413
MAX_GROQ_REQUESTS: Final = 150
MAX_CLOUDFLARE_REQUESTS: Final = 40
MIN_GROQ_REMAINING: Final = 300
PS_COLUMNS: Final = 3


@dataclass(frozen=True, slots=True, kw_only=True)
class BootMetrics:
    boot_seconds: float
    rss_start_mb: float
    rss_end_mb: float
    hardware_label: str = "M4 Mac, not the A1"


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionResult:
    id: str
    query: str
    expected_kind: str
    notes: str
    status_code: int
    event_types: list[str]
    valid_order: bool
    time_to_first_source_ms: float | None
    time_to_first_sentence_ms: float | None
    total_time_ms: float
    sentences_emitted: int
    sentences_refused: int
    rollbacks: int
    written_citation_failures: int
    receipt_valid: bool
    answer_id: str | None
    calls_by_provider: dict[str, int]
    sources: list[dict[str, Any]]
    sentences: list[dict[str, Any]]
    transcript_path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LatencyMetrics:
    first_source_p50_ms: float
    first_source_p95_ms: float
    first_sentence_p50_ms: float
    first_sentence_p95_ms: float
    total_time_p50_ms: float
    total_time_p95_ms: float


@dataclass(frozen=True, slots=True, kw_only=True)
class GateSummary:
    boot: BootMetrics
    latency: LatencyMetrics
    questions_total: int
    questions_run: int
    passed_order_check: bool
    passed_receipt_check: bool
    total_groq_calls: int
    total_cloudflare_calls: int
    early_stopped: bool
    stop_reason: str | None
    results: list[QuestionResult]


def get_rss_mb(pid: int) -> float:
    """Measure total RSS in MB for process and any children on macOS."""
    try:
        out = subprocess.check_output(["/bin/ps", "-o", "pid=,ppid=,rss=", "-ax"], text=True)
        pids = {pid}
        lines = [line.strip().split() for line in out.strip().splitlines() if line.strip()]
        added = True
        while added:
            added = False
            for parts in lines:
                if len(parts) >= PS_COLUMNS:
                    p, pp = int(parts[0]), int(parts[1])
                    if pp in pids and p not in pids:
                        pids.add(p)
                        added = True
        total_kb = 0
        for parts in lines:
            if len(parts) >= PS_COLUMNS:
                p, _, rss = int(parts[0]), int(parts[1]), int(parts[2])
                if p in pids:
                    total_kb += rss
        return total_kb / 1024.0
    except (subprocess.SubprocessError, ValueError, OSError):
        try:
            single = subprocess.check_output(  # noqa: S603
                ["/bin/ps", "-o", "rss=", "-p", str(pid)], text=True
            )
            return int(single.strip()) / 1024.0
        except (subprocess.SubprocessError, ValueError, OSError):
            return 0.0


def percentile(values: Sequence[float], p: float) -> float:
    """Compute the p-th percentile (0 <= p <= 100) using linear interpolation."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    d0 = s[int(f)] * (c - k)
    d1 = s[int(c)] * (k - f)
    return d0 + d1


def verify_receipt_sha256(raw_line: str, fact_obj: dict[str, Any], answer_id: str) -> bool:
    """Verify that fact sha256 matches answer_id."""
    fact_prefix = ',"fact":'
    receipt_suffix = ',"type":"receipt"}'
    if raw_line.startswith('{"answer_id":') and raw_line.endswith(receipt_suffix):
        at = raw_line.find(fact_prefix)
        if at != -1:
            fact_str = raw_line[at + len(fact_prefix) : -len(receipt_suffix)]
            if hashlib.sha256(fact_str.encode("utf-8")).hexdigest() == answer_id:
                return True
    try:
        afo = AnswerFactObject.from_dict(fact_obj)
    except (ValueError, TypeError, KeyError):
        return False
    else:
        return afo.answer_id == answer_id


class ServerRunner:
    """Manages the uvicorn subprocess lifecycle and log streaming."""

    def __init__(self, port: int, log_path: Path) -> None:
        self.port = port
        self.log_path = log_path
        self.proc: subprocess.Popen[str] | None = None
        self._log_file: IO[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self.min_groq_remaining_seen: int | None = None

    def start(self) -> float:
        t0 = time.perf_counter()
        env = os.environ.copy()
        env["LEGAL_DATA_ROOT"] = str(Path.home() / "Code" / "legal-rag-router" / "data")
        env["STORE_DIR"] = "artefacts/store"
        env["GEN_PROVIDERS"] = "groq,cloudflare"
        env["JUDGE_PROVIDERS"] = "groq,cloudflare"

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_path.open("w", encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "chapterverse.app:app",
            "--port",
            str(self.port),
            "--host",
            "127.0.0.1",
        ]
        self.proc = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def drain_output() -> None:
            if self.proc is None or self.proc.stdout is None:
                return
            for line in self.proc.stdout:
                if self._log_file:
                    self._log_file.write(line)
                    self._log_file.flush()
                if "remaining_requests" in line or "x-ratelimit-remaining-requests" in line:
                    for part in line.split():
                        if part.isdigit():
                            val = int(part)
                            if (
                                self.min_groq_remaining_seen is None
                                or val < self.min_groq_remaining_seen
                            ):
                                self.min_groq_remaining_seen = val

        self._reader_thread = threading.Thread(target=drain_output, daemon=True)
        self._reader_thread.start()

        health_url = f"http://127.0.0.1:{self.port}/api/health"
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                res = httpx.get(health_url, timeout=1.0)
                if res.status_code == HTTP_OK:
                    return time.perf_counter() - t0
            except (httpx.HTTPError, OSError):
                pass
            time.sleep(0.05)
        raise RuntimeError("Server failed to respond to /api/health within 30s")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._log_file:
            self._log_file.close()


def _process_sse_stream(  # noqa: PLR0912, PLR0915 - parses SSE events and calculates metrics
    lines: Sequence[str],
    t_req_start: float,
) -> tuple[
    list[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    Counter[str],
    int,
    int,
    int,
    int,
    bool,
    str | None,
    float | None,
    float | None,
    float,
]:
    """Parse SSE lines into events and calculate question metrics."""
    event_types: list[str] = []
    sources: list[dict[str, Any]] = []
    sentences: list[dict[str, Any]] = []
    calls: Counter[str] = Counter()
    rollbacks = 0
    written_fails = 0
    emitted = 0
    refused = 0
    receipt_valid = True
    answer_id: str | None = None
    first_source_ms: float | None = None
    first_sentence_ms: float | None = None
    total_time_ms = 0.0

    current_type: str | None = None
    current_data: list[str] = []

    for line in lines:
        stripped = line.rstrip("\r\n")
        if not stripped:
            if current_type is not None and current_data:
                raw = "\n".join(current_data)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}
                event_types.append(current_type)
                now_ms = (time.perf_counter() - t_req_start) * 1000.0

                if current_type == "sources":
                    if first_source_ms is None:
                        first_source_ms = now_ms
                    sources.extend(payload.get("windows", []))
                elif current_type == "sentence":
                    if first_sentence_ms is None:
                        first_sentence_ms = now_ms
                    s_info = payload.get("sentence", {})
                    sentences.append(payload)
                    verdict = s_info.get("verdict")
                    if verdict == "supported":
                        emitted += 1
                    elif verdict == "refused":
                        refused += 1
                    rollbacks += int(s_info.get("rollbacks", 0))
                    for r in s_info.get("reasons", []):
                        if r == "MODEL_CITATION" or "CITATION" in r:
                            written_fails += 1
                elif current_type == "receipt":
                    aid = payload.get("answer_id")
                    answer_id = aid
                    fact = payload.get("fact", {})
                    if not verify_receipt_sha256(raw, fact, aid):
                        receipt_valid = False
                    for c in fact.get("calls", []):
                        p = c.get("provider", "unknown")
                        calls[p] += 1
                elif current_type == "done":
                    timings = payload.get("timings", {})
                    if timings.get("first_source_ms") is not None:
                        first_source_ms = float(timings["first_source_ms"])
                    if timings.get("first_sentence_ms") is not None:
                        first_sentence_ms = float(timings["first_sentence_ms"])
                    if timings.get("total_ms") is not None:
                        total_time_ms = float(timings["total_ms"])

            current_type = None
            current_data = []
            continue
        if stripped.startswith("event: "):
            current_type = stripped[len("event: ") :].strip()
        elif stripped.startswith("data: "):
            current_data.append(stripped[len("data: ") :])

    if total_time_ms == 0.0:
        total_time_ms = (time.perf_counter() - t_req_start) * 1000.0

    return (
        event_types,
        sources,
        sentences,
        calls,
        rollbacks,
        written_fails,
        emitted,
        refused,
        receipt_valid,
        answer_id,
        first_source_ms,
        first_sentence_ms,
        total_time_ms,
    )


def _run_single_question(
    row: dict[str, Any],
    port: int,
    gates_dir: Path,
) -> QuestionResult:
    qid = row["id"]
    query = row["query"]
    t_req_start = time.perf_counter()
    transcript_path = gates_dir / f"{qid}.transcript.txt"
    url = f"http://127.0.0.1:{port}/api/ask"

    with transcript_path.open("w", encoding="utf-8") as transcript_out:
        try:
            with (
                httpx.Client(timeout=120.0) as client,
                client.stream("POST", url, json={"query": query}) as response,
            ):
                status_code = response.status_code
                if status_code != HTTP_OK:
                    body = response.read().decode("utf-8", "replace")
                    transcript_out.write(f"HTTP {status_code}\n{body}\n")
                    elapsed = (time.perf_counter() - t_req_start) * 1000.0
                    return QuestionResult(
                        id=qid,
                        query=query,
                        expected_kind=row.get("expected_kind", ""),
                        notes=row.get("notes", ""),
                        status_code=status_code,
                        event_types=[f"http_error:{status_code}"],
                        valid_order=True,
                        time_to_first_source_ms=None,
                        time_to_first_sentence_ms=None,
                        total_time_ms=elapsed,
                        sentences_emitted=0,
                        sentences_refused=0,
                        rollbacks=0,
                        written_citation_failures=0,
                        receipt_valid=True,
                        answer_id=None,
                        calls_by_provider={},
                        sources=[],
                        sentences=[],
                        transcript_path=str(transcript_path),
                    )

                raw_lines: list[str] = []
                for line in response.iter_lines():
                    transcript_out.write(line + "\n")
                    raw_lines.append(line)

        except (httpx.HTTPError, OSError) as exc:
            transcript_out.write(f"CLIENT_ERROR: {exc}\n")
            elapsed = (time.perf_counter() - t_req_start) * 1000.0
            return QuestionResult(
                id=qid,
                query=query,
                expected_kind=row.get("expected_kind", ""),
                notes=row.get("notes", ""),
                status_code=0,
                event_types=["client_error"],
                valid_order=False,
                time_to_first_source_ms=None,
                time_to_first_sentence_ms=None,
                total_time_ms=elapsed,
                sentences_emitted=0,
                sentences_refused=0,
                rollbacks=0,
                written_citation_failures=0,
                receipt_valid=False,
                answer_id=None,
                calls_by_provider={},
                sources=[],
                sentences=[],
                transcript_path=str(transcript_path),
            )

    (
        event_types,
        sources,
        sentences,
        calls,
        rollbacks,
        written_fails,
        emitted,
        refused,
        receipt_valid,
        answer_id,
        first_src_ms,
        first_sent_ms,
        total_time_ms,
    ) = _process_sse_stream(raw_lines, t_req_start)

    valid_order = validate_order(event_types)

    return QuestionResult(
        id=qid,
        query=query,
        expected_kind=row.get("expected_kind", ""),
        notes=row.get("notes", ""),
        status_code=HTTP_OK,
        event_types=event_types,
        valid_order=valid_order,
        time_to_first_source_ms=first_src_ms,
        time_to_first_sentence_ms=first_sent_ms,
        total_time_ms=total_time_ms,
        sentences_emitted=emitted,
        sentences_refused=refused,
        rollbacks=rollbacks,
        written_citation_failures=written_fails,
        receipt_valid=receipt_valid,
        answer_id=answer_id,
        calls_by_provider=dict(calls),
        sources=sources,
        sentences=sentences,
        transcript_path=str(transcript_path),
    )


def run_gate(  # noqa: PLR0915 - linear orchestration
    *,
    port: int = 8765,
    questions_file: Path,
    scratch_dir: Path,
    delay_s: float = 10.0,
) -> GateSummary:
    gates_dir = scratch_dir / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)
    server_log = gates_dir / "uvicorn.log"

    server = ServerRunner(port=port, log_path=server_log)
    print(f"Starting server on port {port}...")
    boot_seconds = server.start()
    if server.proc is None:
        raise RuntimeError("Server process failed to start")
    rss_start_mb = get_rss_mb(server.proc.pid)
    print(
        f"Server healthy in {boot_seconds:.3f} s. "
        f"Initial RSS: {rss_start_mb:.1f} MB (M4 Mac, not the A1)"
    )

    with questions_file.open(encoding="utf-8") as f:
        questions = [json.loads(line) for line in f if line.strip()]

    results: list[QuestionResult] = []
    groq_total = 0
    cf_total = 0
    early_stopped = False
    stop_reason: str | None = None

    first_source_times: list[float] = []
    first_sentence_times: list[float] = []
    total_times: list[float] = []

    try:
        for idx, row in enumerate(questions):
            qid = row["id"]
            print(f"[{idx + 1}/{len(questions)}] Running {qid}...")
            q_res = _run_single_question(row, port, gates_dir)
            results.append(q_res)

            q_json_path = gates_dir / f"{qid}.json"
            with q_json_path.open("w", encoding="utf-8") as qf:
                qf.write(json.dumps(asdict(q_res), indent=2, ensure_ascii=False))

            groq_calls = q_res.calls_by_provider.get("groq", 0)
            cf_calls = q_res.calls_by_provider.get("cloudflare", 0)
            groq_total += groq_calls
            cf_total += cf_calls

            if q_res.time_to_first_source_ms is not None:
                first_source_times.append(q_res.time_to_first_source_ms)
            if q_res.time_to_first_sentence_ms is not None:
                first_sentence_times.append(q_res.time_to_first_sentence_ms)
            total_times.append(q_res.total_time_ms)

            print(
                f"  -> total: {q_res.total_time_ms:.1f}ms, emitted: {q_res.sentences_emitted}, "
                f"refused: {q_res.sentences_refused}, groq: {groq_calls}, cf: {cf_calls}"
            )

            if groq_total >= MAX_GROQ_REQUESTS:
                early_stopped = True
                stop_reason = f"Groq request cap reached ({groq_total} >= {MAX_GROQ_REQUESTS})"
                print(f"STOP EARLY: {stop_reason}")
                break
            if cf_total >= MAX_CLOUDFLARE_REQUESTS:
                early_stopped = True
                stop_reason = (
                    f"Cloudflare request cap reached ({cf_total} >= {MAX_CLOUDFLARE_REQUESTS})"
                )
                print(f"STOP EARLY: {stop_reason}")
                break
            if (
                server.min_groq_remaining_seen is not None
                and server.min_groq_remaining_seen < MIN_GROQ_REMAINING
            ):
                early_stopped = True
                stop_reason = (
                    f"Groq remaining requests < {MIN_GROQ_REMAINING} "
                    f"({server.min_groq_remaining_seen})"
                )
                print(f"STOP EARLY: {stop_reason}")
                break

            if idx < len(questions) - 1:
                time.sleep(delay_s)

    finally:
        rss_end_mb = get_rss_mb(server.proc.pid)
        print("Stopping server...")
        server.stop()
        print(f"Server stopped. Final RSS: {rss_end_mb:.1f} MB (M4 Mac, not the A1)")

    boot_metrics = BootMetrics(
        boot_seconds=boot_seconds,
        rss_start_mb=rss_start_mb,
        rss_end_mb=rss_end_mb,
    )

    latency_metrics = LatencyMetrics(
        first_source_p50_ms=percentile(first_source_times, 50.0),
        first_source_p95_ms=percentile(first_source_times, 95.0),
        first_sentence_p50_ms=percentile(first_sentence_times, 50.0),
        first_sentence_p95_ms=percentile(first_sentence_times, 95.0),
        total_time_p50_ms=percentile(total_times, 50.0),
        total_time_p95_ms=percentile(total_times, 95.0),
    )

    summary = GateSummary(
        boot=boot_metrics,
        latency=latency_metrics,
        questions_total=len(questions),
        questions_run=len(results),
        passed_order_check=all(r.valid_order for r in results),
        passed_receipt_check=all(r.receipt_valid for r in results),
        total_groq_calls=groq_total,
        total_cloudflare_calls=cf_total,
        early_stopped=early_stopped,
        stop_reason=stop_reason,
        results=results,
    )

    summary_path = gates_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as sf:
        sf.write(json.dumps(asdict(summary), indent=2, ensure_ascii=False))

    print("\n--- Gate Run Summary ---")
    print(f"Questions run: {len(results)}/{len(questions)}")
    print(f"Order valid on all: {summary.passed_order_check}")
    print(f"Receipt sha256 valid on all: {summary.passed_receipt_check}")
    print(
        f"Groq calls: {groq_total}/{MAX_GROQ_REQUESTS}, "
        f"Cloudflare calls: {cf_total}/{MAX_CLOUDFLARE_REQUESTS}"
    )
    print(
        f"Time to first source: p50={latency_metrics.first_source_p50_ms:.1f}ms, "
        f"p95={latency_metrics.first_source_p95_ms:.1f}ms"
    )
    print(
        f"Time to first sentence: p50={latency_metrics.first_sentence_p50_ms:.1f}ms, "
        f"p95={latency_metrics.first_sentence_p95_ms:.1f}ms"
    )
    print(
        f"Total time: p50={latency_metrics.total_time_p50_ms:.1f}ms, "
        f"p95={latency_metrics.total_time_p95_ms:.1f}ms"
    )
    print(
        f"Boot: {boot_metrics.boot_seconds:.3f}s, "
        f"RSS start: {boot_metrics.rss_start_mb:.1f}MB, end: {boot_metrics.rss_end_mb:.1f}MB "
        f"({boot_metrics.hardware_label})"
    )

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--port", type=int, default=8765, help="Port to run uvicorn on")
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("batteries/slice1_questions.jsonl"),
        help="Questions file",
    )
    parser.add_argument("--scratch", type=Path, default=DEFAULT_SCRATCH, help="Scratch directory")
    parser.add_argument(
        "--delay", type=float, default=10.0, help="Delay in seconds between questions"
    )
    args = parser.parse_args(argv)

    summary = run_gate(
        port=args.port,
        questions_file=args.questions,
        scratch_dir=args.scratch,
        delay_s=args.delay,
    )
    if not summary.passed_order_check or not summary.passed_receipt_check:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
