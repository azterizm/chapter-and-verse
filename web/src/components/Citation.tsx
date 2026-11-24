import type { RenderedCitation } from "../api/events";
import { citationShowTitle } from "../text";
import { Chip } from "./ui/chip";

interface Props {
  citation: RenderedCitation;
  turnId: number;
  onShow(turnId: number, passageId: string): void;
  selected?: boolean | undefined;
}

/** A system-rendered citation chip that jumps to its window in the sources. */
export function Citation({ citation, turnId, onShow, selected }: Props) {
  return (
    <Chip
      variant="citation"
      selected={selected}
      className="mx-1 align-baseline"
      onClick={() => onShow(turnId, citation.passage_id)}
      title={citationShowTitle(citation.passage_id)}
    >
      {citation.label}
    </Chip>
  );
}
