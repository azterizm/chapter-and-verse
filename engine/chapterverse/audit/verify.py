"""The sentence check both generation paths share, and the judge-call log.

:class:`AuditVerifier` is the verifier's :class:`~legal_rag_verifier.judge.JudgedVerifier` with the
passage-reference rules of :mod:`chapterverse.audit.refs` in front: a sentence that fails them is
rolled back without a judge call, and any other sentence is verified with its references
stripped. The rollback verdict carries ``Reason.UNGROUNDED_CITATION`` (the verifier's own enum has
no code for these failures) and names the precise code in ``details`` as ``"<CODE>: <text>"``;
:func:`reason_codes` adds those codes to a sentence's reasons.

:class:`RecordingJudgeChat` wraps a :class:`~chapterverse.contracts.JudgeChat` and logs one
:class:`~chapterverse.contracts.ProviderCall` per call it returns from. A reply may carry the
optional keys ``provider``, ``model_id``, ``quantisation`` and ``prompt_tokens`` (the adapter
knows them from its ``ChatResult``); missing keys are recorded as ``"unknown"``, the chat's
``model``, None and None.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any, Final

from legal_rag_verifier.claims import extract_claims
from legal_rag_verifier.index import build_index
from legal_rag_verifier.judge import Judge, JudgedVerifier
from legal_rag_verifier.premise import Premise
from legal_rag_verifier.verifier import (
    NLIProbs,
    NLIScorer,
    Reason,
    Repair,
    SentenceVerdict,
    Verdict,
    VerifierConfig,
    _in_force_on,
    _nli_candidates,
)

from chapterverse.audit.classify import CLASSIFICATION_SHIFT, classification_failure
from chapterverse.audit.policy import CITED_PASSAGES_CONFIG, verifier_config
from chapterverse.audit.refs import REF_CODES, check_refs
from chapterverse.contracts import JudgeChat, JudgeReply, Policy, ProviderCall
from chapterverse.windows import passages_by_id

__all__ = [
    "CLASSIFICATION_SHIFT",
    "AuditVerifier",
    "RecordingJudgeChat",
    "cited_nli_scorer",
    "make_verifier",
    "reason_codes",
]

log = logging.getLogger(__name__)

AUDIT_CODES: Final = REF_CODES | {CLASSIFICATION_SHIFT}
_UNKEPT_PROBS: Final = NLIProbs(entailment=-1.0, neutral=2.0, contradiction=0.0)


class RecordingJudgeChat:
    """A :class:`JudgeChat` that appends a ``ProviderCall`` to ``sink`` after each reply.

    Exceptions from the wrapped chat propagate unchanged: the judge retries the ones it counts as
    failed attempts, and ``AllProvidersUnavailable`` escapes it.
    """

    def __init__(self, chat: JudgeChat, sink: list[ProviderCall]) -> None:
        self.chat = chat
        self.sink = sink

    @property
    def model(self) -> str:
        return self.chat.model

    def __call__(self, messages: list[dict[str, str]], /) -> JudgeReply:
        reply = self.chat(messages)
        self.sink.append(_judge_call(reply, self.chat.model))
        return reply


def _judge_call(reply: Mapping[str, Any], model: str) -> ProviderCall:
    def opt_int(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def opt_str(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    ms = reply.get("ms")
    return ProviderCall(
        provider=opt_str(reply.get("provider")) or "unknown",
        model_id=opt_str(reply.get("model_id")) or model,
        quantisation=opt_str(reply.get("quantisation")),
        purpose="judge",
        prompt_tokens=opt_int(reply.get("prompt_tokens")),
        completion_tokens=opt_int(reply.get("tokens")),
        latency_ms=float(ms) if isinstance(ms, int | float) and not isinstance(ms, bool) else 0.0,
    )


class _CitedScorer:
    """Restricts ``Verifier._run_nli`` scoring to kept candidates and maps results back."""

    def __init__(
        self,
        inner: NLIScorer,
        expected_texts: tuple[str, ...],
        kept_indices: tuple[int, ...],
        *,
        had_cited: bool,
    ) -> None:
        self._inner = inner
        self.model_id: str = inner.model_id
        self._expected_texts = expected_texts
        self._kept_indices = kept_indices
        self._had_cited = had_cited
        self._warned = False

    def score(self, premises: Sequence[str], hypothesis: str) -> list[NLIProbs]:
        if not self._kept_indices or tuple(premises) != self._expected_texts:
            if not self._warned:
                if not self._had_cited:
                    log.warning("cited judge reached with no cited passage; judging all candidates")
                elif tuple(premises) != self._expected_texts:
                    log.warning("cited judge fell back to all candidates due to candidate mismatch")
                self._warned = True
            return self._inner.score(premises, hypothesis)
        kept_premises = [premises[i] for i in self._kept_indices]
        kept_probs = self._inner.score(kept_premises, hypothesis)
        out = [_UNKEPT_PROBS] * len(premises)
        for orig_idx, prob in zip(self._kept_indices, kept_probs, strict=True):
            out[orig_idx] = prob
        return out


def cited_nli_scorer(
    scorer: NLIScorer,
    premise: Premise,
    sentence: str,
    cited_positions: Collection[int],
    *,
    query: str | None = None,
) -> NLIScorer:
    """Wrap ``scorer`` to score only candidates whose first window is in ``cited_positions``."""
    positions = frozenset(cited_positions)
    if not positions:
        return _CitedScorer(scorer, (), (), had_cited=False)
    index = build_index(premise)
    query_claims = extract_claims(query) if query else []
    when = _in_force_on(index, sentence, query_claims)
    candidates = _nli_candidates(index, when)
    kept = tuple(i for i, (_, win_pos, _) in enumerate(candidates) if win_pos[0] in positions)
    expected = tuple(text for text, _, _ in candidates)
    return _CitedScorer(scorer, expected, kept, had_cited=True)


class AuditVerifier(JudgedVerifier):
    """``JudgedVerifier`` with the passage-reference rules checked first (module docstring)."""

    def __init__(
        self,
        judge: Judge,
        passage_ids: Collection[str],
        config: VerifierConfig,
        *,
        cited_only: bool = False,
    ) -> None:
        super().__init__(judge, config)
        self.passage_ids = frozenset(passage_ids)
        self._cited_only = cited_only

    def check_sentence(
        self, premise: Premise, sentence: str, *, query: str | None = None
    ) -> SentenceVerdict:
        by_id = passages_by_id(premise)
        passage_texts = {pid: p.text for pid, p in by_id.items()}
        passage_coords = {pid: p.coordinate for pid, p in by_id.items() if p.coordinate}
        refs = check_refs(sentence, self.passage_ids, passage_texts, passage_coords)
        if refs.failures:
            return SentenceVerdict(
                refs.text,
                0,
                len(refs.text),
                Verdict.ROLLBACK,
                (Reason.UNGROUNDED_CITATION,),
                aligned=(),
                repair=Repair("ban", 0),
                details=tuple(str(f) for f in refs.failures),
            )
        passage_to_pos = {id(p): i for i, p in enumerate(premise.passages)}
        cited_texts: list[str] = []
        aligned: list[int] = []
        for pid in refs.passage_ids:
            if pid in by_id:
                p = by_id[pid]
                cited_texts.append(p.text)
                pos = passage_to_pos.get(id(p))
                if pos is not None:
                    aligned.append(pos)
        detail = classification_failure(refs.text, cited_texts)
        if detail is not None:
            return SentenceVerdict(
                refs.text,
                0,
                len(refs.text),
                Verdict.ROLLBACK,
                (Reason.UNGROUNDED_CITATION,),
                aligned=tuple(aligned),
                repair=Repair("ban", 0),
                details=(f"{CLASSIFICATION_SHIFT}: {detail}",),
            )
        if self._cited_only:
            self.nli = cited_nli_scorer(self.judge, premise, refs.text, aligned, query=query)
        try:
            return super().check_sentence(premise, refs.text, query=query)
        finally:
            self.nli = self.judge


def make_verifier(
    judge_chat: JudgeChat,
    policy: Policy,
    passage_ids: Collection[str],
    calls: list[ProviderCall],
) -> AuditVerifier:
    """A fresh per-answer verifier whose judge calls are logged into ``calls``."""
    judge = Judge(RecordingJudgeChat(judge_chat, calls))
    judge.preset = policy.judge_preset
    config = verifier_config(policy)
    cited_only = policy.verifier_config_name == CITED_PASSAGES_CONFIG
    return AuditVerifier(judge, passage_ids, config, cited_only=cited_only)


def reason_codes(reasons: Iterable[str], details: Iterable[str]) -> tuple[str, ...]:
    """The verifier's reason codes, then any reference or classification codes in details."""
    out = [str(r) for r in reasons]
    for detail in details:
        code = detail.split(":", 1)[0]
        if code in AUDIT_CODES and code not in out:
            out.append(code)
    return tuple(out)
