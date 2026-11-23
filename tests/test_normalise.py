"""Tests for slice 2 query normalisation (specs/s2-normalise/requirements.md).

Covers R1, R2, R4, R5, and R6.
"""

from __future__ import annotations

import importlib.util
import logging
import time
from pathlib import Path
from types import ModuleType

import pytest
from symspellpy import SymSpell  # type: ignore[import-untyped]

from chapterverse.normalise import Normalised, load_dictionary, normalise

DICT_PATH = Path("artefacts/symspell/dictionary.txt")
BUILD_DIR = Path(__file__).resolve().parents[1] / "build"


def load_build_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"build_{name}", BUILD_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def symspell_dict(tmp_path_factory: pytest.TempPathFactory) -> SymSpell:
    """Provide a loaded SymSpell dictionary.

    Uses artefacts/symspell/dictionary.txt if built, otherwise builds from fixtures
    and supplements test vocabulary.
    """
    if DICT_PATH.is_file():
        return load_dictionary(DICT_PATH)

    tmp_dir = tmp_path_factory.mktemp("symspell")
    fixture_concepts = Path("tests/fixtures/data/concepts")
    if fixture_concepts.is_dir():
        build_mod = load_build_script("symspell_dict")
        build_mod.build_dictionary(fixture_concepts, tmp_dir)
        sym = load_dictionary(tmp_dir / "dictionary.txt")
    else:
        sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)

    # Ensure standard test vocabulary is present
    test_words = {
        "unfair": 2000,
        "dismissal": 200,
        "employment": 1000,
        "rights": 800,
        "act": 50000,
        "compensatory": 50,
        "award": 5000,
        "cap": 1000,
        "order": 10000,
        "section": 5000,
        "tribunal": 1000,
        "redundancy": 5000,
        "pay": 10000,
    }
    for word, count in test_words.items():
        sym.create_dictionary_entry(word, count)
    return sym


def test_r6_unfiar_dismisal(symspell_dict: SymSpell) -> None:
    """R6: 'unfiar dismisal' corrected to 'unfair dismissal'."""
    res = normalise("unfiar dismisal", dictionary=symspell_dict)
    assert res.text == "unfair dismissal"
    assert res.corrections == (("unfiar", "unfair"), ("dismisal", "dismissal"))
    assert res.masked_spans == ()


def test_r6_s124_era_1996_untouched(symspell_dict: SymSpell) -> None:
    """R6: 's.124 ERA 1996' untouched."""
    q = "s.124 ERA 1996"
    res = normalise(q, dictionary=symspell_dict)
    assert res.text == q
    assert res.corrections == ()
    # Spans for s.124 (0, 5), ERA (6, 9), 1996 (10, 14) are protected
    assert len(res.masked_spans) == 3
    assert res.masked_spans == ((0, 5), (6, 9), (10, 14))


def test_r6_si_2011_9999_untouched(symspell_dict: SymSpell) -> None:
    """R6: 'SI 2011/9999' untouched."""
    q = "SI 2011/9999"
    res = normalise(q, dictionary=symspell_dict)
    assert res.text == q
    assert res.corrections == ()
    assert res.masked_spans == ((0, 12),)


def test_r6_employmnet_rights_act_1996_section_98(symspell_dict: SymSpell) -> None:
    """R6: 'Employmnet Rights Act 1996 section 98' corrects only 'Employmnet'."""
    q = "Employmnet Rights Act 1996 section 98"
    res = normalise(q, dictionary=symspell_dict)
    assert res.text == "Employment Rights Act 1996 section 98"
    assert res.corrections == (("Employmnet", "Employment"),)
    # 1996 at (22, 26) and section 98 at (27, 37) are protected
    assert (22, 26) in res.masked_spans
    assert (27, 37) in res.masked_spans


