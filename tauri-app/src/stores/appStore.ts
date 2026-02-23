import { create } from "zustand";
import * as tauri from "@/lib/tauri";
import type { ToastItem, ToastType } from "@/components/ui/toast";

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
  status: "open" | "acknowledged";
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

export type ModelAvailabilityReport = tauri.ModelAvailabilityReport;

interface AppState {
  // Services
  services: Service[];
  setServices: (services: Service[]) => void;
  updateService: (name: string, data: Partial<Service>) => void;

  // Docker
  dockerContainers: DockerContainer[];
  fetchDockerStatus: () => Promise<void>;
  startDocker: () => Promise<void>;
  stopDocker: () => Promise<void>;

  // Jobs
  jobs: Job[];
  setJobs: (jobs: Job[]) => void;
  selectedJobId: string | null;
  setSelectedJobId: (id: string | null) => void;
  selectedJobMilestones: Milestone[];
  jobArtifactsById: Record<string, Artifact[]>;
  jobQualityById: Record<string, QualityReport | null>;
  jobArtifactsLoadingById: Record<string, boolean>;

  // Preflight
  preflightChecks: PreflightCheck[];
  setPreflightChecks: (checks: PreflightCheck[]) => void;

  // Config
  config: AppConfig | null;
  setConfig: (config: AppConfig) => void;

  // Logs
  logs: { time: string; level: string; service: string; message: string }[];
  selectedLogService: string;
  setSelectedLogService: (service: string) => void;

  // KB Health
  kbSyncReport: KbSyncReport | null;
  kbStats: KbStats | null;

  // UI State
  isLoading: boolean;
  setIsLoading: (loading: boolean) => void;
  error: string | null;
  setError: (error: string | null) => void;
  activeTab: string;
  setActiveTab: (tab: string) => void;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;

  // Refresh animation state
  isRefreshing: boolean;
  refreshCurrentPage: () => Promise<void>;

  // Toasts
  toasts: ToastItem[];
  addToast: (type: ToastType, message: string) => void;
  dismissToast: (id: string) => void;

  // Theme
  theme: "light" | "dark" | "system";
  setTheme: (theme: "light" | "dark" | "system") => void;

  // Async Actions
  fetchServices: () => Promise<void>;
  fetchPreflightChecks: () => Promise<void>;
  autoFixPreflight: () => Promise<void>;
  startOpenclaw: () => Promise<void>;
  fetchConfig: () => Promise<void>;
  fetchJobs: (status?: string, opts?: { silent?: boolean }) => Promise<void>;
  fetchJobMilestones: (jobId: string, opts?: { silent?: boolean }) => Promise<void>;
  fetchJobArtifacts: (jobId: string) => Promise<void>;
  startServices: () => Promise<void>;
  stopServices: () => Promise<void>;
  restartServices: () => Promise<void>;
  startService: (serviceId: "telegram" | "worker") => Promise<void>;
  stopService: (serviceId: "telegram" | "worker") => Promise<void>;
  restartService: (serviceId: "telegram" | "worker") => Promise<void>;
  saveConfig: (config: AppConfig) => Promise<void>;
  fetchLogs: (service: string, lines?: number) => Promise<void>;

  // KB Health Actions
  fetchKbSyncReport: () => Promise<void>;
  fetchKbStats: () => Promise<void>;
  syncKbNow: () => Promise<void>;

  // API Providers
  apiProviders: ApiProvider[];
  apiUsage: Record<string, ApiUsage>;
  fetchApiProviders: () => Promise<void>;
  fetchApiUsage: (provider: string) => Promise<void>;
  fetchAllApiUsage: () => Promise<void>;
  setApiKey: (provider: string, key: string) => Promise<void>;
  deleteApiKey: (provider: string) => Promise<void>;

  // Model Availability
  modelAvailabilityReport: ModelAvailabilityReport | null;
  fetchModelAvailabilityReport: () => Promise<void>;

