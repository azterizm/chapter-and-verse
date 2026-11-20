import { describe, expect, it } from "vitest";

import { NOTICE_TITLE, noticeTitle } from "../src/text";

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
});
