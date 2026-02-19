import { invoke } from "@tauri-apps/api/core";

// ============================================================================
// Types matching Rust structs
// ============================================================================

export interface ServiceStatus {
  name: string;
  status: string;
  pid?: number;
  uptime?: string;
  restarts: number;
}

export interface KbSyncReport {
  ok: boolean;
  kb_root: string;
  scanned_count: number;
  created: number;
  updated: number;
  metadata_only: number;
  metadata_only_paths: string[];
  unscoped_skipped: number;
  unscoped_skipped_paths: string[];
  removed: number;
  removed_paths: string[];
  skipped: number;
  errors: unknown[];
  files: unknown[];
  indexed_at: string;
}

export interface KbSourceGroupStat {
  source_group: string;
  count: number;
  chunk_count: number;
}

export interface KbStats {
  total_files: number;
  total_chunks: number;
  last_indexed_at: string | null;
  by_source_group: KbSourceGroupStat[];
}

export interface KbFileRow {
  path: string;
  parser: string;
  source_group: string;
  chunk_count: number;
  indexed_at: string;
  size_bytes: number;
}

export interface KbFileList {
  total: number;
  items: KbFileRow[];
}

export interface PreflightCheck {
  name: string;
  key: string;
  status: string; // "pass" | "warning" | "blocker"
  message: string;
}

export interface Job {
  job_id: string;
  status: string;
  task_type: string;
  sender: string;
  created_at: string;
  updated_at: string;
}

export interface Milestone {
  job_id: string;
  event_type: string;
  timestamp: string;
  payload?: string;
}

export interface AppConfig {
  work_root: string;
  kb_root: string;
  strict_router: boolean;
  require_new: boolean;
  rag_backend: string;
}

export interface Artifact {
  name: string;
  path: string;
  size: number;
  artifact_type: string; // "docx" | "md" | "json" | "xlsx"
}

export interface QualityReport {
  terminology_hit: number;
  structure_fidelity: number;
  purity_score: number;
}

export interface DockerContainer {
  name: string;
  status: string; // "running" | "stopped" | "not_found"
  image: string;
}

// ============================================================================
// Service Commands
// ============================================================================

export const getServiceStatus = (): Promise<ServiceStatus[]> =>
  invoke<ServiceStatus[]>("get_service_status");

export const startAllServices = (): Promise<ServiceStatus[]> =>
  invoke<ServiceStatus[]>("start_all_services");

export const stopAllServices = (): Promise<void> =>
  invoke<void>("stop_all_services");

export const restartAllServices = (): Promise<ServiceStatus[]> =>
  invoke<ServiceStatus[]>("restart_all_services");

export const startService = (serviceId: string): Promise<ServiceStatus[]> =>
  invoke<ServiceStatus[]>("start_service", { serviceId });

export const stopService = (serviceId: string): Promise<ServiceStatus[]> =>
  invoke<ServiceStatus[]>("stop_service", { serviceId });

export const restartService = (serviceId: string): Promise<ServiceStatus[]> =>
  invoke<ServiceStatus[]>("restart_service", { serviceId });

// ============================================================================
// Preflight Commands
// ============================================================================

export const runPreflightCheck = (): Promise<PreflightCheck[]> =>
  invoke<PreflightCheck[]>("run_preflight_check");

export const autoFixPreflight = (): Promise<PreflightCheck[]> =>
  invoke<PreflightCheck[]>("auto_fix_preflight");

export const startOpenclaw = (): Promise<PreflightCheck[]> =>
  invoke<PreflightCheck[]>("start_openclaw");

// ============================================================================
// Config Commands
// ============================================================================

export const getConfig = (): Promise<AppConfig> =>
  invoke<AppConfig>("get_config");

export const saveConfig = (config: AppConfig): Promise<void> =>
  invoke<void>("save_config", { config });

// ============================================================================
// Job Commands
// ============================================================================

export const getJobs = (status?: string, limit?: number): Promise<Job[]> =>
  invoke<Job[]>("get_jobs", { status, limit: limit ?? 50 });

export const getJobMilestones = (jobId: string): Promise<Milestone[]> =>
  invoke<Milestone[]>("get_job_milestones", { jobId });

// ============================================================================
// Artifact Commands
// ============================================================================

export const listVerifyArtifacts = (jobId: string): Promise<Artifact[]> =>
  invoke<Artifact[]>("list_verify_artifacts", { jobId });

