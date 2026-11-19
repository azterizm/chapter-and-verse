# Architecture

This file is the map later agents code against. The shared types live in
`engine/chapterverse/contracts.py` and `engine/chapterverse/config.py`. If this file and the
code disagree, the code is right and this file has a bug.

Plan: `plan-extensively-to-build-bright-sunset.md`, sections 5 (read path), 8 (repo and reuse)
and 9 (slices).

## 1. Repository layout

| Path | Contents | Owner |
|---|---|---|
| `engine/chapterverse/` | The Python package `chapterverse` (hatchling, `packages = ["engine/chapterverse"]`) | see section 2 |
| `build/` | Write path: plain scripts, not a package (a package named `build` would shadow PyPA's `build`) | B1 and later |
| `web/` | React frontend | later agent |
| `deploy/` | Dockerfile, Worker, compose file, Postgres schema | later agent |
| `infra/terraform/` | Terraform modules | later agent |
| `batteries/` | Test batteries | later agent |
| `eval/` | Evaluation runners | later agent |
| `tests/` | Unit and integration tests, fixture corpus | A0 (shared files), each agent adds its own test files |
| `scripts/build_fixtures.py` | Rebuilds `tests/fixtures/` from the real data | A0 |
| `docs/ARCHITECTURE.md` | This file | A0 |

Runtime dependencies: `legal-rag-router==0.1.1`, `legal-rag-verifier==0.5.0` (base, stdlib only,
no torch), `fastapi`, `uvicorn[standard]`, `httpx`, `zstandard`, `orjson`, `psycopg[binary]`. Both
legal libraries come from PyPI, pinned in `uv.lock`. CI never installs them from a local path.

## 2. Module map and ownership

Only `contracts.py`, `config.py` and the empty subpackages exist now. The rest is planned.

| Module | Role | Owner |
|---|---|---|
| `contracts.py` | Shared types, canonical JSON, SSE encoding and order | A0 |
| `config.py` | `Settings.from_env()`, `read_secret()` | A0 |
| `store/` | Text-store reader: implements `TextStore` over the store built in `build/` | B1 |
| `status.py` | Temporal status of a bound coordinate (`TemporalStatus`) | B2 |
| `templates.py` | Refusal and notice wording, no LLM (`RefusalEvent`, `NoticeEvent`) | B2 |
| `extractive.py` | Extractive answer: windows with highlights (`ExtractiveEvent`) | B2 |
| `citations.py` | Renders `RenderedCitation` from passage ids | B2 |
| `providers/` | OpenAI-compatible clients for Groq, Cloudflare Workers AI and Modal SGLang (`ChatProvider`), the `Rotation`, circuit breaker, quota ledger, model pinning, judge presets, the `JudgeChat` adapter | B3 |
| `audit/` | Prompt builder, `ChatContinuationGenerator`, the Modal `InFlightGenerator` path, the verified-sentence stream (`SentenceOut`) | B4 |
| `pipeline.py` | Runs the stages per request and emits events | C1 |
| `app.py` | FastAPI app, SSE endpoint | C1 |
| `persistence.py` | Postgres access (quota ledger, research trail) | C1 |
| `ratelimit.py` | Per-IP token bucket | C1 |

An agent edits only the modules it owns. A change to `contracts.py` or `config.py` goes through
the orchestrator, because every other agent codes against them.

## 3. Read path, by module

| Plan stage | Module | Contract types |
|---|---|---|
| Edge: size cap | `app.py` | `Settings.max_query_bytes` |
| Routing (Layer A) | `pipeline.py` with `legal_rag_router.Router.route` | `RouteEvent.from_route_result` |
| Ambiguous | `pipeline.py` | `ClarifyEvent`, `LabelledCoordinate` |
| Refusal | `templates.py` | `RefusalEvent`, `REFUSAL_KINDS` |
| Retrieval (bound) | `store/` | `TextStore.get` |
| Temporal status (stage 6) | `status.py` | `TemporalStatus`, `Window.status` |
| Premise | `pipeline.py` with `Premise.from_records(store.get(c))` | `Window` |
| Generation (stage 7) | `providers/`, `audit/` | `ChatRequest`, `ChatResult`, `Rotation` |
| Audit (stage 8) | `audit/` with `JudgedVerifier` | `JudgeChat`, `Policy`, `SentenceOut` |
| Citations | `citations.py` | `RenderedCitation` |
| Extractive mode | `extractive.py` | `ExtractiveEvent` |
| Receipt (stage 9) | `pipeline.py` | `AnswerFactObject`, `ProviderCall`, `ReceiptEvent` |

## 4. The contract, interface by interface

### 4.1 Coordinates and status

`Coordinate` is `str` in router format: `uk/ukpga/1996/18/s124/1ZA`. The router returns
`legal_rag_router.Coordinate` objects; convert them with `str()`. Calendar-numbered instruments
have four parts (`uk/ukpga/1996/18`), regnal-year Acts five (`uk/ukpga/Vict/24-25/100`). Use
`legal_rag_verifier.corpus.instrument_coordinate` to cut a coordinate to its instrument.

`TemporalStatus` (`StrEnum`, value equals name):

| Value | Decided from |
|---|---|
| `IN_FORCE` | None of the below |
| `NOT_YET_IN_FORCE` | Provision record `prospective: true` |
| `REPEALED` | Provision record `repealed: true`, or instrument record `repealed: true` |
| `TEXT_NOT_HELD` | Instrument record `structure: "metadata_only"` (then `provision_count` is 0) |
| `HISTORY_NOT_HELD` | The question's `as_at` date is before the instrument record's `version_date` |

Repealed provisions also carry dot-run text (`. . . .`), which `Premise.from_records` drops. The
router binds repealed instruments (`RouteResult.repealed`); it never refuses them. Deciding what a
status means for the answer is B2's job.

### 4.2 TextStore

A `runtime_checkable` Protocol. One instance is shared by every request, so it must be safe to
call from several threads.

| Member | Contract |
|---|---|
| `corpus_id: str` | Identity of the store build (for example the sha256 of its manifest). Goes into every receipt |
| `snapshot: str` | ISO date of the source data; matches the router index snapshot |
| `get(coordinate)` | Instrument record first, then the provision and all its descendants, in file order, in the exact row shape of `legal_rag_verifier.corpus.load_provision`. `Premise.from_records` takes the list unchanged. For an instrument coordinate: the instrument record and every provision. For a title-only instrument: the instrument record alone. Unknown coordinate: `KeyError` |
| `instrument(coordinate)` | The instrument record for any coordinate inside it, or `None` when the store does not hold the instrument |
| `has_text(instrument_coordinate)` | `False` for a title-only instrument. Unknown instrument: `KeyError` |

Row fields (router record schema version 2). Instrument record: `record_type: "instrument"`,
`coordinate`, `instrument_id`, `title`, `year`, `number`, `repealed`, `structure`
(`"full"` or `"metadata_only"`), `provision_count`, `text_version` (`"current"` or
`"as_enacted"`), `version_date`, `enactment_date`, `made_date`, `long_title`, `groups`,
`source_url`, `licence`, `checksum_sha256`, and others. Provision record:
`record_type: "provision"`, `coordinate`, `parent`, `order`, `number_label`, `title`,
`crossheading`, `repealed`, `prospective`, `text` (own text only, before children),
`text_after` (closing words after children), `amendment_history`, `source_url`,
`checksum_sha256`.

`tests/conftest.py` has `JsonlTextStore`, a test double over router JSONL files, exposed as the
`fixture_text_store` fixture. B1's store must pass the same checks.

### 4.3 Window

One numbered passage, shown to the model, the auditor and the user. `passage_id` is `"P1"`,
`"P2"` and so on, in prompt order. `text` and `heading` are the window as `Premise.from_records`
builds it (one subsection with its children and tail). `status` is a `TemporalStatus`.
`version_date` is the instrument record's. `source_url` is the provision's legislation.gov.uk
URL. `highlight` is a `(start, end)` character span in `text`, end exclusive, or `None`.

### 4.4 Chat layer (synchronous)

`ChatMessage` is a TypedDict `{role, content}`, role `"system"`, `"user"` or `"assistant"`.

`ChatRequest(messages, max_tokens, temperature, top_p, stop=(), mode="short", prefill=None,
seed=None, purpose="generate")`, frozen. `messages` is a tuple of `ChatMessage`, so a request
is not hashable. `mode` picks the provider's model settings (`"synthesis"` enables thinking).
`purpose` picks generation or judge settings and is recorded in `ProviderCall`.

`prefill` is text the assistant turn already holds. The model continues it, and `ChatResult.text`
is the continuation only, never the prefill. A provider with `supports_prefill == False` sends
`fold_prefill(messages, prefill)` instead, which writes the prefill into the last user turn with
`PREFILL_USER_TEMPLATE`. That keeps the fallback one rule across providers (plan section 5,
stage 10).

`ChatResult(text, finish_reason, provider, model_id, quantisation, prompt_tokens,
completion_tokens, latency_ms)`. `finish_reason` is normalised to `"stop"`, `"length"`,
`"content_filter"` or `"other"`. `quantisation`, `prompt_tokens` and `completion_tokens` are
`None` when the provider does not say.

`ChatProvider` Protocol: read-only `name`, `model_id`, `quantisation`, `supports_prefill`, and
`complete(req) -> ChatResult`, which raises `ProviderUnavailable`. Known names are
`PROVIDER_NAMES = ("groq", "cloudflare", "modal")`, in rotation order.

`ProviderUnavailable(provider, kind, retry_after_s=None, detail="")`, `kind` one of `"quota"`,
`"rate_limit"`, `"server"`, `"timeout"`, `"model_changed"`, `"config"`. `detail` is for logs
and must never hold a secret.

`AllProvidersUnavailable(failures)` carries the tuple of `ProviderUnavailable`, and
`retry_after_s` is the smallest wait any of them gave.

Both exceptions subclass `Exception` directly. `legal_rag_verifier.judge.Judge.score` treats
`ValueError`, `KeyError`, `IndexError`, `OSError`, `TimeoutError`, `URLError`, `HTTPException`
and `TypeError` as a failed attempt: it retries up to 3 times, then scores the sentence "not
stated", which under `STRICT_FIGURES` rolls it back. An outage that looked like one of those
would turn into a silent refusal of every sentence. `AllProvidersUnavailable` escapes the judge
and the verifier unchanged, and the pipeline turns it into extractive mode (section 5).

`Rotation` Protocol: `complete(req) -> ChatResult` (the first provider able to serve, in
order; raises `AllProvidersUnavailable`), and `available() -> list[str]` (names whose circuit is
closed, with quota left and a pinned model).

API keys are read from the environment at call time and never stored, logged or shown in a repr.
`Settings` holds only the variable names; `config.read_secret(name)` reads the value.

### 4.5 Judge bridge

`JudgeChat` is what `legal_rag_verifier.judge.Judge(chat)` takes. The verifier types it as
`Chat = Callable[[list[dict[str, str]]], Mapping[str, Any]]`. `Judge` calls
`chat(messages)` positionally with `[{"role": ..., "content": ...}]` and reads `content` (str or
None), `ms` and `tokens` (int or None) from the reply. It also reads `getattr(chat, "model")` as
its `model_id`, so the Protocol requires a `model` property. The reply type is the TypedDict
`JudgeReply{content, ms, tokens, finish}`, the keys `OpenAIChat` returns. A `JudgeChat` is
assignable to the verifier's `Chat` alias (checked by mypy in `tests/test_contracts.py`).

B3 writes the adapter from `Rotation` to `JudgeChat` (requests with `purpose="judge"`), and
records a `ProviderCall` per call with `ProviderCall.from_result(result, "judge")`.

### 4.6 Policy

`Policy(judge_preset, prompt_sha256, max_rollbacks_per_sentence=3, max_total_rollbacks=8,
refusal_text=DEFAULT_REFUSAL, steering="inject", verifier_config_name="STRICT_FIGURES")`,
keyword-only. `refusal_text` defaults to `legal_rag_verifier.engine.DEFAULT_REFUSAL`, imported.
`prompt_sha256` is the sha256 of B4's generation prompt template. `policy_hash` is
`sha256_hex(canonical_json(policy))`.

### 4.7 Sentences and citations

`SentenceOut(index, text, verdict, passage_ids, reasons, rollbacks)`. `index` is 0-based.
`verdict` is `"supported"` or `"refused"`. A refused sentence's text is `Policy.refusal_text`
and its `passage_ids` are empty. `reasons` are the verifier's reason codes. `rollbacks` counts
discarded drafts, as the verifier engine counts them (a point refused after three retries
shows 4).

The model cites passage ids only. `RenderedCitation(passage_id, coordinate, label, url)` is
rendered by the system (B2): `label` like `s.124(1ZA)(a) Employment Rights Act 1996`, `url`
the provision's legislation.gov.uk URL. A citation string written by the model fails the
sentence.

### 4.8 Answer Fact Object (receipt)

`AnswerFactObject`, keyword-only, frozen:

| Field | Meaning |
|---|---|
| `query_sha256` | `sha256_hex` of the query's UTF-8 bytes as received. The query itself is never stored |
| `route_status` | The router's `RouteStatus` value |
| `coordinates` | Bound coordinates |
| `passages` | Passage id to window coordinate, so `passage_ids` resolve without the stream |
| `version_dates` | Instrument coordinate to its record's `version_date` |
| `spans` | Window coordinate to the highlighted `(start, end)` in that window's text |
| `sentences` | Tuple of `SentenceOut` |
| `corpus_id` | `TextStore.corpus_id` |
| `calls` | Tuple of `ProviderCall(provider, model_id, quantisation, purpose, prompt_tokens, completion_tokens, latency_ms)`, generation and judge, successful calls only |
| `policy_hash` | `Policy.policy_hash` |
| `mode` | `"generated"`, `"extractive"`, `"refusal"` or `"clarify"` |
| `schema` | `AFO_SCHEMA_VERSION`, now 1 |

`answer_id` is `sha256_hex(canonical_json(afo))`. `to_dict()` gives the JSON form;
`from_dict()` inverts it whatever the key order and rejects another schema. A hypothesis test
checks that the round trip is the identity and that `answer_id` does not change when every dict
is reordered.

### 4.9 Canonical JSON

`canonical_json(obj) -> bytes`: `to_jsonable(obj)` then `json.dumps` with sorted keys,
separators `(",", ":")`, `ensure_ascii=False`, `allow_nan=False`, encoded UTF-8.
`to_jsonable` turns dataclasses into dicts of all their fields (`init=False` fields included),
enums into their values, tuples and lists into lists, sets into sorted lists. Mapping keys must
be strings. NaN, infinity and unknown types raise `TypeError`. `sha256_hex(bytes | str)` hashes
a str as UTF-8.

### 4.10 SSE events

One frozen dataclass per event. Each has a `type` field (a `Literal`, `init=False`), so it is in
the JSON. `encode_sse(event)` returns `event: <type>\ndata: <canonical json>\n\n`. The JSON is
one line, because JSON escapes newlines inside strings. `Event` is the union of all ten.

| `type` | Class | Payload |
|---|---|---|
| `route` | `RouteEvent` | `status`, `next_action`, `coordinates`, `corrections`, `messages`, `index_snapshot`. Build with `RouteEvent.from_route_result(result)` |
| `clarify` | `ClarifyEvent` | `question`, `candidates` (tuple of `LabelledCoordinate{coordinate, label}`) |
| `refusal` | `RefusalEvent` | `kind` (one of `REFUSAL_KINDS`), `message`, `suggestions` (tuple of `LabelledCoordinate`), `snapshot`, `link` (URL for `TEXT_NOT_HELD` and `HISTORY_NOT_HELD`, else null) |
| `sources` | `SourcesEvent` | `windows` |
| `sentence` | `SentenceEvent` | `sentence` (`SentenceOut`), `citations` (tuple of `RenderedCitation`) |
| `extractive` | `ExtractiveEvent` | `reason` (`"providers_unavailable"` or `"provider_warming"`), `windows` with highlights |
| `notice` | `NoticeEvent` | `code`, `message`. Codes in use: `provider_fallback`, `provider_warming`, `corrections_applied` |
| `receipt` | `ReceiptEvent` | `answer_id`, `fact` (the `AnswerFactObject`) |
| `error` | `ErrorEvent` | `code`, `message` (safe to show) |
| `done` | `DoneEvent` | `timings` (`Timings{first_source_ms, first_sentence_ms, total_ms}`, ms from request start, first two null when absent) |

`REFUSAL_KINDS` is every `RouteStatus` value except `ROUTE_BOUNDED` and `ROUTE_AMBIGUOUS`, every
`TemporalStatus` value except `IN_FORCE`, and `out_of_scope`.

## 5. SSE event order

`validate_order(types) -> bool` implements this grammar. `N*` means any number of `notice`.

```
stream   = "route" N* body
         | prefix "error" "done"
body     = "clarify" N* "done"
         | "refusal" N* "receipt" N* "done"
         | "sources" N* ( ("sentence" N*)* | "extractive" N* ) "receipt" N* "done"
prefix   = any proper prefix of a legal stream that has not reached "done" (may be empty)
```

- `route` comes first. The one exception is `error` before routing (rate limit, oversized
  query), sent as `error`, `done`.
- `sentence` and `extractive` never mix in one stream, and `extractive` appears at most once.
- Zero sentences after `sources` is legal (every point refused or an empty answer).
- `notice` may appear anywhere after `route` and before `done`.
- `error` replaces everything after it, and only `done` may follow it.
- `done` is always last and appears once.

Guidance for C1:
- `AllProvidersUnavailable` before the first `sentence`: send `extractive`
  (`providers_unavailable`), then `receipt` with `mode="extractive"`.
- `AllProvidersUnavailable` after a sentence has been sent: the stream cannot switch to
  extractive. Send a `notice`, close the answer, then `receipt`.
- Modal cold start: the stream answers extractive (`provider_warming`). The upgrade to a
  generated answer is a new request, not more events on the same stream.

## 6. Threading model

The verifier, the judge and the router are synchronous stdlib code, so the chat layer is
synchronous too (`httpx.Client`, never `AsyncClient`).

- Per request, `pipeline.py` runs routing, temporal status, premise building, generation and
  audit in one worker thread via `anyio.to_thread.run_sync`.
- The worker pushes events into an `anyio` memory object stream with
  `anyio.from_thread.run(send_stream.send, event)`. The FastAPI SSE response drains the
  receiving end on the event loop and writes `encode_sse(event)`.
- Shared across requests, created once at startup: `Router` (documented thread-safe, immutable
  memory-mapped tables), the `TextStore`, `Settings`, the provider clients and the `Rotation`.
  Their mutable parts (circuit breakers, quota ledger) must be thread-safe.
- Per request, never shared: `Judge`, `JudgedVerifier`, the generator, the `Premise` and the
  call log. `Judge` and `JudgedVerifier` hold per-answer state (the answer so far, the call log)
  and are not thread-safe. Make new ones for each request rather than calling `reset()` on a
  shared one.
- Client disconnect: the response closes its end of the stream; the worker's next send then
  raises, and the worker stops at its next sentence boundary.

## 7. Configuration

`Settings.from_env(environ=None)` reads:

| Variable | Field | Default |
|---|---|---|
| `LEGAL_DATA_ROOT` | `legal_data_root` | None (then both index paths must be set) |
| `ROUTER_INDEX` | `router_index` | `<LEGAL_DATA_ROOT>/index` |
| `CONCEPTS_INDEX` | `concepts_index` | `<LEGAL_DATA_ROOT>/concepts` |
| `STORE_DIR` | `store_dir` | `artefacts/store` |
| `DATABASE_URL` | `database_url` | None |
| `MODAL_URL` | `modal_url` | None |
| `CLOUDFLARE_ACCOUNT_ID` | `cloudflare_account_id` | None |
| `MAX_QUERY_BYTES` | `max_query_bytes` | 4096 |
| `CORS_ORIGINS` | `cors_origins` | none (comma-separated) |

Key variable names, stored as names only: `groq_api_key_env = "GROQ_API_KEY"`,
`cloudflare_token_env = "CLOUDFLARE_AI_WORKER_TOKEN"`, `modal_api_key_env = "MODAL_API_KEY"`.
`read_secret(name)` returns the stripped value or None, and raises `ConfigError` naming the
variable, never the value, when it holds a character that cannot go in an HTTP header.
`DATABASE_URL` can hold a password: the repr shows only `<set>`. Bad input raises `ConfigError`
(a `ValueError`).

## 8. Test fixtures

CI has no access to the 9.6 GB corpus. `tests/fixtures/data/` is a 0.45 MB data root with the
same shape as the real one, built by `scripts/build_fixtures.py` from the real data with the
router's own `ingest.build_index` and `ingest.build_concept_index` (run in the router checkout's
environment; the router repo is not modified). The build is deterministic.

