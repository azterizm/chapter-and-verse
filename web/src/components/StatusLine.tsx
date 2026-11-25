import type { Turn as TurnData } from "../state/reducer";
import { STATUS_STEPS } from "../text";

function activeStep(turn: TurnData): string | null {
  if (turn.phase !== "streaming") return null;
  if (
    turn.sentences.length > 0 ||
    turn.extractive !== null ||
    turn.refusal !== null ||
    turn.clarify !== null ||
    turn.error !== null
  ) {
    return null;
  }
  if (turn.route === null) return STATUS_STEPS[0];
  if (turn.windows.length === 0) return STATUS_STEPS[1];
  return STATUS_STEPS[2];
}

/** Quiet shimmer status line shown while streaming before the first sentence arrives. */
export function StatusLine({ turn }: { turn: TurnData }) {
  const step = activeStep(turn);
  if (step === null) return null;
  return (
    <p className="mt-2 text-caption text-muted-foreground status-shimmer" role="status">
      {step}
    </p>
  );
}
