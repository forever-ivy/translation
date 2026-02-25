import type { AppRoute, PollingPolicy } from "@/shared/types";

export const POLLING_INTERVALS_MS = {
  runtime: 10_000,
  jobs: 8_000,
  milestones: 2_000,
  logs: 5_000,
  verify: 15_000,
} as const;

export const POLLING_POLICIES: PollingPolicy[] = [
  { route: "start-openclaw", intervalMs: POLLING_INTERVALS_MS.runtime, onVisibleOnly: true },
  { route: "jobs", intervalMs: POLLING_INTERVALS_MS.jobs, onVisibleOnly: true },
  { route: "logs", intervalMs: POLLING_INTERVALS_MS.logs, onVisibleOnly: true },
  { route: "verify", intervalMs: POLLING_INTERVALS_MS.verify, onVisibleOnly: true },
];

export function isVisible() {
  return document.visibilityState === "visible";
}

export function shouldPoll(route: AppRoute, policy: PollingPolicy) {
  if (policy.route !== route) return false;
  if (!policy.onVisibleOnly) return true;
  return isVisible();
}
