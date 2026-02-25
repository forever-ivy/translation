import type { AppRoute } from "@/shared/types";

export const APP_ROUTE_PATHS: Record<AppRoute, string> = {
  "start-openclaw": "/start-openclaw",
  "jobs": "/jobs",
  "verify": "/verify",
  "logs": "/logs",
  "kb-health": "/kb-health",
  "glossary": "/glossary",
  "settings": "/settings",
};

export function routeFromPathname(pathname: string): AppRoute {
  const entry = Object.entries(APP_ROUTE_PATHS).find(([, path]) => pathname === path || pathname.startsWith(`${path}/`));
  return (entry?.[0] as AppRoute | undefined) ?? "start-openclaw";
}
