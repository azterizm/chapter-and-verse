import { cn } from "@/lib/utils";
import { splitHighlight } from "../text";

export interface WindowTextProps {
  text: string;
  highlight: [number, number] | null;
  pulsing?: boolean | undefined;
  onPulseEnd?: (() => void) | undefined;
  className?: string | undefined;
}

/** Provision text with its highlight span marked. */
export function WindowText({ text, highlight, pulsing = false, onPulseEnd, className }: WindowTextProps) {
  const [before, marked, after] = splitHighlight(text, highlight);
  return (
    <p className={cn("text-body text-foreground whitespace-pre-wrap break-words", className)}>
      {before}
      {marked && (
        <mark
          className={cn(
            "rounded-[2px] bg-primary-soft px-0.5 text-foreground underline decoration-primary decoration-2 underline-offset-[3px]",
            pulsing && "jump-to-source",
          )}
          onAnimationEnd={onPulseEnd}
        >
          {marked}
        </mark>
      )}
      {after}
    </p>
  );
}