def test_r6_acronyms_untouched(symspell_dict: SymSpell) -> None:
    """R2/R6: ALL CAPS 2-6 letter acronyms such as ERA, TUPE, PACE untouched."""
    q = "claims under TUPE and PACE regarding unfiar dismissal"
    res = normalise(q, dictionary=symspell_dict)
    assert res.text == "claims under TUPE and PACE regarding unfair dismissal"
    assert res.corrections == (("unfiar", "unfair"),)
    tupe_span = (q.index("TUPE"), q.index("TUPE") + 4)
    pace_span = (q.index("PACE"), q.index("PACE") + 4)
    assert tupe_span in res.masked_spans
    assert pace_span in res.masked_spans


def test_r6_numbers_untouched(symspell_dict: SymSpell) -> None:
    """R2/R6: tokens containing digits (years, amounts, sections) are untouched."""
    q = "award cap £500,000 in 2026 for 100m race"
    res = normalise(q, dictionary=symspell_dict)
    assert res.text == q
    assert res.corrections == ()
    assert len(res.masked_spans) >= 3


def test_r6_quoted_text_untouched(symspell_dict: SymSpell) -> None:
    """R2: Quoted text is protected and never corrected."""
    q = "do not correct \"unfiar dismisal\" or “dismisal” or 'unfiar'"
    res = normalise(q, dictionary=symspell_dict)
    assert '"unfiar dismisal"' in res.text
    assert "“dismisal”" in res.text
    assert "'unfiar'" in res.text
    assert res.corrections == ()


def test_r6_identifiers_untouched(symspell_dict: SymSpell) -> None:
    """R2: scan_identifiers matches are protected."""
    url = "https://www.legislation.gov.uk/ukpga/1996/18/section/124"
    q = f"check {url} for details"
    res = normalise(q, dictionary=symspell_dict)
    assert url in res.text
    assert (6, 6 + len(url)) in res.masked_spans


def test_r4_casing_preserved(symspell_dict: SymSpell) -> None:
    """R4: casing pattern is preserved (for words > 6 letters; 2-6 letter all-caps are acronyms)."""
    assert normalise("unfiar", dictionary=symspell_dict).text == "unfair"
    assert normalise("Unfiar", dictionary=symspell_dict).text == "Unfair"
    assert normalise("DISMISAL", dictionary=symspell_dict).text == "DISMISSAL"
    assert normalise("Employmnet", dictionary=symspell_dict).text == "Employment"
    assert normalise("EMPLOYMNET", dictionary=symspell_dict).text == "EMPLOYMENT"


def test_r4_short_words_untouched(symspell_dict: SymSpell) -> None:
    """R4: words of 3 letters or fewer are never changed."""
    q = "the act and law in uk"
    res = normalise(q, dictionary=symspell_dict)
    assert res.text == q
    assert res.corrections == ()


def test_r4_tie_not_corrected(tmp_path: Path) -> None:
    """R4: word not corrected if best suggestion is not unique at smallest distance."""
    dict_file = tmp_path / "dict.txt"
    # two suggestions at distance 1 with equal count
    dict_file.write_text("bat 100\ncat 100\n", encoding="utf-8")
    sym = load_dictionary(dict_file)

    res = normalise("xat", dictionary=sym)
    assert res.text == "xat"
    assert res.corrections == ()


def test_r4_miss_rate_logging(symspell_dict: SymSpell, caplog: pytest.LogCaptureFixture) -> None:
    """R4: log miss rate at debug level per call, never the words."""
    caplog.set_level(logging.DEBUG, logger="chapterverse.normalise")
    # "xyzqqq" is an unknown word with no suggestions
    res = normalise("xyzqqq unfiar", dictionary=symspell_dict)
    assert res.text == "xyzqqq unfair"

    debug_records = [r for r in caplog.records if r.name == "chapterverse.normalise"]
    assert len(debug_records) >= 1
    msg = debug_records[-1].getMessage()
    assert "miss_rate" in msg
    # Crucial: word itself must never appear in the log message
    assert "xyzqqq" not in msg
    assert "unfiar" not in msg


