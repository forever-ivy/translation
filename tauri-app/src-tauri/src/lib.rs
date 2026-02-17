use serde::{Deserialize, Serialize};
use std::collections::HashMap;
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
    pub purity_score: f64,
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

    // Helper to extract value after first '=' and strip quotes
    fn extract_value(line: &str) -> String {
        if let Some(pos) = line.find('=') {
            let value = &line[pos + 1..];
            value.trim().trim_matches('"').to_string()
        } else {
            String::new()
        }
    }

    for line in content.lines() {
        if line.starts_with("V4_WORK_ROOT=") {
            config.work_root = extract_value(line);
        } else if line.starts_with("V4_KB_ROOT=") {
            config.kb_root = extract_value(line);
        } else if line.starts_with("OPENCLAW_STRICT_ROUTER=") {
            config.strict_router = line.contains("1");
        } else if line.starts_with("OPENCLAW_REQUIRE_NEW=") {
            config.require_new = line.contains("1");
        } else if line.starts_with("OPENCLAW_RAG_BACKEND=") {
            config.rag_backend = extract_value(line);
        }
    }

    Ok(config)
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

// ============================================================================
// Preflight Check Commands
// ============================================================================

#[tauri::command]
fn auto_fix_preflight(state: State<'_, AppState>) -> Result<Vec<PreflightCheck>, String> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/ivy".to_string());

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
"#;
        let _ = fs::write(&env_path, template);
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
        if let Some(line) = lines.iter_mut().find(|l| l.starts_with(&key_prefix)) {
            *line = format!("{}={}", key, value);
        } else {
            lines.push(format!("{}={}", key, value));
        }
    }

    update_or_add(&mut lines, "V4_WORK_ROOT", &config.work_root);
    update_or_add(&mut lines, "V4_KB_ROOT", &config.kb_root);
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
    let verify_path = format!("{}/_VERIFY/{}", config.work_root, job_id);

    let path = PathBuf::from(&verify_path);
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
    let report_path = format!("{}/_VERIFY/{}/quality_report.json", config.work_root, job_id);

    let path = PathBuf::from(&report_path);
    if !path.exists() {
        return Ok(None);
    }

    let content = fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read quality report: {}", e))?;

    let json: serde_json::Value = serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse quality report: {}", e))?;

    Ok(Some(QualityReport {
        terminology_hit: json.get("terminology_hit").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
        structure_fidelity: json.get("structure_fidelity").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
        purity_score: json.get("purity_score").and_then(|v| v.as_f64()).unwrap_or(0.0),
    }))
}

#[tauri::command]
fn get_verify_folder_path(state: State<'_, AppState>) -> Result<String, String> {
    let config = get_config_inner(&state)?;
    Ok(format!("{}/_VERIFY", config.work_root))
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
            get_docker_status,
            start_docker_services,
            stop_docker_services,
            open_in_finder,
            read_log_file,
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