| Role | Coordinate | Title | Record fields used |
|---|---|---|---|
| ERA, reduced | `uk/ukpga/1996/18` | Employment Rights Act 1996 | Instrument record unchanged, plus s.94, s.98, s.118 to s.126 (with s.124A) and their descendants |
| Title-only | `uk/ukpga/1980/15` | National Health Service (Invalid Direction) Act 1980 | `structure: "metadata_only"`, `provision_count: 0`, no provision rows |
| Repealed provision | `uk/ukpga/2008/7` | European Union (Amendment) Act 2008 | Instrument `repealed: false`; s4 and s5 `repealed: true` with dot-run text; s3 in force |
| Prospective provision | `uk/ukpga/2018/11` | Secure Tenancies (Victims of Domestic Abuse) Act 2018 | Instrument `repealed: false`; s1 and its subsections `prospective: true`; s2 in force |

`tests/fixtures/fixture.json` records these roles for tests. Fixtures in `tests/conftest.py`:
`fixture_data_root`, `fixture_roles`, `fixture_router`, `fixture_text_store`, `real_data_root`
(skips unless `LEGAL_DATA_ROOT` is set). Tests marked `@pytest.mark.data` skip when
`LEGAL_DATA_ROOT` is unset.

Limits of the fixture, which tests must not mistake for real behaviour:
- ERA sections outside the kept set do not exist in the fixture: "ERA 1996 s.99" is
  `PROVISION_NOT_FOUND` here and bound in the real index.
