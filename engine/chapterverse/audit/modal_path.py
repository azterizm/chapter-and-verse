"""Verified generation on Modal: the verifier's ``InFlightGenerator`` over SGLang (true rollback).

On Modal the model runs on our own SGLang server, so rollback is at token level: every decode
resubmits the committed tokens and RadixAttention serves them from cache (plan §5 stage 8). The
engine runs with ``steering="inject"`` and the policy's limits and refusal; this module supplies:

* the prompt of :mod:`chapterverse.audit.prompt` (``InFlightGenerator.messages`` is overridden);
* the shared sentence check, :class:`~chapterverse.audit.verify.AuditVerifier` (passage references
  stripped and checked, then the judge on ``judge_chat``);
* injected sources written as ``[P#] text`` with the prompt's passage ids
  (``InFlightGenerator._source_text`` is overridden), and ``STEER_NOTE`` once per answer after a
  passage-reference failure, as on the chat path;
* the mapping of each closed point of the engine's trace to a :class:`SentenceOut`.

Auth. ``SGLangBackend`` (legal-rag-verifier 0.5.0) sends no ``Authorization`` header: ``_http``
posts JSON only and ``connect()`` takes no key (it also GETs ``/get_model_info`` and loads a
``transformers`` tokenizer, which this app does not install). Its constructor does take a
``post`` callable, so :class:`BearerPost` adds ``Authorization: Bearer <MODAL_API_KEY>`` (read from
the environment at each call, never stored) without patching the library.

Bans. With inject steering, a retry that has no new window to inject falls back to a ban, and
``SGLangBackend`` applies a ban through a server-side logit mask whose processor string needs
``torch`` and ``sglang`` on the client (or a prebuilt ``processor=``). When the backend cannot
build one, a ban retry runs unconstrained instead; SGLang decodes greedily, so that retry repeats
the draft and the point is refused once its retries are spent.

Streaming. ``InFlightGenerator.generate_verified`` returns once per answer. Each sentence is
passed to ``on_sentence`` as soon as the engine closes its point; iterating yields the same
sentences after the engine returns. ``AllProvidersUnavailable`` (from the judge or from
:class:`BearerPost`) propagates after the sentences decided before it.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, Final

from legal_rag_verifier.backends.sglang import SGLangBackend
from legal_rag_verifier.engine import (
    INJECT_TEMPLATE,
    Answer,
    Ban,
    Constraint,
    InFlightGenerator,
    Segment,
    Tokens,
    _Draft,
    _Point,
    _Retry,
    _Stream,
)
from legal_rag_verifier.engine import Backend as EngineBackend
from legal_rag_verifier.judge import PRESETS
from legal_rag_verifier.premise import Premise
from legal_rag_verifier.verifier import SentenceVerdict

from chapterverse.audit.narration import is_narration
from chapterverse.audit.prompt import (
    STEER_NOTE,
    PriorTurn,
    Prompt,
    SessionState,
    build_prompt,
    is_abstention,
    render_source,
    window_positions,
)
from chapterverse.audit.refs import REF_CODES, split_refs
from chapterverse.audit.verify import make_verifier, reason_codes
from chapterverse.config import ConfigError, read_secret
from chapterverse.contracts import (
    AllProvidersUnavailable,
    ChatMode,
    JudgeChat,
    Policy,
    ProviderCall,
    ProviderUnavailable,
    SentenceOut,
    UnavailableKind,
    Window,
)

__all__ = [
    "DEFAULT_ANSWER_BUDGET_S",
    "MODAL_QUANTISATION",
    "BearerPost",
    "ModalPathGenerator",
    "ModalSGLangBackend",
]

DEFAULT_ANSWER_BUDGET_S: Final = 25.0

MODAL_QUANTISATION: Final = PRESETS["qwen27-ar4"].quantisation
"""The quantisation of the measured preset the Modal server runs (decision 4)."""

_HTTP_CONFIG = frozenset({400, 401, 403, 404, 405, 422})
_HTTP_RATE = 429


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow no redirect, so the key is never resent to another host."""

    def redirect_request(  # noqa: PLR0917 - urllib's signature
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        del newurl
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


Opener = Callable[[urllib.request.Request, float], Any]
"""``opener(request, timeout)`` returning a response with ``read()``; a context manager."""


def _default_opener() -> Opener:
    director = urllib.request.build_opener(_NoRedirect)

    def open_(request: urllib.request.Request, timeout: float) -> Any:
        return director.open(request, timeout=timeout)

    return open_


class BearerPost:
    """``SGLangBackend``'s ``post``: one POST to ``{url}/generate`` with the Modal key.

    The key is read from ``api_key_env`` at each call (``config.read_secret``) and sent as
    ``Authorization: Bearer``; when the variable is unset no header is sent. Failures become
    ``AllProvidersUnavailable`` with one ``ProviderUnavailable`` for ``provider``: HTTP 401, 403
    and other request errors ``config``, 429 ``rate_limit``, a timeout ``timeout``, anything else
    (5xx, connection errors, a body that is not a JSON object) ``server``.
    """

    def __init__(
        self,
        url: str,
        *,
        api_key_env: str = "MODAL_API_KEY",
        provider: str = "modal",
        timeout: float = 300.0,
        opener: Opener | None = None,
    ) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"not an http(s) URL: {url!r}")
        self.url = url.rstrip("/")
        self.api_key_env = api_key_env
        self.provider = provider
        self.timeout = timeout
        self._open = opener or _default_opener()

    def __repr__(self) -> str:
        return f"BearerPost(url={self.url!r}, api_key_env={self.api_key_env!r})"

    def _fail(self, kind: UnavailableKind, detail: str) -> AllProvidersUnavailable:
        return AllProvidersUnavailable([ProviderUnavailable(self.provider, kind, detail=detail)])

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        try:
            key = read_secret(self.api_key_env)
        except ConfigError as exc:
            raise self._fail("config", str(exc)) from None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(  # noqa: S310 - http(s) checked in __init__
            f"{self.url}/generate",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with self._open(request, self.timeout) as response:
                out = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == _HTTP_RATE:
                raise self._fail("rate_limit", f"HTTP {exc.code}") from None
            kind: UnavailableKind = "config" if exc.code in _HTTP_CONFIG else "server"
            raise self._fail(kind, f"HTTP {exc.code}") from None
        except TimeoutError:
            raise self._fail("timeout", "timed out") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            raise self._fail("server", type(exc).__name__) from None
        if not isinstance(out, dict):
            raise self._fail("server", "the body is not a JSON object")
        return out


class ModalSGLangBackend(SGLangBackend):
    """``SGLangBackend`` with the Modal key on every request and the chat rendered thinking-off.

    ``tokenizer`` is the model's Hugging Face tokenizer, loaded by the caller (``transformers``
    is not a dependency of this app). Thinking is always off here: the engine verifies the whole
    decoded stream, so a thinking block would be audited as answer text.
    """

    name = "modal"

    def __init__(
        self,
        url: str,
        tokenizer: Any,
        *,
        model_id: str,
        eos: Sequence[int],
        api_key_env: str = "MODAL_API_KEY",
        processor: str | None = None,
        timeout: float = 300.0,
        post: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            url,
            tokenizer,
            model_id=model_id,
            eos=eos,
            post=post or BearerPost(url, api_key_env=api_key_env, timeout=timeout),
            processor=processor,
            timeout=timeout,
        )

    def chat(self, messages: Sequence[Mapping[str, str]]) -> Tokens:
        if not getattr(self.tokenizer, "chat_template", None):
            return super().chat(messages)
        encoded = self.tokenizer.apply_chat_template(
            [dict(m) for m in messages],
            add_generation_prompt=True,
            return_dict=True,
            enable_thinking=False,
        )
        return tuple(int(t) for t in encoded["input_ids"])


class _RecordingBackend:
    """A :class:`Backend` that logs one ``ProviderCall`` per ``extend`` call."""

    def __init__(
        self,
        inner: EngineBackend,
        sink: list[ProviderCall],
        *,
        provider: str,
        quantisation: str | None,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.model_id = inner.model_id
        self.sink = sink
        self.provider = provider
        self.quantisation = quantisation

    def chat(self, messages: Sequence[Mapping[str, str]]) -> Tokens:
        return self.inner.chat(messages)

    def encode(self, text: str) -> Tokens:
        return self.inner.encode(text)

    def decode(self, tokens: Sequence[int]) -> str:
        return self.inner.decode(tokens)

    def extend(
        self,
        committed: Sequence[int],
        *,
        stop: Sequence[str],
        max_new: int,
        constraint: Constraint | None,
    ) -> Segment:
        segment = self.inner.extend(committed, stop=stop, max_new=max_new, constraint=constraint)
        self.sink.append(
            ProviderCall(
                provider=self.provider,
                model_id=self.model_id,
                quantisation=self.quantisation,
                purpose="generate",
                prompt_tokens=len(committed),
                completion_tokens=len(segment.tokens),
                latency_ms=segment.latency_ns / 1e6,
            )
        )
        return segment


def _can_ban(backend: EngineBackend) -> bool:
    """False for an ``SGLangBackend`` that cannot build its logit-mask processor here."""
    if not isinstance(backend, SGLangBackend):
        return True
    try:
        backend.processor()
    except ImportError:
        return False
    return True


class _Engine(InFlightGenerator):
    """``InFlightGenerator`` with this app's prompt, source rendering and sentence callback."""

    def __init__(
        self,
        backend: EngineBackend,
        verifier: Any,
        *,
        prompt: Prompt,
        positions: Mapping[int, Window],
        can_ban: bool,
        on_close: Callable[[dict[str, Any]], None],
        started: float | None = None,
        answer_budget_s: float = DEFAULT_ANSWER_BUDGET_S,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(backend, verifier, **kwargs)
        self.prompt = prompt
        self.positions = positions
        self.can_ban = can_ban
        self.on_close = on_close
        self.noted = False
        self._clock = clock if clock is not None else time.perf_counter
        start = started if started is not None else self._clock()
        self._deadline = start + answer_budget_s

    def _timed_out(self) -> bool:
        return self._clock() >= self._deadline

    def messages(self, query: str, premise: Premise) -> list[dict[str, str]]:
        del query, premise
        return [{"role": m["role"], "content": m["content"]} for m in self.prompt.messages]

    def _source_text(
        self, premise: Premise, aligned: Sequence[int], injected: set[int]
    ) -> tuple[str, list[int]]:
        del premise
        fresh = [i for i in dict.fromkeys(aligned) if i in self.positions and i not in injected]
        windows = fresh[: self.inject_windows]
        if not windows:
            return "", []
        source = render_source([self.positions[i] for i in windows])
        tokens = self.backend.encode(source)
        if len(tokens) > self.inject_max_tokens:
            source = self.backend.decode(tokens[: self.inject_max_tokens]).rstrip() + " …"
        return source, windows

    def _steer(
        self,
        point: _Point,
        tokens: Sequence[int],
        verdict: SentenceVerdict,
        premise: Premise,
        stream: _Stream,
        *,
        query: str = "",
    ) -> _Retry:
        failed_refs = any(d.split(":", 1)[0] in REF_CODES for d in verdict.details)
        if failed_refs and not self.noted:
            self.noted = True
            note = list(self.backend.encode(STEER_NOTE))
            stream.context += note
            point.injected = True
            return _Retry([], None, "inject", note, {"windows": [], "tokens": len(note)})
        retry = super()._steer(point, tokens, verdict, premise, stream, query=query)
        if isinstance(retry.constraint, Ban) and not self.can_ban:
            return _Retry(retry.seed, None, "retry")
        return retry

    def _close(  # type: ignore[override]  # the base method is static
        self, point: _Point, outcome: str, text: str
    ) -> dict[str, Any]:
        closed = InFlightGenerator._close(point, outcome, text)
        self.on_close(closed)
        return closed

    def generate_verified(self, query: str, premise: Premise) -> Answer:  # noqa: PLR0912, PLR0915
        t0 = time.perf_counter_ns()
        prompt = self.backend.chat(self.messages(query, premise))
        stream = _Stream()
        carry: _Draft | None = None
        points: list[dict[str, Any]] = []
        total_rollbacks = 0
        stop_reason: str | None = None
        has_refusal = False
        by_id = {w.passage_id: w for w in self.positions.values()}

        while stop_reason is None:
            if self._timed_out():
                stop_reason = "time_budget"
                break
            point = _Point(len(stream.answer))
            retry = _Retry(carry.lookahead if carry else [], None, None)
            end = carry.end if carry else None
            carry = None
            while True:
                if self._timed_out():
                    stop_reason = "time_budget"
                    break
                draft = self._draft(prompt, stream, retry.seed, end, retry.constraint)
                sentence = self.backend.decode(draft.tokens).strip()
                if not sentence and point.rollbacks:
                    if has_refusal:
                        stop_reason = "abstained"
                        break
                    has_refusal = True
                    stream.commit(self._refusal(stream.answer))
                    points.append(self._close(point, "refused", self.refusal))
                    break
                if not sentence:
                    stream.commit(draft.tokens)
                    carry = draft
                    break
                if is_abstention(sentence):
                    if has_refusal:
                        stop_reason = "abstained"
                        break
                    has_refusal = True
                    attempt = {
                        "verdict": {
                            "text": sentence,
                            "span": [0, len(sentence)],
                            "verdict": "refused",
                            "reasons": ["ABSTAINED"],
                            "claims": [],
                            "deontics": [],
                            "aligned_windows": [],
                            "nli": None,
                            "repair": None,
                            "claim_latency_ns": 0,
                            "nli_latency_ns": 0,
                            "details": [],
                        },
                        "steering": retry.steering,
                        "constraint": retry.constraint.to_dict() if retry.constraint else None,
                        "injected": retry.injection,
                        "tokens": len(draft.tokens),
                        "tokens_reused": len(retry.seed),
                        "tokens_discarded": 0,
                        "decode_calls": draft.calls,
                        "prefix_cache_hit_tokens": draft.hit_tokens,
                        "decode_latency_ns": draft.latency_ns,
                    }
                    point.attempts.append(attempt)
                    stream.commit(self._refusal(stream.answer))
                    points.append(self._close(point, "refused", self.refusal))
                    stop_reason = "abstained"
                    break
                text, pids = split_refs(sentence)
                cited = tuple(by_id[p].text for p in pids if p in by_id)
                if is_narration(text, cited):
                    if has_refusal:
                        stop_reason = "abstained"
                        break
                    has_refusal = True
                    attempt = {
                        "verdict": {
                            "text": sentence,
                            "span": [0, len(sentence)],
                            "verdict": "refused",
                            "reasons": ["NARRATION"],
                            "claims": [],
                            "deontics": [],
                            "aligned_windows": [],
                            "nli": None,
                            "repair": None,
                            "claim_latency_ns": 0,
                            "nli_latency_ns": 0,
                            "details": [],
                        },
                        "steering": retry.steering,
                        "constraint": retry.constraint.to_dict() if retry.constraint else None,
                        "injected": retry.injection,
                        "tokens": len(draft.tokens),
                        "tokens_reused": len(retry.seed),
                        "tokens_discarded": 0,
                        "decode_calls": draft.calls,
                        "prefix_cache_hit_tokens": draft.hit_tokens,
                        "decode_latency_ns": draft.latency_ns,
                    }
                    point.attempts.append(attempt)
                    stream.commit(self._refusal(stream.answer))
                    points.append(self._close(point, "refused", self.refusal))
                    stop_reason = "abstained"
                    break
                if self._timed_out():
                    stop_reason = "time_budget"
                    break
                verdict = self.verifier.check_sentence(premise, sentence, query=query)
                attempt = self._attempt(draft, verdict, retry)
                point.attempts.append(attempt)
                if verdict.emitted:
                    if point.injected:
                        stream.answer += self._separator(stream.answer, draft.tokens)
                    stream.commit(draft.tokens)
                    carry = draft
                    points.append(self._close(point, "emitted", sentence))
                    break
                point.rollbacks += 1
                total_rollbacks += 1
                if self._refuses(point, total_rollbacks):
                    if has_refusal:
                        stop_reason = "abstained"
                        break
                    has_refusal = True
                    attempt["tokens_discarded"] = len(draft.tokens) + len(draft.lookahead)
                    stream.commit(self._refusal(stream.answer))
                    points.append(self._close(point, "refused", self.refusal))
                    if total_rollbacks > self.max_total_rollbacks:
                        stop_reason = "rollback_budget"
                    break
                retry = self._steer(point, draft.tokens, verdict, premise, stream, query=query)
                end = None
                kept = len(retry.seed)
                attempt["tokens_discarded"] = len(draft.tokens) + len(draft.lookahead) - kept
            if stop_reason is None and carry is not None and carry.end and not carry.lookahead:
                stop_reason = carry.end
            if stop_reason is None and len(stream.answer) >= self.max_new_tokens:
                stop_reason = "length"
        return Answer(
            self.backend.decode(stream.answer).strip(),
            self._trace(prompt, stream.answer, points, stop_reason, time.perf_counter_ns() - t0),
        )


class ModalPathGenerator:
    """Iterate to get the audited :class:`SentenceOut`s of one answer generated on ``backend``.

    ``backend`` is any verifier ``Backend`` (production: :class:`ModalSGLangBackend`). ``calls``
    holds every decode call (``purpose="generate"``) and judge call in order; ``trace`` is the
    engine's trace and ``stop_reason`` its stop reason once the engine has returned.
    """

    def __init__(
        self,
        backend: EngineBackend,
        judge_chat: JudgeChat,
        policy: Policy,
        *,
        query: str,
        premise: Premise,
        windows: Sequence[Window],
        mode: ChatMode = "short",
        session: SessionState | None = None,
        history: Sequence[PriorTurn] = (),
        provider: str = "modal",
        quantisation: str | None = MODAL_QUANTISATION,
        max_new_tokens: int | None = None,
        on_sentence: Callable[[SentenceOut], None] | None = None,
        started: float | None = None,
        answer_budget_s: float = DEFAULT_ANSWER_BUDGET_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.backend = backend
        self.judge_chat = judge_chat
        self.policy = policy
        self.query = query
        self.premise = premise
        self.prompt = build_prompt(query, windows, mode=mode, session=session, history=history)
        self.positions = window_positions(premise, windows)
        self.provider = provider
        self.quantisation = quantisation
        self.max_new_tokens = max_new_tokens or (512 if mode == "short" else 1536)
        self.on_sentence = on_sentence
        self.started = started
        self.answer_budget_s = answer_budget_s
        self.clock = clock if clock is not None else time.perf_counter
        self.calls: list[ProviderCall] = []
        self.sentences: list[SentenceOut] = []
        self.trace: dict[str, Any] | None = None
        self.stop_reason: str | None = None
        self.has_refusal: bool = False
        self._started = False

    def __iter__(self) -> Iterator[SentenceOut]:
        if self._started:
            raise RuntimeError("a ModalPathGenerator answers once")
        self._started = True
        return self._run()

    def _run(self) -> Iterator[SentenceOut]:
        verifier = make_verifier(self.judge_chat, self.policy, self.prompt.passage_ids, self.calls)
        engine = _Engine(
            _RecordingBackend(
                self.backend, self.calls, provider=self.provider, quantisation=self.quantisation
            ),
            verifier,
            prompt=self.prompt,
            positions=self.positions,
            can_ban=_can_ban(self.backend),
            on_close=self._closed,
            started=self.started,
            answer_budget_s=self.answer_budget_s,
            clock=self.clock,
            max_rollbacks=self.policy.max_rollbacks_per_sentence,
            max_total_rollbacks=self.policy.max_total_rollbacks,
            steering=self.policy.steering,
            refusal=self.policy.refusal_text,
            max_new_tokens=self.max_new_tokens,
            inject_template=INJECT_TEMPLATE,
        )
        error: AllProvidersUnavailable | None = None
        try:
            answer = engine.generate_verified(self.query, self.premise)
        except AllProvidersUnavailable as exc:
            error = exc
        else:
            self.trace = answer.trace
            self.stop_reason = answer.trace.get("stop_reason")
        yield from list(self.sentences)
        if error is not None:
            raise error

    def _closed(self, point: Mapping[str, Any]) -> None:
        attempts = point.get("attempts") or []
        verdict = attempts[-1]["verdict"] if attempts else None
        reasons = reason_codes(verdict["reasons"], verdict["details"]) if verdict else ()
        if point["outcome"] == "emitted":
            text, ids = split_refs(str(point["text"]))
            out = SentenceOut(
                index=len(self.sentences),
                text=text,
                verdict="supported",
                passage_ids=ids,
                reasons=reasons,
                rollbacks=int(point["rollbacks"]),
            )
        else:
            if self.has_refusal:
                return
            self.has_refusal = True
            out = SentenceOut(
                index=len(self.sentences),
                text=self.policy.refusal_text,
                verdict="refused",
                passage_ids=(),
                reasons=reasons,
                rollbacks=int(point["rollbacks"]),
            )
        self.sentences.append(out)
        if self.on_sentence is not None:
            self.on_sentence(out)
