// User-facing wording and small text helpers. House style: plain, short, facts only.

import type { ExtractiveReason, TemporalStatus } from "./api/events";

export const STATUS_LABEL: Record<TemporalStatus, string> = {
  IN_FORCE: "In force",
  NOT_YET_IN_FORCE: "Not yet in force",
  REPEALED: "Repealed",
  TEXT_NOT_HELD: "Text not held",
  HISTORY_NOT_HELD: "Earlier text not held",
};

export const REFUSAL_TITLE: Record<string, string> = {
  EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND: "No such instrument",
  EPISTEMIC_ABSTENTION_PROVISION_NOT_FOUND: "No such provision",
  ROUTE_OUT_OF_COVERAGE: "Outside the corpus",
  ROUTE_UNRESOLVED: "Citation not recognised",
  NOT_YET_IN_FORCE: "Not yet in force",
  REPEALED: "Repealed",
  TEXT_NOT_HELD: "Text not held",
  HISTORY_NOT_HELD: "Earlier text not held",
  out_of_scope: "Outside scope",
};

export const refusalTitle = (kind: string): string => REFUSAL_TITLE[kind] ?? "No answer";

export const EXTRACTIVE_REASON: Record<ExtractiveReason, string> = {
  providers_unavailable:
    "Every language model provider is at its limit or failing, so no written answer was made. " +
    "The provision text is shown instead, with the relevant words marked. Ask again later for a written answer.",
  provider_warming:
    "The model server is starting up, so no written answer was made. " +
    "The provision text is shown instead, with the relevant words marked. Ask again in a minute for a written answer.",
};

/** A short title shown before each notice's message, by notice code. */
export const NOTICE_TITLE: Record<string, string> = {
  corrections_applied: "Spelling corrected",
  provider_fallback: "Backup model used",
  provider_warming: "Model starting",
  status_excluded: "Part not answered",
  current_text_only: "Current text only",
  providers_unavailable: "Answer cut short",
  no_text: "No text to answer from",
};

/** The title for a notice code, or null for a code this page does not know. */
export const noticeTitle = (code: string): string | null =>
  Object.prototype.hasOwnProperty.call(NOTICE_TITLE, code) ? (NOTICE_TITLE[code] ?? null) : null;

export const EXAMPLES: readonly string[] = [
  "What is the limit of the compensatory award under s.124 ERA 1996?",
  "What does section 3 of the Marchwood Commercial Arbitration Act 1996 say?",
  "What does the National Health Service (Invalid Direction) Act 1980 say?",
  "Employment Rights Act section 124",
];

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** "2026-09-28" to "28 September 2026"; anything else is returned unchanged. */
export function formatDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const month = MONTHS[Number(m[2]) - 1];
  return month === undefined ? iso : `${Number(m[3])} ${month} ${m[1]}`;
}

/** Milliseconds as "0.21 s" or "850 ms". */
export function formatMs(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

/**
 * Split text around a highlight. The engine's spans are Python string indices, which count
 * Unicode code points; JavaScript strings count UTF-16 units. They differ only when the text
 * holds characters outside the Basic Multilingual Plane, so slice by code point then.
 */
export function splitHighlight(text: string, span: [number, number] | null): [string, string, string] {
  if (span === null) return [text, "", ""];
  const astral = /[\uD800-\uDFFF]/.test(text);
  const units = astral ? Array.from(text) : null;
  const len = units ? units.length : text.length;
  const start = Math.max(0, Math.min(span[0], len));
  const end = Math.max(start, Math.min(span[1], len));
  if (units) return [units.slice(0, start).join(""), units.slice(start, end).join(""), units.slice(end).join("")];
  return [text.slice(0, start), text.slice(start, end), text.slice(end)];
}

export const shortId = (hex: string): string => hex.slice(0, 12);
