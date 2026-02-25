import type {
  AlertRunbook,
  ModelAvailabilityReport,
} from "@/lib/tauri";

export type ServiceStatusType = "running" | "stopped" | "degraded" | "unknown";

export interface Service {
  name: string;
  status: ServiceStatusType;
  pid?: number;
  uptime?: string;
  restarts: number;
}

export interface Job {
  jobId: string;
  status: string;
  taskType: string;
  sender: string;
  createdAt: string;
  updatedAt: string;
}

export interface Milestone {
  eventType: string;
  timestamp: string;
  payload?: string;
}

export interface PreflightCheck {
  name: string;
  key: string;
  status: "pass" | "warning" | "blocker";
  message: string;
}

export interface AppConfig {
  workRoot: string;
  kbRoot: string;
  strictRouter: boolean;
  requireNew: boolean;
  ragBackend: string;
}

export interface Artifact {
  name: string;
  path: string;
  size: number;
  artifactType: string;
}

export interface QualityReport {
  terminologyHit: number;
  structureFidelity: number;
  purityScore: number;
}

export interface DockerContainer {
  name: string;
  status: "running" | "stopped" | "not_found";
  image: string;
}

export interface GatewayStatus {
  running: boolean;
  healthy: boolean;
  loggedIn: boolean;
  baseUrl: string;
  model: string;
  lastError: string;
  updatedAt: string;
  version?: string;
  primaryProvider?: string;
  providers?: Record<string, GatewayProviderStatus>;
}

export interface GatewayProviderStatus {
  provider: string;
  running: boolean;
  healthy: boolean;
  loggedIn: boolean;
  baseUrl: string;
  model: string;
  homeUrl: string;
  lastError: string;
  updatedAt: string;
  sessionCheckedAt: string;
  profileDir: string;
  lastUrl: string;
}

export interface KbSyncReport {
  ok: boolean;
  kbRoot: string;
  scannedCount: number;
  created: number;
  updated: number;
  metadataOnly: number;
  metadataOnlyPaths: string[];
  unscopedSkipped: number;
  unscopedSkippedPaths: string[];
  removed: number;
  removedPaths: string[];
  skipped: number;
  errors: unknown[];
  indexedAt: string;
}

export interface KbSourceGroupStat {
  sourceGroup: string;
  count: number;
  chunkCount: number;
}

export interface KbStats {
  totalFiles: number;
  totalChunks: number;
  lastIndexedAt: string | null;
  bySourceGroup: KbSourceGroupStat[];
}

export interface ApiProvider {
  id: string;
  name: string;
  authType: "oauth" | "api_key" | "none";
  status: "configured" | "missing" | "expired";
  hasKey: boolean;
  email?: string;
  expiresAt?: number;
}

export interface ApiUsage {
  provider: string;
  used: number;
  limit: number;
  remaining: number;
  unit: string;
  resetAt?: number;
  fetchedAt: number;
  source: "real_api" | "estimated_activity" | "unsupported";
  confidence: "high" | "medium" | "low";
  reason?: string;
  activityCalls24h?: number;
  activityErrors24h?: number;
  activitySuccessRate?: number;
  activityLastSeenAt?: number;
}

export interface UsageSample {
  ts: number;
  used: number;
  remaining: number;
  limit: number;
}

export interface OverviewMetrics {
  totalJobs: number;
  completedJobs: number;
  failedJobs: number;
  reviewReadyJobs: number;
  runningJobs: number;
  backlogJobs: number;
  successRate: number;
  avgTurnaroundMinutes: number;
  servicesRunning: number;
  servicesTotal: number;
  openAlerts: number;
  periodHours: number;
  generatedAt: number;
}

export interface TrendPoint {
  timestamp: number;
  label: string;
  value: number;
}

export type OverviewTrendMetric = "throughput" | "failures" | "review_ready";

export interface AlertItem {
  id: string;
  title: string;
  message: string;
  severity: "critical" | "warning" | "info";
  status: "open" | "acknowledged" | "ignored";
  source: string;
  metricValue?: number;
  createdAt: number;
  actionLabel?: string;
}

export interface QueueSnapshot {
  pending: number;
  running: number;
  reviewReady: number;
  done: number;
  failed: number;
  total: number;
}

export interface RunSummary {
  date: string;
  text: string;
  generatedAt: number;
}

export type StartupPhase =
  | "preflight"
  | "login_check"
  | "start_gateway"
  | "start_worker"
  | "start_telegram"
  | "verify"
  | "done"
  | "failed";

export interface StartupStepResult {
  phase: StartupPhase | string;
  status: "success" | "warning" | "failed" | string;
  message: string;
  hintAction?: string;
  startedAt: string;
  endedAt: string;
}

export interface TelegramHealth {
  running: boolean;
  singleInstanceOk: boolean;
  conflict409: boolean;
  pidLock: boolean;
  pollConflict: boolean;
  network: string;
  lastError: string;
  logTail: string[];
  updatedAt: string;
}

export interface StartupSnapshot {
  services: Service[];
  gateway: GatewayStatus;
  telegram: TelegramHealth;
}

export type {
  AlertRunbook,
  ModelAvailabilityReport,
};