def test_r6_empty_input(symspell_dict: SymSpell) -> None:
    """R6: empty input handled safely."""
    res = normalise("", dictionary=symspell_dict)
    assert res == Normalised(text="", corrections=(), masked_spans=())


def test_r6_4kb_input(symspell_dict: SymSpell) -> None:
    """R6: 4 KB inputs handled deterministically and without error."""
    text_4kb = ("unfiar dismisal under s.124 ERA 1996 with TUPE. " * 100)[:4096]
    assert len(text_4kb) == 4096

    res = normalise(text_4kb, dictionary=symspell_dict)
    assert len(res.text) > 0
    assert "s.124 ERA 1996" in res.text
    assert "TUPE" in res.text
    assert "unfiar" not in res.text


def test_r6_determinism(symspell_dict: SymSpell) -> None:
    """R6: repeated runs produce identical outputs."""
    q = "What is unfiar dismisal compensatory award cap under s.124 ERA 1996?"
    first = normalise(q, dictionary=symspell_dict)
    for _ in range(5):
        assert normalise(q, dictionary=symspell_dict) == first


def test_r5_latency_under_5ms(symspell_dict: SymSpell) -> None:
    """R5: p99 under 5 ms for a 200-character query on this Mac after load."""
    base_query = (
        "What is the compensatory award cap for unfiar dismisal under s.124 ERA 1996 "
        "and how does SI 2011/9999 apply to an Employmnet Rights Act 1996 claim when "
        "PACE or TUPE applies in year 2026 for any worker here?"
    )
    query_200 = base_query[:200]
    assert len(query_200) == 200

    # Warm up
    for _ in range(20):
        normalise(query_200, dictionary=symspell_dict)

    latencies: list[float] = []
    for _ in range(200):
        t0 = time.perf_counter()
        normalise(query_200, dictionary=symspell_dict)
        latencies.append((time.perf_counter() - t0) * 1000)

    latencies.sort()
    p99 = latencies[int(len(latencies) * 0.99)]
    assert p99 < 5.0, f"p99 latency was {p99:.3f} ms, expected under 5.0 ms"


def test_dictionary_builder_fixture(tmp_path: Path) -> None:
    """R3: verify build_dictionary outputs dictionary.txt and manifest.json."""
    fixture_concepts = Path("tests/fixtures/data/concepts")
    build_mod = load_build_script("symspell_dict")
    term_count, file_size, elapsed, sha256_digest = build_mod.build_dictionary(
        fixture_concepts, tmp_path
    )
    assert term_count > 0
    assert file_size > 0
    assert elapsed >= 0.0
    assert len(sha256_digest) == 64

    dict_file = tmp_path / "dictionary.txt"
    manifest_file = tmp_path / "symspell-manifest.json"
    assert dict_file.is_file()
    assert manifest_file.is_file()


def test_r2_never_correct_when_original_in_dictionary(symspell_dict: SymSpell) -> None:
    """R2: never apply a correction when the original word is in the dictionary."""
    words = [
        "dismissed",
        "automatically",
        "unfairly",
        "because",
        "puts",
        "unwanted",
        "violating",
        "arrested",
        "defrauding",
        "induced",
        "refused",
        "unmarried",
    ]
    for w in words:
        res = normalise(f"check {w} here", dictionary=symspell_dict)
        assert w in res.text
        assert res.corrections == ()


