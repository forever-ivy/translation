#!/usr/bin/env bash
#
# Translation System One-Click Start/Stop Script
# Usage:
#   ./scripts/start.sh              # Interactive mode
#   ./scripts/start.sh --all        # Start all services
#   ./scripts/start.sh --telegram   # Start Telegram bot only
#   ./scripts/start.sh --email      # Start email poll (one-shot)
#   ./scripts/start.sh --reminder   # Start pending reminder (one-shot)
#   ./scripts/start.sh --status     # Show service status
#   ./scripts/start.sh --stop       # Stop all services
#   ./scripts/start.sh --restart    # Restart all services
#
set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

ROOT_DIR="/Users/Code/workflow/translation"
cd "$ROOT_DIR"

export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"

# Paths
PID_DIR="$HOME/.openclaw/runtime/translation/pids"
LOG_DIR="$HOME/.openclaw/runtime/translation/logs"
PYTHON_BIN="${V4_PYTHON_BIN:-/Users/Code/workflow/translation/.venv/bin/python}"

# =============================================================================
# Helper Functions
# =============================================================================

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()      { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }

ensure_dirs() {
    mkdir -p "$PID_DIR" "$LOG_DIR"
}

load_env() {
    if [[ -f ".env.v4.local" ]]; then
        set -a
        source ".env.v4.local"
        set +a
    fi
}

is_truthy() {
    local v
    v="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]' | xargs)"
    case "$v" in
        1|true|yes|y|on) return 0 ;;
        0|false|no|n|off|"") return 1 ;;
        *) return 0 ;;
    esac
}

service_name() {
    case "$1" in
        telegram) echo "Telegram Bot" ;;
        worker)   echo "Run Worker" ;;
        email)    echo "Email Poll" ;;
        reminder) echo "Pending Reminder" ;;
        *)        echo "$1" ;;
    esac
}

# =============================================================================
# PID Management
# =============================================================================

get_pid_file() {
    echo "$PID_DIR/${1}.pid"
}

get_log_file() {
    echo "$LOG_DIR/${1}.log"
}

is_running() {
    local service="$1"
    local pid_file
    pid_file="$(get_pid_file "$service")"

    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        # Stale PID file
        rm -f "$pid_file"
    fi
    return 1
}

get_pid() {
    local service="$1"
    local pid_file
    pid_file="$(get_pid_file "$service")"
    if [[ -f "$pid_file" ]]; then
        cat "$pid_file" 2>/dev/null || echo ""
    fi
}

save_pid() {
    echo "$2" > "$(get_pid_file "$1")"
}

clear_pid() {
    rm -f "$(get_pid_file "$1")"
}

# =============================================================================
# Service Control
# =============================================================================

start_telegram() {
    if is_running "telegram"; then
        log_warn "Telegram bot is already running (PID: $(get_pid "telegram"))"
        return 0
    fi

    log_info "Starting Telegram bot..."
    load_env

    local log_file
    log_file="$(get_log_file "telegram")"

    # Start worker first if autostart is enabled
    if is_truthy "${OPENCLAW_RUN_WORKER_AUTOSTART:-1}"; then
        start_worker || log_warn "Worker auto-start skipped (may already be running)"
    fi

    nohup "$PYTHON_BIN" -m scripts.telegram_bot >>"$log_file" 2>&1 &
    local pid=$!
    save_pid "telegram" "$pid"

    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        log_ok "Telegram bot started (PID: $pid)"
        log_info "Log: $log_file"
    else
        log_error "Telegram bot failed to start. Check log: $log_file"
        return 1
    fi
}

start_worker() {
    if is_running "worker"; then
        log_warn "Worker is already running (PID: $(get_pid "worker"))"
        return 0
    fi

    log_info "Starting run worker..."
    load_env

    local log_file
    log_file="$(get_log_file "worker")"

    nohup "$PYTHON_BIN" -m scripts.skill_run_worker >>"$log_file" 2>&1 &
    local pid=$!
    save_pid "worker" "$pid"

    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        log_ok "Worker started (PID: $pid)"
        log_info "Log: $log_file"
    else
        log_error "Worker failed to start. Check log: $log_file"
        return 1
    fi
}

