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

from collections.abc import Collection, Iterable, Mapping
from typing import Any, Final

from legal_rag_verifier.judge import Judge, JudgedVerifier
from legal_rag_verifier.premise import Premise
from legal_rag_verifier.verifier import (
    Reason,
    Repair,
    SentenceVerdict,
    Verdict,
    VerifierConfig,
)

from chapterverse.audit.classify import CLASSIFICATION_SHIFT, classification_failure
from chapterverse.audit.policy import verifier_config
from chapterverse.audit.refs import REF_CODES, check_refs
from chapterverse.contracts import JudgeChat, JudgeReply, Policy, ProviderCall
from chapterverse.windows import passages_by_id

__all__ = [
    "CLASSIFICATION_SHIFT",
    "AuditVerifier",
    "RecordingJudgeChat",
    "make_verifier",
    "reason_codes",
]

AUDIT_CODES: Final = REF_CODES | {CLASSIFICATION_SHIFT}


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


class AuditVerifier(JudgedVerifier):
    """``JudgedVerifier`` with the passage-reference rules checked first (module docstring)."""

    def __init__(self, judge: Judge, passage_ids: Collection[str], config: VerifierConfig) -> None:
        super().__init__(judge, config)
        self.passage_ids = frozenset(passage_ids)

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
        passage_to_pos = {p: i for i, p in enumerate(premise.passages)}
        cited_texts: list[str] = []
        aligned: list[int] = []
        for pid in refs.passage_ids:
            if pid in by_id:
                p = by_id[pid]
                cited_texts.append(p.text)
                if p in passage_to_pos:
                    aligned.append(passage_to_pos[p])
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
        return super().check_sentence(premise, refs.text, query=query)


def make_verifier(
    judge_chat: JudgeChat,
    policy: Policy,
    passage_ids: Collection[str],
    calls: list[ProviderCall],
) -> AuditVerifier:
    """A fresh per-answer verifier whose judge calls are logged into ``calls``."""
    judge = Judge(RecordingJudgeChat(judge_chat, calls))
    judge.preset = policy.judge_preset
    return AuditVerifier(judge, passage_ids, verifier_config(policy))


def reason_codes(reasons: Iterable[str], details: Iterable[str]) -> tuple[str, ...]:
    """The verifier's reason codes, then any reference or classification codes in details."""
    out = [str(r) for r in reasons]
    for detail in details:
        code = detail.split(":", 1)[0]
        if code in AUDIT_CODES and code not in out:
            out.append(code)
    return tuple(out)
