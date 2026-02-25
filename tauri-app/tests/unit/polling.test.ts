import { describe, expect, it } from "vitest";
import { shouldPoll } from "@/shared/polling/policies";

describe("polling policies", () => {
  it("does not poll when route mismatches", () => {
    expect(shouldPoll("jobs", { route: "start-openclaw", intervalMs: 1000, onVisibleOnly: false })).toBe(false);
  });

  it("polls when route matches and visibility restriction is disabled", () => {
    expect(shouldPoll("jobs", { route: "jobs", intervalMs: 1000, onVisibleOnly: false })).toBe(true);
  });
});
