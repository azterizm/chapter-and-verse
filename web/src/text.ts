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
  quote_requested: "Statutory text, quoted from legislation.gov.uk.",
};

/** A short title shown before each notice's message, by notice code. */
export const NOTICE_TITLE: Record<string, string> = {
  corrections_applied: "Spelling corrected",
  provider_fallback: "Backup model used",
  provider_warming: "Model starting",
  status_excluded: "Part not answered",
  current_text_only: "Current text only",
  providers_unavailable: "Answer may be incomplete",
  no_text: "No text to answer from",
  daily_cap: "Daily limit reached",
  global_budget: "Daily limit reached",
  quote_truncated: "Part shown",
};

/** Deterministic notice wording by notice code (requirements R4). */
export const NOTICE_MESSAGE: Record<string, string> = {
  daily_cap:
    "Daily limit reached for generated answers. The provision text is shown instead. It resets at 00:00 UTC.",
  global_budget:
    "Today's generated answers are used up. The provision text is shown instead. It resets at 00:00 UTC.",
  providers_unavailable:
    "The model became unavailable while writing. Every sentence shown was checked; there may have been more to say.",
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

// Redesign foundation copy (design.md section 7)
export const PURPOSE_LINE = "UK law, answered with the section it comes from.";
export const COMPOSER_PLACEHOLDER = "Ask about UK legislation";
export const SEND_LABEL = "Send";

export const EXAMPLE_QUESTIONS: readonly string[] = [
  "How long do I have to sue in tort?",
  "How long is the short-term right to reject?",
  "Quote section 172(1) of the Companies Act 2006",
];

export const STATUS_STEPS = ["Finding the law", "Reading", "Checking each point"] as const;

export const SOURCES_HEADING = "Sources";
export const SOURCE_LINK_LABEL = "legislation.gov.uk";
export const COPIED_TOAST = "Copied";
export const COPY_FAILED_TOAST = "Copy failed";

export const ACTION_COPY = "Copy";
export const ACTION_VERIFY = "Verify";
export const ACTION_NEW_QUESTION = "New question";

export const RECEIPT_VERIFIED = "Receipt verified";
export const RECEIPT_MISMATCH = "Receipt does not match";

export const FOOTER_NOT_ADVICE = "Not legal advice";
export const FOOTER_NO_PERSONAL_DATA = "Do not enter personal data";
export const FOOTER_OGL =
  "Contains public sector information licensed under the Open Government Licence v3.0";
export const footerSnapshot = (snapshot: string): string => `Law as at ${snapshot}`;

// W2 answer strings
export const QUESTION_LABEL = "Question: ";
export const NOTICES_ARIA_LABEL = "Notices";
export const CLARIFY_ARIA_LABEL = "Clarify";
export const CLARIFY_TITLE = "Choose a provision";
export const EXTRACTIVE_ARIA_LABEL = "Provision text";
export const EMPTY_ANSWER_TEXT = "No point could be stated reliably from these provisions.";
export const STOPPED_TEXT = "Stopped.";
export const ERROR_SERVER_TITLE = "The server stopped this answer";
export const ERROR_CLIENT_TITLE = "This answer failed";
export const ACTION_RETRY = "Retry";
export const ACTION_DETAILS = "Details";
export const SUGGESTIONS_LABEL = "Did you mean";
export const REFUSAL_LINK_LABEL = "Read on legislation.gov.uk";
export const OPENS_NEW_TAB = " (opens in a new tab)";
export const ANSWER_ID_LABEL = "Answer id";
export const RECEIPT_UNAVAILABLE = "Verification unavailable";
export const citationShowTitle = (passageId: string): string => `Show ${passageId} in the sources`;
export const copyQuoteLabel = (heading: string): string => `Copy ${heading}`;
export const refusalSnapshotText = (snapshot: string): string => `Corpus snapshot ${formatDate(snapshot)}.`;
export const answerIdCopyLine = (answerId: string): string => `Answer id: ${answerId}`;

export function formatDetailsTimings(firstSentenceMs: number | null, totalMs: number): string {
  if (firstSentenceMs !== null) {
    return `First sentence ${formatMs(firstSentenceMs)}, total ${formatMs(totalMs)}`;
  }
  return `Total ${formatMs(totalMs)}`;
}

const UNIT_PATTERNS: readonly { prefix: string; render: (rest: string) => string }[] = [
  { prefix: "sch", render: (r) => (r ? `Sch. ${r}` : "Sch.") },
  { prefix: "rule", render: (r) => (r ? `r. ${r}` : "r.") },
  { prefix: "para", render: (r) => (r ? `para. ${r}` : "para.") },
  { prefix: "group", render: (r) => (r ? `Group ${r}` : "Group") },
  { prefix: "art", render: (r) => (r ? `art. ${r}` : "art.") },
  { prefix: "reg", render: (r) => (r ? `reg. ${r}` : "reg.") },
  { prefix: "app", render: (r) => (r ? `App. ${r}` : "App.") },
  { prefix: "pt", render: (r) => (r ? `Pt. ${r}` : "Pt.") },
  { prefix: "ch", render: (r) => (r ? `Ch. ${r}` : "Ch.") },
  { prefix: "s", render: (r) => `s.${r}` },
];

export interface ParsedCoordinate {
  instrument: string;
  year: string | null;
  provision: string;
}

/** Parse a UK coordinate into its instrument key, numeric year (if present), and short provision label. */
export function parseUkCoordinate(coordinate: string): ParsedCoordinate | null {
  const parts = coordinate.split("/");
  if (parts[0] !== "uk" || parts.length < 4) return null;
  const hasNumericYear = /^\d{4}$/.test(parts[2] ?? "");
  const instLen = hasNumericYear ? 4 : 5;
  if (parts.length < instLen) return null;
  const instrument = parts.slice(0, instLen).join("/");
  const year = hasNumericYear ? (parts[2] ?? null) : null;
  const groups: string[] = [];
  for (const seg of parts.slice(instLen)) {
    const unit = UNIT_PATTERNS.find(
      (u) =>
        seg.startsWith(u.prefix) &&
        (seg.length > u.prefix.length
          ? /^[0-9A-Z.]/.test(seg.slice(u.prefix.length))
          : groups.length === 0 && seg !== "s"),
    );
    if (unit) {
      groups.push(unit.render(seg.slice(unit.prefix.length)));
    } else if (groups.length > 0) {
      groups[groups.length - 1] += `(${seg})`;
    } else {
      groups.push(`(${seg})`);
    }
  }
  return { instrument, year, provision: groups.join(" ") };
}

export function citationInstrumentKey(coordinate: string, label: string): string {
  return parseUkCoordinate(coordinate)?.instrument ?? label;
}

/** Short label for citation chips: provision only for one instrument, or provision plus year when multiple. */
export function citationChipLabel(coordinate: string, label: string, showYear: boolean): string {
  const parsed = parseUkCoordinate(coordinate);
  const provision = parsed?.provision ?? "";
  if (!provision) return label;
  if (!showYear) return provision;
  const year = parsed?.year ?? /\b(1[2-9]\d\d|20\d\d)\b/.exec(label)?.[1] ?? null;
  return year ? `${provision} (${year})` : provision;
}

// W3 source strings
export const SOURCE_STATUS_BADGE: Partial<Record<TemporalStatus, string>> = {
  NOT_YET_IN_FORCE: "Not in force",
  REPEALED: "Repealed",
  TEXT_NOT_HELD: "Text not held",
  HISTORY_NOT_HELD: "Text not held",
};

export const SOURCE_STATUS_PREFIX = "Status: ";
export const SOURCE_LINK_NEW_TAB = " (opens in a new tab)";
export const SOURCE_FINDING = "Finding the provisions";

export function sourceVersionDate(formattedDate: string): string {
  return `Version of ${formattedDate}`;
}

export function formatSourceCopy(text: string, label: string, url: string): string {
  return `${text}\n\n${label}\n${url}`;
}

export interface SourceTitleParts {
  provision: string;
  provisionHeading: string | null;
  instrument: string | null;
}

const HEADING_SPLIT_RE =
  /^(.+?),\s+((?:section|article|regulation|rule|paragraph|schedule|part|chapter|group|appendix)\b.*)$/i;
const TRAILING_PAREN_RE = /\s+\(([^()]*(?:\([^()]*\)[^()]*)*)\)$/;

/** Split a source window heading and coordinate into provision label, optional heading, and instrument name. */
export function parseSourceTitle(
  heading: string,
  coordinate: string,
  citationLabel?: string,
): SourceTitleParts {
  const shortProv = parseUkCoordinate(coordinate)?.provision ?? "";
  const trimmed = heading.trim();
  const split = HEADING_SPLIT_RE.exec(trimmed);
  if (split) {
    const instrument = split[1]?.trim() || null;
    const rest = split[2]?.trim() || "";
    const paren = TRAILING_PAREN_RE.exec(rest);
    const provisionHeading = paren?.[1]?.trim() || null;
    const provFallback = paren ? rest.slice(0, paren.index).trim() : rest;
    return {
      provision: shortProv || provFallback,
      provisionHeading,
      instrument,
    };
  }
  if (shortProv) {
    const labelInst =
      citationLabel && citationLabel.startsWith(`${shortProv} `)
        ? citationLabel.slice(shortProv.length + 1).trim() || null
        : null;
    const fallbackInst = labelInst ?? (trimmed && trimmed !== shortProv ? trimmed : null);
    return {
      provision: shortProv,
      provisionHeading: null,
      instrument: fallbackInst,
    };
  }
  return {
    provision: trimmed || citationLabel || coordinate,
    provisionHeading: null,
    instrument: null,
  };
}

// W4 shell strings
export const WORDMARK = "Chapter & Verse";
export const SKIP_TO_COMPOSER = "Skip to the question box";
export const MAIN_HEADING = "Ask about UK legislation";
export const EXAMPLES_LABEL = "Example questions";
export const COMPOSER_FORM_LABEL = "Ask a question";
export const COMPOSER_TOO_LONG = "Question is too long.";
export const FOOTER_SNAPSHOT_UNKNOWN = "not known";
export const THEME_LABEL = "Theme";
export const THEME_SYSTEM = "System";
export const THEME_LIGHT = "Light";
export const THEME_DARK = "Dark";
