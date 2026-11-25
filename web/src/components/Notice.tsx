import { CircleAlert, Info } from "lucide-react";
import type { NoticeEvent } from "../api/events";
import { noticeTitle } from "../text";

const ALERT_CODES = new Set([
  "status_excluded",
  "providers_unavailable",
  "provider_warming",
  "no_text",
  "daily_cap",
  "global_budget",
  "quote_truncated",
]);

/** Single caption line with a 16 px icon for engine notices (never a box). */
export function Notice({ notice }: { notice: NoticeEvent }) {
  const title = noticeTitle(notice.code);
  const Icon = ALERT_CODES.has(notice.code) ? CircleAlert : Info;
  return (
    <div className="flex items-start gap-2 text-caption text-muted-foreground">
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <span>
        {title !== null && <strong className="font-bold text-foreground">{title}. </strong>}
        {notice.message}
      </span>
    </div>
  );
}
