import type { RenderedCitation } from "../api/events";
import { citationChipLabel } from "../text";
import { Chip } from "./ui/chip";

interface Props {
  citation: RenderedCitation;
  turnId: number;
  onShow(turnId: number, passageId: string): void;
  selected?: boolean | undefined;
  showYear?: boolean | undefined;
}

/** A system-rendered citation chip that jumps to its window in the sources. */
export function Citation({ citation, turnId, onShow, selected, showYear = false }: Props) {
  const shortLabel = citationChipLabel(citation.coordinate, citation.label, showYear);
  return (
    <Chip
      variant="citation"
      selected={selected}
      className="mx-1 align-baseline"
      onClick={() => onShow(turnId, citation.passage_id)}
      aria-label={citation.label}
      title={citation.label}
    >
      {shortLabel}
    </Chip>
  );
}
