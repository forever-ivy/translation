use chrono::{DateTime, Duration, Local, NaiveDate, NaiveDateTime, TimeZone, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Emitter, Manager, State};

// ============================================================================
// Data Types
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceStatus {
    pub name: String,
    pub status: String,
    pub pid: Option<u32>,
    pub uptime: Option<String>,
    pub restarts: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreflightCheck {
    pub name: String,
    pub key: String,
    pub status: String, // "pass" | "warning" | "blocker"
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct GatewayProviderStatus {
    #[serde(default)]
    pub provider: String,
    pub running: bool,
    pub healthy: bool,
    pub logged_in: bool,
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub home_url: String,
    #[serde(default)]
    pub last_error: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub session_checked_at: String,
    #[serde(default)]
    pub profile_dir: String,
    #[serde(default)]
    pub last_url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct GatewayStatus {
    pub running: bool,
    pub healthy: bool,
    pub logged_in: bool,
    pub base_url: String,
    pub model: String,
    pub last_error: String,
    pub updated_at: String,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub primary_provider: String,
    #[serde(default)]
    pub providers: HashMap<String, GatewayProviderStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StartupStepResult {
    pub phase: String,
    pub status: String,
    pub message: String,
    pub hint_action: Option<String>,
    pub started_at: String,
    pub ended_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct TelegramHealth {
    pub running: bool,
    pub single_instance_ok: bool,
    pub conflict_409: bool,
    pub pid_lock: bool,
    pub poll_conflict: bool,
    pub network: String,
    pub last_error: String,
    pub log_tail: Vec<String>,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StartupSnapshot {
    pub services: Vec<ServiceStatus>,
    pub gateway: GatewayStatus,
    pub telegram: TelegramHealth,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StartOpenclawPayload {
    pub force_restart: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StartTelegramPayload {
    pub force_restart: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AuditOperationPayload {
    pub operation_id: Option<String>,
    pub source: String,
    pub action: String,
    pub job_id: Option<String>,
    pub sender: Option<String>,
    pub status: String,
    pub summary: String,
    pub detail: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobInfo {
    pub job_id: String,
    pub status: String,
    pub task_type: String,
    pub sender: String,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Milestone {
    pub job_id: String,
    pub event_type: String,
    pub timestamp: String,
    pub payload: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub work_root: String,
    pub kb_root: String,
    pub strict_router: bool,
    pub require_new: bool,
    pub rag_backend: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnvVarItem {
    pub key: String,
    pub value: String,
    pub is_secret: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnvVarUpdate {
    pub key: String,
    pub value: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Artifact {
    pub name: String,
    pub path: String,
    pub size: u64,
    pub artifact_type: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QualityReport {
    pub terminology_hit: u32,
    pub structure_fidelity: u32,
    pub purity_score: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct KbSyncReport {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub kb_root: String,
    #[serde(default)]
    pub scanned_count: u32,
    #[serde(default)]
    pub created: u32,
    #[serde(default)]
    pub updated: u32,
    #[serde(default)]
    pub metadata_only: u32,
    #[serde(default)]
    pub metadata_only_paths: Vec<String>,
    #[serde(default)]
    pub unscoped_skipped: u32,
    #[serde(default)]
    pub unscoped_skipped_paths: Vec<String>,
    #[serde(default)]
    pub removed: u32,
    #[serde(default)]
    pub removed_paths: Vec<String>,
    #[serde(default)]
    pub skipped: u32,
    #[serde(default)]
    pub errors: Vec<serde_json::Value>,
    #[serde(default)]
    pub files: Vec<serde_json::Value>,
    #[serde(default)]
    pub indexed_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KbSourceGroupStat {
    pub source_group: String,
    pub count: u64,
    pub chunk_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KbStats {
    pub total_files: u64,
    pub total_chunks: u64,
    pub last_indexed_at: Option<String>,
    pub by_source_group: Vec<KbSourceGroupStat>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KbFileRow {
    pub path: String,
    pub parser: String,
    pub source_group: String,
    pub chunk_count: u64,
    pub indexed_at: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KbFileList {
    pub total: u64,
    pub items: Vec<KbFileRow>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlossaryTerm {
    pub company: String,
    pub source_lang: String,
    pub target_lang: String,
    pub language_pair: String,
    pub source_text: String,
    pub target_text: String,
    pub origin: String, // "extracted" | "custom"
    pub source_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlossaryTermList {
    pub total: u64,
    pub items: Vec<GlossaryTerm>,
    pub companies: Vec<String>,
    pub language_pairs: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlossaryLookupItem {
    pub company: String,
    pub source_lang: String,
    pub target_lang: String,
    pub language_pair: String,
    pub source_text: String,
    pub target_text: String,
    pub origin: String,
    pub source_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<String>,
    pub matched_in: String,
    pub match_score: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlossaryLookupResult {
    pub query: String,
    pub total: u64,
    pub items: Vec<GlossaryLookupItem>,
    pub companies: Vec<String>,
}

// ============================================================================
// API Provider Types
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiProvider {
    pub id: String,
    pub name: String,
    pub auth_type: String, // "oauth" | "api_key" | "none"
    pub status: String,    // "configured" | "missing" | "expired"
    pub has_key: bool,
    pub email: Option<String>,
    pub expires_at: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiUsage {
    pub provider: String,
    pub used: u64,
    pub limit: u64,
    pub remaining: u64,
    pub unit: String,
    pub reset_at: Option<i64>,
    pub fetched_at: i64,
    // Extended fields for dual-track (real vs estimated)
    pub source: String,     // "real_api" | "estimated_activity" | "unsupported"
    pub confidence: String, // "high" | "medium" | "low"
    pub reason: Option<String>,
    pub activity_calls_24h: Option<u64>,
    pub activity_errors_24h: Option<u64>,
    pub activity_success_rate: Option<f64>,
    pub activity_last_seen_at: Option<i64>, // epoch ms
}

// ============================================================================
// Overview / Operations Types
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OverviewMetrics {
    pub total_jobs: u64,
    pub completed_jobs: u64,
    pub failed_jobs: u64,
    pub review_ready_jobs: u64,
    pub running_jobs: u64,
    pub backlog_jobs: u64,
    pub success_rate: f64,
    pub avg_turnaround_minutes: f64,
    pub services_running: u64,
    pub services_total: u64,
    pub open_alerts: u64,
    pub period_hours: u32,
    pub generated_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrendPoint {
    pub timestamp: i64,
    pub label: String,
    pub value: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlertItem {
    pub id: String,
    pub title: String,
    pub message: String,
    pub severity: String, // "critical" | "warning" | "info"
    pub status: String,   // "open" | "acknowledged" | "ignored"
    pub source: String,
    pub metric_value: Option<i64>,
    pub created_at: i64,
    pub action_label: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QueueSnapshot {
    pub pending: u64,
    pub running: u64,
    pub review_ready: u64,
    pub done: u64,
    pub failed: u64,
    pub total: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunSummary {
    pub date: String,
    pub text: String,
    pub generated_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlertRunbookAction {
    pub label: String,
    pub tab: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlertRunbook {
    pub headline: String,
    pub steps: Vec<String>,
    pub actions: Vec<AlertRunbookAction>,
}

// ============================================================================
// Model Availability Types
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelAvailabilityReport {
    pub fetched_at: i64, // epoch ms
    pub agents: HashMap<String, AgentAvailability>,
    pub vision: VisionAvailability,
    pub glm: GlmAvailability,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentAvailability {
    pub agent_id: String,
    pub default_model: String,
    pub fallbacks: Vec<String>,
    pub route: Vec<RouteModelStatus>,
    pub runnable_now: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub first_runnable_model: Option<String>,
    pub blocked_reasons: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RouteModelStatus {
    pub model: String,
    pub provider: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub available: Option<bool>,
    pub state: String, // "ok" | "cooldown" | "unavailable" | "expired" | "unknown"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cooldown_until_ms: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auth_expired: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VisionAvailability {
    pub has_google_api_key: bool,
    pub has_gemini_api_key: bool,
    pub has_moonshot_api_key: bool,
    pub has_openai_api_key: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vision_backend: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vision_model: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlmAvailability {
    pub glm_enabled: bool,
    pub has_glm_api_key: bool,
    pub has_zai_profile: bool,
}

// ============================================================================
// Application State
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct AlertStateSnapshot {
    #[serde(default)]
    acknowledged_ids: HashSet<String>,
    #[serde(default)]
    ignored_ids: HashSet<String>,
    #[serde(default)]
    first_seen_ms: HashMap<String, i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct AlertRunbookRuleConfig {
    #[serde(default)]
    source: Option<String>,
    #[serde(default)]
    severity: Option<String>,
    headline: String,
    #[serde(default)]
    steps: Vec<String>,
    #[serde(default)]
    actions: Vec<AlertRunbookAction>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct AlertPolicyConfig {
    #[serde(default = "default_warning_to_critical_minutes")]
    warning_to_critical_minutes: u32,
    #[serde(default)]
    runbooks: Vec<AlertRunbookRuleConfig>,
}

pub struct AppState {
    pub services: Mutex<HashMap<String, ServiceStatus>>,
    pub alert_state: Mutex<AlertStateSnapshot>,
    pub alert_state_path: String,
    pub alert_policy_path: String,
    pub config_path: String,
    pub scripts_path: String,
    pub pids_dir: String,
    pub logs_dir: String,
    pub db_path: String,
}

fn detect_project_root() -> String {
    if let Ok(explicit_root) = std::env::var("OPENCLAW_PROJECT_ROOT") {
        let explicit = PathBuf::from(explicit_root.trim());
        if explicit.join("scripts/start.sh").exists() {
            return explicit.to_string_lossy().to_string();
        }
    }

    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.clone());
        candidates.push(cwd.join(".."));
        candidates.push(cwd.join("../.."));
    }
    candidates.push(PathBuf::from("/Users/Code/workflow/Inifity"));

    for candidate in candidates {
        let normalized = fs::canonicalize(&candidate).unwrap_or(candidate);
        if normalized.join("scripts/start.sh").exists() {
            return normalized.to_string_lossy().to_string();
        }
    }

    "/Users/Code/workflow/Inifity".to_string()
}

impl Default for AppState {
    fn default() -> Self {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
        let runtime_dir = format!("{}/.openclaw/runtime/translation", home);
        let project_root = detect_project_root();
        let alert_state_path = format!("{}/alert_state.json", runtime_dir);
        let alert_state = load_alert_state_snapshot(&alert_state_path);
        let alert_policy_path = format!("{}/config/alert_policy.json", project_root);

        Self {
            services: Mutex::new(HashMap::new()),
            alert_state: Mutex::new(alert_state),
            alert_state_path,
            alert_policy_path,
            config_path: project_root.clone(),
            scripts_path: format!("{}/scripts", project_root),
            pids_dir: format!("{}/pids", runtime_dir),
            logs_dir: format!("{}/logs", runtime_dir),
            db_path: format!("{}/state.sqlite", runtime_dir),
        }
    }
}

// ============================================================================
// Inner helper functions (avoid State<AppState> clone issues)
// ============================================================================

fn get_service_status_inner(state: &AppState) -> Result<Vec<ServiceStatus>, String> {
    let mut services = vec![
        ServiceStatus {
            name: "Telegram Bot".to_string(),
            status: "unknown".to_string(),
            pid: None,
            uptime: None,
            restarts: 0,
        },
        ServiceStatus {
            name: "Run Worker".to_string(),
            status: "unknown".to_string(),
            pid: None,
            uptime: None,
            restarts: 0,
        },
    ];

    for service in &mut services {
        let pid_candidates: Vec<PathBuf> = match service.name.as_str() {
            "Telegram Bot" => vec![
                PathBuf::from(&state.pids_dir).join("telegram.pid"),
                runtime_root_from_state(state).join("tg_bot.pid"),
            ],
            "Run Worker" => vec![PathBuf::from(&state.pids_dir).join("worker.pid")],
            _ => continue,
        };

        let mut found_running = false;
        for pid_file in pid_candidates {
            if let Some(pid) = read_pid_file_u32(&pid_file).filter(|pid| process_is_running(*pid)) {
                found_running = true;
                service.status = "running".to_string();
                service.pid = Some(pid);
                if let Ok(metadata) = fs::metadata(&pid_file) {
                    if let Ok(modified) = metadata.modified() {
                        let elapsed = std::time::SystemTime::now()
                            .duration_since(modified)
                            .unwrap_or(std::time::Duration::ZERO);
                        let mins = elapsed.as_secs() / 60;
                        let hours = mins / 60;
                        if hours > 0 {
                            service.uptime = Some(format!("{}h {}m", hours, mins % 60));
                        } else {
                            service.uptime = Some(format!("{}m", mins));
                        }
                    }
                }
                break;
            }
        }
        if !found_running {
            service.status = "stopped".to_string();
        }
    }

    Ok(services)
}

fn parse_env_assignment(line: &str) -> Option<(String, String)> {
    let mut s = line.trim();
    if s.is_empty() || s.starts_with('#') {
        return None;
    }
    if let Some(rest) = s.strip_prefix("export ") {
        s = rest.trim_start();
    }
    let (k, v) = s.split_once('=')?;
    let key = k.trim().to_string();
    let raw = v.trim();
    if raw.is_empty() {
        return Some((key, String::new()));
    }
    let mut val = raw.to_string();
    // Strip matching surrounding quotes.
    if (val.starts_with('"') && val.ends_with('"'))
        || (val.starts_with('\'') && val.ends_with('\''))
    {
        if val.len() >= 2 {
            val = val[1..val.len() - 1].to_string();
        }
    }
    // After stripping quotes, normalize surrounding whitespace (common for secrets like "  ").
    val = val.trim().to_string();
    Some((key, val))
}

fn env_quote_double(value: &str) -> String {
    // Safe for bash `source` in .env-style files.
    let escaped = value.replace('\\', "\\\\").replace('"', "\\\"");
    format!("\"{}\"", escaped)
}

fn env_quote_single(value: &str) -> String {
    let escaped = value.replace('\'', "'\"'\"'");
    format!("'{}'", escaped)
}

fn format_env_value_for_file(value: &str) -> String {
    if value.is_empty() {
        return "\"\"".to_string();
    }

    let safe_unquoted = value.chars().all(|c| {
        c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.' | '/' | ':' | ',' | '@' | '+')
    });
    if safe_unquoted {
        return value.to_string();
    }

    env_quote_single(value)
}

fn update_or_add_env_line(lines: &mut Vec<String>, key: &str, rendered_value: &str) {
    let key_prefix = format!("{}=", key);
    if let Some(line) = lines.iter_mut().find(|l| {
        let s = l.trim_start();
        if s.starts_with(&key_prefix) {
            return true;
        }
        if let Some(rest) = s.strip_prefix("export ") {
            return rest.trim_start().starts_with(&key_prefix);
        }
        false
    }) {
        let s = line.trim_start();
        if s.starts_with("export ") {
            *line = format!("export {}={}", key, rendered_value);
        } else {
            *line = format!("{}={}", key, rendered_value);
        }
    } else {
        lines.push(format!("{}={}", key, rendered_value));
    }
}

fn is_secret_env_key(key: &str) -> bool {
    let up = key.trim().to_uppercase();
    up.contains("KEY")
        || up.contains("TOKEN")
        || up.contains("PASSWORD")
        || up.contains("SECRET")
        || up.contains("AUTH")
}

fn read_env_map(env_path: &PathBuf) -> HashMap<String, String> {
    let mut out: HashMap<String, String> = HashMap::new();
    let Ok(content) = fs::read_to_string(env_path) else {
        return out;
    };
    for line in content.lines() {
        if let Some((key, value)) = parse_env_assignment(line) {
            out.insert(key, value);
        }
    }
    out
}

fn get_config_inner(state: &AppState) -> Result<AppConfig, String> {
    let env_path = format!("{}/.env.v4.local", state.config_path);

    let content =
        fs::read_to_string(&env_path).map_err(|e| format!("Failed to read config: {}", e))?;

    let mut config = AppConfig {
        work_root: String::new(),
        kb_root: String::new(),
        strict_router: false,
        require_new: false,
        rag_backend: "local".to_string(),
    };

    for line in content.lines() {
        let Some((key, value)) = parse_env_assignment(line) else {
            continue;
        };
        match key.as_str() {
            "V4_WORK_ROOT" => config.work_root = value,
            "V4_KB_ROOT" => config.kb_root = value,
            "OPENCLAW_STRICT_ROUTER" => config.strict_router = value.trim() == "1",
            "OPENCLAW_REQUIRE_NEW" => config.require_new = value.trim() == "1",
            "OPENCLAW_RAG_BACKEND" => config.rag_backend = value,
            _ => {}
        }
    }

    Ok(config)
}

fn verify_root(work_root: &str) -> PathBuf {
    PathBuf::from(work_root)
        .join("Translated -EN")
        .join("_VERIFY")
}

fn find_python_bin(state: &AppState) -> String {
    let env_path = PathBuf::from(&state.config_path).join(".env.v4.local");
    if let Ok(content) = fs::read_to_string(&env_path) {
        for line in content.lines() {
            if let Some((key, value)) = parse_env_assignment(line) {
                if key == "V4_PYTHON_BIN" && !value.trim().is_empty() {
                    return value;
                }
            }
        }
    }
    let venv_python = PathBuf::from(&state.config_path)
        .join(".venv")
        .join("bin")
        .join("python");
    if venv_python.exists() {
        return venv_python.to_string_lossy().to_string();
    }
    "python3".to_string()
}

fn dispatcher_notify_target(state: &AppState) -> String {
    let env_path = PathBuf::from(&state.config_path).join(".env.v4.local");
    let env_map = read_env_map(&env_path);
    env_map
        .get("OPENCLAW_NOTIFY_TARGET")
        .cloned()
        .unwrap_or_default()
}

fn run_dispatcher_json(state: &AppState, args: &[&str]) -> Result<serde_json::Value, String> {
    let config = get_config_inner(state)?;
    let python_bin = find_python_bin(state);
    let notify_target = dispatcher_notify_target(state);

    let mut cmd_args: Vec<String> = vec![
        "-m".to_string(),
        "scripts.openclaw_v4_dispatcher".to_string(),
        "--work-root".to_string(),
        config.work_root,
        "--kb-root".to_string(),
        config.kb_root,
        "--notify-target".to_string(),
        notify_target,
    ];
    cmd_args.extend(args.iter().map(|s| s.to_string()));

    let output = Command::new(&python_bin)
        .args(&cmd_args)
        .current_dir(&state.config_path)
        .output()
        .map_err(|e| format!("Failed to run dispatcher {:?}: {}", args, e))?;

    if !output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let detail = if !stderr.trim().is_empty() {
            stderr
        } else {
            stdout
        };
        return Err(format!("dispatcher {:?} failed: {}", args, detail));
    }

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    serde_json::from_str::<serde_json::Value>(&stdout)
        .map_err(|e| format!("Failed to parse dispatcher output: {}", e))
}

fn parse_gateway_status(value: &serde_json::Value) -> GatewayStatus {
    let result = value.get("result").unwrap_or(value);
    let mut providers: HashMap<String, GatewayProviderStatus> = HashMap::new();
    if let Some(obj) = result.get("providers").and_then(|v| v.as_object()) {
        for (k, v) in obj {
            providers.insert(
                k.to_string(),
                GatewayProviderStatus {
                    provider: v.get("provider").and_then(|x| x.as_str()).unwrap_or(k).to_string(),
                    running: v.get("running").and_then(|x| x.as_bool()).unwrap_or(false),
                    healthy: v.get("healthy").and_then(|x| x.as_bool()).unwrap_or(false),
                    logged_in: v.get("logged_in").and_then(|x| x.as_bool()).unwrap_or(false),
                    base_url: v.get("base_url").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    model: v.get("model").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    home_url: v.get("home_url").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    last_error: v.get("last_error").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    updated_at: v.get("updated_at").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    session_checked_at: v.get("session_checked_at").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    profile_dir: v.get("profile_dir").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    last_url: v.get("last_url").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                },
            );
        }
    }
    GatewayStatus {
        running: result
            .get("running")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        healthy: result
            .get("healthy")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        logged_in: result
            .get("logged_in")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        base_url: result
            .get("base_url")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        model: result
            .get("model")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        last_error: result
            .get("last_error")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        updated_at: result
            .get("updated_at")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        version: result
            .get("version")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        primary_provider: result
            .get("primary_provider")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        providers,
    }
}

fn now_iso() -> String {
    Utc::now().to_rfc3339()
}

fn read_pid_file_u32(path: &PathBuf) -> Option<u32> {
    let content = fs::read_to_string(path).ok()?;
    content.trim().parse::<u32>().ok()
}

fn process_is_running(pid: u32) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn runtime_root_from_state(state: &AppState) -> PathBuf {
    PathBuf::from(&state.pids_dir)
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from(&state.pids_dir))
}

fn telegram_pid_file_path(state: &AppState) -> PathBuf {
    PathBuf::from(&state.pids_dir).join("telegram.pid")
}

fn telegram_lock_pid_file_path(state: &AppState) -> PathBuf {
    runtime_root_from_state(state).join("tg_bot.pid")
}

fn telegram_log_file_path(state: &AppState) -> PathBuf {
    PathBuf::from(&state.logs_dir).join("telegram.log")
}

fn read_log_tail(path: &PathBuf, max_lines: usize) -> Vec<String> {
    if max_lines == 0 {
        return Vec::new();
    }
    let Ok(content) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let mut lines: Vec<String> = content.lines().map(|line| line.to_string()).collect();
    if lines.len() > max_lines {
        lines = lines.split_off(lines.len() - max_lines);
    }
    lines
}

fn find_telegram_processes_via_pgrep() -> Vec<u32> {
    let output = Command::new("pgrep")
        .args(["-f", "scripts.telegram_bot"])
        .output();
    let Ok(out) = output else {
        return Vec::new();
    };
    if !out.status.success() {
        return Vec::new();
    }
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(|line| line.trim().parse::<u32>().ok())
        .collect()
}

fn kill_pid_best_effort(pid: u32) {
    let _ = Command::new("kill").arg(pid.to_string()).output();
}

fn cleanup_telegram_processes(state: &AppState) {
    let mut targets: HashSet<u32> = HashSet::new();
    for path in [
        telegram_pid_file_path(state),
        telegram_lock_pid_file_path(state),
    ] {
        if let Some(pid) = read_pid_file_u32(&path) {
            targets.insert(pid);
        }
    }
    for pid in find_telegram_processes_via_pgrep() {
        targets.insert(pid);
    }

    for pid in targets {
        kill_pid_best_effort(pid);
    }

    std::thread::sleep(std::time::Duration::from_millis(800));

    for pid in find_telegram_processes_via_pgrep() {
        if process_is_running(pid) {
            let _ = Command::new("kill").args(["-9", &pid.to_string()]).output();
        }
    }

    let _ = fs::remove_file(telegram_pid_file_path(state));
    let _ = fs::remove_file(telegram_lock_pid_file_path(state));
}

fn diagnose_telegram_health_inner(state: &AppState) -> TelegramHealth {
    let ts = now_iso();
    let log_tail = read_log_tail(&telegram_log_file_path(state), 50);
    // Only evaluate conflict/errors within the most recent bot session to avoid
    // stale log lines (e.g., old 409s) forcing the UI into a restart loop.
    let session_start_idx = log_tail
        .iter()
        .rposition(|line| line.to_lowercase().contains("starting telegram bot poll loop"));
    let session_lines: &[String] = match session_start_idx {
        Some(idx) => &log_tail[idx..],
        None => &log_tail[..],
    };
    let combined = session_lines.join("\n");
    let lower = combined.to_lowercase();
    let network_issue = lower.contains("network error")
        || lower.contains("timed out")
        || lower.contains("reset by peer")
        || lower.contains("temporary failure");
    let pid_lock = read_pid_file_u32(&telegram_lock_pid_file_path(state))
        .map(process_is_running)
        .unwrap_or(false);
    let mut running_pids: HashSet<u32> = HashSet::new();
    for path in [
        telegram_pid_file_path(state),
        telegram_lock_pid_file_path(state),
    ] {
        if let Some(pid) = read_pid_file_u32(&path).filter(|pid| process_is_running(*pid)) {
            running_pids.insert(pid);
        }
    }
    for pid in find_telegram_processes_via_pgrep()
        .into_iter()
        .filter(|pid| process_is_running(*pid))
    {
        running_pids.insert(pid);
    }
    let running = !running_pids.is_empty();
    let single_instance_ok = running_pids.len() <= 1;
    let mut last_error = String::new();
    for line in session_lines.iter().rev() {
        let up = line.to_uppercase();
        if up.contains("ERROR") || up.contains("TRACEBACK") || up.contains("CONFLICT") {
            last_error = line.clone();
            break;
        }
    }
    let last_error_lower = last_error.to_lowercase();
    let poll_conflict = last_error_lower.contains("getupdates conflict")
        || last_error_lower.contains("conflict: terminated by other getupdates request")
        || last_error_lower.contains("error_code\":409")
        || last_error_lower.contains("http 409");
    TelegramHealth {
        running,
        single_instance_ok,
        conflict_409: poll_conflict,
        pid_lock,
        poll_conflict,
        network: if network_issue {
            "degraded".to_string()
        } else {
            "ok".to_string()
        },
        last_error,
        log_tail,
        updated_at: ts,
    }
}

fn start_telegram_process(state: &AppState) -> Result<u32, String> {
    fs::create_dir_all(&state.logs_dir).map_err(|e| format!("Failed to ensure logs dir: {}", e))?;
    fs::create_dir_all(&state.pids_dir).map_err(|e| format!("Failed to ensure pids dir: {}", e))?;
    let log_path = telegram_log_file_path(state);
    let log_file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| format!("Failed to open telegram log {}: {}", log_path.display(), e))?;
    let log_file_err = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone telegram log descriptor: {}", e))?;

    let python_bin = find_python_bin(state);
    let env_path = PathBuf::from(&state.config_path).join(".env.v4.local");
    let env_map = read_env_map(&env_path);
    let mut cmd = Command::new(&python_bin);
    cmd.args(["-m", "scripts.telegram_bot"])
        .current_dir(&state.config_path)
        .stdout(std::process::Stdio::from(log_file))
        .stderr(std::process::Stdio::from(log_file_err));
    if !env_map.is_empty() {
        cmd.envs(env_map);
    }
    let child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn telegram bot via {}: {}", python_bin, e))?;

    let pid = child.id();
    fs::write(telegram_pid_file_path(state), format!("{}", pid))
        .map_err(|e| format!("Failed to write telegram pid file: {}", e))?;
    Ok(pid)
}

fn start_telegram_bot_v2_inner(
    state: &AppState,
    payload: &StartTelegramPayload,
) -> Result<TelegramHealth, String> {
    let force_restart = payload.force_restart.unwrap_or(false);
    let initial = diagnose_telegram_health_inner(state);

    if force_restart || !initial.single_instance_ok || initial.conflict_409 {
        cleanup_telegram_processes(state);
    } else if initial.running && initial.single_instance_ok {
        return Ok(initial);
    }

    let mut attempt = 0usize;
    while attempt < 2 {
        attempt += 1;
        let _ = start_telegram_process(state)?;
        std::thread::sleep(std::time::Duration::from_secs(2));
        let health = diagnose_telegram_health_inner(state);
        if health.running && health.single_instance_ok && !health.conflict_409 {
            return Ok(health);
        }
        cleanup_telegram_processes(state);
        std::thread::sleep(std::time::Duration::from_millis(
            500 + (attempt as u64 * 500),
        ));
    }
    let final_health = diagnose_telegram_health_inner(state);
    Err(format!(
        "Failed to stabilize telegram bot (running={}, single_instance_ok={}, conflict_409={}, last_error={})",
        final_health.running, final_health.single_instance_ok, final_health.conflict_409, final_health.last_error
    ))
}

fn stop_telegram_inner(state: &AppState) -> Result<(), String> {
    let _ = run_start_script(state, "--stop-telegram");
    cleanup_telegram_processes(state);
    let health = diagnose_telegram_health_inner(state);
    if health.running {
        return Err("telegram still running after stop attempt".to_string());
    }
    Ok(())
}

fn audit_operation_inner(
    state: &AppState,
    payload: &AuditOperationPayload,
) -> Result<serde_json::Value, String> {
    let mut args: Vec<String> = vec![
        "ops-audit".to_string(),
        "--source".to_string(),
        if payload.source.trim().is_empty() {
            "tauri".to_string()
        } else {
            payload.source.clone()
        },
        "--action".to_string(),
        payload.action.clone(),
        "--status".to_string(),
        if payload.status.trim().is_empty() {
            "success".to_string()
        } else {
            payload.status.clone()
        },
        "--summary".to_string(),
        payload.summary.clone(),
    ];
    if let Some(op_id) = payload
        .operation_id
        .as_ref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        args.push("--operation-id".to_string());
        args.push(op_id.to_string());
    }
    if let Some(job_id) = payload
        .job_id
        .as_ref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        args.push("--job-id".to_string());
        args.push(job_id.to_string());
    }
    if let Some(sender) = payload
        .sender
        .as_ref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        args.push("--sender".to_string());
        args.push(sender.to_string());
    }
    if let Some(detail) = payload.detail.as_ref() {
        args.push("--detail-json".to_string());
        args.push(detail.to_string());
    }
    let refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    run_dispatcher_json(state, &refs)
}

fn best_effort_audit_operation(state: &AppState, payload: AuditOperationPayload) {
    let _ = audit_operation_inner(state, &payload);
}

fn fmt_epoch_ms(ms: i64) -> String {
    // Best-effort local timestamp formatting (human-friendly). Fallback: raw ms.
    match Local.timestamp_millis_opt(ms).single() {
        Some(dt) => dt.format("%Y-%m-%d %H:%M:%S").to_string(),
        None => ms.to_string(),
    }
}

fn find_openclaw_bin() -> Option<String> {
    // 1) PATH lookup (best in dev environments)
    if let Ok(path_env) = std::env::var("PATH") {
        for dir in path_env.split(':') {
            if dir.trim().is_empty() {
                continue;
            }
            let cand = PathBuf::from(dir).join("openclaw");
            if cand.exists() {
                return Some(cand.to_string_lossy().to_string());
            }
        }
    }

    // 2) Common install locations
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
    let candidates = [
        format!("{}/.local/bin/openclaw", home),
        "/usr/local/bin/openclaw".to_string(),
        "/opt/homebrew/bin/openclaw".to_string(),
        "/usr/bin/openclaw".to_string(),
    ];
    for cand in candidates {
        if PathBuf::from(&cand).exists() {
            return Some(cand);
        }
    }
    None
}

fn run_openclaw_json(args: &[&str]) -> Result<serde_json::Value, String> {
    let bin = find_openclaw_bin().ok_or("OpenClaw not found in PATH or common locations")?;
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());

    let output = Command::new(&bin)
        .args(args)
        .env("HOME", &home)
        .env(
            "PATH",
            format!(
                "{}:{}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
                std::env::var("PATH").unwrap_or_default(),
                home
            ),
        )
        .output()
        .map_err(|e| format!("Failed to run openclaw {:?}: {}", args, e))?;

    if !output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let detail = if !stderr.trim().is_empty() {
            stderr
        } else {
            stdout
        };
        return Err(format!(
            "openclaw {:?} exited with code {:?}: {}",
            args,
            output.status.code(),
            detail
        ));
    }

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    serde_json::from_str(&stdout)
        .map_err(|e| format!("Failed to parse openclaw JSON output: {}", e))
}

fn compute_fallbacks_with_kimi_defaults(
    current: Vec<String>,
    kimi_model: &str,
    kimi_alt_model: &str,
    fallback_model: &str,
) -> Vec<String> {
    let kimi = kimi_model.trim();
    let kimi_alt = kimi_alt_model.trim();
    let fallback = fallback_model.trim();
    let mut seen: HashSet<String> = HashSet::new();
    let mut out: Vec<String> = Vec::new();

    for item in current {
        let m = item.trim();
        if m.is_empty() {
            continue;
        }
        if (!kimi.is_empty() && m == kimi)
            || (!kimi_alt.is_empty() && m == kimi_alt)
            || (!fallback.is_empty() && m == fallback)
        {
            continue;
        }
        if seen.insert(m.to_string()) {
            out.push(m.to_string());
        }
    }

    let mut head: Vec<String> = Vec::new();
    for model in [kimi, kimi_alt, fallback] {
        if model.is_empty() {
            continue;
        }
        if !head.iter().any(|m| m == model) {
            head.push(model.to_string());
        }
    }

    if head.is_empty() {
        return out;
    }

    let mut non_glm: Vec<String> = Vec::new();
    let mut glm: Vec<String> = Vec::new();
    for model in out {
        if model.starts_with("zai/glm-") {
            glm.push(model);
        } else {
            non_glm.push(model);
        }
    }

    let mut desired: Vec<String> = Vec::new();
    desired.extend(head);
    desired.extend(non_glm);
    desired.extend(glm);

    let mut deduped: Vec<String> = Vec::new();
    let mut dedup_seen: HashSet<String> = HashSet::new();
    for model in desired {
        if dedup_seen.insert(model.clone()) {
            deduped.push(model);
        }
    }
    deduped
}

fn run_openclaw_cmd(args: &[&str]) -> Result<(), String> {
    let bin = find_openclaw_bin().ok_or("OpenClaw not found in PATH or common locations")?;
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
    let path_env = format!(
        "{}:{}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
        std::env::var("PATH").unwrap_or_default(),
        home
    );

    let output = Command::new(&bin)
        .args(args)
        .env("HOME", &home)
        .env("PATH", &path_env)
        .output()
        .map_err(|e| format!("Failed to run openclaw {:?}: {}", args, e))?;

    if !output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let detail = if !stderr.trim().is_empty() {
            stderr
        } else {
            stdout
        };
        return Err(format!(
            "openclaw {:?} exited with code {:?}: {}",
            args,
            output.status.code(),
            detail
        ));
    }
    Ok(())
}

fn set_agent_default_model(agent_id: &str, model: &str) -> Result<(), String> {
    if agent_id.trim().is_empty() || model.trim().is_empty() {
        return Ok(());
    }
    run_openclaw_cmd(&["models", "--agent", agent_id.trim(), "set", model.trim()])
}

fn set_agent_image_model(agent_id: &str, model: &str) -> Result<(), String> {
    if agent_id.trim().is_empty() || model.trim().is_empty() {
        return Ok(());
    }
    run_openclaw_cmd(&[
        "models",
        "--agent",
        agent_id.trim(),
        "set-image",
        model.trim(),
    ])
}

fn force_agent_model_in_openclaw_config(agent_id: &str, model: &str) -> Result<(), String> {
    if agent_id.trim().is_empty() || model.trim().is_empty() {
        return Ok(());
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
    let config_path = PathBuf::from(home).join(".openclaw").join("openclaw.json");
    if !config_path.exists() {
        return Ok(());
    }

    let raw = fs::read_to_string(&config_path).map_err(|e| {
        format!(
            "Failed to read OpenClaw config {}: {}",
            config_path.display(),
            e
        )
    })?;
    let mut root: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| format!("Failed to parse OpenClaw config JSON: {}", e))?;

    let mut changed = false;
    if let Some(list) = root
        .get_mut("agents")
        .and_then(|v| v.get_mut("list"))
        .and_then(|v| v.as_array_mut())
    {
        for item in list {
            let id = item.get("id").and_then(|v| v.as_str()).unwrap_or_default();
            if id == agent_id.trim() {
                let cur = item
                    .get("model")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default();
                if cur != model.trim() {
                    item["model"] = serde_json::Value::String(model.trim().to_string());
                    changed = true;
                }
            }
        }
    }

    if changed {
        let text = serde_json::to_string_pretty(&root)
            .map_err(|e| format!("Failed to serialize OpenClaw config JSON: {}", e))?;
        fs::write(&config_path, text).map_err(|e| {
            format!(
                "Failed to write OpenClaw config {}: {}",
                config_path.display(),
                e
            )
        })?;
    }
    Ok(())
}

fn apply_fallbacks(new_list: &[String]) -> Result<(), String> {
    run_openclaw_cmd(&["models", "fallbacks", "clear"])?;
    for model in new_list {
        let m = model.trim();
        if m.is_empty() {
            continue;
        }
        run_openclaw_cmd(&["models", "fallbacks", "add", m])?;
    }
    Ok(())
}

#[derive(Debug, Clone, Default)]
struct ProviderAuthSummary {
    total_profiles: usize,
    cooldown_profiles: usize,
    cooldown_until_ms: Option<i64>,
    oauth_seen: bool,
    oauth_has_valid: bool,
    api_key_seen: bool,
}

fn provider_summaries_from_models_status(
    models_status: &serde_json::Value,
) -> HashMap<String, ProviderAuthSummary> {
    let mut out: HashMap<String, ProviderAuthSummary> = HashMap::new();

    let profiles = models_status
        .pointer("/auth/oauth/profiles")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    for p in profiles {
        let Some(provider) = p.get("provider").and_then(|v| v.as_str()) else {
            continue;
        };
        if provider.trim().is_empty() {
            continue;
        }
        let entry = out.entry(provider.to_string()).or_default();
        entry.total_profiles += 1;

        let ptype = p.get("type").and_then(|v| v.as_str()).unwrap_or("");
        if ptype == "oauth" {
            entry.oauth_seen = true;
            let remaining_ms = p.get("remainingMs").and_then(|v| v.as_i64()).unwrap_or(0);
            if remaining_ms > 0 {
                entry.oauth_has_valid = true;
            }
        } else if ptype == "api_key" {
            entry.api_key_seen = true;
        }
    }

    let unusable = models_status
        .pointer("/auth/unusableProfiles")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    for u in unusable {
        let Some(provider) = u.get("provider").and_then(|v| v.as_str()) else {
            continue;
        };
        if provider.trim().is_empty() {
            continue;
        }
        let entry = out.entry(provider.to_string()).or_default();
        entry.cooldown_profiles += 1;
        if let Some(until) = u.get("until").and_then(|v| v.as_i64()) {
            entry.cooldown_until_ms = Some(
                entry
                    .cooldown_until_ms
                    .map_or(until, |prev| prev.max(until)),
            );
        }
    }

    out
}

fn models_available_map(models_list: &serde_json::Value) -> HashMap<String, bool> {
    let mut out: HashMap<String, bool> = HashMap::new();
    let Some(models) = models_list.get("models").and_then(|v| v.as_array()) else {
        return out;
    };
    for m in models {
        let Some(key) = m.get("key").and_then(|v| v.as_str()) else {
            continue;
        };
        if let Some(avail) = m.get("available").and_then(|v| v.as_bool()) {
            out.insert(key.to_string(), avail);
        }
    }
    out
}

fn has_provider_profile(
    models_status: &serde_json::Value,
    provider: &str,
    profile_type: Option<&str>,
) -> bool {
    let Some(profiles) = models_status
        .pointer("/auth/oauth/profiles")
        .and_then(|v| v.as_array())
    else {
        return false;
    };
    profiles.iter().any(|p| {
        let Some(p_provider) = p.get("provider").and_then(|v| v.as_str()) else {
            return false;
        };
        if p_provider != provider {
            return false;
        }
        match profile_type {
            Some(t) => p.get("type").and_then(|v| v.as_str()) == Some(t),
            None => true,
        }
    })
}

fn compute_agent_availability(
    agent_id: &str,
    models_status: &serde_json::Value,
    availability: &HashMap<String, bool>,
) -> AgentAvailability {
    let resolved_default = models_status
        .get("resolvedDefault")
        .and_then(|v| v.as_str())
        .or_else(|| models_status.get("defaultModel").and_then(|v| v.as_str()))
        .unwrap_or("")
        .to_string();

    let fallbacks: Vec<String> = models_status
        .get("fallbacks")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();

    let mut route_models: Vec<String> = Vec::new();
    let mut seen_models: HashSet<String> = HashSet::new();
    for item in std::iter::once(resolved_default.clone()).chain(fallbacks.clone()) {
        let m = item.trim();
        if m.is_empty() {
            continue;
        }
        if seen_models.insert(m.to_string()) {
            route_models.push(m.to_string());
        }
    }

    let provider_summaries = provider_summaries_from_models_status(models_status);

    let mut route: Vec<RouteModelStatus> = Vec::new();
    for model in &route_models {
        let provider = model.split('/').next().unwrap_or("").to_string();
        let available = availability.get(model).copied();
        let summary = provider_summaries
            .get(&provider)
            .cloned()
            .unwrap_or_default();

        let provider_all_in_cooldown =
            summary.total_profiles > 0 && summary.cooldown_profiles >= summary.total_profiles;
        let oauth_expired = summary.oauth_seen && !summary.oauth_has_valid;
        let auth_ok = if summary.oauth_seen {
            summary.oauth_has_valid
        } else if summary.api_key_seen {
            true
        } else {
            false
        };

        let mut note: Option<String> = None;
        if available.is_none() {
            note = Some("Model not found in `openclaw models list` output.".to_string());
        } else if available == Some(true) && !auth_ok && summary.total_profiles == 0 {
            note = Some("No auth profiles found for this provider.".to_string());
        }

        let state = if available == Some(false) {
            "unavailable"
        } else if provider_all_in_cooldown {
            "cooldown"
        } else if summary.oauth_seen && oauth_expired {
            "expired"
        } else if available == Some(true) && auth_ok {
            "ok"
        } else {
            "unknown"
        }
        .to_string();

        route.push(RouteModelStatus {
            model: model.to_string(),
            provider: provider.clone(),
            available,
            state: state.clone(),
            cooldown_until_ms: if state == "cooldown" {
                summary.cooldown_until_ms
            } else {
                None
            },
            auth_expired: if summary.oauth_seen {
                Some(oauth_expired)
            } else {
                None
            },
            note,
        });
    }

    let first_runnable_model = route
        .iter()
        .find(|r| r.state == "ok")
        .map(|r| r.model.clone());
    let runnable_now = first_runnable_model.is_some();

    let mut blocked_reasons: Vec<String> = Vec::new();
    if !runnable_now {
        let mut seen_providers: HashSet<String> = HashSet::new();
        for item in &route {
            if !seen_providers.insert(item.provider.clone()) {
                continue;
            }
            let summary = provider_summaries
                .get(&item.provider)
                .cloned()
                .unwrap_or_default();
            let provider_all_in_cooldown =
                summary.total_profiles > 0 && summary.cooldown_profiles >= summary.total_profiles;
            let oauth_expired = summary.oauth_seen && !summary.oauth_has_valid;

            if provider_all_in_cooldown {
                if let Some(until) = summary.cooldown_until_ms {
                    blocked_reasons.push(format!(
                        "{} is in cooldown until {} ({}).",
                        item.provider,
                        fmt_epoch_ms(until),
                        until
                    ));
                } else {
                    blocked_reasons.push(format!("{} is in cooldown.", item.provider));
                }
                continue;
            }

            if route
                .iter()
                .any(|r| r.provider == item.provider && r.state == "unavailable")
            {
                blocked_reasons.push(format!("{} models are unavailable.", item.provider));
                continue;
            }

            if summary.oauth_seen && oauth_expired {
                blocked_reasons.push(format!(
                    "{} OAuth appears expired or missing.",
                    item.provider
                ));
                continue;
            }

            if summary.total_profiles == 0 {
                blocked_reasons.push(format!(
                    "{} has no auth profiles configured.",
                    item.provider
                ));
                continue;
            }

            if route
                .iter()
                .all(|r| r.provider != item.provider || r.state == "unknown")
            {
                blocked_reasons.push(format!("{} availability is unknown.", item.provider));
            }
        }

        if blocked_reasons.is_empty() {
            blocked_reasons.push("No runnable route model found.".to_string());
        }
    }

    AgentAvailability {
        agent_id: agent_id.to_string(),
        default_model: resolved_default,
        fallbacks,
        route,
        runnable_now,
        first_runnable_model,
        blocked_reasons,
    }
}

fn compute_model_availability_report_inner(
    state: &AppState,
) -> Result<ModelAvailabilityReport, String> {
    let models_list = run_openclaw_json(&["models", "list", "--json"])?;
    let availability = models_available_map(&models_list);

    let translator_status =
        run_openclaw_json(&["models", "status", "--agent", "translator-core", "--json"])?;
    let review_status =
        run_openclaw_json(&["models", "status", "--agent", "review-core", "--json"])?;

    let mut agents: HashMap<String, AgentAvailability> = HashMap::new();
    let translator =
        compute_agent_availability("translator-core", &translator_status, &availability);
    let review = compute_agent_availability("review-core", &review_status, &availability);
    agents.insert(translator.agent_id.clone(), translator);
    agents.insert(review.agent_id.clone(), review);

    let env_path = PathBuf::from(&state.config_path).join(".env.v4.local");
    let env_map = read_env_map(&env_path);

    let has_google_api_key = env_map
        .get("GOOGLE_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    let has_gemini_api_key = env_map
        .get("GEMINI_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    let has_moonshot_api_key = env_map
        .get("MOONSHOT_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false)
        || has_provider_profile(&translator_status, "moonshot", Some("api_key"));
    let has_openai_api_key = env_map
        .get("OPENAI_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false)
        || has_provider_profile(&translator_status, "openai-codex", Some("api_key"));

    let vision_backend = env_map
        .get("OPENCLAW_VISION_BACKEND")
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty());

    let backend_norm = vision_backend
        .as_deref()
        .unwrap_or("auto")
        .trim()
        .to_lowercase();

    let vision_model = if backend_norm == "moonshot" || backend_norm == "kimi" {
        env_map
            .get("OPENCLAW_MOONSHOT_VISION_MODEL")
            .or_else(|| env_map.get("OPENCLAW_KIMI_VISION_MODEL"))
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
    } else if backend_norm == "openai" || backend_norm == "openai-codex" {
        env_map
            .get("OPENCLAW_OPENAI_VISION_MODEL")
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
    } else if backend_norm == "gemini" || backend_norm == "google" {
        env_map
            .get("OPENCLAW_GEMINI_VISION_MODEL")
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
    } else if has_google_api_key || has_gemini_api_key {
        env_map
            .get("OPENCLAW_GEMINI_VISION_MODEL")
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
    } else {
        env_map
            .get("OPENCLAW_MOONSHOT_VISION_MODEL")
            .or_else(|| env_map.get("OPENCLAW_KIMI_VISION_MODEL"))
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
            .or_else(|| {
                env_map
                    .get("OPENCLAW_OPENAI_VISION_MODEL")
                    .map(|v| v.trim().to_string())
                    .filter(|v| !v.is_empty())
            })
    };

    let glm_enabled = env_map
        .get("OPENCLAW_GLM_ENABLED")
        .map(|v| v.trim() == "1")
        .unwrap_or(false);
    let has_glm_api_key = env_map
        .get("GLM_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    let has_zai_profile = has_provider_profile(&translator_status, "zai", Some("api_key"));

    let fetched_at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;

    Ok(ModelAvailabilityReport {
        fetched_at,
        agents,
        vision: VisionAvailability {
            has_google_api_key,
            has_gemini_api_key,
            has_moonshot_api_key,
            has_openai_api_key,
            vision_backend,
            vision_model,
        },
        glm: GlmAvailability {
            glm_enabled,
            has_glm_api_key,
            has_zai_profile,
        },
    })
}

fn run_start_script(state: &AppState, flag: &str) -> Result<String, String> {
    let start_script = format!("{}/start.sh", state.scripts_path);
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());

    let output = Command::new("bash")
        .arg(&start_script)
        .arg(flag)
        .current_dir(&state.config_path)
        .env("HOME", &home)
        .env(
            "PATH",
            format!(
                "{}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                home
            ),
        )
        .env("TERM", "dumb")
        .output()
        .map_err(|e| format!("Failed to execute start.sh {}: {}", flag, e))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        let detail = if !stderr.is_empty() { &stderr } else { &stdout };
        return Err(format!(
            "start.sh {} exited with code {:?}: {}",
            flag,
            output.status.code(),
            detail
        ));
    }

    Ok(stdout)
}

fn stop_services_inner(state: &AppState) -> Result<(), String> {
    run_start_script(state, "--stop")?;
    Ok(())
}

fn start_services_inner(state: &AppState) -> Result<(), String> {
    run_start_script(state, "--all")?;
    Ok(())
}

#[tauri::command]
fn audit_operation(
    payload: AuditOperationPayload,
    state: State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    audit_operation_inner(&state, &payload)
}

#[tauri::command]
fn gateway_status(state: State<'_, AppState>) -> Result<GatewayStatus, String> {
    let out = run_dispatcher_json(&state, &["gateway-status"])?;
    Ok(parse_gateway_status(&out))
}

#[tauri::command]
fn gateway_start(state: State<'_, AppState>) -> Result<GatewayStatus, String> {
    let out = run_dispatcher_json(&state, &["gateway-start"])?;
    if !out.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        return Err(format!("gateway-start failed: {}", out));
    }
    Ok(parse_gateway_status(&out))
}

#[tauri::command]
fn gateway_stop(state: State<'_, AppState>) -> Result<GatewayStatus, String> {
    let out = run_dispatcher_json(&state, &["gateway-stop"])?;
    if !out.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        return Err(format!("gateway-stop failed: {}", out));
    }
    Ok(parse_gateway_status(&out))
}

#[tauri::command]
fn gateway_login(
    state: State<'_, AppState>,
    provider: Option<String>,
    interactive_login: Option<bool>,
    timeout_seconds: Option<u32>,
) -> Result<GatewayStatus, String> {
    let mut args: Vec<String> = vec!["gateway-login".to_string()];
    if let Some(p) = provider {
        let p_norm = p.trim().to_string();
        if !p_norm.is_empty() {
            args.push("--provider".to_string());
            args.push(p_norm);
        }
    }
    if interactive_login.unwrap_or(false) {
        args.push("--interactive-login".to_string());
    }
    if let Some(ts) = timeout_seconds {
        args.push("--timeout-seconds".to_string());
        args.push(ts.to_string());
    }
    let args_ref: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    let out = run_dispatcher_json(&state, &args_ref)?;
    if !out.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        return Err(format!("gateway-login failed: {}", out));
    }
    Ok(parse_gateway_status(&out))
}

// ============================================================================
// Service Management Commands
// ============================================================================

#[tauri::command]
async fn get_service_status(state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    get_service_status_inner(&state)
}

#[tauri::command]
async fn start_all_services(state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    let result = async {
        start_services_inner(&state)?;
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
        get_service_status_inner(&state)
    }
    .await;
    match &result {
        Ok(services) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_start".to_string(),
                status: "success".to_string(),
                summary: "start_all_services completed".to_string(),
                detail: Some(serde_json::json!({ "scope": "all", "services": services })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_start".to_string(),
                status: "failed".to_string(),
                summary: "start_all_services failed".to_string(),
                detail: Some(serde_json::json!({ "scope": "all", "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

#[tauri::command]
async fn stop_all_services(state: State<'_, AppState>) -> Result<(), String> {
    let result = stop_services_inner(&state);
    match &result {
        Ok(_) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_stop".to_string(),
                status: "success".to_string(),
                summary: "stop_all_services completed".to_string(),
                detail: Some(serde_json::json!({ "scope": "all" })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_stop".to_string(),
                status: "failed".to_string(),
                summary: "stop_all_services failed".to_string(),
                detail: Some(serde_json::json!({ "scope": "all", "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

#[tauri::command]
async fn restart_all_services(state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    let result = async {
        stop_services_inner(&state)?;
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
        start_services_inner(&state)?;
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
        get_service_status_inner(&state)
    }
    .await;
    match &result {
        Ok(services) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_restart".to_string(),
                status: "success".to_string(),
                summary: "restart_all_services completed".to_string(),
                detail: Some(serde_json::json!({ "scope": "all", "services": services })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_restart".to_string(),
                status: "failed".to_string(),
                summary: "restart_all_services failed".to_string(),
                detail: Some(serde_json::json!({ "scope": "all", "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

fn service_flag(service_id: &str, action: &str) -> Result<&'static str, String> {
    match (service_id, action) {
        ("telegram", "start") => Ok("--telegram"),
        ("worker", "start") => Ok("--worker"),
        ("telegram", "stop") => Ok("--stop-telegram"),
        ("worker", "stop") => Ok("--stop-worker"),
        ("telegram", "restart") => Ok("--restart-telegram"),
        ("worker", "restart") => Ok("--restart-worker"),
        _ => Err(format!("Unknown service/action: {} {}", service_id, action)),
    }
}

#[tauri::command]
async fn start_service(
    service_id: String,
    state: State<'_, AppState>,
) -> Result<Vec<ServiceStatus>, String> {
    let service_name = service_id.clone();
    let result = async {
        let flag = service_flag(service_id.trim(), "start")?;
        run_start_script(&state, flag)?;
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        get_service_status_inner(&state)
    }
    .await;
    match &result {
        Ok(services) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_start".to_string(),
                status: "success".to_string(),
                summary: format!("start_service:{} completed", service_name),
                detail: Some(serde_json::json!({ "service": service_name, "services": services })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_start".to_string(),
                status: "failed".to_string(),
                summary: format!("start_service:{} failed", service_name),
                detail: Some(serde_json::json!({ "service": service_name, "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

#[tauri::command]
async fn stop_service(
    service_id: String,
    state: State<'_, AppState>,
) -> Result<Vec<ServiceStatus>, String> {
    let service_name = service_id.clone();
    let result = async {
        let flag = service_flag(service_id.trim(), "stop")?;
        run_start_script(&state, flag)?;
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        get_service_status_inner(&state)
    }
    .await;
    match &result {
        Ok(services) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_stop".to_string(),
                status: "success".to_string(),
                summary: format!("stop_service:{} completed", service_name),
                detail: Some(serde_json::json!({ "service": service_name, "services": services })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_stop".to_string(),
                status: "failed".to_string(),
                summary: format!("stop_service:{} failed", service_name),
                detail: Some(serde_json::json!({ "service": service_name, "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

#[tauri::command]
async fn restart_service(
    service_id: String,
    state: State<'_, AppState>,
) -> Result<Vec<ServiceStatus>, String> {
    let service_name = service_id.clone();
    let result = async {
        let flag = service_flag(service_id.trim(), "restart")?;
        run_start_script(&state, flag)?;
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
        get_service_status_inner(&state)
    }
    .await;
    match &result {
        Ok(services) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_restart".to_string(),
                status: "success".to_string(),
                summary: format!("restart_service:{} completed", service_name),
                detail: Some(serde_json::json!({ "service": service_name, "services": services })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "service_restart".to_string(),
                status: "failed".to_string(),
                summary: format!("restart_service:{} failed", service_name),
                detail: Some(serde_json::json!({ "service": service_name, "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

// ============================================================================
// Preflight Check Commands
// ============================================================================

#[tauri::command]
fn auto_fix_preflight(state: State<'_, AppState>) -> Result<Vec<PreflightCheck>, String> {
    // Try to create venv if missing
    let venv_path = format!("{}/.venv", state.config_path);
    if !PathBuf::from(&venv_path).exists() {
        let _ = Command::new("python3")
            .args(["-m", "venv", &venv_path])
            .current_dir(&state.config_path)
            .status();
    }

    // Try to install requirements if venv exists
    let req_path = format!("{}/requirements.txt", state.config_path);
    let pip_path = format!("{}/bin/pip", venv_path);
    if PathBuf::from(&pip_path).exists() && PathBuf::from(&req_path).exists() {
        let _ = Command::new(&pip_path)
            .args(["install", "-r", &req_path, "-q"])
            .current_dir(&state.config_path)
            .status();
    }

    // Try to create .env.v4.local template if missing
    let env_path = format!("{}/.env.v4.local", state.config_path);
    if !PathBuf::from(&env_path).exists() {
        let template = r#"# Translation system configuration
	V4_WORK_ROOT=
	V4_KB_ROOT=
	OPENCLAW_STRICT_ROUTER=0
	OPENCLAW_REQUIRE_NEW=0
	OPENCLAW_RAG_BACKEND=local
	OPENCLAW_KIMI_MODEL=moonshot/kimi-k2.5
	OPENCLAW_KIMI_ALT_MODEL=kimi-coding/k2p5
	OPENCLAW_PRIMARY_MODEL=openai-codex/gpt-5.2
	OPENCLAW_FALLBACK_MODEL=kimi-coding/k2p5
	OPENCLAW_IMAGE_MODEL=openai-codex/gpt-5.2
	# Vision QA backend: auto | gemini | moonshot | openai
	OPENCLAW_VISION_BACKEND=openai
	# Optional model overrides:
	# OPENCLAW_GEMINI_VISION_MODEL=gemini-3-pro
	# OPENCLAW_MOONSHOT_VISION_MODEL=moonshot/kimi-k2.5
	# OPENCLAW_OPENAI_VISION_MODEL=openai-codex/gpt-5.2
	"#;
        let _ = fs::write(&env_path, template);
    }

    // Best-effort: ensure Kimi fallback is before any GLM fallbacks.
    // This enables failover to Kimi when Codex/Gemini are unavailable, without live probing.
    if let Some(bin) = find_openclaw_bin() {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
        let health_ok = Command::new(&bin)
            .args(["health", "--json"])
            .env("HOME", &home)
            .env(
                "PATH",
                format!(
                    "{}:{}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
                    std::env::var("PATH").unwrap_or_default(),
                    home
                ),
            )
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);

        if health_ok {
            let env_map = read_env_map(&PathBuf::from(&state.config_path).join(".env.v4.local"));
            let kimi_model = env_map
                .get("OPENCLAW_KIMI_MODEL")
                .cloned()
                .filter(|v| !v.trim().is_empty())
                .unwrap_or_else(|| "moonshot/kimi-k2.5".to_string());
            let kimi_alt_model = env_map
                .get("OPENCLAW_KIMI_ALT_MODEL")
                .cloned()
                .filter(|v| !v.trim().is_empty())
                .unwrap_or_else(|| "kimi-coding/k2p5".to_string());
            let primary_model = env_map
                .get("OPENCLAW_PRIMARY_MODEL")
                .cloned()
                .filter(|v| !v.trim().is_empty())
                .unwrap_or_else(|| "openai-codex/gpt-5.2".to_string());
            let image_model = env_map
                .get("OPENCLAW_IMAGE_MODEL")
                .cloned()
                .filter(|v| !v.trim().is_empty())
                .unwrap_or_else(|| primary_model.clone());
            let fallback_model = env_map
                .get("OPENCLAW_FALLBACK_MODEL")
                .cloned()
                .filter(|v| !v.trim().is_empty())
                .unwrap_or_else(|| kimi_alt_model.clone());

            // Enforce configured defaults for global model and core agents.
            let _ = run_openclaw_cmd(&["models", "set", primary_model.as_str()]);
            for agent in [
                "task-router",
                "translator-core",
                "review-core",
                "qa-gate",
                "glm-reviewer",
            ] {
                let _ = set_agent_default_model(agent, primary_model.as_str());
                let _ = force_agent_model_in_openclaw_config(agent, primary_model.as_str());
            }

            if let Ok(json) = run_openclaw_json(&["models", "fallbacks", "list", "--json"]) {
                let current: Vec<String> = json
                    .get("fallbacks")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|v| v.as_str().map(|s| s.to_string()))
                            .collect::<Vec<String>>()
                    })
                    .unwrap_or_default();

                let desired = compute_fallbacks_with_kimi_defaults(
                    current.clone(),
                    &kimi_model,
                    &kimi_alt_model,
                    &fallback_model,
                );
                if desired != current {
                    let _ = apply_fallbacks(&desired);
                }
            }

            // Best-effort: align image model defaults for vision workflows.
            let _ = run_openclaw_cmd(&["models", "set-image", image_model.as_str()]);
            for agent in [
                "task-router",
                "translator-core",
                "review-core",
                "qa-gate",
                "glm-reviewer",
            ] {
                let _ = set_agent_image_model(agent, image_model.as_str());
            }
        }
    }

    // Re-run preflight checks
    let checks = run_preflight_check_inner(&state);
    best_effort_audit_operation(
        &state,
        AuditOperationPayload {
            source: "tauri".to_string(),
            action: "preflight_autofix".to_string(),
            status: "success".to_string(),
            summary: "auto_fix_preflight completed".to_string(),
            detail: Some(serde_json::json!({ "checks": checks.clone() })),
            ..AuditOperationPayload::default()
        },
    );
    Ok(checks)
}

fn run_preflight_check_inner(state: &AppState) -> Vec<PreflightCheck> {
    let mut checks = Vec::new();

    // Python check
    let python_ok = Command::new("python3")
        .args(["--version"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    checks.push(PreflightCheck {
        name: "Python".to_string(),
        key: "python".to_string(),
        status: if python_ok {
            "pass".to_string()
        } else {
            "blocker".to_string()
        },
        message: if python_ok {
            "Python 3 available".to_string()
        } else {
            "Python 3 not found".to_string()
        },
    });

    // venv check
    let venv_path = format!("{}/.venv", state.config_path);
    let venv_exists = PathBuf::from(&venv_path).exists();

    checks.push(PreflightCheck {
        name: "venv".to_string(),
        key: "venv".to_string(),
        status: if venv_exists {
            "pass".to_string()
        } else {
            "blocker".to_string()
        },
        message: if venv_exists {
            "Virtual environment exists".to_string()
        } else {
            "Run: python -m venv .venv".to_string()
        },
    });

    // requirements check
    let req_path = format!("{}/requirements.txt", state.config_path);
    let req_exists = PathBuf::from(&req_path).exists();

    checks.push(PreflightCheck {
        name: "requirements".to_string(),
        key: "requirements".to_string(),
        status: if req_exists {
            "pass".to_string()
        } else {
            "warning".to_string()
        },
        message: if req_exists {
            "requirements.txt found".to_string()
        } else {
            "requirements.txt not found".to_string()
        },
    });

    // .env check
    let env_path = format!("{}/.env.v4.local", state.config_path);
    let env_exists = PathBuf::from(&env_path).exists();

    checks.push(PreflightCheck {
        name: ".env.v4.local".to_string(),
        key: "env".to_string(),
        status: if env_exists {
            "pass".to_string()
        } else {
            "blocker".to_string()
        },
        message: if env_exists {
            "Config file exists".to_string()
        } else {
            "Create .env.v4.local from template".to_string()
        },
    });

    // Parse env once for mode-dependent checks.
    let env_map = read_env_map(&PathBuf::from(&state.config_path).join(".env.v4.local"));
    let web_gateway_enabled = env_map
        .get("OPENCLAW_WEB_GATEWAY_ENABLED")
        .map(|v| {
            let s = v.trim().to_ascii_lowercase();
            !matches!(s.as_str(), "" | "0" | "false" | "off" | "no")
        })
        .unwrap_or(false);

    // OpenClaw check - try multiple paths with proper environment
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
    let openclaw_paths = [
        format!("{}/.local/bin/openclaw", home),
        "/usr/local/bin/openclaw".to_string(),
        "/opt/homebrew/bin/openclaw".to_string(),
    ];

    let mut openclaw_ok = false;
    for path in &openclaw_paths {
        if !std::path::Path::new(path).exists() {
            continue;
        }
        let result = Command::new(path)
            .args(["health", "--json"])
            .env("HOME", &home)
            .env(
                "PATH",
                format!(
                    "{}:{}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
                    std::env::var("PATH").unwrap_or_default(),
                    home
                ),
            )
            .output();
        if let Ok(output) = result {
            if output.status.success() {
                openclaw_ok = true;
                break;
            }
        }
    }

    checks.push(PreflightCheck {
        name: "OpenClaw".to_string(),
        key: "openclaw".to_string(),
        status: if openclaw_ok {
            "pass".to_string()
        } else if web_gateway_enabled {
            "warning".to_string()
        } else {
            "blocker".to_string()
        },
        message: if openclaw_ok {
            "OpenClaw is running".to_string()
        } else if web_gateway_enabled {
            "OpenClaw gateway is optional in web gateway mode.".to_string()
        } else {
            "Run: openclaw gateway --force".to_string()
        },
    });

    // Model availability checks (fast status; no live probes)
    let vision_has_google = env_map
        .get("GOOGLE_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    let vision_has_gemini = env_map
        .get("GEMINI_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    let vision_has_moonshot_env = env_map
        .get("MOONSHOT_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    let vision_has_openai_env = env_map
        .get("OPENAI_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    let glm_enabled = env_map
        .get("OPENCLAW_GLM_ENABLED")
        .map(|v| v.trim() == "1")
        .unwrap_or(false);
    let glm_has_key = env_map
        .get("GLM_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);

    let report = if openclaw_ok {
        compute_model_availability_report_inner(state).ok()
    } else {
        None
    };
    let vision_has_moonshot = vision_has_moonshot_env
        || report
            .as_ref()
            .map(|r| r.vision.has_moonshot_api_key)
            .unwrap_or(false);
    let vision_has_openai = vision_has_openai_env
        || report
            .as_ref()
            .map(|r| r.vision.has_openai_api_key)
            .unwrap_or(false);

    // translator-core model route (required)
    let (translator_status, translator_msg) = if web_gateway_enabled {
        (
            "warning",
            "Skipped in web gateway mode (generation/review providers are configured via OPENCLAW_WEB_LLM_*).".to_string(),
        )
    } else {
        match report.as_ref().and_then(|r| r.agents.get("translator-core")) {
            Some(a) if a.runnable_now => (
                "pass",
                format!(
                    "Runnable. First usable model: {}. (Inspect: openclaw models status --agent translator-core --json)",
                    a.first_runnable_model.clone().unwrap_or_else(|| "unknown".to_string())
                ),
            ),
            Some(a) => (
                "blocker",
                format!(
                    "Blocked. {} (Inspect: openclaw models status --agent translator-core --json; Fix auth: openclaw models auth login --provider openai-codex)",
                    a.blocked_reasons.join(" ")
                ),
            ),
            None if openclaw_ok => (
                "blocker",
                "Could not evaluate model availability (openclaw models status/list failed). Try: openclaw models status --agent translator-core --json".to_string(),
            ),
            None => (
                "blocker",
                "OpenClaw not running; cannot evaluate translator-core models. Try: openclaw gateway --force".to_string(),
            ),
        }
    };
    checks.push(PreflightCheck {
        name: "Models (translator-core)".to_string(),
        key: "models_translator_core".to_string(),
        status: translator_status.to_string(),
        message: translator_msg,
    });

    // review-core model route (optional-ish: warnings)
    let (review_status, review_msg) = if web_gateway_enabled {
        (
            "warning",
            "Skipped in web gateway mode (review providers are configured via OPENCLAW_WEB_LLM_*).".to_string(),
        )
    } else {
        match report.as_ref().and_then(|r| r.agents.get("review-core")) {
            Some(a) if a.runnable_now => (
                "pass",
                format!(
                    "Runnable. First usable model: {}. (Inspect: openclaw models status --agent review-core --json)",
                    a.first_runnable_model.clone().unwrap_or_else(|| "unknown".to_string())
                ),
            ),
            Some(a) => (
                "warning",
                format!(
                    "Not runnable. {} (Inspect: openclaw models status --agent review-core --json)",
                    a.blocked_reasons.join(" ")
                ),
            ),
            None if openclaw_ok => (
                "warning",
                "Could not evaluate review-core model availability. Try: openclaw models status --agent review-core --json".to_string(),
            ),
            None => (
                "warning",
                "OpenClaw not running; cannot evaluate review-core models.".to_string(),
            ),
        }
    };
    checks.push(PreflightCheck {
        name: "Models (review-core)".to_string(),
        key: "models_review_core".to_string(),
        status: review_status.to_string(),
        message: review_msg,
    });

    // Vision QA keys (Format QA)
    checks.push(PreflightCheck {
        name: "Vision QA Keys".to_string(),
        key: "vision_keys".to_string(),
        status: if vision_has_google
            || vision_has_gemini
            || vision_has_moonshot
            || vision_has_openai
        {
            "pass".to_string()
        } else {
            "warning".to_string()
        },
        message: if vision_has_google
            || vision_has_gemini
            || vision_has_moonshot
            || vision_has_openai
        {
            "Vision QA credentials configured.".to_string()
        } else {
            "Missing vision credentials (Gemini, Moonshot, or OpenAI); Format QA will be skipped."
                .to_string()
        },
    });

    // GLM
    let has_zai_profile = report
        .as_ref()
        .map(|r| r.glm.has_zai_profile)
        .unwrap_or(false);
    let glm_ok = !glm_enabled || glm_has_key || has_zai_profile;
    let glm_status = if glm_ok { "pass" } else { "warning" };
    let glm_message = if !glm_enabled {
        "GLM disabled (OPENCLAW_GLM_ENABLED!=1).".to_string()
    } else if glm_has_key || has_zai_profile {
        "GLM enabled and credentials present (GLM_API_KEY or zai profile).".to_string()
    } else {
        "GLM enabled but no GLM_API_KEY and no zai auth profile found.".to_string()
    };
    checks.push(PreflightCheck {
        name: "GLM".to_string(),
        key: "glm".to_string(),
        status: glm_status.to_string(),
        message: glm_message,
    });

    // LibreOffice check (optional)
    let libreoffice_ok = Command::new("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        .args(["--version"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    checks.push(PreflightCheck {
        name: "LibreOffice".to_string(),
        key: "libreoffice".to_string(),
        status: if libreoffice_ok {
            "pass".to_string()
        } else {
            "warning".to_string()
        },
        message: if libreoffice_ok {
            "LibreOffice available".to_string()
        } else {
            "Optional: Install LibreOffice".to_string()
        },
    });

    checks
}

fn step_result(
    phase: &str,
    status: &str,
    message: String,
    hint_action: Option<String>,
    started_at: String,
) -> StartupStepResult {
    StartupStepResult {
        phase: phase.to_string(),
        status: status.to_string(),
        message,
        hint_action,
        started_at,
        ended_at: now_iso(),
    }
}

#[tauri::command]
fn diagnose_telegram_bot(state: State<'_, AppState>) -> TelegramHealth {
    diagnose_telegram_health_inner(&state)
}

#[tauri::command]
async fn start_telegram_bot_v2(
    payload: Option<StartTelegramPayload>,
    state: State<'_, AppState>,
) -> Result<TelegramHealth, String> {
    let payload = payload.unwrap_or_default();
    let result = start_telegram_bot_v2_inner(&state, &payload);
    match &result {
        Ok(health) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "telegram_start_v2".to_string(),
                status: "success".to_string(),
                summary: "start_telegram_bot_v2 completed".to_string(),
                detail: Some(serde_json::json!({ "health": health })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "telegram_start_v2".to_string(),
                status: "failed".to_string(),
                summary: "start_telegram_bot_v2 failed".to_string(),
                detail: Some(serde_json::json!({ "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

#[tauri::command]
async fn stop_openclaw_component(
    name: String,
    state: State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    let target = name.trim().to_lowercase();
    let result = match target.as_str() {
        "gateway" => {
            let out = run_dispatcher_json(&state, &["gateway-stop"])?;
            Ok(serde_json::json!({ "name": "gateway", "result": out }))
        }
        "worker" => {
            run_start_script(&state, "--stop-worker")?;
            Ok(serde_json::json!({ "name": "worker", "stopped": true }))
        }
        "telegram" => {
            stop_telegram_inner(&state)?;
            Ok(serde_json::json!({ "name": "telegram", "stopped": true }))
        }
        other => Err(format!("Unsupported component name: {}", other)),
    };
    match &result {
        Ok(val) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "component_stop".to_string(),
                status: "success".to_string(),
                summary: format!("stop_openclaw_component:{} completed", target),
                detail: Some(serde_json::json!({ "component": target, "result": val })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "component_stop".to_string(),
                status: "failed".to_string(),
                summary: format!("stop_openclaw_component:{} failed", target),
                detail: Some(serde_json::json!({ "component": target, "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

#[tauri::command]
async fn restart_openclaw_component(
    name: String,
    state: State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    let target = name.trim().to_lowercase();
    let result = match target.as_str() {
        "gateway" => {
            let out = run_dispatcher_json(&state, &["gateway-start"])?;
            Ok(serde_json::json!({ "name": "gateway", "result": out }))
        }
        "worker" => {
            run_start_script(&state, "--restart-worker")?;
            Ok(serde_json::json!({ "name": "worker", "restarted": true }))
        }
        "telegram" => {
            let health = start_telegram_bot_v2_inner(
                &state,
                &StartTelegramPayload {
                    force_restart: Some(true),
                },
            )?;
            Ok(serde_json::json!({ "name": "telegram", "health": health }))
        }
        other => Err(format!("Unsupported component name: {}", other)),
    };
    match &result {
        Ok(val) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "component_restart".to_string(),
                status: "success".to_string(),
                summary: format!("restart_openclaw_component:{} completed", target),
                detail: Some(serde_json::json!({ "component": target, "result": val })),
                ..AuditOperationPayload::default()
            },
        ),
        Err(err) => best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "component_restart".to_string(),
                status: "failed".to_string(),
                summary: format!("restart_openclaw_component:{} failed", target),
                detail: Some(serde_json::json!({ "component": target, "error": err })),
                ..AuditOperationPayload::default()
            },
        ),
    }
    result
}

#[tauri::command]
fn get_startup_snapshot(state: State<'_, AppState>) -> Result<StartupSnapshot, String> {
    let services = get_service_status_inner(&state)?;
    let gateway = run_dispatcher_json(&state, &["gateway-status"])
        .map(|v| parse_gateway_status(&v))
        .unwrap_or_default();
    let telegram = diagnose_telegram_health_inner(&state);
    Ok(StartupSnapshot {
        services,
        gateway,
        telegram,
    })
}

#[tauri::command]
async fn start_openclaw_v2(
    payload: Option<StartOpenclawPayload>,
    state: State<'_, AppState>,
) -> Result<Vec<StartupStepResult>, String> {
    start_openclaw_v2_inner(&state, &payload.unwrap_or_default())
}

fn start_openclaw_v2_inner(
    state: &AppState,
    payload: &StartOpenclawPayload,
) -> Result<Vec<StartupStepResult>, String> {
    let force_restart = payload.force_restart.unwrap_or(false);
    let mut steps: Vec<StartupStepResult> = Vec::new();

    let phase_started = now_iso();
    let checks = run_preflight_check_inner(&state);
    let blockers = checks.iter().filter(|c| c.status == "blocker").count();
    if blockers > 0 {
        steps.push(step_result(
            "preflight",
            "failed",
            format!("{} preflight blockers detected", blockers),
            Some("auto_fix_preflight".to_string()),
            phase_started,
        ));
        best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "openclaw_start_v2".to_string(),
                status: "failed".to_string(),
                summary: "start_openclaw_v2 failed at preflight".to_string(),
                detail: Some(serde_json::json!({ "steps": steps, "blockers": blockers })),
                ..AuditOperationPayload::default()
            },
        );
        return Err(format!("preflight blockers: {}", blockers));
    }
    steps.push(step_result(
        "preflight",
        "success",
        "Preflight checks passed".to_string(),
        None,
        phase_started,
    ));

    let login_started = now_iso();
    let gateway_before = run_dispatcher_json(&state, &["gateway-status"])
        .map(|v| parse_gateway_status(&v))
        .unwrap_or_default();
    if gateway_before.logged_in {
        steps.push(step_result(
            "login_check",
            "success",
            "Gateway login verified".to_string(),
            None,
            login_started,
        ));
    } else {
        steps.push(step_result(
            "login_check",
            "warning",
            "Gateway login is not confirmed".to_string(),
            Some("gateway-login".to_string()),
            login_started,
        ));
    }

    let gateway_started = now_iso();
    let gateway_result = run_dispatcher_json(&state, &["gateway-start"]);
    if let Err(err) = gateway_result {
        steps.push(step_result(
            "start_gateway",
            "failed",
            format!("Failed to start gateway: {}", err),
            Some("gateway-status".to_string()),
            gateway_started,
        ));
        best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "openclaw_start_v2".to_string(),
                status: "failed".to_string(),
                summary: "start_openclaw_v2 failed at gateway".to_string(),
                detail: Some(serde_json::json!({ "steps": steps })),
                ..AuditOperationPayload::default()
            },
        );
        return Err("gateway start failed".to_string());
    }
    steps.push(step_result(
        "start_gateway",
        "success",
        "Gateway started".to_string(),
        None,
        gateway_started,
    ));

    let worker_started = now_iso();
    let worker_result = if force_restart {
        run_start_script(&state, "--restart-worker")
    } else {
        run_start_script(&state, "--worker")
    };
    if let Err(err) = worker_result {
        steps.push(step_result(
            "start_worker",
            "failed",
            format!("Failed to start worker: {}", err),
            Some("restart_service worker".to_string()),
            worker_started,
        ));
        best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "openclaw_start_v2".to_string(),
                status: "failed".to_string(),
                summary: "start_openclaw_v2 failed at worker".to_string(),
                detail: Some(serde_json::json!({ "steps": steps })),
                ..AuditOperationPayload::default()
            },
        );
        return Err("worker start failed".to_string());
    }
    steps.push(step_result(
        "start_worker",
        "success",
        "Worker started".to_string(),
        None,
        worker_started,
    ));

    let tg_started = now_iso();
    let telegram_health = match start_telegram_bot_v2_inner(
        &state,
        &StartTelegramPayload {
            force_restart: Some(force_restart),
        },
    ) {
        Ok(v) => v,
        Err(err) => {
            steps.push(step_result(
                "start_telegram",
                "failed",
                format!("Failed to start telegram: {}", err),
                Some("diagnose_telegram_bot".to_string()),
                tg_started,
            ));
            best_effort_audit_operation(
                &state,
                AuditOperationPayload {
                    source: "tauri".to_string(),
                    action: "openclaw_start_v2".to_string(),
                    status: "failed".to_string(),
                    summary: "start_openclaw_v2 failed at telegram".to_string(),
                    detail: Some(serde_json::json!({ "steps": steps })),
                    ..AuditOperationPayload::default()
                },
            );
            return Err("telegram start failed".to_string());
        }
    };
    steps.push(step_result(
        "start_telegram",
        "success",
        "Telegram bot started".to_string(),
        None,
        tg_started,
    ));

    let verify_started = now_iso();
    let services = get_service_status_inner(&state)?;
    let gateway = run_dispatcher_json(&state, &["gateway-status"])
        .map(|v| parse_gateway_status(&v))
        .unwrap_or_default();
    let worker_running = services
        .iter()
        .any(|s| s.name == "Run Worker" && s.status == "running");
    let telegram_running = services
        .iter()
        .any(|s| s.name == "Telegram Bot" && s.status == "running")
        || telegram_health.running;
    if gateway.running && worker_running && telegram_running {
        steps.push(step_result(
            "verify",
            "success",
            "Gateway, worker and telegram are running".to_string(),
            None,
            verify_started,
        ));
        steps.push(step_result(
            "done",
            "success",
            "OpenClaw startup completed".to_string(),
            None,
            now_iso(),
        ));
    } else {
        steps.push(step_result(
            "verify",
            "failed",
            format!(
                "Verify failed (gateway={}, worker={}, telegram={})",
                gateway.running, worker_running, telegram_running
            ),
            Some("get_startup_snapshot".to_string()),
            verify_started,
        ));
        best_effort_audit_operation(
            &state,
            AuditOperationPayload {
                source: "tauri".to_string(),
                action: "openclaw_start_v2".to_string(),
                status: "failed".to_string(),
                summary: "start_openclaw_v2 failed at verify".to_string(),
                detail: Some(serde_json::json!({ "steps": steps })),
                ..AuditOperationPayload::default()
            },
        );
        return Err("startup verification failed".to_string());
    }

    best_effort_audit_operation(
        &state,
        AuditOperationPayload {
            source: "tauri".to_string(),
            action: "openclaw_start_v2".to_string(),
            status: "success".to_string(),
            summary: "start_openclaw_v2 completed".to_string(),
            detail: Some(serde_json::json!({ "steps": steps })),
            ..AuditOperationPayload::default()
        },
    );
    Ok(steps)
}

#[tauri::command]
async fn start_openclaw(state: State<'_, AppState>) -> Result<Vec<PreflightCheck>, String> {
    let _ = start_openclaw_v2_inner(
        &state,
        &StartOpenclawPayload {
            force_restart: Some(true),
        },
    )?;

    let checks = run_preflight_check_inner(&state);
    best_effort_audit_operation(
        &state,
        AuditOperationPayload {
            source: "tauri".to_string(),
            action: "openclaw_start".to_string(),
            status: "success".to_string(),
            summary: "start_openclaw completed".to_string(),
            detail: Some(serde_json::json!({ "checks": checks.clone() })),
            ..AuditOperationPayload::default()
        },
    );
    Ok(checks)
}

#[tauri::command]
fn run_preflight_check(state: State<'_, AppState>) -> Vec<PreflightCheck> {
    let checks = run_preflight_check_inner(&state);
    best_effort_audit_operation(
        &state,
        AuditOperationPayload {
            source: "tauri".to_string(),
            action: "preflight_run".to_string(),
            status: "success".to_string(),
            summary: "run_preflight_check completed".to_string(),
            detail: Some(serde_json::json!({ "checks": checks.clone() })),
            ..AuditOperationPayload::default()
        },
    );
    checks
}

// ============================================================================
// Model Availability Commands
// ============================================================================

#[tauri::command]
fn get_model_availability_report(
    state: State<'_, AppState>,
) -> Result<ModelAvailabilityReport, String> {
    compute_model_availability_report_inner(&state)
}

// ============================================================================
// Config Commands
// ============================================================================

#[tauri::command]
fn get_config(state: State<'_, AppState>) -> Result<AppConfig, String> {
    get_config_inner(&state)
}

#[tauri::command]
fn save_config(config: AppConfig, state: State<'_, AppState>) -> Result<(), String> {
    let env_path = format!("{}/.env.v4.local", state.config_path);

    // Read existing content to preserve other values
    let existing = fs::read_to_string(&env_path).unwrap_or_default();
    let mut lines: Vec<String> = existing.lines().map(|s| s.to_string()).collect();

    let work_root = env_quote_double(&config.work_root);
    let kb_root = env_quote_double(&config.kb_root);
    update_or_add_env_line(&mut lines, "V4_WORK_ROOT", &work_root);
    update_or_add_env_line(&mut lines, "V4_KB_ROOT", &kb_root);
    update_or_add_env_line(
        &mut lines,
        "OPENCLAW_STRICT_ROUTER",
        if config.strict_router { "1" } else { "0" },
    );
    update_or_add_env_line(
        &mut lines,
        "OPENCLAW_REQUIRE_NEW",
        if config.require_new { "1" } else { "0" },
    );
    update_or_add_env_line(&mut lines, "OPENCLAW_RAG_BACKEND", &config.rag_backend);

    let content = lines.join("\n");
    fs::write(&env_path, content).map_err(|e| format!("Failed to write config: {}", e))?;

    Ok(())
}

#[tauri::command]
fn get_env_settings(state: State<'_, AppState>) -> Result<Vec<EnvVarItem>, String> {
    let env_path = PathBuf::from(&state.config_path).join(".env.v4.local");
    let content = fs::read_to_string(&env_path).unwrap_or_default();

    let mut items: Vec<EnvVarItem> = Vec::new();
    for line in content.lines() {
        if let Some((key, value)) = parse_env_assignment(line) {
            items.push(EnvVarItem {
                is_secret: is_secret_env_key(&key),
                key,
                value,
            });
        }
    }
    items.sort_by(|a, b| a.key.cmp(&b.key));
    Ok(items)
}

#[tauri::command]
fn save_env_settings(updates: Vec<EnvVarUpdate>, state: State<'_, AppState>) -> Result<(), String> {
    let env_path = PathBuf::from(&state.config_path).join(".env.v4.local");
    let existing = fs::read_to_string(&env_path).unwrap_or_default();
    let mut lines: Vec<String> = existing.lines().map(|s| s.to_string()).collect();

    for update in updates {
        let key = update.key.trim();
        if key.is_empty() {
            continue;
        }
        let rendered_value = format_env_value_for_file(update.value.trim());
        update_or_add_env_line(&mut lines, key, &rendered_value);
    }

    let content = lines.join("\n");
    fs::write(&env_path, content).map_err(|e| format!("Failed to write env settings: {}", e))?;
    Ok(())
}

// ============================================================================
// Job Commands
// ============================================================================

#[tauri::command]
fn get_jobs(
    status: Option<String>,
    limit: Option<u32>,
    state: State<'_, AppState>,
) -> Result<Vec<JobInfo>, String> {
    use rusqlite::Connection;

    let conn =
        Connection::open(&state.db_path).map_err(|e| format!("Failed to open database: {}", e))?;

    let limit = limit.unwrap_or(50);

    let mut jobs = Vec::new();

    match status {
        Some(s) => {
            let mut stmt = conn.prepare(
                "SELECT job_id, status, task_type, sender, created_at, updated_at FROM jobs WHERE status = ?1 ORDER BY created_at DESC LIMIT ?2"
            ).map_err(|e| format!("Failed to prepare query: {}", e))?;

            let rows = stmt
                .query_map(rusqlite::params![s, limit], |row| {
                    Ok(JobInfo {
                        job_id: row.get(0)?,
                        status: row.get(1)?,
                        task_type: row.get(2)?,
                        sender: row.get(3)?,
                        created_at: row.get(4)?,
                        updated_at: row.get(5)?,
                    })
                })
                .map_err(|e| format!("Failed to query jobs: {}", e))?;

            for row in rows {
                jobs.push(row.map_err(|e| format!("Failed to collect jobs: {}", e))?);
            }
        }
        None => {
            let mut stmt = conn.prepare(
                "SELECT job_id, status, task_type, sender, created_at, updated_at FROM jobs ORDER BY created_at DESC LIMIT ?1"
            ).map_err(|e| format!("Failed to prepare query: {}", e))?;

            let rows = stmt
                .query_map(rusqlite::params![limit], |row| {
                    Ok(JobInfo {
                        job_id: row.get(0)?,
                        status: row.get(1)?,
                        task_type: row.get(2)?,
                        sender: row.get(3)?,
                        created_at: row.get(4)?,
                        updated_at: row.get(5)?,
                    })
                })
                .map_err(|e| format!("Failed to query jobs: {}", e))?;

            for row in rows {
                jobs.push(row.map_err(|e| format!("Failed to collect jobs: {}", e))?);
            }
        }
    }

    Ok(jobs)
}

#[tauri::command]
fn get_job_milestones(
    job_id: String,
    state: State<'_, AppState>,
) -> Result<Vec<Milestone>, String> {
    use rusqlite::Connection;

    let conn =
        Connection::open(&state.db_path).map_err(|e| format!("Failed to open database: {}", e))?;

    let mut stmt = conn.prepare(
        "SELECT job_id, milestone, created_at, payload_json FROM events WHERE job_id = ?1 ORDER BY created_at ASC"
    ).map_err(|e| format!("Failed to prepare query: {}", e))?;

    let milestones = stmt
        .query_map(rusqlite::params![job_id], |row| {
            Ok(Milestone {
                job_id: row.get(0)?,
                event_type: row.get(1)?,
                timestamp: row.get(2)?,
                payload: row.get(3)?,
            })
        })
        .map_err(|e| format!("Failed to query milestones: {}", e))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("Failed to collect milestones: {}", e))?;

    Ok(milestones)
}

fn load_alert_state_snapshot(path: &str) -> AlertStateSnapshot {
    let content = match fs::read_to_string(path) {
        Ok(content) => content,
        Err(_) => return AlertStateSnapshot::default(),
    };

    if let Ok(snapshot) = serde_json::from_str::<AlertStateSnapshot>(&content) {
        return snapshot;
    }

    if let Ok(legacy_ack_ids) = serde_json::from_str::<Vec<String>>(&content) {
        return AlertStateSnapshot {
            acknowledged_ids: legacy_ack_ids.into_iter().collect(),
            ..AlertStateSnapshot::default()
        };
    }

    AlertStateSnapshot::default()
}

fn persist_alert_state_snapshot(path: &str, snapshot: &AlertStateSnapshot) -> Result<(), String> {
    let path_buf = PathBuf::from(path);
    if let Some(parent) = path_buf.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to prepare alert state dir: {}", e))?;
    }

    let payload = serde_json::to_string_pretty(snapshot)
        .map_err(|e| format!("Failed to serialize alert state: {}", e))?;
    fs::write(path, payload).map_err(|e| format!("Failed to persist alert state: {}", e))?;
    Ok(())
}

fn default_warning_to_critical_minutes() -> u32 {
    30
}

fn default_alert_policy_config() -> AlertPolicyConfig {
    AlertPolicyConfig {
        warning_to_critical_minutes: default_warning_to_critical_minutes(),
        runbooks: vec![
            AlertRunbookRuleConfig {
                source: Some("service".to_string()),
                severity: None,
                headline: "Service health issue".to_string(),
                steps: vec![
                    "Open Service Control and confirm which process is stopped or degraded."
                        .to_string(),
                    "Restart the affected service, then verify status returns to running."
                        .to_string(),
                    "Open Technical Logs and confirm new ERROR lines stop increasing.".to_string(),
                    "Return to Overview and confirm open alerts and backlog begin to drop."
                        .to_string(),
                ],
                actions: vec![
                    AlertRunbookAction {
                        label: "Open Service Control".to_string(),
                        tab: "services".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Technical Logs".to_string(),
                        tab: "logs".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Overview".to_string(),
                        tab: "dashboard".to_string(),
                    },
                ],
            },
            AlertRunbookRuleConfig {
                source: Some("jobs".to_string()),
                severity: None,
                headline: "Job failure cluster".to_string(),
                steps: vec![
                    "Open Task Center and inspect the most recent failed jobs first.".to_string(),
                    "Check whether failures share the same source file, sender, or task type."
                        .to_string(),
                    "If failures repeat, verify services and logs before rerunning jobs."
                        .to_string(),
                    "Monitor recovery in Overview success rate and open alerts.".to_string(),
                ],
                actions: vec![
                    AlertRunbookAction {
                        label: "Open Task Center".to_string(),
                        tab: "jobs".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Technical Logs".to_string(),
                        tab: "logs".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Overview".to_string(),
                        tab: "dashboard".to_string(),
                    },
                ],
            },
            AlertRunbookRuleConfig {
                source: Some("verify".to_string()),
                severity: None,
                headline: "Review queue accumulation".to_string(),
                steps: vec![
                    "Open Review Desk and prioritize the oldest review_ready jobs first."
                        .to_string(),
                    "Process urgent customer-facing files before batch jobs.".to_string(),
                    "Confirm reviewed jobs leave the queue and no new blockers appear.".to_string(),
                ],
                actions: vec![
                    AlertRunbookAction {
                        label: "Open Review Desk".to_string(),
                        tab: "verify".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Task Center".to_string(),
                        tab: "jobs".to_string(),
                    },
                ],
            },
            AlertRunbookRuleConfig {
                source: Some("queue".to_string()),
                severity: None,
                headline: "Pending queue pressure".to_string(),
                steps: vec![
                    "Open Overview Queue Board and identify where jobs accumulate.".to_string(),
                    "If pending is high, verify worker health and processing throughput."
                        .to_string(),
                    "If running is high for too long, inspect logs for retries or API errors."
                        .to_string(),
                ],
                actions: vec![
                    AlertRunbookAction {
                        label: "Open Overview".to_string(),
                        tab: "dashboard".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Service Control".to_string(),
                        tab: "services".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Technical Logs".to_string(),
                        tab: "logs".to_string(),
                    },
                ],
            },
            AlertRunbookRuleConfig {
                source: Some("logs".to_string()),
                severity: None,
                headline: "Error log surge".to_string(),
                steps: vec![
                    "Open Technical Logs and identify the most frequent repeating error."
                        .to_string(),
                    "Decide whether it is transient (rate-limited) or persistent (input/config)."
                        .to_string(),
                    "Apply fix or restart service, then verify error frequency declines."
                        .to_string(),
                ],
                actions: vec![
                    AlertRunbookAction {
                        label: "Open Technical Logs".to_string(),
                        tab: "logs".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Service Control".to_string(),
                        tab: "services".to_string(),
                    },
                ],
            },
            AlertRunbookRuleConfig {
                source: None,
                severity: Some("critical".to_string()),
                headline: "Critical system signal".to_string(),
                steps: vec![
                    "Stabilize service availability first, then reduce queue pressure.".to_string(),
                    "Inspect logs for persistent failures and verify recovery after mitigation."
                        .to_string(),
                    "Acknowledge the alert only after impact is contained.".to_string(),
                ],
                actions: vec![
                    AlertRunbookAction {
                        label: "Open Service Control".to_string(),
                        tab: "services".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Technical Logs".to_string(),
                        tab: "logs".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Overview".to_string(),
                        tab: "dashboard".to_string(),
                    },
                ],
            },
            AlertRunbookRuleConfig {
                source: None,
                severity: None,
                headline: "Operational signal".to_string(),
                steps: vec![
                    "Open Overview and verify trend direction for related metrics.".to_string(),
                    "Use Task Center or Logs to isolate root cause and impact scope.".to_string(),
                    "Acknowledge or ignore only after decision and follow-up action are clear."
                        .to_string(),
                ],
                actions: vec![
                    AlertRunbookAction {
                        label: "Open Overview".to_string(),
                        tab: "dashboard".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Task Center".to_string(),
                        tab: "jobs".to_string(),
                    },
                    AlertRunbookAction {
                        label: "Open Technical Logs".to_string(),
                        tab: "logs".to_string(),
                    },
                ],
            },
        ],
    }
}

fn load_alert_policy_config(state: &AppState) -> AlertPolicyConfig {
    let mut config = default_alert_policy_config();
    let path = PathBuf::from(&state.alert_policy_path);

    let content = match fs::read_to_string(&path) {
        Ok(content) => content,
        Err(_) => return config,
    };

    if let Ok(parsed) = serde_json::from_str::<AlertPolicyConfig>(&content) {
        if parsed.warning_to_critical_minutes > 0 {
            config.warning_to_critical_minutes = parsed.warning_to_critical_minutes;
        }
        if !parsed.runbooks.is_empty() {
            config.runbooks = parsed.runbooks;
        }
    }

    config
}

fn resolve_alert_runbook(config: &AlertPolicyConfig, source: &str, severity: &str) -> AlertRunbook {
    let source_norm = source.to_lowercase();
    let severity_norm = severity.to_lowercase();
    let mut best_score = i32::MIN;
    let mut best_rule: Option<&AlertRunbookRuleConfig> = None;

    for rule in &config.runbooks {
        if let Some(rule_source) = &rule.source {
            if !rule_source.eq_ignore_ascii_case(&source_norm) {
                continue;
            }
        }
        if let Some(rule_severity) = &rule.severity {
            if !rule_severity.eq_ignore_ascii_case(&severity_norm) {
                continue;
            }
        }

        let mut score = 0;
        if rule.source.is_some() {
            score += 2;
        }
        if rule.severity.is_some() {
            score += 1;
        }
        if score > best_score {
            best_score = score;
            best_rule = Some(rule);
        }
    }

    if let Some(rule) = best_rule {
        return AlertRunbook {
            headline: rule.headline.clone(),
            steps: rule.steps.clone(),
            actions: rule.actions.clone(),
        };
    }

    AlertRunbook {
        headline: "Operational signal".to_string(),
        steps: vec![
            "Open Overview and verify trend direction for related metrics.".to_string(),
            "Use Task Center or Logs to isolate root cause and impact scope.".to_string(),
            "Acknowledge or ignore only after decision and follow-up action are clear.".to_string(),
        ],
        actions: vec![
            AlertRunbookAction {
                label: "Open Overview".to_string(),
                tab: "dashboard".to_string(),
            },
            AlertRunbookAction {
                label: "Open Task Center".to_string(),
                tab: "jobs".to_string(),
            },
            AlertRunbookAction {
                label: "Open Technical Logs".to_string(),
                tab: "logs".to_string(),
            },
        ],
    }
}

fn now_epoch_ms() -> i64 {
    Utc::now().timestamp_millis()
}

fn parse_timestamp_local(ts: &str) -> Option<DateTime<Local>> {
    let s = ts.trim();
    if s.is_empty() {
        return None;
    }

    if let Ok(dt) = DateTime::parse_from_rfc3339(s) {
        return Some(dt.with_timezone(&Local));
    }

    if let Ok(naive) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f") {
        return Local
            .from_local_datetime(&naive)
            .single()
            .or_else(|| Local.from_local_datetime(&naive).earliest());
    }

    if let Ok(naive) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S") {
        return Local
            .from_local_datetime(&naive)
            .single()
            .or_else(|| Local.from_local_datetime(&naive).earliest());
    }

    None
}

fn read_log_file_inner(state: &AppState, service: &str, lines: u32) -> Result<Vec<String>, String> {
    let log_file = match service {
        "telegram" => PathBuf::from(&state.logs_dir).join("telegram.log"),
        "worker" => PathBuf::from(&state.logs_dir).join("worker.log"),
        "gateway" => runtime_root_from_state(state).join("web_gateway.log"),
        _ => return Err(format!("Unknown service: {}", service)),
    };

    let output = Command::new("tail")
        .args(["-n", &lines.to_string()])
        .arg(&log_file)
        .output()
        .map_err(|e| format!("Failed to read log: {}", e))?;

    let content = String::from_utf8_lossy(&output.stdout);
    Ok(content.lines().map(|s| s.to_string()).collect())
}

fn load_recent_jobs(state: &AppState, limit: u32) -> Result<Vec<JobInfo>, String> {
    use rusqlite::Connection;

    let conn =
        Connection::open(&state.db_path).map_err(|e| format!("Failed to open database: {}", e))?;

    let mut stmt = conn
        .prepare(
            "SELECT job_id, status, task_type, sender, created_at, updated_at
         FROM jobs
         ORDER BY created_at DESC
         LIMIT ?1",
        )
        .map_err(|e| format!("Failed to prepare query: {}", e))?;

    let rows = stmt
        .query_map(rusqlite::params![limit], |row| {
            Ok(JobInfo {
                job_id: row.get(0)?,
                status: row.get(1)?,
                task_type: row.get(2)?,
                sender: row.get(3)?,
                created_at: row.get(4)?,
                updated_at: row.get(5)?,
            })
        })
        .map_err(|e| format!("Failed to query jobs: {}", e))?;

    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("Failed to collect jobs: {}", e))
}

fn filter_jobs_by_period(jobs: &[JobInfo], period_hours: u32) -> Vec<JobInfo> {
    let cutoff = Local::now() - Duration::hours(period_hours as i64);
    jobs.iter()
        .filter(|job| {
            parse_timestamp_local(&job.created_at)
                .map(|ts| ts >= cutoff)
                .unwrap_or(true)
        })
        .cloned()
        .collect()
}

fn build_queue_snapshot(jobs: &[JobInfo]) -> QueueSnapshot {
    let mut queue = QueueSnapshot {
        pending: 0,
        running: 0,
        review_ready: 0,
        done: 0,
        failed: 0,
        total: jobs.len() as u64,
    };

    for job in jobs {
        let status = job.status.to_lowercase();
        match status.as_str() {
            "verified" => queue.done += 1,
            "failed" => queue.failed += 1,
            "review_ready" | "needs_attention" => queue.review_ready += 1,
            "running" | "round_1_done" | "round_2_done" | "round_3_done" => queue.running += 1,
            _ => queue.pending += 1,
        }
    }

    queue
}

fn push_alert_item(
    alerts: &mut Vec<AlertItem>,
    alert_state: &mut AlertStateSnapshot,
    now_ms: i64,
    warning_to_critical_minutes: u32,
    id: String,
    title: String,
    message: String,
    severity: &str,
    source: &str,
    metric: Option<i64>,
    action: Option<String>,
) {
    let first_seen = *alert_state
        .first_seen_ms
        .entry(id.clone())
        .or_insert(now_ms);
    let mut resolved_severity = severity.to_string();
    let mut resolved_message = message;
    let status = if alert_state.ignored_ids.contains(&id) {
        "ignored"
    } else if alert_state.acknowledged_ids.contains(&id) {
        "acknowledged"
    } else {
        "open"
    };

    if status == "open" && severity.eq_ignore_ascii_case("warning") {
        let escalation_ms = (warning_to_critical_minutes as i64) * 60_000;
        if escalation_ms > 0 && now_ms.saturating_sub(first_seen) >= escalation_ms {
            resolved_severity = "critical".to_string();
            resolved_message = format!(
                "{} Escalated to critical after {} minutes unresolved.",
                resolved_message, warning_to_critical_minutes
            );
        }
    }

    alerts.push(AlertItem {
        id,
        title,
        message: resolved_message,
        severity: resolved_severity,
        status: status.to_string(),
        source: source.to_string(),
        metric_value: metric,
        created_at: first_seen,
        action_label: action,
    });
}

fn build_alerts(
    state: &AppState,
    jobs: &[JobInfo],
    services: &[ServiceStatus],
    queue: &QueueSnapshot,
) -> Vec<AlertItem> {
    let mut alerts: Vec<AlertItem> = Vec::new();
    let now_ms = now_epoch_ms();
    let policy = load_alert_policy_config(state);
    let warning_to_critical_minutes = policy.warning_to_critical_minutes.max(1);
    let mut alert_state = state
        .alert_state
        .lock()
        .map(|s| s.clone())
        .unwrap_or_default();
    let mut active_ids: HashSet<String> = HashSet::new();

    for service in services {
        if service.status != "running" {
            let alert_id = format!(
                "service_{}_not_running",
                service.name.to_lowercase().replace(' ', "_")
            );
            active_ids.insert(alert_id.clone());
            push_alert_item(
                &mut alerts,
                &mut alert_state,
                now_ms,
                warning_to_critical_minutes,
                alert_id,
                format!("{} is not running", service.name),
                "Service health is degraded. Start or restart this service.".to_string(),
                "critical",
                "service",
                None,
                Some("Open Service Control".to_string()),
            );
        }
    }

    let failed_jobs = jobs
        .iter()
        .filter(|j| j.status.eq_ignore_ascii_case("failed"))
        .count() as i64;
    if failed_jobs > 0 {
        let sev = if failed_jobs >= 5 {
            "critical"
        } else {
            "warning"
        };
        active_ids.insert("jobs_failed_recent".to_string());
        push_alert_item(
            &mut alerts,
            &mut alert_state,
            now_ms,
            warning_to_critical_minutes,
            "jobs_failed_recent".to_string(),
            "Failed jobs detected".to_string(),
            format!("{} jobs failed in the selected period.", failed_jobs),
            sev,
            "jobs",
            Some(failed_jobs),
            Some("Review failed jobs".to_string()),
        );
    }

    if queue.review_ready >= 3 {
        active_ids.insert("review_backlog".to_string());
        push_alert_item(
            &mut alerts,
            &mut alert_state,
            now_ms,
            warning_to_critical_minutes,
            "review_backlog".to_string(),
            "Review backlog growing".to_string(),
            format!("{} jobs are waiting for review.", queue.review_ready),
            "warning",
            "verify",
            Some(queue.review_ready as i64),
            Some("Open Verify queue".to_string()),
        );
    }

    if queue.pending >= 10 {
        active_ids.insert("queue_pending_high".to_string());
        push_alert_item(
            &mut alerts,
            &mut alert_state,
            now_ms,
            warning_to_critical_minutes,
            "queue_pending_high".to_string(),
            "Pending queue is high".to_string(),
            format!("{} jobs are still waiting in the queue.", queue.pending),
            "warning",
            "queue",
            Some(queue.pending as i64),
            Some("Check queue board".to_string()),
        );
    }

    if let Ok(worker_lines) = read_log_file_inner(state, "worker", 200) {
        let err_count = worker_lines
            .iter()
            .filter(|line| {
                let up = line.to_uppercase();
                up.contains(" ERROR ") || up.contains("[ERROR]") || up.contains("CRITICAL")
            })
            .count() as i64;

        if err_count > 0 {
            active_ids.insert("worker_error_logs".to_string());
            push_alert_item(
                &mut alerts,
                &mut alert_state,
                now_ms,
                warning_to_critical_minutes,
                "worker_error_logs".to_string(),
                "Worker error logs found".to_string(),
                format!("{} error-level log lines found recently.", err_count),
                if err_count >= 10 {
                    "critical"
                } else {
                    "warning"
                },
                "logs",
                Some(err_count),
                Some("Inspect technical logs".to_string()),
            );
        }
    }

    if alerts.is_empty() {
        active_ids.insert("system_nominal".to_string());
        push_alert_item(
            &mut alerts,
            &mut alert_state,
            now_ms,
            warning_to_critical_minutes,
            "system_nominal".to_string(),
            "No active issues".to_string(),
            "System is healthy. Continue routine monitoring.".to_string(),
            "info",
            "system",
            None,
            None,
        );
    }

    alerts.sort_by(|a, b| {
        let weight = |sev: &str| match sev {
            "critical" => 0,
            "warning" => 1,
            _ => 2,
        };
        let status_weight = |status: &str| match status {
            "open" => 0,
            "acknowledged" => 1,
            _ => 2,
        };
        let sa = status_weight(&a.status);
        let sb = status_weight(&b.status);
        sa.cmp(&sb)
            .then(weight(&a.severity).cmp(&weight(&b.severity)))
            .then(a.created_at.cmp(&b.created_at))
    });

    alert_state
        .first_seen_ms
        .retain(|alert_id, _| active_ids.contains(alert_id));
    alert_state
        .acknowledged_ids
        .retain(|alert_id| active_ids.contains(alert_id));
    alert_state
        .ignored_ids
        .retain(|alert_id| active_ids.contains(alert_id));

    if let Ok(mut guard) = state.alert_state.lock() {
        if *guard != alert_state {
            *guard = alert_state.clone();
            let _ = persist_alert_state_snapshot(&state.alert_state_path, &alert_state);
        }
    }

    alerts
}

fn build_overview_data(
    state: &AppState,
    period_hours: u32,
) -> Result<(OverviewMetrics, QueueSnapshot, Vec<AlertItem>, Vec<JobInfo>), String> {
    let jobs = filter_jobs_by_period(&load_recent_jobs(state, 2000)?, period_hours);
    let queue = build_queue_snapshot(&jobs);
    let services = get_service_status_inner(state)?;
    let alerts = build_alerts(state, &jobs, &services, &queue);

    let completed_jobs = queue.done;
    let failed_jobs = queue.failed;
    let processed_jobs = completed_jobs + failed_jobs;
    let success_rate = if processed_jobs > 0 {
        (completed_jobs as f64 / processed_jobs as f64) * 100.0
    } else {
        0.0
    };

    let mut turnaround_sum = 0f64;
    let mut turnaround_count = 0f64;
    for job in &jobs {
        if !matches!(job.status.as_str(), "verified" | "failed" | "review_ready") {
            continue;
        }
        let created = parse_timestamp_local(&job.created_at);
        let updated = parse_timestamp_local(&job.updated_at);
        if let (Some(c), Some(u)) = (created, updated) {
            let mins = (u - c).num_seconds() as f64 / 60.0;
            if mins.is_finite() && mins >= 0.0 {
                turnaround_sum += mins;
                turnaround_count += 1.0;
            }
        }
    }
    let avg_turnaround_minutes = if turnaround_count > 0.0 {
        turnaround_sum / turnaround_count
    } else {
        0.0
    };

    let services_running = services.iter().filter(|s| s.status == "running").count() as u64;
    let services_total = services.len() as u64;
    let open_alerts = alerts
        .iter()
        .filter(|a| a.status == "open" && a.id != "system_nominal")
        .count() as u64;

    let metrics = OverviewMetrics {
        total_jobs: jobs.len() as u64,
        completed_jobs,
        failed_jobs,
        review_ready_jobs: queue.review_ready,
        running_jobs: queue.running,
        backlog_jobs: queue.pending + queue.running + queue.review_ready,
        success_rate,
        avg_turnaround_minutes,
        services_running,
        services_total,
        open_alerts,
        period_hours,
        generated_at: now_epoch_ms(),
    };

    Ok((metrics, queue, alerts, jobs))
}

#[tauri::command]
fn get_overview_metrics(
    range_hours: Option<u32>,
    state: State<'_, AppState>,
) -> Result<OverviewMetrics, String> {
    let period = range_hours.unwrap_or(24).clamp(1, 24 * 14);
    let (metrics, _, _, _) = build_overview_data(&state, period)?;
    Ok(metrics)
}

#[tauri::command]
fn get_overview_trends(
    metric: String,
    range_hours: Option<u32>,
    state: State<'_, AppState>,
) -> Result<Vec<TrendPoint>, String> {
    let period = range_hours.unwrap_or(24).clamp(6, 24 * 14);
    let jobs = filter_jobs_by_period(&load_recent_jobs(&state, 4000)?, period);
    let metric_key = metric.to_lowercase();
    let now = Local::now().timestamp();
    let current_bucket = now - (now % 3600);

    let mut buckets: HashMap<i64, i64> = HashMap::new();
    for job in jobs {
        let use_job = match metric_key.as_str() {
            "failures" => job.status.eq_ignore_ascii_case("failed"),
            "review_ready" => matches!(job.status.as_str(), "review_ready" | "needs_attention"),
            _ => true,
        };
        if !use_job {
            continue;
        }

        let ts_source = if metric_key == "failures" {
            parse_timestamp_local(&job.updated_at)
                .or_else(|| parse_timestamp_local(&job.created_at))
        } else {
            parse_timestamp_local(&job.created_at)
        };
        if let Some(ts) = ts_source {
            let epoch = ts.timestamp();
            let bucket = epoch - (epoch % 3600);
            *buckets.entry(bucket).or_insert(0) += 1;
        }
    }

    let mut points = Vec::new();
    for idx in 0..period {
        let bucket = current_bucket - ((period - 1 - idx) as i64 * 3600);
        let value = *buckets.get(&bucket).unwrap_or(&0);
        let label = Local
            .timestamp_opt(bucket, 0)
            .single()
            .map(|d| d.format("%m-%d %H:00").to_string())
            .unwrap_or_else(|| bucket.to_string());
        points.push(TrendPoint {
            timestamp: bucket * 1000,
            label,
            value,
        });
    }

    Ok(points)
}

#[tauri::command]
fn get_queue_snapshot(state: State<'_, AppState>) -> Result<QueueSnapshot, String> {
    let jobs = filter_jobs_by_period(&load_recent_jobs(&state, 2000)?, 24);
    Ok(build_queue_snapshot(&jobs))
}

#[tauri::command]
fn list_alerts(
    status: Option<String>,
    severity: Option<String>,
    state: State<'_, AppState>,
) -> Result<Vec<AlertItem>, String> {
    let (_, _, mut alerts, _) = build_overview_data(&state, 24)?;
    if let Some(status_filter) = status {
        let sf = status_filter.to_lowercase();
        alerts.retain(|a| a.status.to_lowercase() == sf);
    }
    if let Some(sev_filter) = severity {
        let sev = sev_filter.to_lowercase();
        alerts.retain(|a| a.severity.to_lowercase() == sev);
    }
    Ok(alerts)
}

#[tauri::command]
fn ack_alert(alert_id: String, state: State<'_, AppState>) -> Result<bool, String> {
    let mut guard = state
        .alert_state
        .lock()
        .map_err(|_| "Failed to lock alert state".to_string())?;
    let inserted = guard.acknowledged_ids.insert(alert_id.clone());
    guard.ignored_ids.remove(&alert_id);
    let snapshot = guard.clone();
    drop(guard);

    persist_alert_state_snapshot(&state.alert_state_path, &snapshot)?;
    Ok(inserted)
}

#[tauri::command]
fn ack_alerts(alert_ids: Vec<String>, state: State<'_, AppState>) -> Result<u64, String> {
    let mut guard = state
        .alert_state
        .lock()
        .map_err(|_| "Failed to lock alert state".to_string())?;

    let mut changed = 0u64;
    for alert_id in alert_ids {
        let inserted = guard.acknowledged_ids.insert(alert_id.clone());
        let removed_ignore = guard.ignored_ids.remove(&alert_id);
        if inserted || removed_ignore {
            changed += 1;
        }
    }

    let snapshot = guard.clone();
    drop(guard);
    persist_alert_state_snapshot(&state.alert_state_path, &snapshot)?;
    Ok(changed)
}

#[tauri::command]
fn ignore_alert(alert_id: String, state: State<'_, AppState>) -> Result<bool, String> {
    let mut guard = state
        .alert_state
        .lock()
        .map_err(|_| "Failed to lock alert state".to_string())?;
    let inserted = guard.ignored_ids.insert(alert_id.clone());
    guard.acknowledged_ids.remove(&alert_id);
    let snapshot = guard.clone();
    drop(guard);

    persist_alert_state_snapshot(&state.alert_state_path, &snapshot)?;
    Ok(inserted)
}

#[tauri::command]
fn ignore_alerts(alert_ids: Vec<String>, state: State<'_, AppState>) -> Result<u64, String> {
    let mut guard = state
        .alert_state
        .lock()
        .map_err(|_| "Failed to lock alert state".to_string())?;

    let mut changed = 0u64;
    for alert_id in alert_ids {
        let inserted = guard.ignored_ids.insert(alert_id.clone());
        let removed_ack = guard.acknowledged_ids.remove(&alert_id);
        if inserted || removed_ack {
            changed += 1;
        }
    }

    let snapshot = guard.clone();
    drop(guard);
    persist_alert_state_snapshot(&state.alert_state_path, &snapshot)?;
    Ok(changed)
}

#[tauri::command]
fn reopen_alert(alert_id: String, state: State<'_, AppState>) -> Result<bool, String> {
    let mut guard = state
        .alert_state
        .lock()
        .map_err(|_| "Failed to lock alert state".to_string())?;

    let removed_ack = guard.acknowledged_ids.remove(&alert_id);
    let removed_ignore = guard.ignored_ids.remove(&alert_id);
    if removed_ack || removed_ignore {
        guard.first_seen_ms.remove(&alert_id);
    }
    let snapshot = guard.clone();
    drop(guard);

    persist_alert_state_snapshot(&state.alert_state_path, &snapshot)?;
    Ok(removed_ack || removed_ignore)
}

#[tauri::command]
fn get_alert_runbook(
    source: String,
    severity: String,
    state: State<'_, AppState>,
) -> Result<AlertRunbook, String> {
    let policy = load_alert_policy_config(&state);
    Ok(resolve_alert_runbook(&policy, &source, &severity))
}

#[tauri::command]
fn export_run_summary(
    date: Option<String>,
    state: State<'_, AppState>,
) -> Result<RunSummary, String> {
    let (metrics, queue, alerts, _) = build_overview_data(&state, 24)?;
    let date_str = if let Some(raw) = date {
        if let Ok(parsed) = NaiveDate::parse_from_str(&raw, "%Y-%m-%d") {
            parsed.format("%Y-%m-%d").to_string()
        } else {
            Local::now().format("%Y-%m-%d").to_string()
        }
    } else {
        Local::now().format("%Y-%m-%d").to_string()
    };

    let open_alerts: Vec<&AlertItem> = alerts
        .iter()
        .filter(|a| a.status == "open" && a.id != "system_nominal")
        .take(3)
        .collect();

    let mut lines = vec![
        format!("Operations Summary ({})", date_str),
        format!(
            "- Jobs: total {}, completed {}, failed {}, success {:.1}%",
            metrics.total_jobs, metrics.completed_jobs, metrics.failed_jobs, metrics.success_rate
        ),
        format!(
            "- Queue: pending {}, running {}, review {}, failed {}",
            queue.pending, queue.running, queue.review_ready, queue.failed
        ),
        format!(
            "- Services: {}/{} running",
            metrics.services_running, metrics.services_total
        ),
        format!(
            "- Avg turnaround: {:.1} min",
            metrics.avg_turnaround_minutes
        ),
    ];

    if open_alerts.is_empty() {
        lines.push("- Alerts: no active issues".to_string());
    } else {
        lines.push(format!("- Alerts: {} active", open_alerts.len()));
        for alert in open_alerts {
            lines.push(format!(
                "  • [{}] {}",
                alert.severity.to_uppercase(),
                alert.title
            ));
        }
    }

    Ok(RunSummary {
        date: date_str,
        text: lines.join("\n"),
        generated_at: now_epoch_ms(),
    })
}

// ============================================================================
// Artifact Commands
// ============================================================================

#[tauri::command]
fn list_verify_artifacts(
    job_id: String,
    state: State<'_, AppState>,
) -> Result<Vec<Artifact>, String> {
    let config = get_config_inner(&state)?;
    let path = verify_root(&config.work_root).join(&job_id);
    if !path.exists() {
        return Ok(vec![]);
    }

    let mut artifacts = Vec::new();

    let entries = fs::read_dir(&path).map_err(|e| format!("Failed to read directory: {}", e))?;

    for entry in entries {
        if let Ok(entry) = entry {
            let entry_path = entry.path();
            if entry_path.is_file() {
                let name = entry_path
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_default();

                let size = entry.metadata().map(|m| m.len()).unwrap_or(0);

                let artifact_type = entry_path
                    .extension()
                    .map(|e| e.to_string_lossy().to_string())
                    .unwrap_or_else(|| "unknown".to_string());

                artifacts.push(Artifact {
                    name,
                    path: entry_path.to_string_lossy().to_string(),
                    size,
                    artifact_type,
                });
            }
        }
    }

    Ok(artifacts)
}

#[tauri::command]
fn get_quality_report(
    job_id: String,
    state: State<'_, AppState>,
) -> Result<Option<QualityReport>, String> {
    let config = get_config_inner(&state)?;
    let path = verify_root(&config.work_root)
        .join(&job_id)
        .join(".system")
        .join("quality_report.json");
    if !path.exists() {
        return Ok(None);
    }

    let content =
        fs::read_to_string(&path).map_err(|e| format!("Failed to read quality report: {}", e))?;

    let json: serde_json::Value = serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse quality report: {}", e))?;

    let rounds = json.get("rounds").and_then(|v| v.as_array());
    let Some(rounds) = rounds else {
        return Ok(None);
    };
    let Some(last) = rounds.last() else {
        return Ok(None);
    };
    let metrics = last.get("metrics").and_then(|m| m.as_object());
    let Some(metrics) = metrics else {
        return Ok(None);
    };

    fn as_rate(v: Option<&serde_json::Value>) -> f64 {
        v.and_then(|x| x.as_f64().or_else(|| x.as_u64().map(|u| u as f64)))
            .unwrap_or(0.0)
    }
    fn pct(rate: f64) -> u32 {
        let mut v = (rate * 100.0).round();
        if v < 0.0 {
            v = 0.0;
        }
        if v > 100.0 {
            v = 100.0;
        }
        v as u32
    }

    Ok(Some(QualityReport {
        terminology_hit: pct(as_rate(metrics.get("terminology_rate"))),
        structure_fidelity: pct(as_rate(metrics.get("structure_complete_rate"))),
        purity_score: pct(as_rate(metrics.get("target_language_purity"))),
    }))
}

#[tauri::command]
fn get_verify_folder_path(state: State<'_, AppState>) -> Result<String, String> {
    let config = get_config_inner(&state)?;
    Ok(verify_root(&config.work_root).to_string_lossy().to_string())
}

// ============================================================================
// KB Health Commands
// ============================================================================

fn kb_sync_report_path(work_root: &str) -> PathBuf {
    PathBuf::from(work_root)
        .join(".system")
        .join("kb")
        .join("kb_sync_latest.json")
}

fn read_kb_sync_report(work_root: &str) -> Result<Option<KbSyncReport>, String> {
    let path = kb_sync_report_path(work_root);
    if !path.exists() {
        return Ok(None);
    }
    let content =
        fs::read_to_string(&path).map_err(|e| format!("Failed to read KB sync report: {}", e))?;
    let report: KbSyncReport = serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse KB sync report: {}", e))?;
    Ok(Some(report))
}

#[tauri::command]
fn get_kb_sync_report(state: State<'_, AppState>) -> Result<Option<KbSyncReport>, String> {
    let config = get_config_inner(&state)?;
    read_kb_sync_report(&config.work_root)
}

#[tauri::command]
fn get_kb_stats(state: State<'_, AppState>) -> Result<KbStats, String> {
    use rusqlite::Connection;

    let conn =
        Connection::open(&state.db_path).map_err(|e| format!("Failed to open database: {}", e))?;

    let total_files: u64 = conn
        .query_row("SELECT COUNT(*) FROM kb_files", [], |row| row.get(0))
        .unwrap_or(0);
    let total_chunks: u64 = conn
        .query_row(
            "SELECT COALESCE(SUM(chunk_count), 0) FROM kb_files",
            [],
            |row| row.get(0),
        )
        .unwrap_or(0);
    let last_indexed_at: Option<String> = conn
        .query_row("SELECT MAX(indexed_at) FROM kb_files", [], |row| row.get(0))
        .ok();

    let mut by_source_group: Vec<KbSourceGroupStat> = Vec::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT source_group, COUNT(*) as c, COALESCE(SUM(chunk_count), 0) as chunks FROM kb_files GROUP BY source_group ORDER BY c DESC"
    ) {
        let rows = stmt
            .query_map([], |row| {
                Ok(KbSourceGroupStat {
                    source_group: row.get(0)?,
                    count: row.get(1)?,
                    chunk_count: row.get(2)?,
                })
            })
            .map_err(|e| format!("Failed to query KB stats: {}", e))?;
        for r in rows {
            by_source_group.push(r.map_err(|e| format!("Failed to collect KB stats: {}", e))?);
        }
    }

    Ok(KbStats {
        total_files,
        total_chunks,
        last_indexed_at,
        by_source_group,
    })
}

#[tauri::command]
async fn kb_sync_now(state: State<'_, AppState>) -> Result<KbSyncReport, String> {
    let config = get_config_inner(&state)?;
    let python_bin = find_python_bin(&state);

    let output = Command::new(&python_bin)
        .args([
            "-m",
            "scripts.openclaw_v4_dispatcher",
            "--work-root",
            &config.work_root,
            "--kb-root",
            &config.kb_root,
            "kb-sync",
        ])
        .current_dir(&state.config_path)
        .output()
        .map_err(|e| format!("Failed to run kb-sync: {}", e))?;

    if !output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let detail = if !stderr.trim().is_empty() {
            stderr
        } else {
            stdout
        };
        return Err(format!("kb-sync failed: {}", detail));
    }

    match read_kb_sync_report(&config.work_root)? {
        Some(r) => Ok(r),
        None => Err("kb-sync finished but kb_sync_latest.json not found".to_string()),
    }
}

#[tauri::command]
fn list_kb_files(
    state: State<'_, AppState>,
    query: Option<String>,
    source_group: Option<String>,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<KbFileList, String> {
    use rusqlite::types::Value;
    use rusqlite::Connection;

    let conn =
        Connection::open(&state.db_path).map_err(|e| format!("Failed to open database: {}", e))?;

    let mut where_clauses: Vec<&'static str> = Vec::new();
    let mut params: Vec<Value> = Vec::new();

    if let Some(q) = query {
        let q = q.trim().to_string();
        if !q.is_empty() {
            where_clauses.push("path LIKE ?");
            params.push(Value::from(format!("%{}%", q)));
        }
    }

    if let Some(sg) = source_group {
        let sg = sg.trim().to_string();
        if !sg.is_empty() {
            where_clauses.push("source_group = ?");
            params.push(Value::from(sg));
        }
    }

    let where_sql = if where_clauses.is_empty() {
        "".to_string()
    } else {
        format!("WHERE {}", where_clauses.join(" AND "))
    };

    let total_sql = format!("SELECT COUNT(*) FROM kb_files {}", where_sql);
    let total: u64 = conn
        .query_row(
            &total_sql,
            rusqlite::params_from_iter(params.iter()),
            |row| row.get(0),
        )
        .unwrap_or(0);

    let limit = limit.unwrap_or(50).clamp(1, 250);
    let offset = offset.unwrap_or(0);

    let mut list_params = params;
    list_params.push(Value::from(i64::from(limit)));
    list_params.push(Value::from(i64::from(offset)));

    let sql = format!(
        "SELECT path, parser, source_group, chunk_count, indexed_at, size_bytes FROM kb_files {} ORDER BY indexed_at DESC LIMIT ? OFFSET ?",
        where_sql
    );
    let mut stmt = conn
        .prepare(&sql)
        .map_err(|e| format!("Failed to query KB files: {}", e))?;

    let rows = stmt
        .query_map(rusqlite::params_from_iter(list_params.iter()), |row| {
            let chunk_count: i64 = row.get(3)?;
            let size_bytes: i64 = row.get(5)?;
            Ok(KbFileRow {
                path: row.get(0)?,
                parser: row.get(1)?,
                source_group: row.get(2)?,
                chunk_count: std::cmp::max(0, chunk_count) as u64,
                indexed_at: row.get(4)?,
                size_bytes: std::cmp::max(0, size_bytes) as u64,
            })
        })
        .map_err(|e| format!("Failed to query KB files: {}", e))?;

    let mut items: Vec<KbFileRow> = Vec::new();
    for r in rows {
        items.push(r.map_err(|e| format!("Failed to collect KB files: {}", e))?);
    }

    Ok(KbFileList { total, items })
}

fn run_glossary_manager_json(
    state: &AppState,
    args: &[String],
) -> Result<serde_json::Value, String> {
    let config = get_config_inner(state)?;
    let python_bin = find_python_bin(state);

    let mut cmd_args = vec!["-m".to_string(), "scripts.glossary_manager".to_string()];
    cmd_args.extend_from_slice(args);

    let output = Command::new(&python_bin)
        .args(&cmd_args)
        .current_dir(&state.config_path)
        .output()
        .map_err(|e| format!("Failed to run glossary manager: {}", e))?;

    if !output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let detail = if !stderr.trim().is_empty() {
            stderr
        } else {
            stdout
        };
        return Err(format!("glossary manager failed: {}", detail));
    }

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let parsed: serde_json::Value = serde_json::from_str(&stdout)
        .map_err(|e| format!("Failed to parse glossary manager output: {}", e))?;
    if !parsed.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        let err = parsed
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown glossary manager error");
        return Err(format!("glossary manager error: {}", err));
    }
    let _ = config; // keep parity with other command helpers that load config.
    Ok(parsed)
}

#[tauri::command]
fn list_glossary_terms(
    state: State<'_, AppState>,
    company: Option<String>,
    language_pair: Option<String>,
    query: Option<String>,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<GlossaryTermList, String> {
    let config = get_config_inner(&state)?;
    let mut args: Vec<String> = vec![
        "list".to_string(),
        "--kb-root".to_string(),
        config.kb_root,
        "--limit".to_string(),
        limit.unwrap_or(500).to_string(),
        "--offset".to_string(),
        offset.unwrap_or(0).to_string(),
    ];

    if let Some(c) = company {
        let c = c.trim();
        if !c.is_empty() {
            args.push("--company".to_string());
            args.push(c.to_string());
        }
    }
    if let Some(lp) = language_pair {
        let lp = lp.trim();
        if !lp.is_empty() {
            args.push("--language-pair".to_string());
            args.push(lp.to_string());
        }
    }
    if let Some(q) = query {
        let q = q.trim();
        if !q.is_empty() {
            args.push("--query".to_string());
            args.push(q.to_string());
        }
    }

    let parsed = run_glossary_manager_json(&state, &args)?;
    let result = parsed
        .get("result")
        .cloned()
        .ok_or("glossary manager returned no result")?;
    serde_json::from_value::<GlossaryTermList>(result)
        .map_err(|e| format!("Failed to decode glossary terms: {}", e))
}

#[tauri::command]
fn upsert_glossary_term(
    state: State<'_, AppState>,
    company: String,
    source_lang: String,
    target_lang: String,
    source_text: String,
    target_text: String,
) -> Result<GlossaryTerm, String> {
    let config = get_config_inner(&state)?;
    let args: Vec<String> = vec![
        "upsert".to_string(),
        "--kb-root".to_string(),
        config.kb_root,
        "--company".to_string(),
        company,
        "--source-lang".to_string(),
        source_lang,
        "--target-lang".to_string(),
        target_lang,
        "--source-text".to_string(),
        source_text,
        "--target-text".to_string(),
        target_text,
    ];
    let parsed = run_glossary_manager_json(&state, &args)?;
    let item = parsed
        .get("item")
        .cloned()
        .ok_or("glossary manager returned no item")?;
    serde_json::from_value::<GlossaryTerm>(item)
        .map_err(|e| format!("Failed to decode glossary term: {}", e))
}

#[tauri::command]
fn delete_glossary_term(
    state: State<'_, AppState>,
    company: String,
    source_lang: String,
    target_lang: String,
    source_text: String,
) -> Result<bool, String> {
    let config = get_config_inner(&state)?;
    let args: Vec<String> = vec![
        "delete".to_string(),
        "--kb-root".to_string(),
        config.kb_root,
        "--company".to_string(),
        company,
        "--source-lang".to_string(),
        source_lang,
        "--target-lang".to_string(),
        target_lang,
        "--source-text".to_string(),
        source_text,
    ];
    let _ = run_glossary_manager_json(&state, &args)?;
    Ok(true)
}

#[tauri::command]
fn lookup_glossary_text(
    state: State<'_, AppState>,
    text: String,
    company: Option<String>,
    limit: Option<u32>,
) -> Result<GlossaryLookupResult, String> {
    let config = get_config_inner(&state)?;
    let mut args: Vec<String> = vec![
        "lookup".to_string(),
        "--kb-root".to_string(),
        config.kb_root,
        "--text".to_string(),
        text,
        "--limit".to_string(),
        limit.unwrap_or(20).to_string(),
    ];
    if let Some(c) = company {
        let c = c.trim();
        if !c.is_empty() {
            args.push("--company".to_string());
            args.push(c.to_string());
        }
    }

    let parsed = run_glossary_manager_json(&state, &args)?;
    let result = parsed
        .get("result")
        .cloned()
        .ok_or("glossary manager returned no result")?;
    serde_json::from_value::<GlossaryLookupResult>(result)
        .map_err(|e| format!("Failed to decode glossary lookup: {}", e))
}

// ============================================================================
// Docker / ClawRAG Commands
// ============================================================================

const CLAWRAG_CONTAINERS: &[&str] = &[
    "clawrag-gateway",
    "clawrag-backend",
    "clawrag-chromadb",
    "clawrag-ollama",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DockerContainer {
    pub name: String,
    pub status: String, // "running" | "stopped" | "not_found"
    pub image: String,
}

const DOCKER_PATHS: &[&str] = &[
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/usr/bin/docker",
];

fn find_docker() -> Option<String> {
    for path in DOCKER_PATHS {
        if std::path::Path::new(path).exists() {
            return Some(path.to_string());
        }
    }
    None
}

fn docker_available() -> bool {
    if let Some(docker) = find_docker() {
        Command::new(&docker)
            .arg("info")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    } else {
        false
    }
}

fn docker_cmd(args: &[&str]) -> Result<std::process::Output, String> {
    let docker = find_docker().ok_or("Docker binary not found")?;
    Command::new(&docker)
        .args(args)
        .output()
        .map_err(|e| format!("Failed to run docker: {}", e))
}

fn parse_docker_containers(stdout: &str) -> Vec<DockerContainer> {
    let mut containers: Vec<DockerContainer> = Vec::new();
    for name in CLAWRAG_CONTAINERS {
        let mut found = false;
        for line in stdout.lines() {
            let parts: Vec<&str> = line.split('\t').collect();
            if parts.len() >= 3 && parts[0] == *name {
                let status = if parts[1].starts_with("Up") {
                    "running"
                } else {
                    "stopped"
                };
                containers.push(DockerContainer {
                    name: name.to_string(),
                    status: status.to_string(),
                    image: parts[2].to_string(),
                });
                found = true;
                break;
            }
        }
        if !found {
            containers.push(DockerContainer {
                name: name.to_string(),
                status: "not_found".to_string(),
                image: String::new(),
            });
        }
    }
    containers
}

#[tauri::command]
fn get_docker_status() -> Result<Vec<DockerContainer>, String> {
    if !docker_available() {
        return Err("Docker is not running".to_string());
    }

    let output = docker_cmd(&[
        "ps",
        "-a",
        "--format",
        "{{.Names}}\t{{.Status}}\t{{.Image}}",
    ])?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(parse_docker_containers(&stdout))
}

#[tauri::command]
async fn start_docker_services() -> Result<Vec<DockerContainer>, String> {
    if !docker_available() {
        return Err("Docker is not running. Please start Docker Desktop first.".to_string());
    }

    for name in CLAWRAG_CONTAINERS {
        let _ = docker_cmd(&["start", name]);
    }

    tokio::time::sleep(std::time::Duration::from_secs(3)).await;

    let output = docker_cmd(&[
        "ps",
        "-a",
        "--format",
        "{{.Names}}\t{{.Status}}\t{{.Image}}",
    ])?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(parse_docker_containers(&stdout))
}

#[tauri::command]
async fn stop_docker_services() -> Result<(), String> {
    if !docker_available() {
        return Err("Docker is not running".to_string());
    }

    for name in CLAWRAG_CONTAINERS {
        let _ = docker_cmd(&["stop", name]);
    }

    Ok(())
}

// ============================================================================
// Utility Commands
// ============================================================================

#[tauri::command]
fn open_in_finder(path: String) -> Result<(), String> {
    let path_buf = PathBuf::from(&path);

    // Check if path exists
    if !path_buf.exists() {
        // Try to open parent folder if it exists
        if let Some(parent) = path_buf.parent() {
            if parent.exists() {
                let status = Command::new("open")
                    .arg(parent)
                    .status()
                    .map_err(|e| format!("Failed to open Finder: {}", e))?;

                if status.success() {
                    return Err(format!(
                        "Folder not found. Opened parent directory instead."
                    ));
                }
            }
        }
        return Err(format!("Path does not exist: {}", path));
    }

    // Use open command - if it's a directory, open it; if file, reveal in Finder
    let status = Command::new("open")
        .arg(&path)
        .status()
        .map_err(|e| format!("Failed to open Finder: {}", e))?;

    if !status.success() {
        return Err(format!("Failed to open path: {}", path));
    }

    Ok(())
}

#[tauri::command]
fn read_log_file(
    state: State<'_, AppState>,
    service: String,
    lines: u32,
) -> Result<Vec<String>, String> {
    read_log_file_inner(&state, &service, lines)
}

// ============================================================================
// API Provider Commands
// ============================================================================

/// Get the path to auth-profiles.json
fn get_auth_profiles_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
    PathBuf::from(format!(
        "{}/.openclaw/agents/main/agent/auth-profiles.json",
        home
    ))
}

/// Read auth profiles from JSON file
fn read_auth_profiles() -> Result<serde_json::Value, String> {
    let path = get_auth_profiles_path();
    if !path.exists() {
        return Ok(serde_json::json!({"profiles": {}}));
    }
    let content =
        fs::read_to_string(&path).map_err(|e| format!("Failed to read auth profiles: {}", e))?;
    serde_json::from_str(&content).map_err(|e| format!("Failed to parse auth profiles: {}", e))
}

/// Write auth profiles to JSON file
fn write_auth_profiles(profiles: &serde_json::Value) -> Result<(), String> {
    let path = get_auth_profiles_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("Failed to create directory: {}", e))?;
    }
    let content = serde_json::to_string_pretty(profiles)
        .map_err(|e| format!("Failed to serialize auth profiles: {}", e))?;
    fs::write(&path, content).map_err(|e| format!("Failed to write auth profiles: {}", e))
}

/// Known provider definitions
fn get_known_providers() -> Vec<(&'static str, &'static str, &'static str)> {
    vec![
        ("openai-codex", "OpenAI Codex", "oauth"),
        ("google-antigravity", "Google (Antigravity Proxy)", "oauth"),
        ("openrouter", "OpenRouter", "api_key"),
        ("moonshot", "Moonshot (Kimi)", "api_key"),
        ("zai", "Zai (GLM)", "api_key"),
    ]
}

#[tauri::command]
fn get_api_providers() -> Result<Vec<ApiProvider>, String> {
    let profiles = read_auth_profiles()?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;

    // Helper to find the active profile for a provider
    fn find_active_profile<'a>(
        profiles: &'a serde_json::Value,
        provider_id: &str,
    ) -> Option<(&'a str, &'a serde_json::Value)> {
        // First check lastGood for the active profile key
        if let Some(last_good) = profiles.get("lastGood").and_then(|lg| lg.get(provider_id)) {
            if let Some(key) = last_good.as_str() {
                if let Some(profile) = profiles.get("profiles").and_then(|p| p.get(key)) {
                    return Some((key, profile));
                }
            }
        }

        // Fallback: search for any profile matching this provider
        if let Some(profiles_obj) = profiles.get("profiles").and_then(|p| p.as_object()) {
            for (key, profile) in profiles_obj {
                if key.starts_with(&format!("{}:", provider_id)) {
                    return Some((key, profile));
                }
            }
        }

        None
    }

    let providers: Vec<ApiProvider> = get_known_providers()
        .into_iter()
        .map(|(id, name, auth_type)| {
            let profile_data = find_active_profile(&profiles, id);
            let (_profile_key, profile) = match profile_data {
                Some((key, p)) => (Some(key), Some(p)),
                None => (None, None),
            };

            let (has_key, email, expires_at, status) = match auth_type {
                "oauth" => {
                    // For OAuth providers, check for access token presence
                    match profile {
                        Some(p) => {
                            let access_token = p.get("access").and_then(|a| a.as_str());
                            let refresh_token = p.get("refresh").and_then(|r| r.as_str());
                            let email_val = p
                                .get("email")
                                .and_then(|e| e.as_str().map(|s| s.to_string()));
                            let expires = p.get("expires").and_then(|e| e.as_i64());
                            let provider_type = p.get("type").and_then(|t| t.as_str());

                            // Validate OAuth profile structure
                            let is_valid_oauth = provider_type == Some("oauth")
                                && access_token.is_some()
                                && refresh_token.is_some();

                            if !is_valid_oauth {
                                (false, email_val, expires, "missing".to_string())
                            } else if let Some(exp) = expires {
                                if exp < now {
                                    (true, email_val, Some(exp), "expired".to_string())
                                } else {
                                    (true, email_val, Some(exp), "configured".to_string())
                                }
                            } else {
                                (true, email_val, None, "configured".to_string())
                            }
                        }
                        None => (false, None, None, "missing".to_string()),
                    }
                }
                "api_key" => {
                    // For API key providers, check for key presence
                    match profile {
                        Some(p) => {
                            let key = p.get("key").and_then(|k| k.as_str());
                            let provider_type = p.get("type").and_then(|t| t.as_str());
                            let is_valid = provider_type == Some("api_key") && key.is_some();
                            (
                                is_valid,
                                None,
                                None,
                                if is_valid { "configured" } else { "missing" }.to_string(),
                            )
                        }
                        None => (false, None, None, "missing".to_string()),
                    }
                }
                _ => (false, None, None, "missing".to_string()),
            };

            ApiProvider {
                id: id.to_string(),
                name: name.to_string(),
                auth_type: auth_type.to_string(),
                status,
                has_key,
                email,
                expires_at,
            }
        })
        .collect();

    Ok(providers)
}

/// Provider activity estimation from local logs
struct ProviderActivity {
    calls: u64,
    errors: u64,
    last_seen_at: Option<i64>,
}

/// Estimate provider activity by parsing worker.log and telegram.log
fn estimate_provider_activity(
    state: &AppState,
    provider: &str,
    range_hours: u64,
) -> ProviderActivity {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;
    let cutoff = now - (range_hours as i64 * 3600 * 1000);

    // Provider keywords to match in log lines
    let (provider_keywords, error_keywords) = match provider {
        "moonshot" => (
            vec!["moonshot", "Moonshot", "MOONSHOT"],
            vec!["error", "Error", "failed", "timeout", "rate limit"],
        ),
        "zai" => (
            vec!["zai", "Zai", "ZAI", "zhipu"],
            vec!["error", "Error", "failed", "timeout", "rate limit"],
        ),
        "openai-codex" => (
            vec!["openai", "OpenAI", "gpt-", "codex"],
            vec!["error", "Error", "failed", "timeout", "rate limit"],
        ),
        "google-antigravity" | "google" | "gemini" => (
            vec!["google", "Gemini", "gemini", "antigravity"],
            vec!["error", "Error", "failed", "timeout", "rate limit"],
        ),
        _ => {
            return ProviderActivity {
                calls: 0,
                errors: 0,
                last_seen_at: None,
            }
        }
    };

    let mut calls: u64 = 0;
    let mut errors: u64 = 0;
    let mut last_seen_at: Option<i64> = None;

    // Parse timestamp from log line (format: "2026-02-18 23:45:07 ...")
    fn parse_log_timestamp(line: &str) -> Option<i64> {
        let re = regex::Regex::new(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})").ok()?;
        let caps = re.captures(line)?;
        let year: i32 = caps.get(1)?.as_str().parse().ok()?;
        let month: u32 = caps.get(2)?.as_str().parse().ok()?;
        let day: u32 = caps.get(3)?.as_str().parse().ok()?;
        let hour: u32 = caps.get(4)?.as_str().parse().ok()?;
        let min: u32 = caps.get(5)?.as_str().parse().ok()?;
        let sec: u32 = caps.get(6)?.as_str().parse().ok()?;

        let date = chrono::NaiveDate::from_ymd_opt(year, month, day)?;
        let time = chrono::NaiveTime::from_hms_opt(hour, min, sec)?;
        let dt = chrono::NaiveDateTime::new(date, time);
        Some(dt.and_utc().timestamp_millis())
    }

    // Check if line matches provider and error patterns
    fn line_matches(
        line: &str,
        provider_keywords: &[&str],
        error_keywords: &[&str],
    ) -> (bool, bool) {
        let line_lower = line.to_lowercase();
        let has_provider = provider_keywords
            .iter()
            .any(|k| line.contains(k) || line_lower.contains(&k.to_lowercase()));
        let has_error = error_keywords
            .iter()
            .any(|k| line.contains(k) || line_lower.contains(&k.to_lowercase()));
        (has_provider, has_error)
    }

    // Process a single log file
    fn process_log_file(
        path: &str,
        provider_keywords: &[&str],
        error_keywords: &[&str],
        cutoff: i64,
        calls: &mut u64,
        errors: &mut u64,
        last_seen_at: &mut Option<i64>,
    ) {
        if let Ok(content) = std::fs::read_to_string(path) {
            for line in content.lines() {
                let ts = parse_log_timestamp(line);
                if let Some(ts) = ts {
                    if ts < cutoff {
                        continue;
                    }
                }

                let (has_provider, has_error) =
                    line_matches(line, provider_keywords, error_keywords);
                if has_provider {
                    *calls += 1;
                    if has_error {
                        *errors += 1;
                    }
                    if let Some(ts) = ts {
                        *last_seen_at = Some(last_seen_at.unwrap_or(0).max(ts));
                    }
                }
            }
        }
    }

    // Check worker.log (primary) and telegram.log (secondary)
    let worker_log = format!("{}/worker.log", state.logs_dir);
    let telegram_log = format!("{}/telegram.log", state.logs_dir);

    process_log_file(
        &worker_log,
        &provider_keywords,
        &error_keywords,
        cutoff,
        &mut calls,
        &mut errors,
        &mut last_seen_at,
    );
    process_log_file(
        &telegram_log,
        &provider_keywords,
        &error_keywords,
        cutoff,
        &mut calls,
        &mut errors,
        &mut last_seen_at,
    );

    ProviderActivity {
        calls,
        errors,
        last_seen_at,
    }
}

#[tauri::command]
async fn get_api_usage(
    provider: String,
    state: State<'_, AppState>,
) -> Result<Option<ApiUsage>, String> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;

    let profiles = read_auth_profiles()?;

    match provider.as_str() {
        "openrouter" => {
            let profile_key = "openrouter:default";
            let api_key = profiles
                .get("profiles")
                .and_then(|p| p.get(profile_key))
                .and_then(|p| p.get("key").and_then(|k| k.as_str()));

            if let Some(key) = api_key {
                let client = reqwest::Client::new();
                let response = client
                    .get("https://openrouter.ai/api/v1/auth/key")
                    .header("Authorization", format!("Bearer {}", key))
                    .send()
                    .await
                    .map_err(|e| format!("Failed to fetch usage: {}", e))?;

                if response.status().is_success() {
                    let json: serde_json::Value = response
                        .json()
                        .await
                        .map_err(|e| format!("Failed to parse response: {}", e))?;

                    let data = json.get("data").unwrap_or(&serde_json::Value::Null);
                    let limit_remaining = data
                        .get("limit_remaining")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0);
                    let usage = data.get("usage").and_then(|v| v.as_u64()).unwrap_or(0);
                    let limit = limit_remaining + usage;

                    return Ok(Some(ApiUsage {
                        provider: provider.clone(),
                        used: usage,
                        limit,
                        remaining: limit_remaining,
                        unit: "credits".to_string(),
                        reset_at: None,
                        fetched_at: now,
                        source: "real_api".to_string(),
                        confidence: "high".to_string(),
                        reason: None,
                        activity_calls_24h: None,
                        activity_errors_24h: None,
                        activity_success_rate: None,
                        activity_last_seen_at: None,
                    }));
                }
            }
            // Fallback to estimated activity if API call failed
            let activity = estimate_provider_activity(&state, &provider, 24);
            if activity.calls > 0 {
                let success_rate = if activity.calls > 0 {
                    Some(((activity.calls - activity.errors) as f64) / (activity.calls as f64))
                } else {
                    None
                };
                Ok(Some(ApiUsage {
                    provider: provider.clone(),
                    used: 0,
                    limit: 0,
                    remaining: 0,
                    unit: "credits".to_string(),
                    reset_at: None,
                    fetched_at: now,
                    source: "estimated_activity".to_string(),
                    confidence: "low".to_string(),
                    reason: Some("API query failed, using log-based estimation".to_string()),
                    activity_calls_24h: Some(activity.calls),
                    activity_errors_24h: Some(activity.errors),
                    activity_success_rate: success_rate,
                    activity_last_seen_at: activity.last_seen_at,
                }))
            } else {
                Ok(None)
            }
        }
        // All estimatable providers (moonshot, zai, openai-codex, google-antigravity, etc.)
        pid
        @ ("moonshot" | "zai" | "openai-codex" | "google-antigravity" | "google" | "gemini") => {
            let profile_key = format!("{}:default", pid);
            let has_key = profiles
                .get("profiles")
                .and_then(|p| p.get(&profile_key))
                .is_some();

            // Try to estimate from logs
            let activity = estimate_provider_activity(&state, pid, 24);

            if activity.calls > 0 {
                // We have activity data from logs
                let success_rate = if activity.calls > 0 {
                    Some(((activity.calls - activity.errors) as f64) / (activity.calls as f64))
                } else {
                    None
                };
                let confidence = if activity.calls >= 10 {
                    "medium"
                } else {
                    "low"
                };

                Ok(Some(ApiUsage {
                    provider: provider.clone(),
                    used: 0,
                    limit: 0,
                    remaining: 0,
                    unit: "requests".to_string(),
                    reset_at: None,
                    fetched_at: now,
                    source: "estimated_activity".to_string(),
                    confidence: confidence.to_string(),
                    reason: Some(format!(
                        "Provider has no public usage API; estimated from {} log entries in 24h",
                        activity.calls
                    )),
                    activity_calls_24h: Some(activity.calls),
                    activity_errors_24h: Some(activity.errors),
                    activity_success_rate: success_rate,
                    activity_last_seen_at: activity.last_seen_at,
                }))
            } else if has_key {
                // Has key but no recent activity
                Ok(Some(ApiUsage {
                    provider: provider.clone(),
                    used: 0,
                    limit: 0,
                    remaining: 0,
                    unit: "requests".to_string(),
                    reset_at: None,
                    fetched_at: now,
                    source: "unsupported".to_string(),
                    confidence: "low".to_string(),
                    reason: Some(
                        "Provider has no public usage API and no recent local activity found"
                            .to_string(),
                    ),
                    activity_calls_24h: Some(0),
                    activity_errors_24h: Some(0),
                    activity_success_rate: None,
                    activity_last_seen_at: None,
                }))
            } else {
                // No key configured
                Ok(None)
            }
        }
        // Unknown providers
        _ => Ok(None),
    }
}

#[tauri::command]
fn set_api_key(provider: String, key: String) -> Result<(), String> {
    let mut profiles = read_auth_profiles()?;

    let profile_key = format!("{}:default", provider);
    let profiles_obj = profiles
        .get_mut("profiles")
        .ok_or("Invalid profiles structure")?
        .as_object_mut()
        .ok_or("Profiles is not an object")?;

    profiles_obj.insert(
        profile_key,
        serde_json::json!({
            "type": "api_key",
            "provider": provider,
            "key": key
        }),
    );

    write_auth_profiles(&profiles)
}

#[tauri::command]
fn delete_api_key(provider: String) -> Result<(), String> {
    let mut profiles = read_auth_profiles()?;

    let profile_key = format!("{}:default", provider);
    if let Some(profiles_obj) = profiles.get_mut("profiles").and_then(|p| p.as_object_mut()) {
        profiles_obj.remove(&profile_key);
    }

    write_auth_profiles(&profiles)
}

// ============================================================================
// Entry Point
// ============================================================================

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            get_service_status,
            start_all_services,
            stop_all_services,
            restart_all_services,
            start_service,
            stop_service,
            restart_service,
            run_preflight_check,
            auto_fix_preflight,
            start_openclaw,
            start_openclaw_v2,
            start_telegram_bot_v2,
            diagnose_telegram_bot,
            stop_openclaw_component,
            restart_openclaw_component,
            get_startup_snapshot,
            audit_operation,
            gateway_start,
            gateway_stop,
            gateway_status,
            gateway_login,
            get_config,
            save_config,
            get_env_settings,
            save_env_settings,
            get_jobs,
            get_job_milestones,
            get_overview_metrics,
            get_overview_trends,
            get_queue_snapshot,
            list_alerts,
            ack_alert,
            ack_alerts,
            ignore_alert,
            ignore_alerts,
            reopen_alert,
            get_alert_runbook,
            export_run_summary,
            list_verify_artifacts,
            get_quality_report,
            get_verify_folder_path,
            get_kb_sync_report,
            get_kb_stats,
            kb_sync_now,
            list_kb_files,
            list_glossary_terms,
            upsert_glossary_term,
            delete_glossary_term,
            lookup_glossary_text,
            get_docker_status,
            start_docker_services,
            stop_docker_services,
            open_in_finder,
            read_log_file,
            get_api_providers,
            get_api_usage,
            set_api_key,
            delete_api_key,
            get_model_availability_report,
        ])
        .setup(|app| {
            // Create system tray
            let open_item = MenuItem::with_id(app, "open", "Open Dashboard", true, None::<&str>)
                .expect("Failed to create open menu item");
            let restart_item =
                MenuItem::with_id(app, "restart", "Restart Services", true, None::<&str>)
                    .expect("Failed to create restart menu item");
            let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)
                .expect("Failed to create quit menu item");

            let menu = Menu::with_items(app, &[&open_item, &restart_item, &quit_item])
                .expect("Failed to create tray menu");

            let _tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "restart" => {
                        let _ = app.emit("tray-restart-services", ());
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)
                .expect("Failed to create tray icon");

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Write;

    #[test]
    fn read_env_map_parses_basic_assignments() {
        let dir = std::env::temp_dir().join(format!("openclaw_env_test_{}", std::process::id()));
        let _ = fs::create_dir_all(&dir);
        let path = dir.join(".env.v4.local");

        let mut f = fs::File::create(&path).expect("create env file");
        writeln!(f, "# comment").unwrap();
        writeln!(f, "export GOOGLE_API_KEY=\"abc\"").unwrap();
        writeln!(f, "GEMINI_API_KEY=").unwrap();
        writeln!(f, "OPENCLAW_GLM_ENABLED=1").unwrap();
        writeln!(f, "GLM_API_KEY='  '").unwrap();

        let map = read_env_map(&path);
        assert_eq!(map.get("GOOGLE_API_KEY").cloned(), Some("abc".to_string()));
        assert_eq!(map.get("GEMINI_API_KEY").cloned(), Some("".to_string()));
        assert_eq!(
            map.get("OPENCLAW_GLM_ENABLED").cloned(),
            Some("1".to_string())
        );
        assert_eq!(map.get("GLM_API_KEY").cloned(), Some("".to_string()));
    }

    #[test]
    fn agent_availability_marks_cooldown_when_all_profiles_in_cooldown() {
        let status = json!({
            "resolvedDefault": "openai-codex/gpt-5.2",
            "fallbacks": ["openai-codex/gpt-5.3-codex"],
            "auth": {
                "oauth": {
                    "profiles": [
                        {"provider": "openai-codex", "type": "oauth", "remainingMs": 1000}
                    ]
                },
                "unusableProfiles": [
                    {"provider": "openai-codex", "until": 12345}
                ]
            }
        });

        let mut availability: HashMap<String, bool> = HashMap::new();
        availability.insert("openai-codex/gpt-5.2".to_string(), true);
        availability.insert("openai-codex/gpt-5.3-codex".to_string(), true);

        let a = compute_agent_availability("translator-core", &status, &availability);
        assert!(!a.runnable_now);
        assert_eq!(a.route[0].state, "cooldown");
        assert!(a
            .blocked_reasons
            .iter()
            .any(|r| r.contains("openai-codex") && r.contains("12345")));
    }

    #[test]
    fn agent_availability_marks_expired_oauth() {
        let status = json!({
            "resolvedDefault": "openai-codex/gpt-5.2",
            "fallbacks": [],
            "auth": {
                "oauth": {
                    "profiles": [
                        {"provider": "openai-codex", "type": "oauth", "remainingMs": -1}
                    ]
                },
                "unusableProfiles": []
            }
        });

        let mut availability: HashMap<String, bool> = HashMap::new();
        availability.insert("openai-codex/gpt-5.2".to_string(), true);

        let a = compute_agent_availability("translator-core", &status, &availability);
        assert!(!a.runnable_now);
        assert_eq!(a.route[0].state, "expired");
        assert!(a.blocked_reasons.iter().any(|r| r.contains("OAuth")));
    }

    #[test]
    fn agent_availability_marks_ok_for_api_key_provider() {
        let status = json!({
            "resolvedDefault": "zai/glm-5",
            "fallbacks": [],
            "auth": {
                "oauth": {
                    "profiles": [
                        {"provider": "zai", "type": "api_key"}
                    ]
                },
                "unusableProfiles": []
            }
        });

        let mut availability: HashMap<String, bool> = HashMap::new();
        availability.insert("zai/glm-5".to_string(), true);

        let a = compute_agent_availability("glm-reviewer", &status, &availability);
        assert!(a.runnable_now);
        assert_eq!(a.first_runnable_model, Some("zai/glm-5".to_string()));
        assert_eq!(a.route[0].state, "ok");
    }

    #[test]
    fn fallbacks_place_kimi_models_first_and_glm_last() {
        let current = vec![
            "google-antigravity/gemini-3-pro-high".to_string(),
            "zai/glm-5".to_string(),
            "zai/glm-4.6v".to_string(),
        ];
        let desired = compute_fallbacks_with_kimi_defaults(
            current,
            "moonshot/kimi-k2.5",
            "kimi-coding/k2p5",
            "kimi-coding/k2p5",
        );
        assert_eq!(
            desired,
            vec![
                "moonshot/kimi-k2.5".to_string(),
                "kimi-coding/k2p5".to_string(),
                "google-antigravity/gemini-3-pro-high".to_string(),
                "zai/glm-5".to_string(),
                "zai/glm-4.6v".to_string(),
            ]
        );
    }

    #[test]
    fn fallbacks_keep_non_glm_order_after_kimi_defaults() {
        let current = vec![
            "openai-codex/gpt-5.2".to_string(),
            "google/gemini-2.5-pro".to_string(),
        ];
        let desired = compute_fallbacks_with_kimi_defaults(
            current,
            "moonshot/kimi-k2.5",
            "kimi-coding/k2p5",
            "kimi-coding/k2p5",
        );
        assert_eq!(
            desired,
            vec![
                "moonshot/kimi-k2.5".to_string(),
                "kimi-coding/k2p5".to_string(),
                "openai-codex/gpt-5.2".to_string(),
                "google/gemini-2.5-pro".to_string(),
            ]
        );
    }

    #[test]
    fn fallbacks_moves_existing_kimi_models_without_duplication() {
        let current = vec![
            "openai-codex/gpt-5.2".to_string(),
            "zai/glm-5".to_string(),
            "moonshot/kimi-k2.5".to_string(),
            "kimi-coding/k2p5".to_string(),
            "google/gemini-2.5-pro".to_string(),
        ];
        let desired = compute_fallbacks_with_kimi_defaults(
            current,
            "moonshot/kimi-k2.5",
            "kimi-coding/k2p5",
            "kimi-coding/k2p5",
        );
        assert_eq!(
            desired,
            vec![
                "moonshot/kimi-k2.5".to_string(),
                "kimi-coding/k2p5".to_string(),
                "openai-codex/gpt-5.2".to_string(),
                "google/gemini-2.5-pro".to_string(),
                "zai/glm-5".to_string(),
            ]
        );
    }

    #[test]
    fn fallbacks_dedupes_preserving_order() {
        let current = vec![
            "openai-codex/gpt-5.2".to_string(),
            "openai-codex/gpt-5.2".to_string(),
            "kimi-coding/k2p5".to_string(),
            "zai/glm-5".to_string(),
            "zai/glm-5".to_string(),
        ];
        let desired = compute_fallbacks_with_kimi_defaults(
            current,
            "moonshot/kimi-k2.5",
            "kimi-coding/k2p5",
            "kimi-coding/k2p5",
        );
        assert_eq!(
            desired,
            vec![
                "moonshot/kimi-k2.5".to_string(),
                "kimi-coding/k2p5".to_string(),
                "openai-codex/gpt-5.2".to_string(),
                "zai/glm-5".to_string(),
            ]
        );
    }

    #[test]
    fn parse_gateway_status_reads_nested_result_payload() {
        let payload = json!({
            "ok": true,
            "result": {
                "running": true,
                "healthy": true,
                "logged_in": false,
                "base_url": "http://127.0.0.1:8765",
                "model": "chatgpt-web",
                "last_error": "",
                "updated_at": "2026-01-01T00:00:00Z"
            }
        });
        let status = parse_gateway_status(&payload);
        assert!(status.running);
        assert!(status.healthy);
        assert!(!status.logged_in);
        assert_eq!(status.base_url, "http://127.0.0.1:8765");
        assert_eq!(status.model, "chatgpt-web");
    }

    #[test]
    fn parse_gateway_status_handles_flat_payload() {
        let payload = json!({
            "running": false,
            "healthy": false,
            "logged_in": false,
            "base_url": "",
            "model": "",
            "last_error": "gateway_unavailable",
            "updated_at": ""
        });
        let status = parse_gateway_status(&payload);
        assert!(!status.running);
        assert_eq!(status.last_error, "gateway_unavailable");
    }
}
