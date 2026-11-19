import type { MouseEvent } from "react";
import type { RenderedCitation } from "../api/events";
import { windowAnchor } from "../state/reducer";
import { ExternalIcon } from "./ExternalIcon";

interface Props {
  citation: RenderedCitation;
  turnId: number;
  onShow(turnId: number, passageId: string): void;
}

/** A system-rendered citation: jumps to its window in the source panel, with a second link to legislation.gov.uk. */
export function Citation({ citation, turnId, onShow }: Props) {
  const anchor = windowAnchor(turnId, citation.passage_id);
  const show = (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    onShow(turnId, citation.passage_id);
  };
  return (
    <span className="cite">
      <a className="cite-chip" href={`#${anchor}`} onClick={show} title={`Show ${citation.passage_id} in the sources`}>
        {citation.label}
      </a>
      <a
        className="cite-ext"
        href={citation.url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`${citation.label} on legislation.gov.uk (opens in a new tab)`}
        title="legislation.gov.uk"
      >
        <ExternalIcon />
      </a>
    </span>
  );
}
