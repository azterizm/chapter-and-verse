import { useEffect, useState } from "react";
import { sha256Hex } from "../api/client";
import type { Timings } from "../api/events";
import type { Receipt as ReceiptData } from "../state/reducer";
import { formatMs, shortId } from "../text";

function download(receipt: ReceiptData): void {
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

type Check = "pending" | "match" | "mismatch" | "unavailable";

export function ReceiptLine({ receipt }: { receipt: ReceiptData }) {
  // The hash result, keyed by the answer it was computed for.
  const [result, setResult] = useState<{ id: string; check: Check } | null>(null);
  useEffect(() => {
    if (!receipt.exact) return;
    let live = true;
    const settle = (check: Check) => live && setResult({ id: receipt.answer_id, check });
    sha256Hex(receipt.json).then(
      (h) => settle(h === null ? "unavailable" : h === receipt.answer_id ? "match" : "mismatch"),
      () => settle("unavailable"),
    );
    return () => {
      live = false;
    };
  }, [receipt]);
  const check: Check = !receipt.exact
    ? "unavailable"
    : result?.id === receipt.answer_id
      ? result.check
      : "pending";

  return (
    <p className="receipt">
      Answer id <code title={receipt.answer_id}>{shortId(receipt.answer_id)}</code>.{" "}
      <button type="button" className="link-button" onClick={() => download(receipt)}>
        Download the receipt
      </button>
      .{check === "match" && " Its SHA-256 matches the answer id."}
      {check === "mismatch" && <span className="warn-text"> Its SHA-256 does not match the answer id.</span>}
    </p>
  );
}

export function TimingsLine({ timings }: { timings: Timings }) {
  const parts: string[] = [];
  if (timings.first_source_ms !== null) parts.push(`first source ${formatMs(timings.first_source_ms)}`);
  if (timings.first_sentence_ms !== null) parts.push(`first sentence ${formatMs(timings.first_sentence_ms)}`);
  parts.push(`total ${formatMs(timings.total_ms)}`);
  const text = parts.join(", ");
  return <p className="timings">{text.charAt(0).toUpperCase() + text.slice(1)}.</p>;
}
