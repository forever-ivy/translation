import { invoke } from "@tauri-apps/api/core";

const INVOKE_DEFAULT_TIMEOUT_MS = 15000;

function tInvoke<T>(
  command: string,
  args?: Record<string, unknown>,
  timeoutMs: number = INVOKE_DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const invokePromise = invoke<T>(command, args);
  const timeoutPromise = new Promise<T>((_, reject) => {
    const id = window.setTimeout(() => {
      reject(new Error(`Command timeout: ${command} (${timeoutMs}ms)`));
    }, Math.max(1000, timeoutMs));
    invokePromise.finally(() => window.clearTimeout(id));
  });
  return Promise.race([invokePromise, timeoutPromise]);
}

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

export interface GlossaryTerm {
  company: string;
  source_lang: string;
  target_lang: string;
  language_pair: string;
  source_text: string;
  target_text: string;
  origin: "extracted" | "custom" | string;
  source_path: string;
  updated_at?: string;
}

export interface GlossaryTermList {
  total: number;
  items: GlossaryTerm[];
  companies: string[];
  language_pairs: string[];
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
  tInvoke<ServiceStatus[]>("get_service_status");

export const startAllServices = (): Promise<ServiceStatus[]> =>
  tInvoke<ServiceStatus[]>("start_all_services");

export const stopAllServices = (): Promise<void> =>
  tInvoke<void>("stop_all_services");

export const restartAllServices = (): Promise<ServiceStatus[]> =>
  tInvoke<ServiceStatus[]>("restart_all_services");

export const startService = (serviceId: string): Promise<ServiceStatus[]> =>
  tInvoke<ServiceStatus[]>("start_service", { serviceId });

export const stopService = (serviceId: string): Promise<ServiceStatus[]> =>
  tInvoke<ServiceStatus[]>("stop_service", { serviceId });

export const restartService = (serviceId: string): Promise<ServiceStatus[]> =>
  tInvoke<ServiceStatus[]>("restart_service", { serviceId });

// ============================================================================
// Preflight Commands
// ============================================================================

export const runPreflightCheck = (): Promise<PreflightCheck[]> =>
  tInvoke<PreflightCheck[]>("run_preflight_check");

export const autoFixPreflight = (): Promise<PreflightCheck[]> =>
  tInvoke<PreflightCheck[]>("auto_fix_preflight");

export const startOpenclaw = (): Promise<PreflightCheck[]> =>
  tInvoke<PreflightCheck[]>("start_openclaw");

// ============================================================================
// Config Commands
// ============================================================================

export const getConfig = (): Promise<AppConfig> =>
  tInvoke<AppConfig>("get_config");

export const saveConfig = (config: AppConfig): Promise<void> =>
  tInvoke<void>("save_config", { config });

// ============================================================================
// Job Commands
// ============================================================================

export const getJobs = (status?: string, limit?: number): Promise<Job[]> =>
  tInvoke<Job[]>("get_jobs", { status, limit: limit ?? 50 });

export const getJobMilestones = (jobId: string): Promise<Milestone[]> =>
  tInvoke<Milestone[]>("get_job_milestones", { jobId });

// ============================================================================
// Artifact Commands
// ============================================================================

export const listVerifyArtifacts = (jobId: string): Promise<Artifact[]> =>
  tInvoke<Artifact[]>("list_verify_artifacts", { jobId });

export const getQualityReport = (jobId: string): Promise<QualityReport | null> =>
  tInvoke<QualityReport | null>("get_quality_report", { jobId });

// ============================================================================
// Log Commands
// ============================================================================

export const readLogFile = (service: string, lines: number): Promise<string[]> =>
  tInvoke<string[]>("read_log_file", { service, lines });

// ============================================================================
// Utility Commands
// ============================================================================

export const openInFinder = (path: string): Promise<void> =>
  tInvoke<void>("open_in_finder", { path });

export const getVerifyFolderPath = (): Promise<string> =>
  tInvoke<string>("get_verify_folder_path");

// ============================================================================
// KB Health Commands
// ============================================================================

export const getKbSyncReport = (): Promise<KbSyncReport | null> =>
  tInvoke<KbSyncReport | null>("get_kb_sync_report");

export const getKbStats = (): Promise<KbStats> =>
  tInvoke<KbStats>("get_kb_stats");

export const kbSyncNow = (): Promise<KbSyncReport> =>
  tInvoke<KbSyncReport>("kb_sync_now");

export const listKbFiles = (args?: {
  query?: string;
  sourceGroup?: string;
  limit?: number;
  offset?: number;
}): Promise<KbFileList> =>
  tInvoke<KbFileList>("list_kb_files", {
    query: args?.query,
    sourceGroup: args?.sourceGroup,
    limit: args?.limit,
    offset: args?.offset,
  });

export const listGlossaryTerms = (args?: {
  company?: string;
  languagePair?: string;
  query?: string;
  limit?: number;
  offset?: number;
}): Promise<GlossaryTermList> =>
  tInvoke<GlossaryTermList>("list_glossary_terms", {
    company: args?.company,
    languagePair: args?.languagePair,
    query: args?.query,
    limit: args?.limit,
    offset: args?.offset,
  });

export const upsertGlossaryTerm = (args: {
  company: string;
  sourceLang: string;
  targetLang: string;
  sourceText: string;
  targetText: string;
}): Promise<GlossaryTerm> =>
  tInvoke<GlossaryTerm>("upsert_glossary_term", {
    company: args.company,
    sourceLang: args.sourceLang,
    targetLang: args.targetLang,
    sourceText: args.sourceText,
    targetText: args.targetText,
  });

export const deleteGlossaryTerm = (args: {
  company: string;
  sourceLang: string;
  targetLang: string;
  sourceText: string;
}): Promise<boolean> =>
  tInvoke<boolean>("delete_glossary_term", {
    company: args.company,
    sourceLang: args.sourceLang,
    targetLang: args.targetLang,
    sourceText: args.sourceText,
  });

// ============================================================================
// Docker / ClawRAG Commands
// ============================================================================

export const getDockerStatus = (): Promise<DockerContainer[]> =>
  tInvoke<DockerContainer[]>("get_docker_status");

export const startDockerServices = (): Promise<DockerContainer[]> =>
  tInvoke<DockerContainer[]>("start_docker_services");

export const stopDockerServices = (): Promise<void> =>
  tInvoke<void>("stop_docker_services");

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
  tInvoke<ApiProvider[]>("get_api_providers");

export const getApiUsage = (provider: string): Promise<ApiUsage | null> =>
  tInvoke<ApiUsage | null>("get_api_usage", { provider });

export const setApiKey = (provider: string, key: string): Promise<void> =>
  tInvoke<void>("set_api_key", { provider, key });

export const deleteApiKey = (provider: string): Promise<void> =>
  tInvoke<void>("delete_api_key", { provider });

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
  tInvoke<ModelAvailabilityReport>("get_model_availability_report");