start_email() {
    log_info "Running email poll (one-shot)..."
    load_env

    if [[ -z "${V4_IMAP_HOST:-}" ]]; then
        log_error "V4_IMAP_HOST is not set. Configure .env.v4.local first."
        return 1
    fi

    local work_root="${V4_WORK_ROOT:-/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task}"
    local kb_root="${V4_KB_ROOT:-/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository}"
    local notify_target="${OPENCLAW_NOTIFY_TARGET:-+8615071054627}"

    local args=(
        -m scripts.openclaw_v4_dispatcher
        --work-root "$work_root"
        --kb-root "$kb_root"
        --notify-target "$notify_target"
        email-poll
        --imap-host "$V4_IMAP_HOST"
        --imap-port "${V4_IMAP_PORT:-993}"
        --imap-user "${V4_IMAP_USER:-}"
        --imap-password "${V4_IMAP_PASSWORD:-}"
        --mailbox "${V4_IMAP_MAILBOX:-INBOX}"
        --from-filter "${V4_IMAP_FROM_FILTER:-modeh@eventranz.com}"
        --max-messages "${V4_IMAP_MAX_MESSAGES:-5}"
    )

    if [[ "${V5_EMAIL_AUTO_RUN:-0}" == "1" ]]; then
        args+=(--auto-run)
    fi

    "$PYTHON_BIN" "${args[@]}"
    log_ok "Email poll completed"
}

start_reminder() {
    log_info "Running pending reminder (one-shot)..."
    load_env

    local work_root="${V4_WORK_ROOT:-/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task}"
    local kb_root="${V4_KB_ROOT:-/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository}"
    local notify_target="${OPENCLAW_NOTIFY_TARGET:-+8615071054627}"

    "$PYTHON_BIN" -m scripts.openclaw_v4_dispatcher \
        --work-root "$work_root" \
        --kb-root "$kb_root" \
        --notify-target "$notify_target" \
        pending-reminder

    log_ok "Pending reminder completed"
}

stop_service() {
    local service="$1"
    local name
    name=$(service_name "$service")
    local pid

    # Determine the process pattern for pkill fallback
    local proc_pattern
    case "$service" in
        telegram) proc_pattern="scripts.telegram_bot" ;;
        worker)   proc_pattern="scripts.skill_run_worker" ;;
        *)        proc_pattern="" ;;
    esac

    # Try PID file first
    if is_running "$service"; then
        pid=$(get_pid "$service")
        log_info "Stopping $name (PID: $pid)..."
        kill "$pid" 2>/dev/null || true

        local i=0
        while [[ $i -lt 5 ]]; do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
            i=$((i + 1))
        done

        if kill -0 "$pid" 2>/dev/null; then
            log_warn "Force killing $name..."
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi

    # Fallback: kill by process name if still running (handles stale PID files)
    if [[ -n "$proc_pattern" ]]; then
        local real_pid
        real_pid=$(pgrep -f "$proc_pattern" 2>/dev/null | head -1 || true)
        if [[ -n "$real_pid" ]]; then
            log_info "Stopping stale $name (PID: $real_pid via pgrep)..."
            kill "$real_pid" 2>/dev/null || true

            local i=0
            while [[ $i -lt 5 ]]; do
                if ! kill -0 "$real_pid" 2>/dev/null; then
                    break
                fi
                sleep 1
                i=$((i + 1))
            done

            if kill -0 "$real_pid" 2>/dev/null; then
                kill -9 "$real_pid" 2>/dev/null || true
            fi
        fi
    fi

    clear_pid "$service"
    log_ok "$name stopped"
}

stop_all() {
    log_info "Stopping all services..."
    stop_service "telegram"
    stop_service "worker"
    log_ok "All services stopped"
}

# =============================================================================
# Status Display
# =============================================================================