def test_r2_same_first_5_letters_ending_difference(tmp_path: Path) -> None:
    """R2: never apply correction when original and suggestion share same first 5 letters

    and differ only in ending, unless original has no dictionary entry, distance is 1, and len >= 6.
    """
    dict_file = tmp_path / "dict_r2_endings.txt"
    dict_file.write_text(
        "dismisses 1000\n"
        "violation 1000\n"
        "defraying 1000\n"
        "employment 1000\n"
        "dismissal 1000\n"
        "orders 1000\n",
        encoding="utf-8",
    )
    sym = load_dictionary(dict_file)

    # violating -> violation differs by 2 edits: rejected
    assert normalise("violating", dictionary=sym).text == "violating"

    # defrauding -> defraying differs by 2 edits: rejected
    assert normalise("defrauding", dictionary=sym).text == "defrauding"

    # 5-letter word not in dict sharing first 5 letters cannot be corrected (requires len >= 6)
    # "order" (5 letters) sharing first 5 letters with "orders" (6 letters) is rejected
    assert normalise("order", dictionary=sym).text == "order"

    # employmnet -> employment (len 10, dist 1, not in dict): allowed
    assert normalise("employmnet", dictionary=sym).text == "employment"

    # dismisal -> dismissal (len 8, dist 1, not in dict): allowed
    assert normalise("dismisal", dictionary=sym).text == "dismissal"


def test_r2_negating_prefix_protection(tmp_path: Path) -> None:
    """R2: never apply a correction that adds or removes a negating prefix."""
    dict_file = tmp_path / "dict_r2_prefix.txt"
    dict_file.write_text(
        "married 5000\n"
        "unmarried 50\n"
        "unfair 5000\n"
        "domestic 5000\n"
        "agree 5000\n"
        "lawful 5000\n"
        "active 5000\n"
        "legal 5000\n"
        "proper 5000\n"
        "relevant 5000\n"
        "leading 5000\n",
        encoding="utf-8",
    )
    sym = load_dictionary(dict_file)

    # unmarried never corrected to married (removes un)
    assert normalise("unmarried", dictionary=sym).text == "unmarried"

    # married never corrected to unmarried (adds un)
    assert normalise("married", dictionary=sym).text == "married"

    # non-domestic never corrected to domestic (removes non)
    assert normalise("non-domestic", dictionary=sym).text == "non-domestic"

    # disagree never corrected to agree (removes dis)
    assert normalise("disagree", dictionary=sym).text == "disagree"

    # unfiar still corrected to unfair (negating prefix neither added nor removed)
    assert normalise("unfiar", dictionary=sym).text == "unfair"


def test_r2_capitalised_words_in_middle_of_sentence(symspell_dict: SymSpell) -> None:
    """R2: never correct capitalised words in the middle of a sentence."""
    # Ravensbourne is in middle of sentence, capitalised: must not be corrected
    q1 = "claims under the Ravensbourne Tenancies Act"
    assert normalise(q1, dictionary=symspell_dict).text == q1

    # Smith is in middle of sentence: must not be corrected; unfiar is lowercase and corrected
    q2 = "Did Smith receive unfiar dismissal?"
    assert normalise(q2, dictionary=symspell_dict).text == "Did Smith receive unfair dismissal?"

    # Employmnet at the start of sentence IS corrected
    q3 = "Employmnet Rights Act 1996 section 98"
    assert normalise(q3, dictionary=symspell_dict).text == "Employment Rights Act 1996 section 98"

    # Unfiar at the start of sentence IS corrected
    q4 = "Unfiar dismissal"
    assert normalise(q4, dictionary=symspell_dict).text == "Unfair dismissal"


def test_r3_real_typos_fixed(symspell_dict: SymSpell) -> None:
    """R3: real typos must still be fixed."""
    res1 = normalise("unfiar dismisal compensatory award cap", dictionary=symspell_dict)
    assert res1.text == "unfair dismissal compensatory award cap"
    assert res1.corrections == (("unfiar", "unfair"), ("dismisal", "dismissal"))

    res2 = normalise("Employmnet Rights Act 1996 section 98", dictionary=symspell_dict)
    assert res2.text == "Employment Rights Act 1996 section 98"
    assert res2.corrections == (("Employmnet", "Employment"),)

    res3 = normalise("redundnacy pay", dictionary=symspell_dict)
    assert res3.text == "redundancy pay"
    assert res3.corrections == (("redundnacy", "redundancy"),)


