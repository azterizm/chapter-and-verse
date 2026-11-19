"""Slice 1 smoke script (R2).

Verifies a deployed or local Chapter & Verse endpoint against the four Slice 1 "done when"
questions:
1. s.124 ERA 1996
2. Marchwood Commercial Arbitration Act 1996 (invented)
3. National Health Service (Invalid Direction) Act 1980 (title-only)
4. s.124 ERA 1996 (repeat)

Usage:
    uv run python -m eval.smoke --base-url http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import httpx
from legal_rag_router import Coordinate, CoordinateError

from chapterverse.contracts import AnswerFactObject, validate_order

__all__ = [
    "QUESTIONS",
    "CheckResult",
    "ParsedEvent",
    "check_citation_url",
    "check_marchwood_stream",
    "check_s124_stream",
    "check_stream_citations",
    "check_stream_order",
    "check_stream_receipt",
    "check_title_only_stream",
    "main",
    "parse_sse_events",
    "run_smoke_checks",
    "verify_receipt_hash",
]

QUESTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("s124_first", "What is the limit of the compensatory award under s.124 ERA 1996?"),
    (
        "marchwood",
        "What does section 3 of the Marchwood Commercial Arbitration Act 1996 say?",
    ),
    (
        "title_only",
        "What does the National Health Service (Invalid Direction) Act 1980 say?",
    ),
    ("s124_second", "What is the limit of the compensatory award under s.124 ERA 1996?"),
)

LEGISLATION_BASE: Final = "https://www.legislation.gov.uk/"
HTTP_OK: Final = 200


@dataclass(frozen=True, slots=True, kw_only=True)
class ParsedEvent:
    event_type: str
    data: dict[str, Any]
    raw_data: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckResult:
    name: str
    passed: bool
    detail: str

    def format_line(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}: {self.detail}"


def parse_sse_events(lines: Sequence[str]) -> list[ParsedEvent]:
    """Parse SSE lines into a list of ParsedEvent objects."""
    events: list[ParsedEvent] = []
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
                events.append(ParsedEvent(event_type=current_type, data=payload, raw_data=raw))
            current_type = None
            current_data = []
            continue
        if stripped.startswith("event: "):
            current_type = stripped[len("event: ") :].strip()
        elif stripped.startswith("data: "):
            current_data.append(stripped[len("data: ") :])
    if current_type is not None and current_data:
        raw = "\n".join(current_data)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        events.append(ParsedEvent(event_type=current_type, data=payload, raw_data=raw))
    return events


def verify_receipt_hash(raw_data: str, data: Mapping[str, Any]) -> bool:
    """Verify that sha256 of the receipt fact equals answer_id."""
    answer_id = data.get("answer_id")
    if not isinstance(answer_id, str) or not answer_id:
        return False
    # 1. Byte slice extraction matching web/src
    fact_prefix = ',"fact":'
    receipt_suffix = ',"type":"receipt"}'
    if raw_data.startswith('{"answer_id":') and raw_data.endswith(receipt_suffix):
        at = raw_data.find(fact_prefix)
        if at != -1:
            fact_str = raw_data[at + len(fact_prefix) : -len(receipt_suffix)]
            h = hashlib.sha256(fact_str.encode("utf-8")).hexdigest()
            if h == answer_id:
                return True
    # 2. Re-serialised canonical JSON of AnswerFactObject
    fact = data.get("fact")
    if isinstance(fact, dict):
        try:
            afo = AnswerFactObject.from_dict(fact)
            if afo.answer_id == answer_id:
                return True
        except (ValueError, TypeError, KeyError):
            return False
    return False


def check_citation_url(coordinate: str, url: str) -> bool:
    """Check that citation URL starts with legislation base and matches coordinate path."""
    if not url.startswith(LEGISLATION_BASE):
        return False
    try:
        parsed = Coordinate.parse(coordinate)
    except (CoordinateError, ValueError, TypeError):
        return False
    inst_path = "/".join(parsed.instrument)
    if f"{LEGISLATION_BASE}{inst_path}" not in url:
        return False
    if parsed.provision:
        main_prov = parsed.provision[0].lstrip("s").lstrip("reg").lstrip("art")
        if main_prov and main_prov not in url:
            return False
    return True


def check_stream_order(events: Sequence[ParsedEvent]) -> tuple[bool, str]:
    types = [e.event_type for e in events]
    if not types:
        return False, "empty stream"
    valid = validate_order(types)
    if not valid:
        return False, f"invalid event order: {types}"
    return True, f"valid event order: {' -> '.join(types)}"


def check_stream_receipt(events: Sequence[ParsedEvent]) -> tuple[bool, str]:
    receipt_ev = next((e for e in events if e.event_type == "receipt"), None)
    if receipt_ev is None:
        return False, "missing receipt event"
    if not verify_receipt_hash(receipt_ev.raw_data, receipt_ev.data):
        aid = receipt_ev.data.get("answer_id")
        return False, f"receipt sha256 mismatch for answer_id {aid!r}"
    return True, f"receipt sha256 matches answer_id ({receipt_ev.data.get('answer_id')})"


def check_stream_citations(events: Sequence[ParsedEvent]) -> tuple[bool, str]:
    checked = 0
    for e in events:
        if e.event_type == "sentence":
            cits = e.data.get("citations", [])
            for c in cits:
                coord = c.get("coordinate", "")
                url = c.get("url", "")
                checked += 1
                if not check_citation_url(coord, url):
                    return False, f"citation URL {url!r} does not match coordinate {coord!r}"
    return True, f"{checked} citation URLs verified"


def check_s124_stream(events: Sequence[ParsedEvent]) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    # 1. Order
    ok, detail = check_stream_order(events)
    results.append(("event_order", ok, detail))
    # 2. ROUTE_BOUNDED with supported sentence or extractive
    route_ev = next((e for e in events if e.event_type == "route"), None)
    if route_ev is None:
        results.append(("s124_bounded", False, "missing route event"))
    elif route_ev.data.get("status") != "ROUTE_BOUNDED":
        status = route_ev.data.get("status")
        results.append(("s124_bounded", False, f"status {status!r} != ROUTE_BOUNDED"))
    else:
        has_extractive = any(e.event_type == "extractive" for e in events)
        supported = [
            e
            for e in events
            if e.event_type == "sentence"
            and isinstance(e.data.get("sentence"), dict)
            and e.data["sentence"].get("verdict") == "supported"
        ]
        if has_extractive:
            results.append(("s124_bounded", True, "ROUTE_BOUNDED with extractive answer"))
        elif supported:
            count = len(supported)
            results.append(
                ("s124_bounded", True, f"ROUTE_BOUNDED with {count} supported sentence(s)")
            )
        else:
            results.append(
                ("s124_bounded", False, "neither extractive answer nor supported sentence found")
            )
    # 3. Citation URLs
    ok, detail = check_stream_citations(events)
    results.append(("citation_urls", ok, detail))
    # 4. Receipt
    ok, detail = check_stream_receipt(events)
    results.append(("receipt_sha256", ok, detail))
    return results


def check_marchwood_stream(events: Sequence[ParsedEvent]) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    # 1. Order
    ok, detail = check_stream_order(events)
    results.append(("event_order", ok, detail))
    # 2. EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND
    route_ev = next((e for e in events if e.event_type == "route"), None)
    expected_status = "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND"
    if route_ev is None:
        results.append(("marchwood_abstention", False, "missing route event"))
    elif route_ev.data.get("status") != expected_status:
        status = route_ev.data.get("status")
        results.append(("marchwood_abstention", False, f"status {status!r} != {expected_status}"))
    else:
        results.append(("marchwood_abstention", True, f"status is {expected_status}"))
    # 3. Receipt
    ok, detail = check_stream_receipt(events)
    results.append(("receipt_sha256", ok, detail))
    return results


def check_title_only_stream(events: Sequence[ParsedEvent]) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    # 1. Order
    ok, detail = check_stream_order(events)
    results.append(("event_order", ok, detail))
    # 2. Refusal TEXT_NOT_HELD with link
    refusal_ev = next((e for e in events if e.event_type == "refusal"), None)
    if refusal_ev is None:
        results.append(("title_only_refusal", False, "missing refusal event"))
    elif refusal_ev.data.get("kind") != "TEXT_NOT_HELD":
        kind = refusal_ev.data.get("kind")
        results.append(("title_only_refusal", False, f"refusal kind {kind!r} != TEXT_NOT_HELD"))
    else:
        link = refusal_ev.data.get("link")
        if not link or not isinstance(link, str) or not link.startswith(LEGISLATION_BASE):
            results.append(
                ("title_only_refusal", False, f"link {link!r} is not a valid legislation URL")
            )
        else:
            results.append(("title_only_refusal", True, f"TEXT_NOT_HELD with link {link}"))
    # 3. Receipt
    ok, detail = check_stream_receipt(events)
    results.append(("receipt_sha256", ok, detail))
    return results


def fetch_stream(base_url: str, query: str, timeout_s: float = 60.0) -> list[ParsedEvent]:
    url = f"{base_url.rstrip('/')}/api/ask"
    with (
        httpx.Client(timeout=timeout_s) as client,
        client.stream("POST", url, json={"query": query}) as response,
    ):
        if response.status_code != HTTP_OK:
            err = response.read().decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {response.status_code}: {err}")
        lines = list(response.iter_lines())
    return parse_sse_events(lines)


def run_smoke_checks(base_url: str) -> list[CheckResult]:
    all_results: list[CheckResult] = []
    for label, query in QUESTIONS:
        try:
            events = fetch_stream(base_url, query)
        except (httpx.HTTPError, RuntimeError, OSError) as exc:
            all_results.append(
                CheckResult(
                    name=f"{label}:fetch",
                    passed=False,
                    detail=f"failed to fetch stream: {exc}",
                )
            )
            continue
        if label in ("s124_first", "s124_second"):
            checks = check_s124_stream(events)
        elif label == "marchwood":
            checks = check_marchwood_stream(events)
        elif label == "title_only":
            checks = check_title_only_stream(events)
        else:
            checks = []
        for name, ok, detail in checks:
            all_results.append(CheckResult(name=f"{label}:{name}", passed=ok, detail=detail))
    return all_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--base-url", required=True, help="Base URL of deployed or local app")
    args = parser.parse_args(argv)

    results = run_smoke_checks(args.base_url)
    all_passed = True
    for res in results:
        print(res.format_line())
        if not res.passed:
            all_passed = False

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
