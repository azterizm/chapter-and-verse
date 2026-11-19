"""Offline tests for eval.smoke checks (R2.4).

Tests event parsing, order validation, s.124 verification, Marchwood verification,
title-only verification, citation URL checking, and receipt hashing against recorded
or synthesized streams without any network access.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chapterverse.contracts import (  # noqa: E402
    AnswerFactObject,
    SentenceOut,
    canonical_json,
)
from eval.smoke import (  # noqa: E402
    ParsedEvent,
    check_citation_url,
    check_marchwood_stream,
    check_s124_stream,
    check_stream_order,
    check_title_only_stream,
    parse_sse_events,
    run_smoke_checks,
    verify_receipt_hash,
)


def _make_receipt_events(
    route_status: str,
    coordinates: tuple[str, ...],
    sentences: tuple[SentenceOut, ...] = (),
    mode: str = "generated",
) -> tuple[str, dict[str, Any], str]:
    afo = AnswerFactObject(
        query_sha256=hashlib.sha256(b"test query").hexdigest(),
        route_status=route_status,
        coordinates=coordinates,
        passages={"P1": coordinates[0]} if coordinates else {},
        version_dates={},
        spans={},
        sentences=sentences,
        corpus_id="test_corpus",
        calls=(),
        policy_hash="test_policy",
        mode=mode,  # type: ignore[arg-type]
    )
    receipt_data = {
        "answer_id": afo.answer_id,
        "fact": json.loads(canonical_json(afo).decode("utf-8")),
        "type": "receipt",
    }
    raw = canonical_json(receipt_data).decode("utf-8")
    return afo.answer_id, receipt_data, raw


def test_parse_sse_events() -> None:
    lines = [
        "event: route",
        'data: {"status": "ROUTE_BOUNDED"}',
        "",
        "event: done",
        'data: {"total_ms": 100}',
        "",
    ]
    events = parse_sse_events(lines)
    assert len(events) == 2
    assert events[0].event_type == "route"
    assert events[0].data == {"status": "ROUTE_BOUNDED"}
    assert events[1].event_type == "done"
    assert events[1].data == {"total_ms": 100}


def test_verify_receipt_hash_valid() -> None:
    _, receipt_data, raw = _make_receipt_events("ROUTE_BOUNDED", ("uk/ukpga/1996/18/s124",))
    assert verify_receipt_hash(raw, receipt_data) is True


def test_verify_receipt_hash_tampered() -> None:
    _, receipt_data, raw = _make_receipt_events("ROUTE_BOUNDED", ("uk/ukpga/1996/18/s124",))
    receipt_data["answer_id"] = "bad" * 16
    assert verify_receipt_hash(raw, receipt_data) is False


def test_check_citation_url() -> None:
    assert check_citation_url(
        "uk/ukpga/1996/18/s124", "https://www.legislation.gov.uk/ukpga/1996/18/section/124"
    )
    assert check_citation_url(
        "uk/ukpga/1996/18/s124/1ZA", "https://www.legislation.gov.uk/ukpga/1996/18/section/124/1ZA"
    )
    assert check_citation_url(
        "uk/uksi/1998/1833/reg13", "https://www.legislation.gov.uk/uksi/1998/1833/regulation/13"
    )

    # Wrong host
    assert not check_citation_url(
        "uk/ukpga/1996/18/s124", "https://not.legislation.gov.uk/ukpga/1996/18/section/124"
    )
    # Wrong instrument
    assert not check_citation_url(
        "uk/ukpga/1996/18/s124", "https://www.legislation.gov.uk/ukpga/2000/1/section/124"
    )
    # Malformed coordinate
    assert not check_citation_url("invalid", "https://www.legislation.gov.uk/invalid")


def test_check_stream_order() -> None:
    valid_events = [
        ParsedEvent(event_type="route", data={}, raw_data="{}"),
        ParsedEvent(event_type="sources", data={}, raw_data="{}"),
        ParsedEvent(event_type="receipt", data={}, raw_data="{}"),
        ParsedEvent(event_type="done", data={}, raw_data="{}"),
    ]
    ok, _ = check_stream_order(valid_events)
    assert ok is True

    invalid_events = [
        ParsedEvent(event_type="sources", data={}, raw_data="{}"),
        ParsedEvent(event_type="done", data={}, raw_data="{}"),
    ]
    ok, _ = check_stream_order(invalid_events)
    assert ok is False


def test_check_s124_stream_supported() -> None:
    s_out = SentenceOut(
        index=0,
        text="The limit is set by statutory order.",
        verdict="supported",
        passage_ids=("P1",),
        reasons=(),
        rollbacks=0,
    )
    _, r_data, r_raw = _make_receipt_events(
        "ROUTE_BOUNDED", ("uk/ukpga/1996/18/s124",), sentences=(s_out,)
    )

    events = [
        ParsedEvent(
            event_type="route",
            data={"status": "ROUTE_BOUNDED", "type": "route"},
            raw_data='{"status":"ROUTE_BOUNDED","type":"route"}',
        ),
        ParsedEvent(
            event_type="sources",
            data={"windows": [], "type": "sources"},
            raw_data='{"windows":[],"type":"sources"}',
        ),
        ParsedEvent(
            event_type="sentence",
            data={
                "sentence": {
                    "index": 0,
                    "text": s_out.text,
                    "verdict": "supported",
                    "passage_ids": ["P1"],
                    "reasons": [],
                    "rollbacks": 0,
                },
                "citations": [
                    {
                        "passage_id": "P1",
                        "coordinate": "uk/ukpga/1996/18/s124",
                        "label": "s.124 Employment Rights Act 1996",
                        "url": "https://www.legislation.gov.uk/ukpga/1996/18/section/124",
                    }
                ],
                "type": "sentence",
            },
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=r_data, raw_data=r_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]

    checks = check_s124_stream(events)
    for name, ok, detail in checks:
        assert ok, f"check {name} failed: {detail}"


def test_check_s124_stream_extractive() -> None:
    _, r_data, r_raw = _make_receipt_events(
        "ROUTE_BOUNDED", ("uk/ukpga/1996/18/s124",), mode="extractive"
    )
    events = [
        ParsedEvent(
            event_type="route",
            data={"status": "ROUTE_BOUNDED", "type": "route"},
            raw_data='{"status":"ROUTE_BOUNDED"}',
        ),
        ParsedEvent(
            event_type="sources",
            data={"windows": [], "type": "sources"},
            raw_data='{"windows":[]}',
        ),
        ParsedEvent(
            event_type="extractive",
            data={"reason": "providers_unavailable", "windows": [], "type": "extractive"},
            raw_data='{"reason":"providers_unavailable"}',
        ),
        ParsedEvent(event_type="receipt", data=r_data, raw_data=r_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]
    checks = check_s124_stream(events)
    for name, ok, detail in checks:
        assert ok, f"check {name} failed: {detail}"


def test_check_s124_stream_fails_when_unsupported_and_no_extractive() -> None:
    s_out = SentenceOut(
        index=0,
        text="Refused statement.",
        verdict="refused",
        passage_ids=(),
        reasons=("NOT_STATED",),
        rollbacks=4,
    )
    _, r_data, r_raw = _make_receipt_events(
        "ROUTE_BOUNDED", ("uk/ukpga/1996/18/s124",), sentences=(s_out,)
    )
    events = [
        ParsedEvent(
            event_type="route",
            data={"status": "ROUTE_BOUNDED", "type": "route"},
            raw_data='{"status":"ROUTE_BOUNDED"}',
        ),
        ParsedEvent(
            event_type="sources",
            data={"windows": [], "type": "sources"},
            raw_data='{"windows":[]}',
        ),
        ParsedEvent(
            event_type="sentence",
            data={
                "sentence": {
                    "index": 0,
                    "text": s_out.text,
                    "verdict": "refused",
                    "passage_ids": [],
                    "reasons": ["NOT_STATED"],
                    "rollbacks": 4,
                },
                "citations": [],
                "type": "sentence",
            },
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=r_data, raw_data=r_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]
    checks = {name: (ok, detail) for name, ok, detail in check_s124_stream(events)}
    assert checks["s124_bounded"][0] is False


def test_check_marchwood_stream() -> None:
    status = "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND"
    _, r_data, r_raw = _make_receipt_events(status, (), mode="refusal")
    events = [
        ParsedEvent(
            event_type="route",
            data={"status": status, "type": "route"},
            raw_data=f'{{"status":"{status}"}}',
        ),
        ParsedEvent(
            event_type="refusal",
            data={"kind": status, "message": "not found", "type": "refusal"},
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=r_data, raw_data=r_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]
    checks = check_marchwood_stream(events)
    for name, ok, detail in checks:
        assert ok, f"check {name} failed: {detail}"


def test_check_marchwood_stream_wrong_status() -> None:
    _, r_data, r_raw = _make_receipt_events("ROUTE_BOUNDED", (), mode="refusal")
    events = [
        ParsedEvent(
            event_type="route",
            data={"status": "ROUTE_BOUNDED", "type": "route"},
            raw_data='{"status":"ROUTE_BOUNDED"}',
        ),
        ParsedEvent(
            event_type="refusal",
            data={"kind": "ROUTE_BOUNDED", "message": "msg", "type": "refusal"},
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=r_data, raw_data=r_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]
    checks = {name: (ok, detail) for name, ok, detail in check_marchwood_stream(events)}
    assert checks["marchwood_abstention"][0] is False


def test_check_title_only_stream() -> None:
    _, r_data, r_raw = _make_receipt_events("ROUTE_BOUNDED", ("uk/ukpga/1980/15",), mode="refusal")
    link = "https://www.legislation.gov.uk/ukpga/1980/15"
    events = [
        ParsedEvent(
            event_type="route",
            data={"status": "ROUTE_BOUNDED", "type": "route"},
            raw_data='{"status":"ROUTE_BOUNDED"}',
        ),
        ParsedEvent(
            event_type="refusal",
            data={
                "kind": "TEXT_NOT_HELD",
                "message": "Title only",
                "link": link,
                "type": "refusal",
            },
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=r_data, raw_data=r_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]
    checks = check_title_only_stream(events)
    for name, ok, detail in checks:
        assert ok, f"check {name} failed: {detail}"


def test_check_title_only_stream_missing_link() -> None:
    _, r_data, r_raw = _make_receipt_events("ROUTE_BOUNDED", ("uk/ukpga/1980/15",), mode="refusal")
    events = [
        ParsedEvent(
            event_type="route",
            data={"status": "ROUTE_BOUNDED", "type": "route"},
            raw_data='{"status":"ROUTE_BOUNDED"}',
        ),
        ParsedEvent(
            event_type="refusal",
            data={
                "kind": "TEXT_NOT_HELD",
                "message": "Title only",
                "link": None,
                "type": "refusal",
            },
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=r_data, raw_data=r_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]
    checks = {name: (ok, detail) for name, ok, detail in check_title_only_stream(events)}
    assert checks["title_only_refusal"][0] is False


def test_run_smoke_checks_offline_mock(monkeypatch: Any) -> None:
    s_out = SentenceOut(
        index=0,
        text="The limit is set by statutory order.",
        verdict="supported",
        passage_ids=("P1",),
        reasons=(),
        rollbacks=0,
    )
    _, r1_data, r1_raw = _make_receipt_events(
        "ROUTE_BOUNDED", ("uk/ukpga/1996/18/s124",), sentences=(s_out,)
    )
    s124_stream = [
        ParsedEvent(
            event_type="route",
            data={"status": "ROUTE_BOUNDED", "type": "route"},
            raw_data='{"status":"ROUTE_BOUNDED"}',
        ),
        ParsedEvent(
            event_type="sources",
            data={"windows": [], "type": "sources"},
            raw_data='{"windows":[]}',
        ),
        ParsedEvent(
            event_type="sentence",
            data={
                "sentence": {
                    "index": 0,
                    "text": s_out.text,
                    "verdict": "supported",
                    "passage_ids": ["P1"],
                    "reasons": [],
                    "rollbacks": 0,
                },
                "citations": [
                    {
                        "passage_id": "P1",
                        "coordinate": "uk/ukpga/1996/18/s124",
                        "label": "s.124 Employment Rights Act 1996",
                        "url": "https://www.legislation.gov.uk/ukpga/1996/18/section/124",
                    }
                ],
                "type": "sentence",
            },
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=r1_data, raw_data=r1_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]

    m_status = "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND"
    _, rm_data, rm_raw = _make_receipt_events(m_status, (), mode="refusal")
    marchwood_stream = [
        ParsedEvent(
            event_type="route",
            data={"status": m_status, "type": "route"},
            raw_data=f'{{"status":"{m_status}"}}',
        ),
        ParsedEvent(
            event_type="refusal",
            data={"kind": m_status, "message": "not found", "type": "refusal"},
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=rm_data, raw_data=rm_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]

    _, rt_data, rt_raw = _make_receipt_events(
        "ROUTE_BOUNDED", ("uk/ukpga/1980/15",), mode="refusal"
    )
    title_stream = [
        ParsedEvent(
            event_type="route",
            data={"status": "ROUTE_BOUNDED", "type": "route"},
            raw_data='{"status":"ROUTE_BOUNDED"}',
        ),
        ParsedEvent(
            event_type="refusal",
            data={
                "kind": "TEXT_NOT_HELD",
                "message": "Title only",
                "link": "https://www.legislation.gov.uk/ukpga/1980/15",
                "type": "refusal",
            },
            raw_data="{}",
        ),
        ParsedEvent(event_type="receipt", data=rt_data, raw_data=rt_raw),
        ParsedEvent(event_type="done", data={"type": "done"}, raw_data='{"type":"done"}'),
    ]

    def mock_fetch(base_url: str, query: str, timeout_s: float = 60.0) -> list[ParsedEvent]:
        if "s.124" in query:
            return s124_stream
        if "Marchwood" in query:
            return marchwood_stream
        if "Invalid Direction" in query:
            return title_stream
        raise ValueError(f"unexpected query {query}")

    monkeypatch.setattr("eval.smoke.fetch_stream", mock_fetch)
    results = run_smoke_checks("http://mock-test")
    assert len(results) == 14
    for r in results:
        assert r.passed, f"{r.name} failed: {r.detail}"