show_status() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║           Translation System Service Status                 ║"
    echo "╠════════════════════════════════════════════════════════════╣"

    for service in telegram worker; do
        local name status pid

        name=$(service_name "$service")

        if is_running "$service"; then
            pid=$(get_pid "$service")
            printf "║  %-20s  " "$name"
            echo -e "${GREEN}● Running${NC}  (PID: $pid)     ║"
        else
            printf "║  %-20s  " "$name"
            echo -e "${RED}○ Stopped${NC}                       ║"
        fi
    done

    echo "╠════════════════════════════════════════════════════════════╣"
    echo "║  One-shot services (run manually):                          ║"
    echo "║    • Email Poll      : ./scripts/start.sh --email          ║"
    echo "║    • Pending Reminder: ./scripts/start.sh --reminder       ║"
    echo "╠════════════════════════════════════════════════════════════╣"
    echo "║  Logs: $LOG_DIR"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
}

# =============================================================================
# Interactive Mode
# =============================================================================

interactive_menu() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║           Translation System Control Panel                  ║"
    echo "╠════════════════════════════════════════════════════════════╣"
    echo "║  1) Start all services                                      ║"
    echo "║  2) Start Telegram bot only                                 ║"
    echo "║  3) Stop all services                                       ║"
    echo "║  4) Restart all services                                    ║"
    echo "║  5) Show status                                             ║"
    echo "║  6) Run email poll (one-shot)                               ║"
    echo "║  7) Run pending reminder (one-shot)                         ║"
    echo "║  8) Tail Telegram bot log                                   ║"
    echo "║  9) Tail Worker log                                         ║"
    echo "║  q) Quit                                                    ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    read -p "Select option [1-9,q]: " choice

    case "$choice" in
        1) start_all ;;
        2) start_telegram ;;
        3) stop_all ;;
        4) stop_all; start_all ;;
        5) show_status ;;
        6) start_email ;;
        7) start_reminder ;;
        8) tail_log "telegram" ;;
        9) tail_log "worker" ;;
        q|Q) log_info "Goodbye!"; exit 0 ;;
        *) log_error "Invalid option"; interactive_menu ;;
    esac
}

start_all() {
    log_info "Starting all services..."
    start_telegram
    show_status
}

tail_log() {
    local service="$1"
    local log_file
    log_file="$(get_log_file "$service")"

    if [[ ! -f "$log_file" ]]; then
        log_warn "No log file found: $log_file"
        return 1
    fi

    log_info "Tailing $service log (Ctrl+C to exit)..."
    tail -f "$log_file"
}

# =============================================================================
# Main Entry Point
# =============================================================================

ensure_dirs

case "${1:-}" in
    --all|-a)
        start_all
        ;;
    --telegram|-t)
        start_telegram
        ;;
    --worker|-w)
        start_worker
        ;;
    --email|-e)
        start_email
        ;;
    --reminder|-r)
        start_reminder
        ;;
    --status|-s)
        show_status
        ;;
    --stop)
        stop_all
        ;;
    --restart)
        stop_all
        start_all
        ;;
    --logs)
        echo "Log directory: $LOG_DIR"
        ls -la "$LOG_DIR" 2>/dev/null || echo "(empty)"
        ;;
    -h|--help)
        echo "Usage: $0 [OPTION]"
        echo ""
        echo "Options:"
        echo "  --all, -a       Start all services (Telegram + Worker)"
        echo "  --telegram, -t  Start Telegram bot only"
        echo "  --worker, -w    Start worker only"
        echo "  --email, -e     Run email poll (one-shot)"
        echo "  --reminder, -r  Run pending reminder (one-shot)"
        echo "  --status, -s    Show service status"
        echo "  --stop          Stop all services"
        echo "  --restart       Restart all services"
        echo "  --logs          Show log directory"
        echo "  -h, --help      Show this help"
        echo ""
        echo "No option: Interactive menu mode"
        ;;
    "")
        interactive_menu
        ;;
    *)
        log_error "Unknown option: $1"
        echo "Run '$0 --help' for usage"
        exit 1
        ;;
esac
