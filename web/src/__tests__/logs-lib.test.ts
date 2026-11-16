import { describe, expect, it } from "vitest";

import { TTFA_NO_CONTENT_LABEL, ttfaMissing } from "@/lib/logs";

describe("ttfaMissing", () => {
  it("flags a stream that got a first token but no answer", () => {
    // Thinking-only stream: TTFT is set, TTFA is null. Tool calls are an answer
    // surface now, so this label may only mean "reasoning, nothing else".
    expect(ttfaMissing({ ttft_ms: 120, ttfa_ms: null })).toBe(true);
    expect(TTFA_NO_CONTENT_LABEL).toBe("无正文与工具调用（仅推理）");
  });

  it("stays quiet when the answer arrived or there is no first token", () => {
    expect(ttfaMissing({ ttft_ms: 120, ttfa_ms: 320 })).toBe(false);
    expect(ttfaMissing({ ttft_ms: null, ttfa_ms: null })).toBe(false);
  });
});
