import { test, expect } from "@playwright/test";

test("loads runtime route", async ({ page }) => {
  await page.goto("/start-openclaw");
  await expect(page.getByRole("button", { name: "One-Click Start" })).toBeVisible();
});
