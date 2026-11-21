"""Unit tests for engine/chapterverse/nlp.py."""

from __future__ import annotations

from chapterverse.nlp import Analysis, Cues, Entity, analyse, get_nlp


def test_lazy_load_nlp() -> None:
    nlp = get_nlp()
    assert nlp is not None
    # Subsequent call returns same instance
    assert get_nlp() is nlp


def test_analyse_empty() -> None:
    res = analyse("")
    assert res.noun_chunks == ()
    assert res.terms == ()
    assert res.intent_spans == ()
    assert res.entities == ()
    assert res.cues == Cues(has_date=False, has_negation=False, has_existence_question=False)
    assert res.protected_spans == ()
    assert res.doc is None


def test_analyse_basic_terms_and_noun_chunks() -> None:
    text = "Can my employer Acme Ltd dismiss John Smith for whistleblowing?"
    res = analyse(text)
    assert isinstance(res, Analysis)
    # Check noun chunks contain expected phrases
    chunk_texts = [c.lower() for c in res.noun_chunks]
    assert any("employer" in c for c in chunk_texts)
    assert any("acme ltd" in c for c in chunk_texts)
    assert any("john smith" in c for c in chunk_texts)

    # Check terms contain unigrams and bigrams of NOUN, PROPN, ADJ, NUM
    assert "employer" in res.terms
    assert "acme" in res.terms
    assert "john smith" in res.terms
    # Preposition 'for' or verb 'dismiss' should not bridge across to form bigram
    assert "ltd john" not in res.terms


def test_analyse_intent_split_clausal_conjunction() -> None:
    text = (
        "Can my employer Acme Ltd dismiss John Smith for whistleblowing "
        "and what is the statutory cap on compensation?"
    )
    res = analyse(text)
    assert len(res.intent_spans) == 2
    assert res.intent_spans[0] == "Can my employer Acme Ltd dismiss John Smith for whistleblowing"
    assert res.intent_spans[1] == "what is the statutory cap on compensation?"


def test_analyse_intent_no_split_on_noun_conjunction() -> None:
    text = "I want to know about section 98 and section 99."
    res = analyse(text)
    assert len(res.intent_spans) == 1
    assert res.intent_spans[0] == "I want to know about section 98 and section 99."


def test_analyse_intent_comma_and_split() -> None:
    text = "Is there a law on whistleblowing, and does it protect contractors?"
    res = analyse(text)
    assert len(res.intent_spans) == 2
    assert res.intent_spans[0] == "Is there a law on whistleblowing"
    assert res.intent_spans[1] == "does it protect contractors?"


def test_analyse_entities() -> None:
    text = "Can my employer Acme Ltd dismiss John Smith for whistleblowing?"
    res = analyse(text)
    entity_map = {e.text: e.label for e in res.entities}
    assert "Acme Ltd" in entity_map
    assert entity_map["Acme Ltd"] in ("ORG", "FAC")
    assert "John Smith" in entity_map
    assert entity_map["John Smith"] == "PERSON"
    for e in res.entities:
        assert isinstance(e, Entity)
        start, end = e.span
        assert text[start:end] == e.text


def test_analyse_r6_sentence_initial_person_and_ni() -> None:
    # "Email" on its own at start of sentence is not emitted as PERSON
    text_email = "Email me at alice@test.co.uk."
    res_email = analyse(text_email)
    assert not any(e.text == "Email" and e.label == "PERSON" for e in res_email.entities)

    # Multi-token PERSON span at start of sentence is preserved
    text_multi = "John Smith was dismissed by Acme Ltd."
    res_multi = analyse(text_multi)
    assert any(e.text == "John Smith" and e.label == "PERSON" for e in res_multi.entities)

    # Standalone NI is not emitted as entity
    text_ni = "The dispute occurred in NI yesterday."
    res_ni = analyse(text_ni)
    assert not any(e.text.strip().upper() == "NI" for e in res_ni.entities)


def test_analyse_protected_citation_spans() -> None:
    text = "Under Equality Act 2010 s.39, discrimination in employment is unlawful."
    citation = "Equality Act 2010 s.39"
    start = text.index(citation)
    end = start + len(citation)

    res = analyse(text, protected_spans=[(start, end)])
    assert res.doc is not None
    # Token for citation should be merged into a single token
    token_texts = [t.text for t in res.doc]
    assert citation in token_texts


def test_analyse_cues_date() -> None:
    # Year in text
    res = analyse("Was this enacted on 1 January 2020?")
    assert res.cues.has_date is True

    # Year inside protected citation span should not trigger has_date
    text = "Equality Act 2010 s.39 is the relevant section."
    cit = "Equality Act 2010 s.39"
    s = text.index(cit)
    e = s + len(cit)
    res_protected = analyse(text, protected_spans=[(s, e)])
    assert res_protected.cues.has_date is False


def test_analyse_cues_negation() -> None:
    assert analyse("Can an employer not pay notice pay?").cues.has_negation is True
    assert analyse("Acme Ltd never paid the statutory bonus.").cues.has_negation is True
    assert analyse("Employee didn't receive notice.").cues.has_negation is True
    assert analyse("This was decided without notice.").cues.has_negation is True
    assert analyse("Can an employer dismiss John Smith?").cues.has_negation is False


def test_analyse_cues_existence() -> None:
    assert analyse("Is there a law on whistleblowing?").cues.has_existence_question is True
    assert analyse("Is there an exemption for small employers").cues.has_existence_question is True
    assert analyse("Does a right to notice exist in UK law?").cues.has_existence_question is True
    assert analyse("Does whistleblowing protection exist").cues.has_existence_question is True
    assert analyse("Can my employer dismiss me?").cues.has_existence_question is False
