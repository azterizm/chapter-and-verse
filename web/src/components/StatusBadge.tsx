import { Ban, Clock, FileQuestion } from "lucide-react";
import type { TemporalStatus } from "../api/events";
import { cn } from "@/lib/utils";
import { SOURCE_STATUS_BADGE, SOURCE_STATUS_PREFIX } from "../text";

const STATUS_ICON: Partial<Record<TemporalStatus, typeof Clock>> = {
  NOT_YET_IN_FORCE: Clock,
  REPEALED: Ban,
  TEXT_NOT_HELD: FileQuestion,
  HISTORY_NOT_HELD: FileQuestion,
};

export interface StatusBadgeProps {
  status: TemporalStatus;
  id?: string | undefined;
  className?: string | undefined;
}

/** Monochrome temporal status pill; renders nothing for IN_FORCE. */
export function StatusBadge({ status, id, className }: StatusBadgeProps) {
  const label = SOURCE_STATUS_BADGE[status];
  if (!label) return null;
  const Icon = STATUS_ICON[status];

  return (
    <span
      id={id}
      className={`text-caption ${cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border border-muted-foreground px-2 py-0.5 text-muted-foreground whitespace-nowrap",
        className,
      )}`}
    >
      {Icon && <Icon className="size-3 shrink-0" aria-hidden="true" />}
      <span className="sr-only">{SOURCE_STATUS_PREFIX}</span>
      {label}
    </span>
  );
}
