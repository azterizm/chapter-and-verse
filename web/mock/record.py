"""Write the mock event streams in web/mock/streams/ from the engine's own contract.

Every event is built with the dataclasses in ``chapterverse.contracts`` and encoded with
``encode_sse``, so the fixtures are the exact bytes the engine sends (canonical JSON, sorted
keys, one line per event). Each stream is checked with ``validate_order`` before it is written,
except ``broken.sse``, which breaks the order on purpose.

Window text and headings for ERA 1996 s.124 come from ``Premise.from_records`` over the test
fixture corpus, so they match what the engine shows. Provider names, model ids, latencies,
hashes and timings are illustrative.

Also writes ``order-cases.json``: event sequences with ``validate_order``'s verdict on each, so
the TypeScript validator is tested against the Python one.

Run from the repo root: ``.venv/bin/python web/mock/record.py`` (or ``npm run mock:record`` in
``web/``).
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

from legal_rag_verifier.premise import Premise

from chapterverse.contracts import (
    EVENT_TYPES,
    AnswerFactObject,
    ClarifyEvent,
    DoneEvent,
    ErrorEvent,
    Event,
    ExtractiveEvent,
    LabelledCoordinate,
    NoticeEvent,
    Policy,
    ProviderCall,
    ReceiptEvent,
    RefusalEvent,
    RenderedCitation,
    RouteEvent,
    SentenceEvent,
    SentenceOut,
    SourcesEvent,
    TemporalStatus,
    Timings,
    Window,
    encode_sse,
    sha256_hex,
    validate_order,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "streams"
ERA_ROWS = ROOT / "tests/fixtures/data/uk/ukpga/1996/uk_ukpga_1996_18.jsonl"
INDEX_MANIFEST = ROOT / "tests/fixtures/data/index/index-manifest.json"

SNAPSHOT = json.loads(INDEX_MANIFEST.read_text())["snapshot"]
CORPUS_ID = sha256_hex("chapter-and-verse mock corpus")
POLICY = Policy(judge_preset="cloudflare-qwen-judge", prompt_sha256=sha256_hex("mock prompt v1"))
ERA = "uk/ukpga/1996/18"
S124 = f"{ERA}/s124"
ANSWER_QUERY = "What is the limit of the compensatory award under s.124 ERA 1996?"


def era_windows() -> dict[str, Window]:
    rows = [json.loads(line) for line in ERA_ROWS.read_text().splitlines()]
    instrument = rows[0]
    keep = [instrument] + [
        r for r in rows[1:] if r["coordinate"] == S124 or r["coordinate"].startswith(S124 + "/")
    ]
    passages = {p.coordinate: p for p in Premise.from_records(keep).windows}
    url = {r["coordinate"]: r["source_url"] for r in keep}

    def window(pid: str, coord: str, highlight_from: str | None) -> Window:
        p = passages[coord]
        span = None
        if highlight_from is not None:
            start = p.text.index(highlight_from)
            span = (start, len(p.text))
        return Window(
            passage_id=pid,
            coordinate=coord,
            heading=p.heading,
            text=p.text,
            status=TemporalStatus.IN_FORCE,
            version_date=instrument["version_date"],
            source_url=url[coord],
            highlight=span,
        )

    return {
        "P1": window("P1", f"{S124}/1", "shall not exceed"),
        "P2": window("P2", f"{S124}/1ZA", "the lower of"),
        "P3": window("P3", f"{S124}/1A", None),
    }


def label(coord: str) -> str:
    sub = coord.removeprefix(S124 + "/")
    return f"s.124({sub}) Employment Rights Act 1996"


def citation(w: Window) -> RenderedCitation:
    return RenderedCitation(
        passage_id=w.passage_id, coordinate=w.coordinate, label=label(w.coordinate), url=w.source_url
    )


def route(status: str, action: str, coords: tuple[str, ...] = ()) -> RouteEvent:
    return RouteEvent(
        status=status,
        next_action=action,
        coordinates=coords,
        corrections=(),
        messages=(),
        index_snapshot=SNAPSHOT,
    )


def receipt(fact: AnswerFactObject) -> ReceiptEvent:
    return ReceiptEvent(answer_id=fact.answer_id, fact=fact)


def fact(query: str, **kw: object) -> AnswerFactObject:
    base: dict[str, object] = {
        "query_sha256": sha256_hex(query),
        "coordinates": (),
        "passages": {},
        "version_dates": {},
        "spans": {},
        "sentences": (),
        "corpus_id": CORPUS_ID,
        "calls": (),
        "policy_hash": POLICY.policy_hash,
    }
    base.update(kw)
    return AnswerFactObject(**base)  # type: ignore[arg-type]


def answer(w: dict[str, Window]) -> list[Event]:
    texts = [
        (
            "P1",
            "A compensatory award for unfair dismissal cannot exceed the amount specified in"
            " subsection (1ZA).",
            0,
        ),
        (
            "P2",
            "That amount is the lower of £123,543 and 52 multiplied by a week's pay of the person"
            " concerned.",
            1,
        ),
        (
            "P3",
            "The limit does not apply where the person is regarded as unfairly dismissed by virtue"
            " of section 100, 103A, 105(3) or 105(6A).",
            0,
        ),
    ]
    sentences = [
        SentenceOut(
            index=i, text=t, verdict="supported", passage_ids=(pid,), reasons=("GROUNDED",), rollbacks=rb
        )
        for i, (pid, t, rb) in enumerate(texts)
    ]
    windows = tuple(w.values())
    calls = (
        ProviderCall("cloudflare", "qwen3.8-27b", None, "generate", 1840, 96, 1412.0),
        ProviderCall("cloudflare", "qwen3.8-27b", None, "judge", 702, 4, 388.5),
        ProviderCall("cloudflare", "qwen3.8-27b", None, "judge", 731, 4, 401.0),
        ProviderCall("cloudflare", "qwen3.8-27b", None, "judge", 745, 4, 379.25),
    )
    f = fact(
        ANSWER_QUERY,
        route_status="ROUTE_BOUNDED",
        coordinates=(S124,),
        passages={x.passage_id: x.coordinate for x in windows},
        version_dates={ERA: windows[0].version_date},
        spans={x.coordinate: x.highlight for x in windows if x.highlight is not None},
        sentences=tuple(sentences),
        calls=calls,
        mode="generated",
    )
    return [
        route("ROUTE_BOUNDED", "RETRIEVE_BOUNDED", (S124,)),
        SourcesEvent(windows=windows),
        NoticeEvent(
            code="provider_fallback",
            message="Groq was at its limit. Cloudflare Workers AI answered instead.",
        ),
        *[SentenceEvent(sentence=s, citations=(citation(w[s.passage_ids[0]]),)) for s in sentences],
        receipt(f),
        DoneEvent(timings=Timings(first_source_ms=212.4, first_sentence_ms=1693.0, total_ms=3920.7)),
    ]


def extractive(w: dict[str, Window]) -> list[Event]:
    windows = (w["P1"], w["P2"])
    query = ANSWER_QUERY + " (all providers 429)"
    f = fact(
        query,
        route_status="ROUTE_BOUNDED",
        coordinates=(S124,),
        passages={x.passage_id: x.coordinate for x in windows},
        version_dates={ERA: windows[0].version_date},
        spans={x.coordinate: x.highlight for x in windows if x.highlight is not None},
        mode="extractive",
    )
    return [
        route("ROUTE_BOUNDED", "RETRIEVE_BOUNDED", (S124,)),
        SourcesEvent(windows=windows),
        ExtractiveEvent(reason="providers_unavailable", windows=windows),
        receipt(f),
        DoneEvent(timings=Timings(first_source_ms=198.0, first_sentence_ms=None, total_ms=1240.5)),
    ]


def refused() -> list[Event]:
    status = "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND"
    query = "What does section 3 of the Marchwood Commercial Arbitration Act 1996 say?"
    return [
        route(status, "REFUSE"),
        RefusalEvent(
            kind=status,
            message=(
                "There is no Act called the Marchwood Commercial Arbitration Act 1996 in the"
                " corpus. It may not exist. Check the title and year."
            ),
            suggestions=(LabelledCoordinate("uk/ukpga/1996/23", "Arbitration Act 1996"),),
            snapshot=SNAPSHOT,
            link=None,
        ),
        receipt(fact(query, route_status=status, mode="refusal")),
        DoneEvent(timings=Timings(first_source_ms=None, first_sentence_ms=None, total_ms=41.2)),
    ]


def text_not_held() -> list[Event]:
    coord = "uk/ukpga/1980/15"
    query = "What does the National Health Service (Invalid Direction) Act 1980 say?"
    return [
        route("ROUTE_BOUNDED", "RETRIEVE_BOUNDED", (coord,)),
        RefusalEvent(
            kind="TEXT_NOT_HELD",
            message=(
                "The corpus holds the title of the National Health Service (Invalid Direction)"
                " Act 1980 but not its text. Read it on legislation.gov.uk."
            ),
            suggestions=(),
            snapshot=SNAPSHOT,
            link="https://www.legislation.gov.uk/ukpga/1980/15",
        ),
        receipt(
            fact(
                query,
                route_status="ROUTE_BOUNDED",
                coordinates=(coord,),
                version_dates={coord: None},
                mode="refusal",
            )
        ),
        DoneEvent(timings=Timings(first_source_ms=None, first_sentence_ms=None, total_ms=37.9)),
    ]


def clarify() -> list[Event]:
    return [
        route("ROUTE_AMBIGUOUS", "ASK_USER"),
        ClarifyEvent(
            question="Several instruments match. Which one do you mean?",
            candidates=(
                LabelledCoordinate(S124, "s.124 Employment Rights Act 1996"),
                LabelledCoordinate(
                    "uk/nisi/1996/1919", "Employment Rights (Northern Ireland) Order 1996"
                ),
            ),
        ),
        DoneEvent(timings=Timings(first_source_ms=None, first_sentence_ms=None, total_ms=29.6)),
    ]


def out_of_coverage() -> list[Event]:
    status = "ROUTE_OUT_OF_COVERAGE"
    return [
        route(status, "DECLARE_OUT_OF_COVERAGE"),
        RefusalEvent(
            kind=status,
            message="That instrument is outside the corpus this service answers from.",
            suggestions=(),
            snapshot=SNAPSHOT,
            link=None,
        ),
        receipt(fact("uk/nisi/1996/1919", route_status=status, mode="refusal")),
        DoneEvent(timings=Timings(first_source_ms=None, first_sentence_ms=None, total_ms=22.0)),
    ]


def too_long() -> list[Event]:
    return [
        ErrorEvent(code="query_too_long", message="The question is longer than 4096 bytes."),
        DoneEvent(timings=Timings(first_source_ms=None, first_sentence_ms=None, total_ms=0.4)),
    ]


def write(name: str, events: list[Event]) -> None:
    types = [e.type for e in events]
    if not validate_order(types):
        sys.exit(f"{name}: illegal order {types}")
    (OUT / f"{name}.sse").write_bytes(b"".join(encode_sse(e) for e in events))


def order_cases() -> list[dict[str, object]]:
    legal = [
        ["route", "clarify", "done"],
        ["route", "notice", "clarify", "notice", "done"],
        ["route", "refusal", "receipt", "done"],
        ["route", "sources", "receipt", "done"],
        ["route", "sources", "sentence", "sentence", "receipt", "done"],
        ["route", "sources", "notice", "sentence", "notice", "sentence", "receipt", "notice", "done"],
        ["route", "sources", "extractive", "notice", "receipt", "done"],
        ["error", "done"],
        ["route", "error", "done"],
        ["route", "sources", "sentence", "error", "done"],
    ]
    rng = random.Random(124)
    seqs: list[list[str]] = [list(s) for s in legal]
    seqs += [[], ["done"], ["route"], ["route", "done"], ["error"], ["error", "error", "done"]]
    seqs += [["route", "sources", "extractive", "sentence", "receipt", "done"]]
    seqs += [["route", "sources", "extractive", "extractive", "receipt", "done"]]
    seqs += [["route", "clarify", "receipt", "done"], ["route", "refusal", "done"]]
    seqs += [["route", "sources", "sentence", "receipt", "done", "notice"]]
    seqs += [["route", "error", "notice", "done"], ["notice", "route", "clarify", "done"]]
    while len(seqs) < 400:
        s = list(rng.choice(legal))
        for _ in range(rng.randint(0, 2)):
            op = rng.choice(("insert", "delete", "swap", "replace"))
            i = rng.randrange(len(s) + (op == "insert"))
            if op == "insert":
                s.insert(i, rng.choice(EVENT_TYPES))
            elif op == "delete" and s:
                del s[min(i, len(s) - 1)]
            elif op == "swap" and len(s) > 1:
                j = rng.randrange(len(s))
                i = min(i, len(s) - 1)
                s[i], s[j] = s[j], s[i]
            elif op == "replace" and s:
                s[min(i, len(s) - 1)] = rng.choice(EVENT_TYPES)
        seqs.append(s)
    return [{"types": s, "valid": validate_order(s)} for s in seqs]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    w = era_windows()
    write("answer", answer(w))
    write("refused", refused())
    write("text-not-held", text_not_held())
    write("extractive", extractive(w))
    write("clarify", clarify())
    write("out-of-coverage", out_of_coverage())
    write("too-long", too_long())
    # Deliberately out of order: a sentence before any sources.
    good = answer(w)
    broken = [good[0], good[3], good[1], *good[4:]]
    (OUT / "broken.sse").write_bytes(b"".join(encode_sse(e) for e in broken))
    cases = order_cases()
    (OUT / "order-cases.json").write_text(
        "[\n" + ",\n".join(json.dumps(c, separators=(",", ":")) for c in cases) + "\n]\n"
    )
    bad = re.compile("[–—‘’“”→]")
    for e in (*answer(w), *refused(), *text_not_held(), *clarify(), *out_of_coverage()):
        for msg in (getattr(e, "message", ""), getattr(getattr(e, "sentence", None), "text", "")):
            if bad.search(msg or ""):
                sys.exit(f"house style: dash, curly quote or arrow in {msg!r}")
    print(f"wrote {len(list(OUT.glob('*.sse')))} streams and {len(cases)} order cases to {OUT}")


if __name__ == "__main__":
    main()