  // Operations Overview
  overviewMetrics: OverviewMetrics | null;
  overviewTrends: TrendPoint[];
  overviewTrendMetric: OverviewTrendMetric;
  setOverviewTrendMetric: (metric: OverviewTrendMetric) => void;
  overviewAlerts: AlertItem[];
  queueSnapshot: QueueSnapshot | null;
  runSummary: RunSummary | null;
  fetchOverviewMetrics: (rangeHours?: number) => Promise<void>;
  fetchOverviewTrends: (metric?: OverviewTrendMetric, rangeHours?: number) => Promise<void>;
  fetchOverviewAlerts: (status?: "open" | "acknowledged", severity?: "critical" | "warning" | "info") => Promise<void>;
  ackOverviewAlert: (alertId: string) => Promise<void>;
  fetchQueueSnapshot: () => Promise<void>;
  fetchRunSummary: (date?: string) => Promise<void>;
  refreshOverviewData: (opts?: { includeTrends?: boolean }) => Promise<void>;

  // Consolidated polling actions
  refreshDashboardData: (opts?: { silent?: boolean; includeJobs?: boolean }) => Promise<void>;
  refreshJobsData: (opts?: { silent?: boolean }) => Promise<void>;
  refreshSelectedJobMilestones: (opts?: { silent?: boolean }) => Promise<void>;
  refreshLogsData: (opts?: { silent?: boolean; lines?: number }) => Promise<void>;
  refreshVerifyData: (opts?: { silent?: boolean }) => Promise<void>;
  refreshApiConfigUsage: () => Promise<void>;
  refreshApiConfigAvailability: () => Promise<void>;
  refreshApiConfigData: () => Promise<void>;
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
let servicesFetchInFlight = false;
const logsFetchInFlight: Record<string, boolean> = {};
const artifactRequestSeqByJob: Record<string, number> = {};

export const useAppStore = create<AppState>((set, get) => ({
  // Services
  services: [
    { name: "Telegram Bot", status: "unknown", restarts: 0 },
    { name: "Run Worker", status: "unknown", restarts: 0 },
  ],
  setServices: (services) => set({ services }),
  updateService: (name, data) =>
    set((state) => ({
      services: state.services.map((s) =>
        s.name === name ? { ...s, ...data } : s
      ),
    })),

  // Docker
  dockerContainers: [],
  fetchDockerStatus: async () => {
    try {
      const containers = await tauri.getDockerStatus();
      set({
        dockerContainers: containers.map((c) => ({
          name: c.name,
          status: c.status as "running" | "stopped" | "not_found",
          image: c.image,
        })),
      });
    } catch {
      set({ dockerContainers: [] });
    }
  },
  startDocker: async () => {
    set({ isLoading: true });
    try {
      const containers = await tauri.startDockerServices();
      set({
        dockerContainers: containers.map((c) => ({
          name: c.name,
          status: c.status as "running" | "stopped" | "not_found",
          image: c.image,
        })),
      });
      const running = containers.filter((c) => c.status === "running").length;
      if (running === containers.length) {
        get().addToast("success", "ClawRAG containers started");
      } else {
        get().addToast("warning", `${running}/${containers.length} containers running`);
      }
    } catch (err) {
      get().addToast("error", `Failed to start Docker: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },
  stopDocker: async () => {
    set({ isLoading: true });
    try {
      await tauri.stopDockerServices();
      await delay(2000);
      await get().fetchDockerStatus();
      get().addToast("success", "ClawRAG containers stopped");
    } catch (err) {
      get().addToast("error", `Failed to stop Docker: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  // Jobs
  jobs: [],
  setJobs: (jobs) => set({ jobs }),
  selectedJobId: null,
  setSelectedJobId: (id) => set({ selectedJobId: id }),
  selectedJobMilestones: [],
  jobArtifactsById: {},
  jobQualityById: {},
  jobArtifactsLoadingById: {},

  // Preflight
  preflightChecks: [],
  setPreflightChecks: (preflightChecks) => set({ preflightChecks }),

  // Config
  config: null,
  setConfig: (config) => set({ config }),

  // Logs
  logs: [],
  selectedLogService: "telegram",
  setSelectedLogService: (service) => set({ selectedLogService: service }),

  // KB Health
  kbSyncReport: null,
  kbStats: null,

  // Operations Overview
  overviewMetrics: null,
  overviewTrends: [],
  overviewTrendMetric: "throughput",
  setOverviewTrendMetric: (overviewTrendMetric) => set({ overviewTrendMetric }),
  overviewAlerts: [],
  queueSnapshot: null,
  runSummary: null,

  // UI State
  isLoading: false,
  setIsLoading: (isLoading) => set({ isLoading }),
  error: null,
  setError: (error) => set({ error }),
  activeTab: "dashboard",
  setActiveTab: (activeTab) => set({ activeTab }),
  sidebarCollapsed: false,
  setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),

  // Refresh animation state
  isRefreshing: false,
  refreshCurrentPage: async () => {
    if (get().isRefreshing) return;
    set({ isRefreshing: true });

    const { activeTab } = get();

    try {
      if (activeTab === "dashboard") {
        await get().refreshDashboardData({ includeJobs: true });
      } else if (activeTab === "services") {
        await Promise.all([
          get().fetchServices(),
          get().fetchPreflightChecks(),
          get().fetchDockerStatus(),
        ]);
      } else if (activeTab === "jobs") {
        await Promise.all([get().refreshJobsData(), get().refreshSelectedJobMilestones()]);
      } else if (activeTab === "verify") {
        await get().refreshVerifyData();
      } else if (activeTab === "logs") {
        await get().refreshLogsData();
      } else if (activeTab === "api-config") {
        await get().refreshApiConfigData();
      } else if (activeTab === "kb-health") {
        await Promise.all([
          get().fetchKbSyncReport(),
          get().fetchKbStats(),
        ]);
      }
    } finally {
      set({ isRefreshing: false });
    }
  },

  // Toasts
  toasts: [],
  addToast: (type, message) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    set((state) => ({ toasts: [...state.toasts.slice(-4), { id, type, message }] }));
  },
  dismissToast: (id) => {
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
  },

  // Theme
  theme: (localStorage.getItem("theme") as "light" | "dark" | "system") || "system",
  setTheme: (theme) => {
    localStorage.setItem("theme", theme);
    set({ theme });
  },

  // Async Actions
  fetchServices: async () => {
    if (servicesFetchInFlight) return;
    servicesFetchInFlight = true;
    try {
      const services = await tauri.getServiceStatus();
      set({
        services: services.map((s) => ({
          name: s.name,
          status: s.status as ServiceStatusType,
          pid: s.pid,
          uptime: s.uptime,
          restarts: s.restarts,
        })),
      });
    } catch (err) {
      // Polling path: keep this silent to avoid toast storms when backend is slow.
      console.warn("fetchServices failed:", err);
    } finally {
      servicesFetchInFlight = false;
    }
  },

  fetchPreflightChecks: async () => {
    try {
      const checks = await tauri.runPreflightCheck();
      set({
        preflightChecks: checks.map((c) => ({
          name: c.name,
          key: c.key,
          status: c.status as "pass" | "warning" | "blocker",
          message: c.message,
        })),
      });
    } catch (err) {
      get().addToast("error", `Preflight checks failed: ${err}`);
    }
  },

  autoFixPreflight: async () => {
    set({ isLoading: true });
    try {
      const checks = await tauri.autoFixPreflight();
      set({
        preflightChecks: checks.map((c) => ({
          name: c.name,
          key: c.key,
          status: c.status as "pass" | "warning" | "blocker",
          message: c.message,
        })),
      });
      const blockers = checks.filter((c) => c.status === "blocker");
      if (blockers.length === 0) {
        get().addToast("success", "All issues resolved");
      } else {
        get().addToast("warning", `${blockers.length} issues require manual fix`);
      }
    } catch (err) {
      get().addToast("error", `Auto-fix failed: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  startOpenclaw: async () => {
    set({ isLoading: true });
    try {
      const checks = await tauri.startOpenclaw();
      set({
        preflightChecks: checks.map((c) => ({
          name: c.name,
          key: c.key,
          status: c.status as "pass" | "warning" | "blocker",
          message: c.message,
        })),
      });
      const openclaw = checks.find((c) => c.key === "openclaw");
      if (openclaw?.status === "pass") {
        get().addToast("success", "OpenClaw started successfully");
      } else {
        get().addToast("error", "Failed to start OpenClaw");
      }
    } catch (err) {
      get().addToast("error", `Failed to start OpenClaw: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  fetchConfig: async () => {
    try {
      const config = await tauri.getConfig();
      set({
        config: {
          workRoot: config.work_root,
          kbRoot: config.kb_root,
          strictRouter: config.strict_router,
          requireNew: config.require_new,
          ragBackend: config.rag_backend,
        },
      });
    } catch (err) {
      get().addToast("error", `Failed to fetch config: ${err}`);
    }
  },

  fetchJobs: async (status?: string, opts?: { silent?: boolean }) => {
    try {
      const jobs = await tauri.getJobs(status);
      set({
        jobs: jobs.map((j) => ({
          jobId: j.job_id,
          status: j.status,
          taskType: j.task_type,
          sender: j.sender,
          createdAt: j.created_at,
          updatedAt: j.updated_at,
        })),
      });
    } catch (err) {
      if (!opts?.silent) {
        get().addToast("error", `Failed to fetch jobs: ${err}`);
      }
    }
  },

  fetchJobMilestones: async (jobId: string, opts?: { silent?: boolean }) => {
    try {
      const milestones = await tauri.getJobMilestones(jobId);
      set({
        selectedJobMilestones: milestones.map((m) => ({
          eventType: m.event_type,
          timestamp: m.timestamp,
          payload: m.payload,
        })),
      });
    } catch (err) {
      if (!opts?.silent) {
        get().addToast("error", `Failed to fetch milestones: ${err}`);
      }
    }
  },

  fetchJobArtifacts: async (jobId: string) => {
    const requestSeq = (artifactRequestSeqByJob[jobId] || 0) + 1;
    artifactRequestSeqByJob[jobId] = requestSeq;
    set((state) => ({
      jobArtifactsLoadingById: {
        ...state.jobArtifactsLoadingById,
        [jobId]: true,
      },
    }));

    try {
      const [artifacts, quality] = await Promise.all([
        tauri.listVerifyArtifacts(jobId),
        tauri.getQualityReport(jobId).catch(() => null),
      ]);

      if (artifactRequestSeqByJob[jobId] !== requestSeq) {
        return;
      }

      set({
        jobArtifactsById: {
          ...get().jobArtifactsById,
          [jobId]: artifacts.map((a) => ({
            name: a.name,
            path: a.path,
            size: a.size,
            artifactType: a.artifact_type,
          })),
        },
        jobQualityById: {
          ...get().jobQualityById,
          [jobId]: quality
            ? {
                terminologyHit: quality.terminology_hit,
                structureFidelity: quality.structure_fidelity,
                purityScore: quality.purity_score,
              }
            : null,
        },
      });
    } catch (err) {
      get().addToast("error", `Failed to fetch artifacts: ${err}`);
    } finally {
      if (artifactRequestSeqByJob[jobId] === requestSeq) {
        set((state) => ({
          jobArtifactsLoadingById: {
            ...state.jobArtifactsLoadingById,
            [jobId]: false,
          },
        }));
      }
    }
  },

  startServices: async () => {
    set({ isLoading: true, error: null });
    try {
      await tauri.startAllServices();
      await delay(2000);
      await get().fetchServices();
      const { services } = get();
      const running = services.filter((s) => s.status === "running").length;
      if (running === services.length) {
        get().addToast("success", "All services started successfully");
      } else if (running > 0) {
        get().addToast("warning", `${running}/${services.length} services running`);
      } else {
        get().addToast("error", "Services failed to start");
      }
    } catch (err) {
      get().addToast("error", `Failed to start services: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  stopServices: async () => {
    set({ isLoading: true, error: null });
    try {
      await tauri.stopAllServices();
      await delay(2000);
      await get().fetchServices();
      get().addToast("success", "All services stopped");
    } catch (err) {
      get().addToast("error", `Failed to stop services: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  restartServices: async () => {
    set({ isLoading: true, error: null });
    try {
      await tauri.restartAllServices();
      await delay(2000);
      await get().fetchServices();
      get().addToast("success", "All services restarted");
    } catch (err) {
      get().addToast("error", `Failed to restart services: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  startService: async (serviceId: "telegram" | "worker") => {
    set({ isLoading: true, error: null });
    try {
      const services = await tauri.startService(serviceId);
      set({
        services: services.map((s) => ({
          name: s.name,
          status: s.status as ServiceStatusType,
          pid: s.pid,
          uptime: s.uptime,
          restarts: s.restarts,
        })),
      });
      get().addToast("success", `Started ${serviceId}`);
    } catch (err) {
      get().addToast("error", `Failed to start ${serviceId}: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  stopService: async (serviceId: "telegram" | "worker") => {
    set({ isLoading: true, error: null });
    try {
      const services = await tauri.stopService(serviceId);
      set({
        services: services.map((s) => ({
          name: s.name,
          status: s.status as ServiceStatusType,
          pid: s.pid,
          uptime: s.uptime,
          restarts: s.restarts,
        })),
      });
      get().addToast("success", `Stopped ${serviceId}`);
    } catch (err) {
      get().addToast("error", `Failed to stop ${serviceId}: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  restartService: async (serviceId: "telegram" | "worker") => {
    set({ isLoading: true, error: null });
    try {
      const services = await tauri.restartService(serviceId);
      set({
        services: services.map((s) => ({
          name: s.name,
          status: s.status as ServiceStatusType,
          pid: s.pid,
          uptime: s.uptime,
          restarts: s.restarts,
        })),
      });
      get().addToast("success", `Restarted ${serviceId}`);
    } catch (err) {
      get().addToast("error", `Failed to restart ${serviceId}: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  saveConfig: async (config: AppConfig) => {
    set({ isLoading: true, error: null });
    try {
      await tauri.saveConfig({
        work_root: config.workRoot,
        kb_root: config.kbRoot,
        strict_router: config.strictRouter,
        require_new: config.requireNew,
        rag_backend: config.ragBackend,
      });
      set({ config });
      get().addToast("success", "Settings saved successfully");
    } catch (err) {
      get().addToast("error", `Failed to save config: ${err}`);
      throw err;
    } finally {
      set({ isLoading: false });
    }
  },

  fetchLogs: async (service: string, lines = 100) => {
    const key = `${service}:${lines}`;
    if (logsFetchInFlight[key]) return;
    logsFetchInFlight[key] = true;
    try {
      const logLines = await tauri.readLogFile(service, lines);
      const logs = logLines.map((line) => {
        const normalizeLevel = (lvl: string) => {
          const up = (lvl || "").trim().toUpperCase();
          if (up === "WARNING") return "WARN";
          if (up === "CRITICAL" || up === "FATAL") return "ERROR";
          return up || "INFO";
        };

        const trimmed = String(line || "").trim();
        if (trimmed.startsWith("{")) {
          return { time: "", level: "INFO", service, message: line };
        }

        // Primary format:
        // "2026-02-18 23:45:07 [run-worker] INFO message..."
        const m1 = line.match(
          /^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+\[([^\]]+)\]\s+([A-Z]+)\s+(.*)$/
        );
        if (m1) {
          return {
            time: m1[2],
            level: normalizeLevel(m1[4]),
            service: m1[3] || service,
            message: m1[5],
          };
        }

        // Back-compat: "YYYY-MM-DD HH:MM:SS [INFO] message" OR "HH:MM:SS [INFO] message"
        const m2 = line.match(/^(\d{4}-\d{2}-\d{2}\s+)?(\d{2}:\d{2}:\d{2})\s*\[([A-Z]+)\]\s*(.*)$/);
        if (m2) {
          return {
            time: m2[2],
            level: normalizeLevel(m2[3]),
            service,
            message: m2[4],
          };
        }

        return { time: "", level: "INFO", service, message: line };
      });
      set({ logs, selectedLogService: service });
    } catch (err) {
      // High-frequency refresh path: avoid user-facing toast spam.
      console.warn(`fetchLogs failed for ${service}:`, err);
    } finally {
      logsFetchInFlight[key] = false;
    }
  },

  // KB Health Actions
  fetchKbSyncReport: async () => {
    try {
      const report = await tauri.getKbSyncReport();
      set({
        kbSyncReport: report
          ? {
              ok: report.ok,
              kbRoot: report.kb_root,
              scannedCount: report.scanned_count,
              created: report.created,
              updated: report.updated,
              metadataOnly: report.metadata_only,
              metadataOnlyPaths: report.metadata_only_paths || [],
              unscopedSkipped: report.unscoped_skipped,
              unscopedSkippedPaths: report.unscoped_skipped_paths || [],
              removed: report.removed,
              removedPaths: report.removed_paths || [],
              skipped: report.skipped,
              errors: report.errors || [],
              indexedAt: report.indexed_at,
            }
          : null,
      });
    } catch (err) {
      get().addToast("error", `Failed to fetch KB sync report: ${err}`);
    }
  },

  fetchKbStats: async () => {
    try {
      const stats = await tauri.getKbStats();
      set({
        kbStats: {
          totalFiles: stats.total_files,
          totalChunks: stats.total_chunks,
          lastIndexedAt: stats.last_indexed_at ?? null,
          bySourceGroup: (stats.by_source_group || []).map((g) => ({
            sourceGroup: g.source_group,
            count: g.count,
            chunkCount: g.chunk_count,
          })),
        },
      });
    } catch (err) {
      get().addToast("error", `Failed to fetch KB stats: ${err}`);
    }
  },

  syncKbNow: async () => {
    set({ isLoading: true, error: null });
    try {
      const report = await tauri.kbSyncNow();
      set({
        kbSyncReport: {
          ok: report.ok,
          kbRoot: report.kb_root,
          scannedCount: report.scanned_count,
          created: report.created,
          updated: report.updated,
          metadataOnly: report.metadata_only,
          metadataOnlyPaths: report.metadata_only_paths || [],
          unscopedSkipped: report.unscoped_skipped,
          unscopedSkippedPaths: report.unscoped_skipped_paths || [],
          removed: report.removed,
          removedPaths: report.removed_paths || [],
          skipped: report.skipped,
          errors: report.errors || [],
          indexedAt: report.indexed_at,
        },
      });
      await get().fetchKbStats();
      get().addToast(report.ok ? "success" : "warning", "KB sync completed");
    } catch (err) {
      get().addToast("error", `KB sync failed: ${err}`);
    } finally {
      set({ isLoading: false });
    }
  },

  // API Providers
  apiProviders: [],
  apiUsage: {},
  fetchApiProviders: async () => {
    try {
      const providers = await tauri.getApiProviders();
      set({
        apiProviders: providers.map((p) => ({
          id: p.id,
          name: p.name,
          authType: p.auth_type as "oauth" | "api_key" | "none",
          status: p.status as "configured" | "missing" | "expired",
          hasKey: p.has_key,
          email: p.email,
          expiresAt: p.expires_at,
        })),
      });
    } catch (err) {
      get().addToast("error", `Failed to fetch API providers: ${err}`);
    }
  },
  fetchApiUsage: async (provider: string) => {
    try {
      const usage = await tauri.getApiUsage(provider);
      if (usage) {
        set((state) => ({
          apiUsage: {
            ...state.apiUsage,
            [provider]: {
              provider: usage.provider,
              used: usage.used,
              limit: usage.limit,
              remaining: usage.remaining,
              unit: usage.unit,
              resetAt: usage.reset_at,
              fetchedAt: usage.fetched_at,
            },
          },
        }));
      }
    } catch (err) {
      // Silently ignore usage fetch errors
      console.error(`Failed to fetch usage for ${provider}:`, err);
    }
  },
  fetchAllApiUsage: async () => {
    const { apiProviders } = get();
    for (const provider of apiProviders) {
      if (provider.authType === "api_key" && provider.hasKey) {
        await get().fetchApiUsage(provider.id);
      }
    }
  },
  setApiKey: async (provider: string, key: string) => {
    try {
      await tauri.setApiKey(provider, key);
      await get().fetchApiProviders();
      get().addToast("success", `API key saved for ${provider}`);
    } catch (err) {
      get().addToast("error", `Failed to save API key: ${err}`);
    }
  },
  deleteApiKey: async (provider: string) => {
    try {
      await tauri.deleteApiKey(provider);
      await get().fetchApiProviders();
      // Clear usage data
      set((state) => {
        const newUsage = { ...state.apiUsage };
        delete newUsage[provider];
        return { apiUsage: newUsage };
      });
      get().addToast("success", `API key removed for ${provider}`);
    } catch (err) {
      get().addToast("error", `Failed to remove API key: ${err}`);
    }
  },

  // Operations Overview
  fetchOverviewMetrics: async (rangeHours = 24) => {
    try {
      const metrics = await tauri.getOverviewMetrics(rangeHours);
      set({
        overviewMetrics: {
          totalJobs: metrics.total_jobs,
          completedJobs: metrics.completed_jobs,
          failedJobs: metrics.failed_jobs,
          reviewReadyJobs: metrics.review_ready_jobs,
          runningJobs: metrics.running_jobs,
          backlogJobs: metrics.backlog_jobs,
          successRate: metrics.success_rate,
          avgTurnaroundMinutes: metrics.avg_turnaround_minutes,
          servicesRunning: metrics.services_running,
          servicesTotal: metrics.services_total,
          openAlerts: metrics.open_alerts,
          periodHours: metrics.period_hours,
          generatedAt: metrics.generated_at,
        },
      });
    } catch (err) {
      console.warn("fetchOverviewMetrics failed:", err);
    }
  },
  fetchOverviewTrends: async (metric, rangeHours = 24) => {
    const selectedMetric = metric ?? get().overviewTrendMetric;
    try {
      const points = await tauri.getOverviewTrends(selectedMetric, rangeHours);
      set({
        overviewTrendMetric: selectedMetric,
        overviewTrends: points.map((p) => ({
          timestamp: p.timestamp,
          label: p.label,
          value: p.value,
        })),
      });
    } catch (err) {
      console.warn("fetchOverviewTrends failed:", err);
    }
  },
  fetchOverviewAlerts: async (status, severity) => {
    try {
      const alerts = await tauri.listAlerts(status, severity);
      set({
        overviewAlerts: alerts.map((a) => ({
          id: a.id,
          title: a.title,
          message: a.message,
          severity: a.severity,
          status: a.status,
          source: a.source,
          metricValue: a.metric_value,
          createdAt: a.created_at,
          actionLabel: a.action_label,
        })),
      });
    } catch (err) {
      console.warn("fetchOverviewAlerts failed:", err);
    }
  },
  ackOverviewAlert: async (alertId: string) => {
    try {
      await tauri.ackAlert(alertId);
      set((state) => ({
        overviewMetrics: state.overviewMetrics
          ? { ...state.overviewMetrics, openAlerts: Math.max(0, state.overviewMetrics.openAlerts - 1) }
          : state.overviewMetrics,
        overviewAlerts: state.overviewAlerts.map((item) =>
          item.id === alertId ? { ...item, status: "acknowledged" } : item
        ),
      }));
      await get().fetchOverviewAlerts();
      get().addToast("success", "Alert acknowledged");
    } catch (err) {
      get().addToast("error", `Failed to acknowledge alert: ${err}`);
    }
  },
  fetchQueueSnapshot: async () => {
    try {
      const queue = await tauri.getQueueSnapshot();
      set({
        queueSnapshot: {
          pending: queue.pending,
          running: queue.running,
          reviewReady: queue.review_ready,
          done: queue.done,
          failed: queue.failed,
          total: queue.total,
        },
      });
    } catch (err) {
      console.warn("fetchQueueSnapshot failed:", err);
    }
  },
  fetchRunSummary: async (date?: string) => {
    try {
      const summary = await tauri.exportRunSummary(date);
      set({
        runSummary: {
          date: summary.date,
          text: summary.text,
          generatedAt: summary.generated_at,
        },
      });
    } catch (err) {
      console.warn("fetchRunSummary failed:", err);
    }
  },
  refreshOverviewData: async (opts?: { includeTrends?: boolean }) => {
    await Promise.all([
      get().fetchOverviewMetrics(24),
      get().fetchOverviewAlerts(),
      get().fetchQueueSnapshot(),
      get().fetchRunSummary(),
      opts?.includeTrends === false ? Promise.resolve() : get().fetchOverviewTrends(get().overviewTrendMetric, 24),
    ]);
  },

  // Consolidated polling actions
  refreshDashboardData: async (opts?: { silent?: boolean; includeJobs?: boolean }) => {
    await Promise.all([
      get().fetchServices(),
      get().fetchDockerStatus(),
      opts?.includeJobs ? get().fetchJobs(undefined, { silent: opts?.silent }) : Promise.resolve(),
      get().refreshOverviewData({ includeTrends: !!opts?.includeJobs }),
    ]);
  },
  refreshJobsData: async (opts?: { silent?: boolean }) => {
    await get().fetchJobs(undefined, { silent: opts?.silent });
  },
  refreshSelectedJobMilestones: async (opts?: { silent?: boolean }) => {
    const selectedJobId = get().selectedJobId;
    if (!selectedJobId) return;
    const selectedJob = get().jobs.find((j) => j.jobId === selectedJobId);
    if (!selectedJob || selectedJob.status !== "running") return;
    await get().fetchJobMilestones(selectedJobId, { silent: opts?.silent });
  },
  refreshLogsData: async (opts?: { silent?: boolean; lines?: number }) => {
    const service = get().selectedLogService;
    await get().fetchLogs(service, opts?.lines ?? 200);
  },
  refreshVerifyData: async (opts?: { silent?: boolean }) => {
    await get().fetchJobs("review_ready", { silent: opts?.silent });
  },
  refreshApiConfigUsage: async () => {
    await get().fetchAllApiUsage();
  },
  refreshApiConfigAvailability: async () => {
    await get().fetchModelAvailabilityReport();
  },
  refreshApiConfigData: async () => {
    await get().fetchApiProviders();
    await Promise.all([
      get().fetchModelAvailabilityReport(),
      get().fetchAllApiUsage(),
    ]);
  },

  // Model Availability
  modelAvailabilityReport: null,
  fetchModelAvailabilityReport: async () => {
    try {
      const report = await tauri.getModelAvailabilityReport();
      set({ modelAvailabilityReport: report });
    } catch (err) {
      get().addToast("error", `Failed to fetch model availability: ${err}`);
    }
  },
}));
