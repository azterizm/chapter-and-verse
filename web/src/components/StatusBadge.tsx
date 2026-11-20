import type { TemporalStatus } from "../api/events";
import { STATUS_LABEL } from "../text";

/** Each status has its own colour and border style, and always its words, so no state relies on colour. */
export function StatusBadge({ status }: { status: TemporalStatus }) {
  return (
    <span className={`badge badge-${status.toLowerCase().replace(/_/g, "-")}`}>
      <span className="visually-hidden">Status: </span>
      {STATUS_LABEL[status]}
    </span>
  );
}