- The router's aliases file names instruments the fixture lacks, so the index manifest says
  `partial: true` and only 3 aliases survive.
- The coverage catalogue is the first 2 rows of each series, not the full catalogue.
- The concept index has no citation in-degree priors (no harvest file was given).
- Record lines are copied byte for byte, so checksums verify; the ERA instrument record still
  says `provision_count: 5832`.

Rebuild: `uv run python scripts/build_fixtures.py [--data PATH] [--router PATH]`.

## 9. Where the contract departs from the brief, and why

| Change | Reason |
|---|---|
| `AnswerFactObject.passages` added (passage id to coordinate) | Sentences cite passage ids only. Without the map a downloaded receipt cannot say which provision backs a sentence |
| `AnswerFactObject.schema` added (1) | Receipts are downloaded and kept; a reader must know the field set |
| `RefusalEvent.link` added | Plan section 5 stage 6: `TEXT_NOT_HELD` and `HISTORY_NOT_HELD` give a link |
| `JudgeChat` requires `model` and returns the `JudgeReply` TypedDict | `Judge` reads `chat.model` for its `model_id` (else `"unknown"`) and the keys `content`, `ms`, `tokens` |
| Exceptions subclass `Exception` directly; `ProviderUnavailable` gains `detail` | `Judge` swallows `ValueError`, `OSError`, `TimeoutError` and others as retryable failures (section 4.4) |
| `ChatResult` token counts and `quantisation` may be None; `finish_reason` normalised | Providers do not all report them; raw finish reasons differ by provider |
| `fold_prefill` and `PREFILL_USER_TEMPLATE` added | One rule for providers that reject an assistant prefill |
| `ProviderCall.from_result` added | Every call site builds the receipt entry the same way |
| `Timings` dataclass instead of a free dict | Same JSON; typed keys |
| `LabelledCoordinate` for clarify candidates and refusal suggestions | Same shape as the router's `Candidate`, but with a string coordinate |
| `RouteEvent.from_route_result` added | One conversion from router types, never the raw query |
| `Policy`, `AnswerFactObject` and events are keyword-only | Required fields come after defaulted ones in the brief's order |
| `TextStore.instrument` returns None for an unknown instrument; `get` and `has_text` raise `KeyError` | `instrument` is the existence check; the others need a known instrument |
| `validate_order` allows `error`, `done` before `route` | Rate limits and oversized queries fail before routing |
| `Settings.legal_data_root` is optional when both index paths are set; `read_secret` and `ConfigError` added | Production may mount the indexes without the raw records; one shared rule for reading keys |