export const getQualityReport = (jobId: string): Promise<QualityReport | null> =>
  invoke<QualityReport | null>("get_quality_report", { jobId });

// ============================================================================
// Log Commands
// ============================================================================

export const readLogFile = (service: string, lines: number): Promise<string[]> =>
  invoke<string[]>("read_log_file", { service, lines });

// ============================================================================
// Utility Commands
// ============================================================================

export const openInFinder = (path: string): Promise<void> =>
  invoke<void>("open_in_finder", { path });

export const getVerifyFolderPath = (): Promise<string> =>
  invoke<string>("get_verify_folder_path");

// ============================================================================
// KB Health Commands
// ============================================================================

export const getKbSyncReport = (): Promise<KbSyncReport | null> =>
  invoke<KbSyncReport | null>("get_kb_sync_report");

export const getKbStats = (): Promise<KbStats> =>
  invoke<KbStats>("get_kb_stats");

export const kbSyncNow = (): Promise<KbSyncReport> =>
  invoke<KbSyncReport>("kb_sync_now");

export const listKbFiles = (args?: {
  query?: string;
  sourceGroup?: string;
  limit?: number;
  offset?: number;
}): Promise<KbFileList> =>
  invoke<KbFileList>("list_kb_files", {
    query: args?.query,
    sourceGroup: args?.sourceGroup,
    limit: args?.limit,
    offset: args?.offset,
  });

// ============================================================================
// Docker / ClawRAG Commands
// ============================================================================

export const getDockerStatus = (): Promise<DockerContainer[]> =>
  invoke<DockerContainer[]>("get_docker_status");

export const startDockerServices = (): Promise<DockerContainer[]> =>
  invoke<DockerContainer[]>("start_docker_services");

export const stopDockerServices = (): Promise<void> =>
  invoke<void>("stop_docker_services");

// ============================================================================
// API Provider Types
// ============================================================================

export interface ApiProvider {
  id: string;
  name: string;
  auth_type: "oauth" | "api_key" | "none";
  status: "configured" | "missing" | "expired";
  has_key: boolean;
  email?: string;
  expires_at?: number;
}

export interface ApiUsage {
  provider: string;
  used: number;
  limit: number;
  remaining: number;
  unit: string;
  reset_at?: number;
  fetched_at: number;
}

// ============================================================================
// API Provider Commands
// ============================================================================

export const getApiProviders = (): Promise<ApiProvider[]> =>
  invoke<ApiProvider[]>("get_api_providers");

export const getApiUsage = (provider: string): Promise<ApiUsage | null> =>
  invoke<ApiUsage | null>("get_api_usage", { provider });

export const setApiKey = (provider: string, key: string): Promise<void> =>
  invoke<void>("set_api_key", { provider, key });

export const deleteApiKey = (provider: string): Promise<void> =>
  invoke<void>("delete_api_key", { provider });

// ============================================================================
// Model Availability Types
// ============================================================================

export type RouteModelState = "ok" | "cooldown" | "unavailable" | "expired" | "unknown";

export interface RouteModelStatus {
  model: string;
  provider: string;
  available?: boolean;
  state: RouteModelState;
  cooldown_until_ms?: number;
  auth_expired?: boolean;
  note?: string;
}

export interface AgentAvailability {
  agent_id: string;
  default_model: string;
  fallbacks: string[];
  route: RouteModelStatus[];
  runnable_now: boolean;
  first_runnable_model?: string;
  blocked_reasons: string[];
}

export interface VisionAvailability {
  has_google_api_key: boolean;
  has_gemini_api_key: boolean;
  has_moonshot_api_key: boolean;
  has_openai_api_key: boolean;
  vision_backend?: string;
  vision_model?: string;
}

export interface GlmAvailability {
  glm_enabled: boolean;
  has_glm_api_key: boolean;
  has_zai_profile: boolean;
}

export interface ModelAvailabilityReport {
  fetched_at: number; // epoch ms
  agents: Record<string, AgentAvailability>;
  vision: VisionAvailability;
  glm: GlmAvailability;
}

// ============================================================================
// Model Availability Commands
// ============================================================================

export const getModelAvailabilityReport = (): Promise<ModelAvailabilityReport> =>
  invoke<ModelAvailabilityReport>("get_model_availability_report");
