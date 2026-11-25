import * as React from "react";
import { cn } from "@/lib/utils";
import { panelWindows, type Turn } from "../state/reducer";
import { SOURCE_FINDING, SOURCES_HEADING } from "../text";
import { SourceCard } from "./SourceCard";

export interface SourcePanelProps {
  turn: Turn | null;
  className?: string | undefined;
}

export function SourcePanel({ turn, className }: SourcePanelProps) {
  const headingId = React.useId();

  if (turn === null) return null;

  const windows = panelWindows(turn);
  if (windows.length === 0 && turn.phase !== "streaming") return null;

  const labelsByPassage = new Map<string, string>();
  for (const ev of turn.sentences) {
    for (const c of ev.citations) {
      if (!labelsByPassage.has(c.passage_id)) {
        labelsByPassage.set(c.passage_id, c.label);
      }
    }
  }

  return (
    <aside aria-labelledby={headingId} className={cn("space-y-3", className)}>
      <h2 id={headingId} className="text-ui font-bold text-foreground">
        {SOURCES_HEADING}
      </h2>

      {windows.length === 0 ? (
        <p className="text-caption text-muted-foreground">{SOURCE_FINDING}</p>
      ) : (
        <ul className="m-0 list-none space-y-2.5 p-0">
          {windows.map((w) => (
            <li key={`${turn.id}-${w.passage_id}`}>
              <SourceCard window={w} turnId={turn.id} citationLabel={labelsByPassage.get(w.passage_id)} />
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
