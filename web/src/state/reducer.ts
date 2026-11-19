import { type AskFailure, factBytesFromRaw } from "../api/client";
import type {
  AnswerFactObject,
  ClarifyEvent,
  CvEvent,
  ExtractiveEvent,
  NoticeEvent,
  RefusalEvent,
  RouteEvent,
  SentenceEvent,
  Timings,
  Window,
} from "../api/events";

export type TurnPhase = "streaming" | "done" | "failed" | "stopped";

export interface Receipt {
  answer_id: string;
  fact: AnswerFactObject;
  /** The fact JSON to download. Byte-exact when `exact`, so its sha256 is answer_id. */
  json: string;
  exact: boolean;
}

export interface TurnError {
  /** "server": an error event; "client": the stream or connection failed here. */
  source: "server" | "client";
  code: string;
  message: string;
}

export interface Turn {
  id: number;
  query: string;
  phase: TurnPhase;
  route: RouteEvent | null;
  clarify: ClarifyEvent | null;
  refusal: RefusalEvent | null;
  windows: Window[];
  sentences: SentenceEvent[];
  extractive: ExtractiveEvent | null;
  notices: NoticeEvent[];
  receipt: Receipt | null;
  error: TurnError | null;
  timings: Timings | null;
}

export interface AppState {
  turns: Turn[];
  /** The turn whose sources the source panel shows. */
  activeTurnId: number | null;
  /** Corpus snapshot date: the latest route.index_snapshot, else /api/health. */
  snapshot: string | null;
}

export type Action =
  | { type: "start"; turnId: number; query: string }
  | { type: "event"; turnId: number; event: CvEvent; raw: string }
  | { type: "failure"; turnId: number; failure: AskFailure }
  | { type: "health"; snapshot: string }
  | { type: "focus"; turnId: number };

export const initialState: AppState = { turns: [], activeTurnId: null, snapshot: null };

function newTurn(id: number, query: string): Turn {
  return {
    id,
    query,
    phase: "streaming",
    route: null,
    clarify: null,
    refusal: null,
    windows: [],
    sentences: [],
    extractive: null,
    notices: [],
    receipt: null,
    error: null,
    timings: null,
  };
}

export function applyEvent(turn: Turn, event: CvEvent, raw: string): Turn {
  switch (event.type) {
    case "route":
      return { ...turn, route: event };
    case "clarify":
      return { ...turn, clarify: event };
    case "refusal":
      return { ...turn, refusal: event };
    case "sources":
      return { ...turn, windows: event.windows };
    case "sentence":
      return { ...turn, sentences: [...turn.sentences, event] };
    case "extractive":
      return { ...turn, extractive: event };
    case "notice":
      return { ...turn, notices: [...turn.notices, event] };
    case "receipt": {
      const exact = factBytesFromRaw(raw);
      return {
        ...turn,
        receipt: {
          answer_id: event.answer_id,
          fact: event.fact,
          json: exact ?? JSON.stringify(event.fact),
          exact: exact !== null,
        },
      };
    }
    case "error":
      return { ...turn, error: { source: "server", code: event.code, message: event.message } };
    case "done":
      return { ...turn, timings: event.timings, phase: "done" };
  }
}

function failureToError(f: AskFailure): TurnError | null {
  switch (f.kind) {
    case "aborted":
      return null;
    case "fault":
      return {
        source: "client",
        code: f.code,
        message:
          f.code === "incomplete"
            ? `The answer stopped before it finished. ${f.message}`
            : `The answer stream broke the protocol, so it was stopped. ${f.message}`,
      };
    case "http":
      return { source: "client", code: `http_${f.status}`, message: f.message };
    case "network":
      return { source: "client", code: "network", message: f.message };
  }
}

function updateTurn(state: AppState, id: number, fn: (t: Turn) => Turn): AppState {
  return { ...state, turns: state.turns.map((t) => (t.id === id ? fn(t) : t)) };
}

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "start":
      return {
        ...state,
        turns: [...state.turns, newTurn(action.turnId, action.query)],
        activeTurnId: action.turnId,
      };
    case "event": {
      const next = updateTurn(state, action.turnId, (t) => applyEvent(t, action.event, action.raw));
      return action.event.type === "route" ? { ...next, snapshot: action.event.index_snapshot } : next;
    }
    case "failure":
      return updateTurn(state, action.turnId, (t) => {
        if (t.phase !== "streaming") return t;
        const error = failureToError(action.failure);
        return error === null ? { ...t, phase: "stopped" } : { ...t, phase: "failed", error };
      });
    case "health":
      return state.snapshot === null ? { ...state, snapshot: action.snapshot } : state;
    case "focus":
      return { ...state, activeTurnId: action.turnId };
  }
}

/** Windows the source panel shows for a turn: extractive windows carry the highlights. */
export function panelWindows(turn: Turn): Window[] {
  return turn.extractive !== null && turn.extractive.windows.length > 0 ? turn.extractive.windows : turn.windows;
}

export const windowAnchor = (turnId: number, passageId: string): string => `w-${turnId}-${passageId}`;
