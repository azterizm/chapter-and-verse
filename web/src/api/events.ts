// Mirror of engine/chapterverse/contracts.py: every SSE event and payload type, with the
// snake_case JSON field names the engine sends (canonical JSON, docs/ARCHITECTURE.md §4.10).
// Tuples arrive as arrays. Optional Python fields arrive as null, never missing.

export type Coordinate = string;

export const TEMPORAL_STATUSES = [
  "IN_FORCE",
  "NOT_YET_IN_FORCE",
  "REPEALED",
  "TEXT_NOT_HELD",
  "HISTORY_NOT_HELD",
] as const;
export type TemporalStatus = (typeof TEMPORAL_STATUSES)[number];

/** legal_rag_router RouteStatus values (RouteEvent.status, AnswerFactObject.route_status). */
export const ROUTE_STATUSES = [
  "ROUTE_BOUNDED",
  "ROUTE_AMBIGUOUS",
  "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND",
  "EPISTEMIC_ABSTENTION_PROVISION_NOT_FOUND",
  "ROUTE_OUT_OF_COVERAGE",
  "ROUTE_UNRESOLVED",
] as const;
export type RouteStatus = (typeof ROUTE_STATUSES)[number];

/** legal_rag_router NextAction values (RouteEvent.next_action). */
export const NEXT_ACTIONS = [
  "RETRIEVE_BOUNDED",
  "ASK_USER",
  "REFUSE",
  "VERIFY_LIVE",
  "DECLARE_OUT_OF_COVERAGE",
  "DISCOVER_THEN_BIND",
] as const;
export type NextAction = (typeof NEXT_ACTIONS)[number];

export interface Window {
  passage_id: string;
  coordinate: Coordinate;
  heading: string;
  text: string;
  status: TemporalStatus;
  version_date: string | null;
  source_url: string;
  /** [start, end) in Unicode code points of `text` (Python indices), or null. */
  highlight: [number, number] | null;
}

export type Verdict = "supported" | "refused";
export type AnswerMode = "generated" | "extractive" | "refusal" | "clarify";
export type Purpose = "generate" | "judge";

export interface SentenceOut {
  index: number;
  text: string;
  verdict: Verdict;
  passage_ids: string[];
  reasons: string[];
  rollbacks: number;
}

export interface RenderedCitation {
  passage_id: string;
  coordinate: Coordinate;
  label: string;
  url: string;
}

export interface ProviderCall {
  provider: string;
  model_id: string;
  quantisation: string | null;
  purpose: Purpose;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  latency_ms: number;
}

export const AFO_SCHEMA_VERSION = 1;

export interface AnswerFactObject {
  query_sha256: string;
  route_status: string;
  coordinates: Coordinate[];
  passages: Record<string, Coordinate>;
  version_dates: Record<Coordinate, string | null>;
  spans: Record<Coordinate, [number, number]>;
  sentences: SentenceOut[];
  corpus_id: string;
  calls: ProviderCall[];
  policy_hash: string;
  mode: AnswerMode;
  schema: number;
}

export const EVENT_TYPES = [
  "route",
  "clarify",
  "refusal",
  "sources",
  "sentence",
  "extractive",
  "notice",
  "receipt",
  "error",
  "done",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export const REFUSAL_KINDS: ReadonlySet<string> = new Set([
  "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND",
  "EPISTEMIC_ABSTENTION_PROVISION_NOT_FOUND",
  "ROUTE_OUT_OF_COVERAGE",
  "ROUTE_UNRESOLVED",
  "NOT_YET_IN_FORCE",
  "REPEALED",
  "TEXT_NOT_HELD",
  "HISTORY_NOT_HELD",
  "out_of_scope",
]);

export const EXTRACTIVE_REASONS = [
  "providers_unavailable",
  "provider_warming",
  "quote_requested",
] as const;
export type ExtractiveReason = (typeof EXTRACTIVE_REASONS)[number];

export interface LabelledCoordinate {
  coordinate: Coordinate;
  label: string;
}

export interface RouteEvent {
  type: "route";
  status: string;
  next_action: string;
  coordinates: Coordinate[];
  corrections: [string, string][];
  messages: string[];
  index_snapshot: string;
}

export interface ClarifyEvent {
  type: "clarify";
  question: string;
  candidates: LabelledCoordinate[];
}

export interface RefusalEvent {
  type: "refusal";
  kind: string;
  message: string;
  suggestions: LabelledCoordinate[];
  snapshot: string;
  link: string | null;
}

export interface SourcesEvent {
  type: "sources";
  windows: Window[];
}

export interface SentenceEvent {
  type: "sentence";
  sentence: SentenceOut;
  citations: RenderedCitation[];
}

export interface ExtractiveEvent {
  type: "extractive";
  reason: ExtractiveReason;
  windows: Window[];
}

export interface NoticeEvent {
  type: "notice";
  code: string;
  message: string;
}

export interface ReceiptEvent {
  type: "receipt";
  answer_id: string;
  fact: AnswerFactObject;
}

export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
}

export interface Timings {
  first_source_ms: number | null;
  first_sentence_ms: number | null;
  total_ms: number;
}

export interface DoneEvent {
  type: "done";
  timings: Timings;
}

export type CvEvent =
  | RouteEvent
  | ClarifyEvent
  | RefusalEvent
  | SourcesEvent
  | SentenceEvent
  | ExtractiveEvent
  | NoticeEvent
  | ReceiptEvent
  | ErrorEvent
  | DoneEvent;

// ---------------------------------------------------------------------------- runtime checks
// The stream is untrusted input. Each payload is checked for the fields the UI reads before it
// reaches the reducer; a payload that fails is a protocol fault, not something to render.

