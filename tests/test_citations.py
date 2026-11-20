"""System-rendered citations: labels and legislation.gov.uk URLs (chapterverse.citations)."""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from legal_rag_router.grammars.uk import provision_from_legislation_tokens
from legal_rag_verifier.citations import coordinate_tail

from chapterverse.citations import (
    LEGISLATION_BASE,
    dated_url,
    instrument_name,
    official_citation,
    provision_label,
    provision_url,
    record_url,
    regnal_citation,
    render_citation,
)
from chapterverse.contracts import RenderedCitation

FIXTURE_UK = Path(__file__).resolve().parent / "fixtures" / "data" / "uk"


def _fixture_rows() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(instrument record, provision record) for every fixture provision."""
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for path in sorted(FIXTURE_UK.glob("*/*/*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        instrument = json.loads(lines[0])
        out.extend((instrument, json.loads(line)) for line in lines[1:])
    return out


FIXTURE_ROWS = _fixture_rows()
ERA = {"coordinate": "uk/ukpga/1996/18", "title": "Employment Rights Act 1996"}


# ---------------------------------------------------------------------------- label forms


@pytest.mark.parametrize(
    ("coordinate", "record", "label"),
    [
        ("uk/ukpga/1996/18/s124/1ZA/a", ERA, "s.124(1ZA)(a) Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/s124A", ERA, "s.124A Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/sch1/para3", ERA, "Sch. 1 para. 3 Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/sch1/para3/2/b", ERA, "Sch. 1 para. 3(2)(b) Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/sch/para10", ERA, "Sch. para. 10 Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/sch", ERA, "Sch. Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/sch2/pt1", ERA, "Sch. 2 Pt. 1 Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/pt2A/ch1", ERA, "Pt. 2A Ch. 1 Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/ptII", ERA, "Pt. II Employment Rights Act 1996"),
        ("uk/ukpga/1996/18/schA1/para1", ERA, "Sch. A1 para. 1 Employment Rights Act 1996"),
        ("uk/ukpga/1996/18", ERA, "Employment Rights Act 1996"),
        (
            "uk/uksi/2011/3006/reg4/1C",
            {"title": "The Employment Rights (Increase of Limits) Order 2011"},
            "reg. 4(1C) Employment Rights (Increase of Limits) Order 2011 (SI 2011/3006)",
        ),
        (
            "uk/uksi/2011/3006/art2/1",
            {"title": "The Employment Rights (Increase of Limits) Order 2011"},
            "art. 2(1) Employment Rights (Increase of Limits) Order 2011 (SI 2011/3006)",
        ),
        (
            "uk/uksi/1998/3132/rule3.1/2",
            {"title": "The Civil Procedure Rules 1998"},
            "r. 3.1(2) Civil Procedure Rules 1998 (SI 1998/3132)",
        ),
        (
            "uk/uksi/1998/3132/sch1/rule2",
            {"title": "The Civil Procedure Rules 1998"},
            "Sch. 1 r. 2 Civil Procedure Rules 1998 (SI 1998/3132)",
        ),
        (
            "uk/wsi/2016/691/reg3/1",
            {
                "title": "The Waste (Meaning of Recovery) (Wales) Regulations 2016",
                "alternative_numbers": [{"category": "W", "value": "189"}],
            },
            "reg. 3(1) Waste (Meaning of Recovery) (Wales) Regulations 2016 (SI 2016/691 (W. 189))",
        ),
        (
            "uk/wsi/2026/10/reg3",
            {
                "title": "The Example (Wales) Regulations 2026",
                "alternative_numbers": [{"category": "W", "value": "10"}],
            },
            "reg. 3 Example (Wales) Regulations 2026 (Welsh SI 2026/10 (W. 10))",
        ),
        (
            "uk/nisi/1996/2597/art3/1/a",
            {
                "title": (
                    "The Housing Benefit (Payment to Third Parties) (Northern Ireland) Order 1996"
                ),
                "alternative_numbers": [{"category": "NI", "value": "20"}],
            },
            (
                "art. 3(1)(a) Housing Benefit (Payment to Third Parties) (Northern Ireland) "
                "Order 1996 (SI 1996/2597 (N.I. 20))"
            ),
        ),
        (
            "uk/nisi/1995/2702/art2",
            {"title": "The Child Support (Northern Ireland) Order 1995"},
            "art. 2 Child Support (Northern Ireland) Order 1995 (SI 1995/2702)",
        ),
        (
            "uk/ukpga/Vict/24-25/100/s18",
            {"title": "Offences against the Person Act 1861"},
            "s.18 Offences against the Person Act 1861",
        ),
        (
            "uk/ukpga/Vict/20-21/74/s1",
            {"title": "An Act to continue the Act concerning the Management of Estates."},
            "s.1 20 & 21 Vict. c. 74",
        ),
        (
            "uk/ukpga/Geo3Sess2/47/78/sI",
            {"title": "Statute of Examples"},
            "s.I Statute of Examples (47 Geo. 3 Sess. 2 c. 78)",
        ),
        ("uk/ukpga/Edw7and1Geo5/10/15/s2", {}, "s.2 10 Edw. 7 & 1 Geo. 5 c. 15"),
        ("uk/ukpga/1996/18/s1", {}, "s.1 1996 c. 18"),
    ],
)
def test_label_forms(coordinate: str, record: dict[str, Any], label: str) -> None:
    citation = render_citation("P1", coordinate, record)
    assert citation == RenderedCitation("P1", coordinate, label, provision_url(coordinate))


@pytest.mark.parametrize(
    ("instrument", "text"),
    [
        (("ukpga", "Vict", "24-25", "100"), "24 & 25 Vict. c. 100"),
        (("ukpga", "Eliz2", "8-9", "69"), "8 & 9 Eliz. 2 c. 69"),
        (("ukpga", "Geo3Sess2", "47", "78"), "47 Geo. 3 Sess. 2 c. 78"),
        (("ukpga", "Edw7and1Geo5", "10", "15"), "10 Edw. 7 & 1 Geo. 5 c. 15"),
        (("ukpga", "Geo6", "12-13-14", "1"), "12, 13 & 14 Geo. 6 c. 1"),
        (("ukpga", "WillandMar", "2", "5"), "2 Will. & Mar. c. 5"),
    ],
)
def test_regnal_citation(instrument: tuple[str, ...], text: str) -> None:
    assert regnal_citation(instrument) == text


def test_official_citation_and_name() -> None:
    assert official_citation("uk/ukpga/1996/18/s1") == "1996 c. 18"
    assert official_citation("uk/uksi/2011/3006") == "SI 2011/3006"
    assert official_citation("uk/wsi/2013/2729") == "SI 2013/2729"
    assert instrument_name("uk/uksi/2011/3006", {}) == "SI 2011/3006"
    assert instrument_name("uk/ukpga/1996/18", {"title": "  Employment   Rights Act 1996 "}) == (
        "Employment Rights Act 1996"
    )


# ---------------------------------------------------------------------------- URLs


def test_url_prefers_the_record_source_url(fixture_text_store: Any) -> None:
    rows = fixture_text_store.get("uk/ukpga/1996/18/s124/1ZA/a")
    instrument, provision = rows[0], rows[1]
    citation = render_citation("P2", provision["coordinate"], instrument, provision)
    assert citation.url == provision["source_url"]
    assert citation.url == "https://www.legislation.gov.uk/ukpga/1996/18/section/124/1ZA/a"
    assert citation.label == "s.124(1ZA)(a) Employment Rights Act 1996"
    # When the record's URL differs from the built one, the record wins.
    moved = {**provision, "source_url": "https://www.legislation.gov.uk/ukpga/1996/18/section/124"}
    assert (
        render_citation("P2", provision["coordinate"], instrument, moved).url
        == (moved["source_url"])
    )
    no_url = {k: v for k, v in provision.items() if k != "source_url"}
    assert (
        render_citation("P2", provision["coordinate"], instrument, no_url).url
        == (provision["source_url"])
    )


def test_instrument_citation_uses_instrument_url(fixture_text_store: Any) -> None:
    record = fixture_text_store.instrument("uk/ukpga/1980/15")
    citation = render_citation("P1", "uk/ukpga/1980/15", record)
    assert citation.url == "https://www.legislation.gov.uk/ukpga/1980/15"
    assert citation.label == "National Health Service (Invalid Direction) Act 1980"


def test_foreign_source_url_is_ignored() -> None:
    record = {"coordinate": "uk/ukpga/1996/18/s1", "source_url": "https://example.com/x"}
    assert record_url("uk/ukpga/1996/18/s1", record) == (
        "https://www.legislation.gov.uk/ukpga/1996/18/section/1"
    )


def test_dated_url() -> None:
    url = provision_url("uk/ukpga/1996/18/s124")
    assert dated_url(url, date(2020, 4, 6)) == (
        "https://www.legislation.gov.uk/ukpga/1996/18/section/124/2020-04-06"
    )
    assert dated_url(url + "/", date(2020, 4, 6)).endswith("/124/2020-04-06")


@pytest.mark.parametrize(
    ("coordinate", "instrument", "provision"),
    [
        ("uk/ukpga/1996/18/s124", {"coordinate": "uk/ukpga/2008/7"}, None),
        ("uk/ukpga/1996/18/s124", ERA, {"coordinate": "uk/ukpga/1996/18/s125"}),
        ("es/boe/1885/6627/art42", {}, None),
        ("not a coordinate", {}, None),
    ],
)
def test_render_rejects_mismatches(
    coordinate: str, instrument: dict[str, Any], provision: dict[str, Any] | None
) -> None:
    with pytest.raises(ValueError, match=r".+"):
        render_citation("P1", coordinate, instrument, provision)


def test_every_fixture_provision_renders() -> None:
    for instrument, provision in FIXTURE_ROWS:
        coordinate = provision["coordinate"]
        citation = render_citation("P1", coordinate, instrument, provision)
        assert citation.url == provision["source_url"]
        assert citation.label.endswith(instrument["title"])
        assert citation.label.startswith(provision_label(coordinate))


# ---------------------------------------------------------------------------- properties


@settings(max_examples=200, deadline=None)
@given(st.sampled_from(FIXTURE_ROWS))
def test_fixture_coordinates_round_trip(rows: tuple[dict[str, Any], dict[str, Any]]) -> None:
    instrument, provision = rows
    coordinate = provision["coordinate"]
    segments = tuple(coordinate_tail(coordinate).split("/"))
    built = provision_url(coordinate)
    assert built.startswith(LEGISLATION_BASE + instrument["coordinate"][3:] + "/")
    path = built.removeprefix(LEGISLATION_BASE + instrument["coordinate"][3:] + "/")
    assert provision_from_legislation_tokens(path.split("/")) == segments
    label = render_citation("P9", coordinate, instrument).label
    assert label == f"{provision_label(coordinate)} {instrument['title']}"


_UNITS = st.sampled_from(["s", "art", "reg", "rule", "para", "sch", "pt", "ch"])
_DESIGNATOR = st.from_regex(r"[1-9][0-9]{0,2}[A-Z]{0,2}", fullmatch=True)
_SUBDIVISION = st.one_of(
    st.from_regex(r"[1-9][0-9]?[A-Z]{0,2}", fullmatch=True),
    st.sampled_from(["a", "b", "aa", "za", "i", "ii", "iv", "xi"]),
)


@settings(max_examples=300, deadline=None)
@given(_UNITS, _DESIGNATOR, st.lists(_SUBDIVISION, max_size=4))
def test_synthetic_tails(unit: str, designator: str, subdivisions: list[str]) -> None:
    segments = (unit + designator, *subdivisions)
    coordinate = "uk/ukpga/1996/18/" + "/".join(segments)
    label = provision_label(coordinate)
    assert designator in label
    assert label.endswith("".join(f"({s})" for s in subdivisions) or designator)
    path = provision_url(coordinate).removeprefix(LEGISLATION_BASE + "ukpga/1996/18/")
    assert provision_from_legislation_tokens(path.split("/")) == segments


# ---------------------------------------------------------------------------- real data


@pytest.mark.data
def test_500_real_coordinates_render(real_data_root: Path) -> None:
    rng = random.Random(20261119)
    files = sorted((real_data_root / "uk").glob("*/*/*.jsonl"))
    rendered = 0
    attempts = 0
    while rendered < 500:
        attempts += 1
        assert attempts < 20_000, "too few instruments with text"
        lines = rng.choice(files).read_text(encoding="utf-8").splitlines()
        if len(lines) < 2:
            continue
        instrument = json.loads(lines[0])
        provision = json.loads(rng.choice(lines[1:]))
        coordinate = provision["coordinate"]
        citation = render_citation(f"P{rendered + 1}", coordinate, instrument, provision)
        assert citation.url == provision["source_url"], coordinate
        assert citation.label.startswith(provision_label(coordinate)), coordinate
        assert citation.label.endswith(instrument_name(coordinate, instrument)), coordinate
        rendered += 1
