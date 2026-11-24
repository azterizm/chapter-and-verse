import { sha256Hex } from "../api/client";
import type { Timings } from "../api/events";
import type { Receipt as ReceiptData } from "../state/reducer";
import { ANSWER_ID_LABEL, formatMs, shortId } from "../text";

export type ReceiptCheck = "match" | "mismatch" | "unavailable";

export function downloadReceipt(receipt: ReceiptData): void {
  if (typeof URL.createObjectURL !== "function") return;
  const blob = new Blob([receipt.json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${receipt.answer_id}.json`;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export async function verifyReceiptHash(receipt: ReceiptData): Promise<ReceiptCheck> {
  if (!receipt.exact) return "unavailable";
  try {
    const hash = await sha256Hex(receipt.json);
    if (hash === null) return "unavailable";
    return hash === receipt.answer_id ? "match" : "mismatch";
  } catch {
    return "unavailable";
  }
}

export function ReceiptLine({ receipt }: { receipt: ReceiptData }) {
  return (
    <p className="text-caption text-muted-foreground">
      {ANSWER_ID_LABEL}{" "}
      <code className="font-mono tabular-nums" title={receipt.answer_id}>
        {shortId(receipt.answer_id)}
      </code>
    </p>
  );
}

export function TimingsLine({ timings }: { timings: Timings }) {
  const parts: string[] = [];
  if (timings.first_source_ms !== null) parts.push(`first source ${formatMs(timings.first_source_ms)}`);
  if (timings.first_sentence_ms !== null) parts.push(`first sentence ${formatMs(timings.first_sentence_ms)}`);
  parts.push(`total ${formatMs(timings.total_ms)}`);
  const text = parts.join(", ");
  return (
    <p className="text-caption text-muted-foreground tabular-nums">
      {text.charAt(0).toUpperCase() + text.slice(1)}.
    </p>
  );
}