export class PayloadError extends Error {
  override name = "PayloadError";
}

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isInt = (v: unknown): v is number => Number.isInteger(v);
const isOptStr = (v: unknown): v is string | null => v === null || isStr(v);
const isOptNum = (v: unknown): v is number | null => v === null || isNum(v);
const isStrArr = (v: unknown): v is string[] => Array.isArray(v) && v.every(isStr);
const isSpan = (v: unknown): v is [number, number] =>
  Array.isArray(v) && v.length === 2 && isInt(v[0]) && isInt(v[1]) && v[0] <= v[1];

function need(ok: boolean, what: string): void {
  if (!ok) throw new PayloadError(what);
}

function checkLabelled(v: unknown, where: string): void {
  need(isObj(v) && isStr(v.coordinate) && isStr(v.label), `${where}: bad labelled coordinate`);
}

function checkWindow(v: unknown, where: string): void {
  need(isObj(v), `${where}: window is not an object`);
  const w = v as Obj;
  need(
    isStr(w.passage_id) &&
      isStr(w.coordinate) &&
      isStr(w.heading) &&
      isStr(w.text) &&
      isStr(w.status) &&
      (TEMPORAL_STATUSES as readonly string[]).includes(w.status) &&
      isOptStr(w.version_date) &&
      isStr(w.source_url) &&
      (w.highlight === null || isSpan(w.highlight)),
    `${where}: bad window`,
  );
}

function checkSentenceOut(v: unknown): void {
  need(isObj(v), "sentence: not an object");
  const s = v as Obj;
  need(
    isInt(s.index) &&
      isStr(s.text) &&
      (s.verdict === "supported" || s.verdict === "refused") &&
      isStrArr(s.passage_ids) &&
      isStrArr(s.reasons) &&
      isInt(s.rollbacks),
    "sentence: bad sentence",
  );
}

function checkCitation(v: unknown): void {
  need(
    isObj(v) && isStr(v.passage_id) && isStr(v.coordinate) && isStr(v.label) && isStr(v.url),
    "sentence: bad citation",
  );
}

function checkFact(v: unknown): void {
  need(isObj(v), "receipt: fact is not an object");
  const f = v as Obj;
  need(
    isStr(f.query_sha256) &&
      isStr(f.route_status) &&
      isStrArr(f.coordinates) &&
      isObj(f.passages) &&
      isObj(f.version_dates) &&
      isObj(f.spans) &&
      Array.isArray(f.sentences) &&
      isStr(f.corpus_id) &&
      Array.isArray(f.calls) &&
      isStr(f.policy_hash) &&
      (f.mode === "generated" || f.mode === "extractive" || f.mode === "refusal" || f.mode === "clarify") &&
      isInt(f.schema),
    "receipt: bad fact object",
  );
  (f.sentences as unknown[]).forEach(checkSentenceOut);
}

/** Parse one SSE data line into a typed event. Throws PayloadError on any mismatch. */
export function parseEventData(eventName: string, data: string): CvEvent {
  need((EVENT_TYPES as readonly string[]).includes(eventName), `unknown event type "${eventName}"`);
  let v: unknown;
  try {
    v = JSON.parse(data);
  } catch {
    throw new PayloadError(`${eventName}: data is not JSON`);
  }
  need(isObj(v), `${eventName}: data is not an object`);
  const o = v as Obj;
  need(o.type === eventName, `${eventName}: data type is "${String(o.type)}"`);
  switch (eventName as EventType) {
    case "route":
      need(
        isStr(o.status) &&
          isStr(o.next_action) &&
          isStrArr(o.coordinates) &&
          Array.isArray(o.corrections) &&
          o.corrections.every((c) => Array.isArray(c) && c.length === 2 && c.every(isStr)) &&
          isStrArr(o.messages) &&
          isStr(o.index_snapshot),
        "route: bad payload",
      );
      break;
    case "clarify":
      need(isStr(o.question) && Array.isArray(o.candidates), "clarify: bad payload");
      (o.candidates as unknown[]).forEach((c) => checkLabelled(c, "clarify"));
      break;
    case "refusal":
      need(
        isStr(o.kind) &&
          isStr(o.message) &&
          Array.isArray(o.suggestions) &&
          isStr(o.snapshot) &&
          isOptStr(o.link),
        "refusal: bad payload",
      );
      (o.suggestions as unknown[]).forEach((c) => checkLabelled(c, "refusal"));
      break;
    case "sources":
      need(Array.isArray(o.windows), "sources: bad payload");
      (o.windows as unknown[]).forEach((w) => checkWindow(w, "sources"));
      break;
    case "sentence":
      checkSentenceOut(o.sentence);
      need(Array.isArray(o.citations), "sentence: bad citations");
      (o.citations as unknown[]).forEach(checkCitation);
      break;
    case "extractive":
      need(
        isStr(o.reason) &&
          (EXTRACTIVE_REASONS as readonly string[]).includes(o.reason) &&
          Array.isArray(o.windows),
        "extractive: bad payload",
      );
      (o.windows as unknown[]).forEach((w) => checkWindow(w, "extractive"));
      break;
    case "notice":
    case "error":
      need(isStr(o.code) && isStr(o.message), `${eventName}: bad payload`);
      break;
    case "receipt":
      need(isStr(o.answer_id), "receipt: bad answer_id");
      checkFact(o.fact);
      break;
    case "done": {
      const t = o.timings;
      need(
        isObj(t) && isOptNum(t.first_source_ms) && isOptNum(t.first_sentence_ms) && isNum(t.total_ms),
        "done: bad timings",
      );
      break;
    }
  }
  return o as unknown as CvEvent;
}
