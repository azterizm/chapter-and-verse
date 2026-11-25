/**
 * Copy plain text to the clipboard using the async Clipboard API with a hidden
 * textarea + `execCommand("copy")` fallback for restricted contexts.
 */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to execCommand fallback.
    }
  }

  if (typeof document === "undefined" || !document.body) {
    return false;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.readOnly = true;
  textarea.setAttribute("aria-hidden", "true");
  textarea.tabIndex = -1;
  textarea.className = "fixed -left-[9999px] top-0 opacity-0 pointer-events-none";

  const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  try {
    document.body.appendChild(textarea);
    textarea.focus({ preventScroll: true });
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    return typeof document.execCommand === "function" && document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
    active?.focus({ preventScroll: true });
  }
}
