import { describe, expect, it } from "vitest";

import { EXTRACTIVE_REASON, NOTICE_MESSAGE, NOTICE_TITLE, noticeTitle } from "../src/text";

describe("notice titles", () => {
  const REQUIRED_CODES = [
    "corrections_applied",
    "provider_fallback",
    "provider_warming",
    "status_excluded",
    "current_text_only",
    "providers_unavailable",
    "no_text",
    "daily_cap",
    "global_budget",
    "quote_truncated",
  ] as const;

  it("has a plain title for every notice code the engine sends", () => {
    for (const code of REQUIRED_CODES) {
      const title = noticeTitle(code);
      expect(title).not.toBeNull();
      expect(title).toMatch(/^[A-Z][a-z ]+$/);
    }
  });

  it("returns null for an unknown code, so the message shows alone", () => {
    expect(noticeTitle("something_new")).toBeNull();
    expect(noticeTitle("toString")).toBeNull();
    for (const code of REQUIRED_CODES) {
      expect(NOTICE_TITLE).toHaveProperty(code);
    }
  });

  it("has updated wording for providers_unavailable (R2)", () => {
    expect(NOTICE_TITLE.providers_unavailable).toBe("Answer may be incomplete");
    expect(NOTICE_MESSAGE.providers_unavailable).toBe(
      "The model became unavailable while writing. Every sentence shown was checked; there may have been more to say.",
    );
  });

  it("has wording for quote_requested extractive reason", () => {
    expect(EXTRACTIVE_REASON.quote_requested).toBe("Statutory text, quoted from legislation.gov.uk.");
  });
});
