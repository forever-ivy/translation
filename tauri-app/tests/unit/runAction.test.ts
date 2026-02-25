import { describe, expect, it, vi } from "vitest";
import { runAction } from "@/shared/async/runAction";

describe("runAction", () => {
  it("executes success lifecycle in order", async () => {
    const events: string[] = [];
    const result = await runAction({
      before: () => events.push("before"),
      action: async () => {
        events.push("action");
        return 42;
      },
      success: () => events.push("success"),
      after: () => events.push("after"),
    });

    expect(result).toBe(42);
    expect(events).toEqual(["before", "action", "success", "after"]);
  });

  it("maps and throws domain error on failure", async () => {
    const failure = vi.fn();
    await expect(
      runAction({
        action: async () => {
          throw new Error("boom");
        },
        mapError: () => ({ code: "x", message: "mapped" }),
        failure,
      }),
    ).rejects.toMatchObject({ code: "x", message: "mapped" });

    expect(failure).toHaveBeenCalledWith({ code: "x", message: "mapped" });
  });
});
