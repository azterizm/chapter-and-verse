"""The judge bridge: a ``Rotation`` behind the verifier judge's chat callable.

:class:`RotationJudgeChat` implements :class:`~chapterverse.contracts.JudgeChat` (ARCHITECTURE
§4.5) for ``legal_rag_verifier.judge.Judge(chat)``. Each call sends a ``purpose="judge"`` request
with the measured ``qwen27-ar4`` sampling and records a ``ProviderCall``. Make one per answer:
its call list is per-instance state, like the ``Judge`` that holds it.

:class:`~chapterverse.contracts.AllProvidersUnavailable` propagates unchanged: ``Judge`` does not
catch it, so an outage becomes extractive mode, never a silent "not stated" on every sentence.
"""

from __future__ import annotations

from chapterverse.contracts import JudgeReply, ProviderCall, Rotation
from chapterverse.providers.presets import MODAL_MODEL_ID, judge_request

__all__ = ["RotationJudgeChat"]


class RotationJudgeChat:
    """``chat(messages) -> JudgeReply`` over a rotation. Not thread-safe (per answer).

    ``model`` is what ``Judge`` records as its ``model_id``: the measured preset's model and
    revision by default. The model each call actually used is in :attr:`calls`.
    """

    def __init__(self, rotation: Rotation, *, model: str = MODAL_MODEL_ID) -> None:
        self.rotation = rotation
        self._model = model
        self.calls: list[ProviderCall] = []

    def __repr__(self) -> str:
        return f"RotationJudgeChat(model={self._model!r}, calls={len(self.calls)})"

    @property
    def model(self) -> str:
        return self._model

    def __call__(self, messages: list[dict[str, str]], /) -> JudgeReply:
        """One judge call.

        Raises:
            AllProvidersUnavailable: no provider could serve (not caught by ``Judge``).
            ValueError, KeyError: a malformed message (``Judge`` counts it a failed attempt).
        """
        result = self.rotation.complete(judge_request(messages))
        self.calls.append(ProviderCall.from_result(result, "judge"))
        return JudgeReply(
            content=result.text,
            ms=result.latency_ms,
            tokens=result.completion_tokens,
            finish=result.finish_reason,
        )
