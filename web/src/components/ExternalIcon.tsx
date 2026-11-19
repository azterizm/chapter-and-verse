/** A small "opens elsewhere" mark. Decorative: the link text or label says where it goes. */
export function ExternalIcon() {
  return (
    <svg className="icon-ext" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">
      <path d="M5 2H2v8h8V7M7 1h4v4M11 1 5.5 6.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}
