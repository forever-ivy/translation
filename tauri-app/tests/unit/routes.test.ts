import { describe, expect, it } from "vitest";
import { APP_ROUTE_PATHS, routeFromPathname } from "@/shared/routes";

describe("route mapping", () => {
  it("maps every declared route to a path", () => {
    expect(APP_ROUTE_PATHS["start-openclaw"]).toBe("/start-openclaw");
    expect(APP_ROUTE_PATHS.jobs).toBe("/jobs");
    expect(APP_ROUTE_PATHS.verify).toBe("/verify");
    expect(APP_ROUTE_PATHS.logs).toBe("/logs");
    expect(APP_ROUTE_PATHS["kb-health"]).toBe("/kb-health");
    expect(APP_ROUTE_PATHS.glossary).toBe("/glossary");
    expect(APP_ROUTE_PATHS.settings).toBe("/settings");
  });

  it("converts pathname back to route", () => {
    expect(routeFromPathname("/jobs")).toBe("jobs");
    expect(routeFromPathname("/jobs/123")).toBe("jobs");
    expect(routeFromPathname("/unknown")).toBe("start-openclaw");
  });
});
