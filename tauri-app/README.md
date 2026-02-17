# Inifity

**Version 1.0.0**

A modern desktop dashboard application for managing the Arabic-to-English translation automation system. Built with Tauri, React, and TypeScript.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Version](https://img.shields.io/badge/version-1.0.0-green.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Development](#development)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Inifity is a desktop application that provides a visual interface for managing the OpenClaw-based translation automation pipeline. It allows users to:

- Monitor and control background services (Telegram Bot, Worker)
- Run pre-flight system checks
- Manage translation jobs
- Review and verify translated artifacts
- View system logs
- Monitor knowledge base health
- Configure system settings

The application communicates with the local OpenClaw gateway and SQLite database to provide real-time status updates and job management capabilities.

---

## Features

### Dashboard
- Real-time service status monitoring with visual indicators
- Docker/ClawRAG container status
- Quick actions for starting/stopping/restarting services
- Recent jobs overview
- One-click access to verify folder

### Services
- Pre-flight checks for Python, venv, requirements, OpenClaw, and LibreOffice
- Auto-fix functionality for common issues
- Start OpenClaw gateway directly from the UI
- Individual service control

### Jobs
- List all translation jobs with status filtering
- Detailed job milestones and event tracking
- Artifact preview with quality metrics
- Job lifecycle management

### Verify
- Review-ready jobs dashboard
- Artifact browser with file type icons
- Quality report visualization (Terminology Hit, Structure Fidelity, Purity Score)
- One-click "Open in Finder" for file access

### Logs
- Real-time log viewing for Telegram Bot and Worker services
- Auto-refresh capability
- Log level indicators

### KB Health
- Knowledge base synchronization status
- RAG backend health monitoring
- Document index statistics

### Settings
- Work root and KB root path configuration
- OpenClaw router settings (Strict Mode, Require New)
- RAG backend selection
- Persistent configuration storage

---

## Requirements

### System Requirements
- **macOS**: 10.15 (Catalina) or later
- **Windows**: Windows 10 or later
- **Linux**: Ubuntu 18.04+ or equivalent

### Prerequisites
- [OpenClaw](https://github.com/anthropics/openclaw) installed and configured
- Python 3.8+ with virtual environment
- Docker Desktop (for ClawRAG containers, optional)
- LibreOffice (optional, for document conversion)

---

## Installation

### Download Pre-built Binary

Download the latest release for your platform from the [Releases](https://github.com/your-repo/inifity/releases) page.

- **macOS**: `Inifity_1.0.0_x64.dmg` or `Inifity_1.0.0_aarch64.dmg`
- **Windows**: `Inifity_1.0.0_x64.msi` or `Inifity_1.0.0_x64-setup.exe`
- **Linux**: `Inifity_1.0.0_amd64.AppImage` or `.deb` package

### Install from Source

```bash
# Clone the repository
git clone https://github.com/your-repo/inifity.git
cd inifity

# Install dependencies
pnpm install

# Build for production
pnpm tauri build

# The built application will be in src-tauri/target/release/bundle/
```

---

## Quick Start

### For End Users

1. **Install OpenClaw** (if not already installed)
   ```bash
   pip install openclaw
   openclaw gateway --force
   ```

2. **Launch Inifity**
   - Open the application from your Applications folder (macOS)
   - Or run the executable (Windows/Linux)

3. **Run Pre-flight Checks**
   - Navigate to **Services** tab
   - Click **Run Pre-flight Check**
   - Fix any issues using **Auto Fix All** or manually

4. **Start Services**
   - Click **Start All** in the Services tab
   - Or use the Dashboard quick actions

5. **Configure Settings**
   - Navigate to **Settings** tab
   - Set your Work Root and KB Root paths
   - Configure OpenClaw router options

6. **Monitor Jobs**
   - Check **Jobs** tab for active translation jobs
   - Use **Verify** tab to review completed translations

### For Developers

```bash
# Development mode with hot reload
pnpm tauri dev

# Build release version
pnpm tauri build

# Run linting
pnpm lint
```

---

## Configuration

### Environment Variables

Create a `.env.v4.local` file in the translation project root:

```env
# Required paths
V4_WORK_ROOT="/path/to/Translation Task"
V4_KB_ROOT="/path/to/Knowledge Repository"

# OpenClaw settings
OPENCLAW_STRICT_ROUTER=0
OPENCLAW_REQUIRE_NEW=0
OPENCLAW_RAG_BACKEND=local

# Telegram configuration
TELEGRAM_BOT_TOKEN="your-bot-token"
TELEGRAM_DIRECT_MODE=1
```

### Application Settings

Settings are stored persistently and can be modified through the Settings UI:

| Setting | Description | Default |
|---------|-------------|---------|
| Work Root | Base directory for translation tasks | Required |
| KB Root | Knowledge base directory | Required |
| Strict Router | Enable strict message routing | false |
| Require New | Require explicit "new" command | false |
| RAG Backend | Retrieval backend (local/clawrag) | local |

---

## Architecture

### Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 19, TypeScript, Tailwind CSS 4 |
| State Management | Zustand |
| Animations | Framer Motion |
| Desktop Framework | Tauri 2 |
| Backend | Rust |
| Database | SQLite (via rusqlite) |
| IPC | Tauri Commands |

### Directory Structure

```
tauri-app/
├── src/                          # Frontend source
│   ├── components/
│   │   ├── layout/
│   │   │   └── Sidebar.tsx       # Navigation sidebar
│   │   └── ui/
│   │       ├── badge.tsx         # Status badges
│   │       ├── button.tsx        # Button components
│   │       ├── card.tsx          # Card containers
│   │       ├── motion.tsx        # Animation components
│   │       └── toast.tsx         # Toast notifications
│   ├── lib/
│   │   ├── tauri.ts              # Tauri API bindings
│   │   └── utils.ts              # Utility functions
│   ├── pages/
│   │   ├── Dashboard.tsx         # Main dashboard
│   │   ├── Services.tsx          # Service management
│   │   ├── Jobs.tsx              # Job listing
│   │   ├── Verify.tsx            # Artifact review
│   │   ├── Logs.tsx              # Log viewer
│   │   ├── KBHealth.tsx          # KB status
│   │   └── Settings.tsx          # Configuration
│   ├── stores/
│   │   └── appStore.ts           # Global state (Zustand)
│   ├── App.tsx                   # Root component
│   ├── main.tsx                  # Entry point
│   └── index.css                 # Global styles
│
├── src-tauri/                    # Backend source (Rust)
│   ├── src/
│   │   ├── lib.rs                # Tauri commands & logic
│   │   └── main.rs               # Entry point
│   ├── Cargo.toml                # Rust dependencies
│   ├── tauri.conf.json           # Tauri configuration
│   └── icons/                    # Application icons
│
├── package.json
├── vite.config.ts
└── README.md
```

### IPC Commands

The frontend communicates with the Rust backend via Tauri commands:

| Command | Description |
|---------|-------------|
| `get_service_status` | Get status of Telegram Bot and Worker |
| `start_all_services` | Start all background services |
| `stop_all_services` | Stop all background services |
| `restart_all_services` | Restart all services |
| `run_preflight_check` | Run system requirement checks |
| `auto_fix_preflight` | Automatically fix common issues |
| `start_openclaw` | Start OpenClaw gateway |
| `get_config` | Read configuration from .env.v4.local |
| `save_config` | Persist configuration changes |
| `get_jobs` | List jobs with optional status filter |
| `get_job_milestones` | Get events for a specific job |
| `list_verify_artifacts` | List files in _VERIFY folder |
| `get_quality_report` | Get quality metrics for a job |
| `get_docker_status` | Check ClawRAG container status |
| `start_docker_services` | Start ClawRAG containers |
| `stop_docker_services` | Stop ClawRAG containers |
| `open_in_finder` | Open path in file manager |
| `read_log_file` | Read recent log lines |

### State Management

Application state is managed using Zustand with the following structure:

```typescript
interface AppState {
  // Data
  services: Service[];
  jobs: Job[];
  dockerContainers: DockerContainer[];
  preflightChecks: PreflightCheck[];
  config: AppConfig | null;

  // UI State
  activeTab: string;
  isLoading: boolean;
  isRefreshing: boolean;
  theme: 'light' | 'dark' | 'system';

  // Actions
  fetchServices: () => Promise<void>;
  fetchJobs: (status?: string) => Promise<void>;
  // ... more actions
}
```

### Theming

The application supports light and dark themes with automatic system detection:

- Theme preference stored in localStorage
- CSS variables for dynamic theming
- Custom glass-morphism effects
- Subtle glow indicators for status

---

## Development

### Prerequisites

- Node.js 18+
- pnpm 8+
- Rust 1.70+
- Platform-specific build tools

### Setup

```bash
# Install frontend dependencies
pnpm install

# Install Rust toolchain (if not installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Run in development mode
pnpm tauri dev
```

### Build Commands

```bash
# Development server
pnpm dev

# Build frontend only
pnpm build

# Build Tauri application
pnpm tauri build

# Build for specific target
pnpm tauri build --target aarch64-apple-darwin
```

### Code Style

- TypeScript with strict mode
- ESLint for linting
- Prettier for formatting
- Conventional commits

### Adding New Pages

1. Create component in `src/pages/NewPage.tsx`
2. Add to routing in `App.tsx`
3. Add navigation item in `Sidebar.tsx`
4. Add any required API bindings in `lib/tauri.ts`
5. Add Rust command in `src-tauri/src/lib.rs` if needed

---

## API Reference

### Tauri API (lib/tauri.ts)

```typescript
// Service Management
getServiceStatus(): Promise<ServiceStatus[]>
startAllServices(): Promise<ServiceStatus[]>
stopAllServices(): Promise<void>
restartAllServices(): Promise<ServiceStatus[]>

// Configuration
getConfig(): Promise<AppConfig>
saveConfig(config: AppConfig): Promise<void>

// Jobs
getJobs(status?: string, limit?: number): Promise<Job[]>
getJobMilestones(jobId: string): Promise<Milestone[]>

// Artifacts
listVerifyArtifacts(jobId: string): Promise<Artifact[]>
getQualityReport(jobId: string): Promise<QualityReport | null>

// Docker
getDockerStatus(): Promise<DockerContainer[]>
startDockerServices(): Promise<DockerContainer[]>
stopDockerServices(): Promise<void>

// Utility
openInFinder(path: string): Promise<void>
getVerifyFolderPath(): Promise<string>
readLogFile(service: string, lines: number): Promise<string[]>
```

---

## Troubleshooting

### Common Issues

**Services show "unknown" status**
- Ensure services have been started at least once
- Check PID files in `~/.openclaw/runtime/translation/pids/`

**Pre-flight checks fail**
- Run `Auto Fix All` to install missing dependencies
- Manually verify Python and venv installation

**Cannot open folders**
- Verify Work Root path in Settings
- Check if OneDrive sync is causing issues

**Dark mode not working**
- Click the theme icon in the sidebar footer to cycle themes
- Check system preferences for "system" theme mode

**Docker containers not found**
- Ensure Docker Desktop is running
- Verify ClawRAG containers exist: `docker ps -a | grep clawrag`

### Logs Location

- Application logs: `~/.openclaw/runtime/translation/logs/`
- Service PIDs: `~/.openclaw/runtime/translation/pids/`
- Database: `~/.openclaw/runtime/translation/state.sqlite`

---

## Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Write tests for new functionality
- Update documentation for API changes
- Follow existing code patterns
- Keep PRs focused and atomic

---

## License

This project is licensed under the MIT License.

```
MIT License

Copyright (c) 2024 Inifity Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Acknowledgments

- [Tauri](https://tauri.app/) - Build smaller, faster, and more secure desktop apps
- [React](https://react.dev/) - The library for web and native user interfaces
- [Tailwind CSS](https://tailwindcss.com/) - A utility-first CSS framework
- [Framer Motion](https://www.framer.com/motion/) - Production-ready motion library
- [Lucide](https://lucide.dev/) - Beautiful & consistent icons
- [OpenClaw](https://github.com/anthropics/openclaw) - CLI orchestration framework

---

**Made with care for the translation automation community.**
