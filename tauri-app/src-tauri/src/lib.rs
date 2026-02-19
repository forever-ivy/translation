use serde::{Deserialize, Serialize};
use chrono::{Local, TimeZone};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use tauri::{Manager, Emitter, State};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;

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

pub struct AppState {
    pub services: Mutex<HashMap<String, ServiceStatus>>,
    pub config_path: String,
    pub scripts_path: String,
    pub pids_dir: String,
    pub logs_dir: String,
    pub db_path: String,
}

impl Default for AppState {
    fn default() -> Self {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
        let runtime_dir = format!("{}/.openclaw/runtime/translation", home);

        Self {
            services: Mutex::new(HashMap::new()),
            config_path: "/Users/Code/workflow/translation".to_string(),
            scripts_path: "/Users/Code/workflow/translation/scripts".to_string(),
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
        let pid_file = match service.name.as_str() {
            "Telegram Bot" => format!("{}/telegram.pid", state.pids_dir),
            "Run Worker" => format!("{}/worker.pid", state.pids_dir),
            _ => continue,
        };

        if let Ok(content) = fs::read_to_string(&pid_file) {
            if let Ok(pid) = content.trim().parse::<u32>() {
                let is_running = Command::new("kill")
                    .args(["-0", &pid.to_string()])
                    .output()
                    .map(|o| o.status.success())
                    .unwrap_or(false);

                if is_running {
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
                } else {
                    service.status = "stopped".to_string();
                }
            }
        } else {
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
    if (val.starts_with('"') && val.ends_with('"')) || (val.starts_with('\'') && val.ends_with('\'')) {
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

    let content = fs::read_to_string(&env_path)
        .map_err(|e| format!("Failed to read config: {}", e))?;

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
        let detail = if !stderr.trim().is_empty() { stderr } else { stdout };
        return Err(format!(
            "openclaw {:?} exited with code {:?}: {}",
            args,
            output.status.code(),
            detail
        ));
    }

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    serde_json::from_str(&stdout).map_err(|e| format!("Failed to parse openclaw JSON output: {}", e))
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
        let detail = if !stderr.trim().is_empty() { stderr } else { stdout };
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
    run_openclaw_cmd(&["models", "--agent", agent_id.trim(), "set-image", model.trim()])
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

    let raw = fs::read_to_string(&config_path)
        .map_err(|e| format!("Failed to read OpenClaw config {}: {}", config_path.display(), e))?;
    let mut root: serde_json::Value =
        serde_json::from_str(&raw).map_err(|e| format!("Failed to parse OpenClaw config JSON: {}", e))?;

    let mut changed = false;
    if let Some(list) = root
        .get_mut("agents")
        .and_then(|v| v.get_mut("list"))
        .and_then(|v| v.as_array_mut())
    {
        for item in list {
            let id = item.get("id").and_then(|v| v.as_str()).unwrap_or_default();
            if id == agent_id.trim() {
                let cur = item.get("model").and_then(|v| v.as_str()).unwrap_or_default();
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
        fs::write(&config_path, text)
            .map_err(|e| format!("Failed to write OpenClaw config {}: {}", config_path.display(), e))?;
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

fn provider_summaries_from_models_status(models_status: &serde_json::Value) -> HashMap<String, ProviderAuthSummary> {
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
            entry.cooldown_until_ms = Some(entry.cooldown_until_ms.map_or(until, |prev| prev.max(until)));
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

fn has_provider_profile(models_status: &serde_json::Value, provider: &str, profile_type: Option<&str>) -> bool {
    let Some(profiles) = models_status.pointer("/auth/oauth/profiles").and_then(|v| v.as_array()) else {
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
        let summary = provider_summaries.get(&provider).cloned().unwrap_or_default();

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
            cooldown_until_ms: if state == "cooldown" { summary.cooldown_until_ms } else { None },
            auth_expired: if summary.oauth_seen { Some(oauth_expired) } else { None },
            note,
        });
    }

    let first_runnable_model = route.iter().find(|r| r.state == "ok").map(|r| r.model.clone());
    let runnable_now = first_runnable_model.is_some();

    let mut blocked_reasons: Vec<String> = Vec::new();
    if !runnable_now {
        let mut seen_providers: HashSet<String> = HashSet::new();
        for item in &route {
            if !seen_providers.insert(item.provider.clone()) {
                continue;
            }
            let summary = provider_summaries.get(&item.provider).cloned().unwrap_or_default();
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

            if route.iter().any(|r| r.provider == item.provider && r.state == "unavailable") {
                blocked_reasons.push(format!("{} models are unavailable.", item.provider));
                continue;
            }

            if summary.oauth_seen && oauth_expired {
                blocked_reasons.push(format!("{} OAuth appears expired or missing.", item.provider));
                continue;
            }

            if summary.total_profiles == 0 {
                blocked_reasons.push(format!("{} has no auth profiles configured.", item.provider));
                continue;
            }

            if route.iter().all(|r| r.provider != item.provider || r.state == "unknown") {
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

fn compute_model_availability_report_inner(state: &AppState) -> Result<ModelAvailabilityReport, String> {
    let models_list = run_openclaw_json(&["models", "list", "--json"])?;
    let availability = models_available_map(&models_list);

    let translator_status = run_openclaw_json(&["models", "status", "--agent", "translator-core", "--json"])?;
    let review_status = run_openclaw_json(&["models", "status", "--agent", "review-core", "--json"])?;

    let mut agents: HashMap<String, AgentAvailability> = HashMap::new();
    let translator = compute_agent_availability("translator-core", &translator_status, &availability);
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
        .env("PATH", format!(
            "{}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            home
        ))
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

// ============================================================================
// Service Management Commands
// ============================================================================

#[tauri::command]
async fn get_service_status(state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    get_service_status_inner(&state)
}

#[tauri::command]
async fn start_all_services(state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    start_services_inner(&state)?;
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    get_service_status_inner(&state)
}

#[tauri::command]
async fn stop_all_services(state: State<'_, AppState>) -> Result<(), String> {
    stop_services_inner(&state)
}

#[tauri::command]
async fn restart_all_services(state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    stop_services_inner(&state)?;
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    start_services_inner(&state)?;
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    get_service_status_inner(&state)
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
async fn start_service(service_id: String, state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    let flag = service_flag(service_id.trim(), "start")?;
    run_start_script(&state, flag)?;
    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    get_service_status_inner(&state)
}

#[tauri::command]
async fn stop_service(service_id: String, state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    let flag = service_flag(service_id.trim(), "stop")?;
    run_start_script(&state, flag)?;
    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    get_service_status_inner(&state)
}

#[tauri::command]
async fn restart_service(service_id: String, state: State<'_, AppState>) -> Result<Vec<ServiceStatus>, String> {
    let flag = service_flag(service_id.trim(), "restart")?;
    run_start_script(&state, flag)?;
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    get_service_status_inner(&state)
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
            for agent in ["task-router", "translator-core", "review-core", "qa-gate", "glm-reviewer"] {
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
            for agent in ["task-router", "translator-core", "review-core", "qa-gate", "glm-reviewer"] {
                let _ = set_agent_image_model(agent, image_model.as_str());
            }
	        }
	    }

    // Re-run preflight checks
    let checks = run_preflight_check_inner(&state);
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
        status: if python_ok { "pass".to_string() } else { "blocker".to_string() },
        message: if python_ok { "Python 3 available".to_string() } else { "Python 3 not found".to_string() },
    });

    // venv check
    let venv_path = format!("{}/.venv", state.config_path);
    let venv_exists = PathBuf::from(&venv_path).exists();

    checks.push(PreflightCheck {
        name: "venv".to_string(),
        key: "venv".to_string(),
        status: if venv_exists { "pass".to_string() } else { "blocker".to_string() },
        message: if venv_exists { "Virtual environment exists".to_string() } else { "Run: python -m venv .venv".to_string() },
    });

    // requirements check
    let req_path = format!("{}/requirements.txt", state.config_path);
    let req_exists = PathBuf::from(&req_path).exists();

    checks.push(PreflightCheck {
        name: "requirements".to_string(),
        key: "requirements".to_string(),
        status: if req_exists { "pass".to_string() } else { "warning".to_string() },
        message: if req_exists { "requirements.txt found".to_string() } else { "requirements.txt not found".to_string() },
    });

    // .env check
    let env_path = format!("{}/.env.v4.local", state.config_path);
    let env_exists = PathBuf::from(&env_path).exists();

    checks.push(PreflightCheck {
        name: ".env.v4.local".to_string(),
        key: "env".to_string(),
        status: if env_exists { "pass".to_string() } else { "blocker".to_string() },
        message: if env_exists { "Config file exists".to_string() } else { "Create .env.v4.local from template".to_string() },
    });

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
            .env("PATH", format!(
                "{}:{}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
                std::env::var("PATH").unwrap_or_default(),
                home
            ))
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
        status: if openclaw_ok { "pass".to_string() } else { "blocker".to_string() },
        message: if openclaw_ok { "OpenClaw is running".to_string() } else { "Run: openclaw gateway --force".to_string() },
    });

    // Model availability checks (fast status; no live probes)
    let env_path = PathBuf::from(&state.config_path).join(".env.v4.local");
    let env_map = read_env_map(&env_path);
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

    let report = if openclaw_ok { compute_model_availability_report_inner(state).ok() } else { None };
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
    let (translator_status, translator_msg) = match report.as_ref().and_then(|r| r.agents.get("translator-core")) {
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
    };
    checks.push(PreflightCheck {
        name: "Models (translator-core)".to_string(),
        key: "models_translator_core".to_string(),
        status: translator_status.to_string(),
        message: translator_msg,
    });

    // review-core model route (optional-ish: warnings)
    let (review_status, review_msg) = match report.as_ref().and_then(|r| r.agents.get("review-core")) {
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
        status: if vision_has_google || vision_has_gemini || vision_has_moonshot || vision_has_openai {
            "pass".to_string()
        } else {
            "warning".to_string()
        },
        message: if vision_has_google || vision_has_gemini || vision_has_moonshot || vision_has_openai {
            "Vision QA credentials configured.".to_string()
        } else {
            "Missing vision credentials (Gemini, Moonshot, or OpenAI); Format QA will be skipped.".to_string()
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
        status: if libreoffice_ok { "pass".to_string() } else { "warning".to_string() },
        message: if libreoffice_ok { "LibreOffice available".to_string() } else { "Optional: Install LibreOffice".to_string() },
    });

    checks
}

#[tauri::command]
async fn start_openclaw(state: State<'_, AppState>) -> Result<Vec<PreflightCheck>, String> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
    let openclaw_paths = [
        format!("{}/.local/bin/openclaw", home),
        "/usr/local/bin/openclaw".to_string(),
        "/opt/homebrew/bin/openclaw".to_string(),
    ];

    let mut started = false;
    for path in &openclaw_paths {
        if PathBuf::from(&path).exists() {
            let result = Command::new(&path)
                .args(["gateway", "--force"])
                .env("HOME", &home)
                .env("PATH", format!(
                    "{}:{}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
                    std::env::var("PATH").unwrap_or_default(),
                    home
                ))
                .spawn();

            if result.is_ok() {
                started = true;
                // Wait a moment for gateway to start
                tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                break;
            }
        }
    }

    if !started {
        return Err("OpenClaw not found. Please install it first.".to_string());
    }

    // Re-run preflight checks
    let checks = run_preflight_check_inner(&state);
    Ok(checks)
}

#[tauri::command]
fn run_preflight_check(state: State<'_, AppState>) -> Vec<PreflightCheck> {
    run_preflight_check_inner(&state)
}

// ============================================================================
// Model Availability Commands
// ============================================================================

#[tauri::command]
fn get_model_availability_report(state: State<'_, AppState>) -> Result<ModelAvailabilityReport, String> {
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

    // Helper to update or add a line
    fn update_or_add(lines: &mut Vec<String>, key: &str, value: &str) {
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
                *line = format!("export {}={}", key, value);
            } else {
                *line = format!("{}={}", key, value);
            }
        } else {
            lines.push(format!("{}={}", key, value));
        }
    }

    let work_root = env_quote_double(&config.work_root);
    let kb_root = env_quote_double(&config.kb_root);
    update_or_add(&mut lines, "V4_WORK_ROOT", &work_root);
    update_or_add(&mut lines, "V4_KB_ROOT", &kb_root);
    update_or_add(&mut lines, "OPENCLAW_STRICT_ROUTER", if config.strict_router { "1" } else { "0" });
    update_or_add(&mut lines, "OPENCLAW_REQUIRE_NEW", if config.require_new { "1" } else { "0" });
    update_or_add(&mut lines, "OPENCLAW_RAG_BACKEND", &config.rag_backend);

    let content = lines.join("\n");
    fs::write(&env_path, content).map_err(|e| format!("Failed to write config: {}", e))?;

    Ok(())
}

// ============================================================================
// Job Commands
// ============================================================================

#[tauri::command]
fn get_jobs(status: Option<String>, limit: Option<u32>, state: State<'_, AppState>) -> Result<Vec<JobInfo>, String> {
    use rusqlite::Connection;

    let conn = Connection::open(&state.db_path)
        .map_err(|e| format!("Failed to open database: {}", e))?;

    let limit = limit.unwrap_or(50);

    let mut jobs = Vec::new();

    match status {
        Some(s) => {
            let mut stmt = conn.prepare(
                "SELECT job_id, status, task_type, sender, created_at, updated_at FROM jobs WHERE status = ?1 ORDER BY created_at DESC LIMIT ?2"
            ).map_err(|e| format!("Failed to prepare query: {}", e))?;

            let rows = stmt.query_map(rusqlite::params![s, limit], |row| {
                Ok(JobInfo {
                    job_id: row.get(0)?,
                    status: row.get(1)?,
                    task_type: row.get(2)?,
                    sender: row.get(3)?,
                    created_at: row.get(4)?,
                    updated_at: row.get(5)?,
                })
            }).map_err(|e| format!("Failed to query jobs: {}", e))?;

            for row in rows {
                jobs.push(row.map_err(|e| format!("Failed to collect jobs: {}", e))?);
            }
        }
        None => {
            let mut stmt = conn.prepare(
                "SELECT job_id, status, task_type, sender, created_at, updated_at FROM jobs ORDER BY created_at DESC LIMIT ?1"
            ).map_err(|e| format!("Failed to prepare query: {}", e))?;

            let rows = stmt.query_map(rusqlite::params![limit], |row| {
                Ok(JobInfo {
                    job_id: row.get(0)?,
                    status: row.get(1)?,
                    task_type: row.get(2)?,
                    sender: row.get(3)?,
                    created_at: row.get(4)?,
                    updated_at: row.get(5)?,
                })
            }).map_err(|e| format!("Failed to query jobs: {}", e))?;

            for row in rows {
                jobs.push(row.map_err(|e| format!("Failed to collect jobs: {}", e))?);
            }
        }
    }

    Ok(jobs)
}

#[tauri::command]
fn get_job_milestones(job_id: String, state: State<'_, AppState>) -> Result<Vec<Milestone>, String> {
    use rusqlite::Connection;

    let conn = Connection::open(&state.db_path)
        .map_err(|e| format!("Failed to open database: {}", e))?;

    let mut stmt = conn.prepare(
        "SELECT job_id, milestone, created_at, payload_json FROM events WHERE job_id = ?1 ORDER BY created_at ASC"
    ).map_err(|e| format!("Failed to prepare query: {}", e))?;

    let milestones = stmt.query_map(rusqlite::params![job_id], |row| {
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

// ============================================================================
// Artifact Commands
// ============================================================================

#[tauri::command]
fn list_verify_artifacts(job_id: String, state: State<'_, AppState>) -> Result<Vec<Artifact>, String> {
    let config = get_config_inner(&state)?;
    let path = verify_root(&config.work_root).join(&job_id);
    if !path.exists() {
        return Ok(vec![]);
    }

    let mut artifacts = Vec::new();

    let entries = fs::read_dir(&path)
        .map_err(|e| format!("Failed to read directory: {}", e))?;

    for entry in entries {
        if let Ok(entry) = entry {
            let entry_path = entry.path();
            if entry_path.is_file() {
                let name = entry_path.file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_default();

                let size = entry.metadata().map(|m| m.len()).unwrap_or(0);

                let artifact_type = entry_path.extension()
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
fn get_quality_report(job_id: String, state: State<'_, AppState>) -> Result<Option<QualityReport>, String> {
    let config = get_config_inner(&state)?;
    let path = verify_root(&config.work_root)
        .join(&job_id)
        .join(".system")
        .join("quality_report.json");
    if !path.exists() {
        return Ok(None);
    }

    let content = fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read quality report: {}", e))?;

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
    let content = fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read KB sync report: {}", e))?;
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

    let conn = Connection::open(&state.db_path)
        .map_err(|e| format!("Failed to open database: {}", e))?;

    let total_files: u64 = conn
        .query_row("SELECT COUNT(*) FROM kb_files", [], |row| row.get(0))
        .unwrap_or(0);
    let total_chunks: u64 = conn
        .query_row("SELECT COALESCE(SUM(chunk_count), 0) FROM kb_files", [], |row| row.get(0))
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
        let detail = if !stderr.trim().is_empty() { stderr } else { stdout };
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

    let conn = Connection::open(&state.db_path)
        .map_err(|e| format!("Failed to open database: {}", e))?;

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
                let status = if parts[1].starts_with("Up") { "running" } else { "stopped" };
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

    let output = docker_cmd(&["ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"])?;
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

    let output = docker_cmd(&["ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"])?;
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
                    return Err(format!("Folder not found. Opened parent directory instead."));
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
fn read_log_file(state: State<'_, AppState>, service: String, lines: u32) -> Result<Vec<String>, String> {
    let log_file = match service.as_str() {
        "telegram" => format!("{}/telegram.log", state.logs_dir),
        "worker" => format!("{}/worker.log", state.logs_dir),
        _ => return Err(format!("Unknown service: {}", service)),
    };

    let output = Command::new("tail")
        .args(["-n", &lines.to_string(), &log_file])
        .output()
        .map_err(|e| format!("Failed to read log: {}", e))?;

    let content = String::from_utf8_lossy(&output.stdout);
    Ok(content.lines().map(|s| s.to_string()).collect())
}

// ============================================================================
// API Provider Commands
// ============================================================================

/// Get the path to auth-profiles.json
fn get_auth_profiles_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());
    PathBuf::from(format!("{}/.openclaw/agents/main/agent/auth-profiles.json", home))
}

/// Read auth profiles from JSON file
fn read_auth_profiles() -> Result<serde_json::Value, String> {
    let path = get_auth_profiles_path();
    if !path.exists() {
        return Ok(serde_json::json!({"profiles": {}}));
    }
    let content = fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read auth profiles: {}", e))?;
    serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse auth profiles: {}", e))
}

/// Write auth profiles to JSON file
fn write_auth_profiles(profiles: &serde_json::Value) -> Result<(), String> {
    let path = get_auth_profiles_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create directory: {}", e))?;
    }
    let content = serde_json::to_string_pretty(profiles)
        .map_err(|e| format!("Failed to serialize auth profiles: {}", e))?;
    fs::write(&path, content)
        .map_err(|e| format!("Failed to write auth profiles: {}", e))
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
                            let email_val = p.get("email").and_then(|e| e.as_str().map(|s| s.to_string()));
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
                            (is_valid, None, None, if is_valid { "configured" } else { "missing" }.to_string())
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

#[tauri::command]
async fn get_api_usage(provider: String) -> Result<Option<ApiUsage>, String> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;

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
                    let json: serde_json::Value = response.json().await
                        .map_err(|e| format!("Failed to parse response: {}", e))?;

                    let data = json.get("data").unwrap_or(&serde_json::Value::Null);
                    let limit_remaining = data.get("limit_remaining").and_then(|v| v.as_u64()).unwrap_or(0);
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
                    }));
                }
            }
            Ok(None)
        }
        "zai" => {
            // Zai API usage - check if we have the key
            let profile_key = "zai:default";
            let has_key = profiles
                .get("profiles")
                .and_then(|p| p.get(profile_key))
                .is_some();

            if has_key {
                // Return placeholder - actual API would need to be implemented
                Ok(Some(ApiUsage {
                    provider: provider.clone(),
                    used: 0,
                    limit: 0,
                    remaining: 0,
                    unit: "tokens".to_string(),
                    reset_at: None,
                    fetched_at: now,
                }))
            } else {
                Ok(None)
            }
        }
        _ => Ok(None),
    }
}

#[tauri::command]
fn set_api_key(provider: String, key: String) -> Result<(), String> {
    let mut profiles = read_auth_profiles()?;

    let profile_key = format!("{}:default", provider);
    let profiles_obj = profiles.get_mut("profiles")
        .ok_or("Invalid profiles structure")?
        .as_object_mut()
        .ok_or("Profiles is not an object")?;

    profiles_obj.insert(profile_key, serde_json::json!({
        "type": "api_key",
        "provider": provider,
        "key": key
    }));

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
            get_config,
            save_config,
            get_jobs,
            get_job_milestones,
            list_verify_artifacts,
            get_quality_report,
            get_verify_folder_path,
            get_kb_sync_report,
            get_kb_stats,
            kb_sync_now,
            list_kb_files,
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
            let restart_item = MenuItem::with_id(app, "restart", "Restart Services", true, None::<&str>)
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
        assert_eq!(map.get("OPENCLAW_GLM_ENABLED").cloned(), Some("1".to_string()));
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
        assert!(a.blocked_reasons.iter().any(|r| r.contains("openai-codex") && r.contains("12345")));
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
        let current = vec!["openai-codex/gpt-5.2".to_string(), "google/gemini-2.5-pro".to_string()];
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
}
