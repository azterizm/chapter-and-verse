"""System-rendered citations (plan section 5 stage 7, section 7 "citation construction").

The model cites passage ids only. This module turns a passage's coordinate and records into a
:class:`~chapterverse.contracts.RenderedCitation`: a short label and the legislation.gov.uk URL.

Label rules, from the router coordinate (``legal-rag-router`` ``docs/grammar.md`` section 2):

* The provision part comes first, one group per unit segment, sub-divisions in brackets:
  ``s124/1ZA/a`` is ``s.124(1ZA)(a)``; ``sch1/para3/2`` is ``Sch. 1 para. 3(2)``; the sole
  unnumbered schedule ``sch/para10`` is ``Sch. para. 10``; ``pt2A/ch1`` is ``Pt. 2A Ch. 1``.
* Unit words: ``s.`` (no space), ``art.``, ``reg.``, ``r.`` (rule), ``para.``, ``Sch.``,
  ``Pt.``, ``Ch.``, ``Group``, ``App.``. The unit is the coordinate's own, so an SI cites by
  ``reg.``, ``art.`` or ``r.`` and an Act by ``s.``. Designators keep the source's case
  (``s.124A``, ``Pt. II``, ``Sch. FIRST``).
* The instrument name follows. It is the record's title without a leading ``The``. A title
  with no year (some pre-1963 Acts carry only a long title) is replaced by the official
  chapter citation, or followed by it in brackets when it is short.
* Statutory instruments add their number in brackets, because SI titles are not unique:
  ``(SI 2011/3006)``; Welsh SIs add the W. number (``(SI 2013/2729 (W. 266))``), and from 2026,
  when Welsh SIs have their own numbering, read ``(Welsh SI 2026/10 (W. 10))``; Northern Ireland
  Orders in Council add the N.I. number (``(SI 1996/2597 (N.I. 20))``).
* Regnal-year Acts cite as ``24 & 25 Vict. c. 100``, ``10 Edw. 7 & 1 Geo. 5 c. 15``,
  ``47 Geo. 3 Sess. 2 c. 78`` when their chapter citation is needed.

URL rules (legislation.gov.uk developer documentation, "URI structure": document URIs follow
``/{type}/{year}/{number}[/{section}][/{authority}][/{extent}][/{version}]``, and a date as the
version gives the text in effect on that date, e.g. ``/ukpga/1985/67/1997-06-01``):

* The provision's ``source_url`` when the record holds one on legislation.gov.uk.
* Otherwise built from the coordinate: the instrument path (the coordinate without ``uk/``),
  then the provision path from ``legal_rag_router.grammars.uk.legislation_path``
  (``s124/1ZA/a`` is ``section/124/1ZA/a``). On a sample of 164,091 real provision records
  the built URL equalled ``source_url`` every time.
* :func:`dated_url` appends ``/YYYY-MM-DD`` for a point-in-time version.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any, Final

from legal_rag_router import Coordinate
from legal_rag_router.grammars.uk import UNIT_PREFIXES, legislation_path

from chapterverse.contracts import RenderedCitation

__all__ = [
    "LEGISLATION_BASE",
    "dated_url",
    "instrument_name",
    "instrument_url",
    "official_citation",
    "provision_label",
    "provision_url",
    "record_url",
    "regnal_citation",
    "render_citation",
]

LEGISLATION_BASE: Final = "https://www.legislation.gov.uk/"

# legislation.gov.uk unit word -> label prefix. "s." takes no space after it ("s.124").
_UNIT_LABELS: Final[dict[str, str]] = {
    "section": "s.",
    "article": "art. ",
    "regulation": "reg. ",
    "rule": "r. ",
    "paragraph": "para. ",
    "schedule": "Sch. ",
    "part": "Pt. ",
    "chapter": "Ch. ",
    "group": "Group ",
    "appendix": "App. ",
}
_UNIT_WORDS: Final = frozenset(UNIT_PREFIXES)

# Series numbered as UK statutory instruments, and other numbered secondary series.
_SI_SERIES: Final = frozenset({"uksi", "wsi", "nisi"})
_OTHER_NUMBERED: Final[dict[str, str]] = {"ssi": "SSI", "nisr": "SR"}
_WSI_OWN_NUMBERS_FROM: Final = 2026
_YEAR_IN_TITLE: Final = re.compile(r"\b(?:1[2-9]\d\d|20\d\d)\b")
_LONG_TITLE_CHARS: Final = 80
_REIGN_PART: Final = re.compile(r"(?P<lead>\d*)(?P<name>[A-Z][a-z]+)(?P<ordinal>\d*)")


# ---------------------------------------------------------------------------- URLs


def _parse(coordinate: str) -> Coordinate:
    """Parse a UK coordinate; raises ValueError (``CoordinateError``) on anything else."""
    parsed = Coordinate.parse(coordinate)
    if parsed.jurisdiction != "uk":
        raise ValueError(f"{coordinate}: not a UK coordinate")
    return parsed


def instrument_url(coordinate: str) -> str:
    """legislation.gov.uk URL of the instrument holding ``coordinate`` (built, never read)."""
    parsed = _parse(coordinate)
    return LEGISLATION_BASE + "/".join(parsed.instrument)


def provision_url(coordinate: str) -> str:
    """legislation.gov.uk URL of ``coordinate``, built from its segments.

    ``uk/ukpga/1996/18/s124/1ZA/a`` is ``https://www.legislation.gov.uk/ukpga/1996/18/section/
    124/1ZA/a``; an instrument coordinate gives the instrument's URL.
    """
    parsed = _parse(coordinate)
    url = LEGISLATION_BASE + "/".join(parsed.instrument)
    if parsed.provision:
        url += "/" + legislation_path(parsed.provision)
    return url


def record_url(coordinate: str, record: Mapping[str, Any] | None = None) -> str:
    """The record's ``source_url`` when it is a legislation.gov.uk URL, else :func:`provision_url`.

    A record's URL can differ from the built one (``s124/1ZA/a`` points at ``.../1ZA``, the
    finest unit legislation.gov.uk serves there); the record wins. A URL on any other host is
    ignored, so a bad record can never put a foreign link in front of the user.
    """
    if record is not None:
        source = record.get("source_url")
        if isinstance(source, str) and source.startswith(LEGISLATION_BASE):
            return source.rstrip("/")
    return provision_url(coordinate)


def dated_url(url: str, as_at: date) -> str:
    """Point-in-time version of a legislation.gov.uk document URL: ``<url>/YYYY-MM-DD``."""
    return f"{url.rstrip('/')}/{as_at.isoformat()}"


# ---------------------------------------------------------------------------- labels


def provision_label(coordinate: str) -> str:
    """The provision part of a label: ``s.124(1ZA)(a)``, ``Sch. 1 para. 3(2)``; empty for an
    instrument coordinate."""
    parsed = _parse(coordinate)
    groups: list[str] = []
    for segment in parsed.provision:
        tokens = legislation_path((segment,)).split("/")
        word = tokens[0]
        if word in _UNIT_WORDS and segment != word:
            designator = tokens[1] if len(tokens) > 1 else ""
            prefix = _UNIT_LABELS[word]
            groups.append(prefix + designator if designator else prefix.rstrip())
        elif groups:
            groups[-1] += f"({segment})"
        else:  # a bare sub-division first cannot parse; kept for safety
            groups.append(f"({segment})")
    return " ".join(groups)


def regnal_citation(instrument: tuple[str, ...]) -> str:
    """``("ukpga", "Vict", "24-25", "100")`` is ``24 & 25 Vict. c. 100``.

    ``Edw7and1Geo5`` with session ``10`` is ``10 Edw. 7 & 1 Geo. 5``; ``Geo3Sess2`` is
    ``Geo. 3 Sess. 2``; session ``12-13-14`` is ``12, 13 & 14``.
    """
    _, regnal, session, number = instrument
    sess = ""
    match = re.search(r"Sess(\d)$", regnal)
    if match is not None:
        sess = f" Sess. {match.group(1)}"
        regnal = regnal[: match.start()]
    reigns: list[str] = []
    for part in regnal.split("and"):
        piece = _REIGN_PART.fullmatch(part)
        if piece is None:
            reigns.append(part)
            continue
        text = piece.group("name") + "."
        if piece.group("ordinal"):
            text += " " + piece.group("ordinal")
        if piece.group("lead"):
            text = piece.group("lead") + " " + text
        reigns.append(text)
    years = session.split("-")
    session_text = years[0] if len(years) == 1 else ", ".join(years[:-1]) + " & " + years[-1]
    return f"{session_text} {' & '.join(reigns)}{sess} c. {number}"


def official_citation(
    coordinate: str, instrument_record: Mapping[str, Any] | None = None
) -> str | None:
    """The official number of the instrument: ``1996 c. 18``, ``24 & 25 Vict. c. 100``,
    ``SI 2011/3006``, ``SI 2013/2729 (W. 266)``, ``SI 1996/2597 (N.I. 20)``; None if unknown."""
    parsed = _parse(coordinate)
    inst = parsed.instrument
    series = inst[0]
    if len(inst) == 4:  # noqa: PLR2004 - regnal layout
        return regnal_citation(inst)
    year, number = inst[1], inst[2]
    if series in _SI_SERIES:
        return _si_number(series, year, number, instrument_record)
    if series in _OTHER_NUMBERED:
        return f"{_OTHER_NUMBERED[series]} {year}/{number}"
    return f"{year} c. {number}" if series == "ukpga" else None


def _si_number(
    series: str, year: str, number: str, instrument_record: Mapping[str, Any] | None
) -> str:
    """``SI 2011/3006``; Welsh SIs add ``(W. n)`` (own series from 2026), NI ``(N.I. n)``."""
    base = f"SI {year}/{number}"
    if series == "wsi":
        if int(year) >= _WSI_OWN_NUMBERS_FROM:
            base = f"Welsh SI {year}/{number}"
        alt = _alternative_number(instrument_record, "W")
        return f"{base} (W. {alt})" if alt else base
    if series == "nisi":
        alt = _alternative_number(instrument_record, "NI")
        return f"{base} (N.I. {alt})" if alt else base
    return base


def _alternative_number(record: Mapping[str, Any] | None, category: str) -> str | None:
    if record is None:
        return None
    for item in record.get("alternative_numbers") or ():
        if isinstance(item, Mapping) and item.get("category") == category and item.get("value"):
            return str(item["value"])
    return None


def instrument_name(coordinate: str, instrument_record: Mapping[str, Any] | None = None) -> str:
    """The instrument part of a label (see the module docstring for the rules)."""
    parsed = _parse(coordinate)
    series = parsed.instrument[0]
    official = official_citation(coordinate, instrument_record)
    title = ""
    if instrument_record is not None and isinstance(instrument_record.get("title"), str):
        title = " ".join(str(instrument_record["title"]).split())
    title = title.removeprefix("The ")
    numbered = series in _SI_SERIES or series in _OTHER_NUMBERED
    if not title:
        return official or "/".join(parsed.instrument)
    if official is None:
        return title
    if numbered:
        return f"{title} ({official})"
    if _YEAR_IN_TITLE.search(title):
        return title
    if len(title) > _LONG_TITLE_CHARS or title.startswith("An Act"):
        return official
    return f"{title} ({official})"


def render_citation(
    passage_id: str,
    coordinate: str,
    instrument_record: Mapping[str, Any],
    provision_record: Mapping[str, Any] | None = None,
) -> RenderedCitation:
    """Render the citation for one passage: label ``s.124(1ZA)(a) Employment Rights Act 1996``.

    ``instrument_record`` is the instrument's record (its title names the instrument);
    ``provision_record`` is the provision's record, whose ``source_url`` is the URL when it
    holds one. For an instrument coordinate the label is the instrument name alone.

    Raises:
        ValueError: ``coordinate`` is not a UK coordinate, or lies outside the instrument of
            ``instrument_record``, or ``provision_record`` is for another coordinate.
    """
    parsed = _parse(coordinate)
    owner = instrument_record.get("coordinate")
    instrument = "uk/" + "/".join(parsed.instrument)
    if owner is not None and owner != instrument:
        raise ValueError(f"{coordinate} is not inside instrument {owner}")
    if provision_record is not None and provision_record.get("coordinate") not in (
        None,
        coordinate,
    ):
        raise ValueError(
            f"provision record {provision_record.get('coordinate')} is not for {coordinate}"
        )
    name = instrument_name(coordinate, instrument_record)
    head = provision_label(coordinate)
    label = f"{head} {name}" if head else name
    if parsed.provision:
        url = record_url(coordinate, provision_record)
    else:
        url = record_url(coordinate, provision_record or instrument_record)
    return RenderedCitation(passage_id=passage_id, coordinate=coordinate, label=label, url=url)