def test_r1_length_scaled_edit_distance(tmp_path: Path) -> None:
    """R1: allowed edit distance is 0 for <=3 letters, 1 for 4-7 letters, 2 for 8+ letters."""
    dict_file = tmp_path / "dict_r1.txt"
    dict_file.write_text(
        "cat 1000\nword 1000\nunfair 2000\ntribunal 1000\nemployment 1000\ndismissal 2000\n",
        encoding="utf-8",
    )
    sym = load_dictionary(dict_file)

    # <= 3 letters: 0 edits (never corrected)
    assert normalise("ca", dictionary=sym).text == "ca"
    assert normalise("cta", dictionary=sym).text == "cta"

    # 4 to 7 letters: 1 edit allowed
    # "wrod" -> "word" (len 4, dist 1): allowed
    assert normalise("wrod", dictionary=sym).text == "word"
    # "unfiar" -> "unfair" (len 6, dist 1): allowed
    assert normalise("unfiar", dictionary=sym).text == "unfair"

    # 4 to 7 letters: 2 edits NOT allowed
    # "woxx" is 2 edits from "word" (len 4): rejected
    assert normalise("woxx", dictionary=sym).text == "woxx"
    # "unfxxy" is 2 edits from "unfair" (len 6): rejected
    assert normalise("unfxxy", dictionary=sym).text == "unfxxy"

    # 8+ letters: 2 edits allowed
    # "employmnet" -> "employment" (len 10, dist 1): allowed
    assert normalise("employmnet", dictionary=sym).text == "employment"


def test_r2_not_a_word_rejections(symspell_dict: SymSpell) -> None:
    """R2: never correct tokens with digits or no vowels (a,e,i,o,u,y)."""
    # Digits
    assert normalise("plan2", dictionary=symspell_dict).text == "plan2"
    assert normalise("award2026", dictionary=symspell_dict).text == "award2026"

    # No vowels
    assert normalise("zxqv", dictionary=symspell_dict).text == "zxqv"
    assert normalise("bcdfgh", dictionary=symspell_dict).text == "bcdfgh"
    assert normalise("brrr", dictionary=symspell_dict).text == "brrr"

    # 'y' counts as vowel: words with 'y' are not rejected by the vowel check
    res_crypt = normalise("crypt", dictionary=symspell_dict)
    assert "crypt" in res_crypt.text

    # More tokens without vowels: not corrected
    assert normalise("blrxpt", dictionary=symspell_dict).text == "blrxpt"
    assert normalise("zzqqvv", dictionary=symspell_dict).text == "zzqqvv"
    assert (
        normalise("zxqv blorptastic wibbleflange", dictionary=symspell_dict).text
        == "zxqv blorptastic wibbleflange"
    )
    assert normalise("zxqv blorptastic wibbleflange", dictionary=symspell_dict).corrections == ()

    # A real typo is still corrected
    assert normalise("landlrod", dictionary=symspell_dict).text == "landlord"


def test_r3_mandatory_correction_cases(symspell_dict: SymSpell) -> None:
    """R3: verify the specified correction cases.

    - 'unfiar dismisal' -> 'unfair dismissal'
    - 'compensaton' -> not corrected today (it exists in dictionary with count 3; do not force it)
    - 'tribunel' -> 'tribunal'
    - 'landlrod' -> 'landlord'
    - 'employmnet' -> 'employment'
    - 'discrimnation' -> 'discrimination'
    """
    assert normalise("unfiar dismisal", dictionary=symspell_dict).text == "unfair dismissal"
    assert normalise("tribunel", dictionary=symspell_dict).text == "tribunal"
    assert normalise("landlrod", dictionary=symspell_dict).text == "landlord"
    assert normalise("employmnet", dictionary=symspell_dict).text == "employment"
    assert normalise("discrimnation", dictionary=symspell_dict).text == "discrimination"

    # compensaton is present in dictionary with count 3, so it is untouched today and not forced
    res_comp = normalise("compensaton", dictionary=symspell_dict)
    assert res_comp.text == "compensaton"
    assert res_comp.corrections == ()
