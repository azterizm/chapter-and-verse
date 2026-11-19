import { splitHighlight } from "../text";

/** Provision text with its highlight span marked. */
export function WindowText({ text, highlight }: { text: string; highlight: [number, number] | null }) {
  const [before, marked, after] = splitHighlight(text, highlight);
  return (
    <p className="window-text">
      {before}
      {marked && <mark>{marked}</mark>}
      {after}
    </p>
  );
}
