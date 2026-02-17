# Translation Automation System

**Version 1.0.0**

An intelligent Arabic-to-English translation automation system powered by OpenClaw orchestration, featuring multi-round AI review, knowledge base integration, and a modern desktop dashboard.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![OpenClaw](https://img.shields.io/badge/openclaw-required-orange.svg)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [System Requirements](#system-requirements)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage Guide](#usage-guide)
- [Desktop Dashboard](#desktop-dashboard)
- [Architecture](#architecture)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

---

## Overview

This system automates the Arabic-to-English translation workflow with the following capabilities:

- **Intelligent Orchestration**: OpenClaw-based pipeline management
- **Multi-Round AI Review**: Codex (GPT-5.2) + Gemini review loop for quality assurance
- **Knowledge Base Integration**: RAG-powered terminology and context retrieval
- **Multiple Input Channels**: Telegram bot and email polling
- **Human-in-the-Loop**: Manual verification before final delivery
- **Desktop Dashboard**: Modern GUI for monitoring and control

---

## Features

### Core Pipeline
- 3-round translation + review cycle with configurable reasoning levels
- Automatic terminology extraction and consistency checking
- Structure fidelity and format preservation
- Quality scoring and validation gates

### Input Channels
- **Telegram Bot**: Real-time command interface with file attachments
- **Email Polling**: IMAP-based attachment processing

### Knowledge Management
- Document indexing with ClawRAG vector store
- Glossary and terminology management
- Reference document retrieval
- Company-isolated knowledge bases

### Output Management
- Verify folder for human review
- Quality reports with metrics
- Change logs and delta summaries
- No automatic delivery (manual control)

---

## System Requirements

### Runtime
- **Python**: 3.8 or later
- **OpenClaw**: Latest version with required skills
- **Docker** (optional): For ClawRAG containers

### Supported Platforms
- macOS 10.15+ (primary)
- Windows 10+ (community support)
- Linux Ubuntu 18.04+ (community support)

### Optional Dependencies
- **LibreOffice**: Document format conversion
- **Tauri**: For desktop dashboard application

---

## Quick Start

### 1. Install Dependencies

```bash
# Clone the repository
git clone <repository-url>
cd translation

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 2. Setup OpenClaw

```bash
# Run setup script
./scripts/setup_openclaw_v4.sh

# Install translation skill
./scripts/install_openclaw_translation_skill.sh

# Start OpenClaw gateway
openclaw gateway --force
```

### 3. Configure Environment

Create `.env.v4.local` with your paths:

```env
V4_WORK_ROOT="/path/to/Translation Task"
V4_KB_ROOT="/path/to/Knowledge Repository"
TELEGRAM_BOT_TOKEN="your-bot-token"
```

### 4. Start Services

```bash
# Start Telegram bot
./scripts/run_telegram_bot.sh

# Start email poller (in another terminal)
./scripts/run_v4_email_poll.sh

# Start background worker
./scripts/run_v4_run_worker.sh
```

---

## Installation

### Full Installation

```bash
cd /path/to/translation

# Install Python dependencies
.venv/bin/pip install -r requirements.txt

# Make scripts executable
chmod +x scripts/*.sh

# Setup OpenClaw
./scripts/setup_openclaw_v4.sh
./scripts/install_openclaw_translation_skill.sh

# Verify installation
openclaw health --json
```

### Desktop Dashboard (Optional)

```bash
cd tauri-app

# Install Node.js dependencies
pnpm install

# Run in development mode
pnpm tauri dev

# Build production binary
pnpm tauri build
```

---

## Configuration

### Environment Variables

Create `.env.v4.local` in the project root:

```env
# === Required Paths ===
V4_WORK_ROOT="/path/to/Translation Task"
V4_KB_ROOT="/path/to/Knowledge Repository"
V4_PYTHON_BIN="/path/to/translation/.venv/bin/python"

# === OpenClaw Settings ===
OPENCLAW_STRICT_ROUTER=1
OPENCLAW_REQUIRE_NEW=1
OPENCLAW_RAG_BACKEND=clawrag
OPENCLAW_TRANSLATION_THINKING=high

# === Telegram Configuration ===
TELEGRAM_BOT_TOKEN="your-bot-token"
TELEGRAM_DIRECT_MODE=1
OPENCLAW_NOTIFY_TARGET="+1234567890"
OPENCLAW_NOTIFY_CHANNEL="telegram"

# === IMAP Configuration (Email Polling) ===
V4_IMAP_HOST="imap.example.com"
V4_IMAP_PORT=993
V4_IMAP_USER="your@email.com"
V4_IMAP_PASSWORD="your-imap-password"
V4_IMAP_MAILBOX="INBOX"
V4_IMAP_FROM_FILTER="sender@example.com"
V4_IMAP_MAX_MESSAGES=5

# === RAG Backend ===
OPENCLAW_RAG_BASE_URL=http://127.0.0.1:8080
OPENCLAW_RAG_COLLECTION=translation-kb
OPENCLAW_RAG_COLLECTION_MODE=auto

# === Knowledge Base ===
OPENCLAW_KB_ISOLATION_MODE=company_strict
OPENCLAW_KB_RERANK_FINAL_K=12

# === State Management ===
OPENCLAW_STATE_DB_PATH=/path/to/.openclaw/runtime/translation/state.sqlite
```

### Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENCLAW_STRICT_ROUTER` | Enable strict command parsing | `1` |
| `OPENCLAW_REQUIRE_NEW` | Require `new` before `run` | `1` |
| `OPENCLAW_TRANSLATION_THINKING` | AI reasoning level (off/minimal/low/medium/high) | `high` |
| `OPENCLAW_RAG_BACKEND` | RAG backend (local/clawrag) | `clawrag` |
| `OPENCLAW_KB_ISOLATION_MODE` | KB access isolation | `company_strict` |

---

## Usage Guide

### Command Protocol

The system uses a strict command protocol for task management:

| Command | Description |
|---------|-------------|
| `new` | Create a new job for current sender |
| `run` | Start translation pipeline |
| `status` | Get current job status |
| `ok` | Mark job as verified |
| `no {reason}` | Mark job as needs revision |
| `rerun` | Re-execute current job |
| `cancel` | Cancel current job |

### Typical Workflow

1. **Create Job**
   ```
   Send: new
   ```

2. **Upload Content**
   - Attach files (PDF, DOCX, XLSX)
   - Or send task description text

3. **Execute Pipeline**
   ```
   Send: run
   ```
   - System will ask for company selection
   - Reply with company number

4. **Monitor Progress**
   - Receive milestone notifications
   - Check status with `status` command

5. **Verify Output**
   - Review files in `_VERIFY/{job_id}`
   - Check quality report

6. **Confirm Delivery**
   ```
   Send: ok
   ```
   - Optionally upload your FINAL file for KB archiving

### Message Flow Diagram

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Telegram  │     │    Email    │     │   Dashboard │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                    ┌──────▼──────┐
                    │  OpenClaw   │
                    │   Gateway   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
       ┌──────▼──────┐ ┌───▼───┐ ┌──────▼──────┐
       │ Translation │ │  KB   │ │   Quality   │
       │  Pipeline   │ │ Sync  │ │    Gate     │
       └──────┬──────┘ └───────┘ └──────┬──────┘
              │                         │
              └────────────┬────────────┘
                           │
                    ┌──────▼──────┐
                    │   _VERIFY   │
                    │  (Human)    │
                    └─────────────┘
```

---

## Desktop Dashboard

The project includes a modern desktop application for system management.

### Features
- **Dashboard**: Real-time service status and quick actions
- **Services**: Pre-flight checks and service control
- **Jobs**: Job listing with milestones and artifacts
- **Verify**: Review-ready jobs with quality metrics
- **Logs**: Real-time log viewer
- **KB Health**: Knowledge base status
- **Settings**: Configuration management

### Installation

```bash
cd tauri-app
pnpm install
pnpm tauri build
```

### Usage

Launch the application and use the sidebar to navigate:

1. **Dashboard** - Overview of system health
2. **Services** - Run pre-flight checks, start/stop services
3. **Jobs** - Monitor translation jobs
4. **Verify** - Review completed translations
5. **Logs** - View service logs
6. **Settings** - Configure paths and options

---

## Architecture

### System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           TRANSLATION AUTOMATION SYSTEM                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   Telegram   │  │    Email     │  │   Dashboard  │  │     CLI      │         │
│  │     Bot      │  │   Poller     │  │   (Tauri)    │  │   Commands   │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                 │                  │                 │                │
│         └─────────────────┴──────────────────┴─────────────────┘                │
│                                    │                                             │
│  ┌─────────────────────────────────▼─────────────────────────────────┐          │
│  │                        MESSAGE ROUTER                              │          │
│  │            (Command Parsing + File Extraction)                     │          │
│  └─────────────────────────────────┬─────────────────────────────────┘          │
│                                    │                                             │
│  ┌─────────────────────────────────▼─────────────────────────────────┐          │
│  │                       OPENCLAW GATEWAY                             │          │
│  │                     (Orchestration Layer)                          │          │
│  └─────────────────────────────────┬─────────────────────────────────┘          │
│                                    │                                             │
│         ┌──────────────────────────┼──────────────────────────┐                 │
│         │                          │                          │                 │
│  ┌──────▼──────┐  ┌────────────────▼────────────────┐  ┌──────▼──────┐         │
│  │    SKILLS   │  │          PIPELINE                │  │     KB      │         │
│  │  - Router   │  │  ┌─────────┐  ┌─────────────┐   │  │   SYSTEM    │         │
│  │  - Memory   │  │  │ Intent  │→ │ Translation │   │  │             │         │
│  │  - Himalaya │  │  │ Class.  │  │   Engine    │   │  │ ┌─────────┐ │         │
│  │  - PDF      │  │  └─────────┘  └──────┬──────┘   │  │ │ClawRAG  │ │         │
│  │  - Docx     │  │                      │          │  │ │ + RAG   │ │         │
│  └─────────────┘  │  ┌───────────────────▼────────┐ │  │ └─────────┘ │         │
│                   │  │      QUALITY GATE           │ │  └──────┬──────┘         │
│                   │  │  (Terminology + Structure)  │ │         │                │
│                   │  └───────────────────┬────────┘ │         │                │
│                   └──────────────────────┼──────────┘         │                │
│                                          │                    │                │
│  ┌───────────────────────────────────────▼────────────────────▼───────────┐   │
│  │                          ARTIFACT WRITER                                │   │
│  │                    (_VERIFY/{job_id} + DB Update)                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                          STATE LAYER                                     │   │
│  │    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                │   │
│  │    │   SQLite    │    │    Logs     │    │    PIDs     │                │   │
│  │    │  (State DB) │    │  (Runtime)  │    │  (Services) │                │   │
│  │    └─────────────┘    └─────────────┘    └─────────────┘                │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Component | Technology | Purpose |
|-------|-----------|------------|---------|
| **Presentation** | Desktop App | Tauri 2 + React 19 | GUI Dashboard |
| **Presentation** | Bot Interface | python-telegram-bot | Telegram commands |
| **Orchestration** | Gateway | OpenClaw | Pipeline coordination |
| **AI/ML** | Translation | GPT-5.2 (Codex) | Primary translation |
| **AI/ML** | Review | Gemini | Quality review |
| **AI/ML** | RAG | ClawRAG + ChromaDB | Knowledge retrieval |
| **Data** | Database | SQLite | State persistence |
| **Integration** | Email | IMAP + himalaya | Email polling |
| **Integration** | Files | LibreOffice | Document conversion |

### Component Interaction Diagram

```
                    ┌─────────────────────────────────────────┐
                    │              EXTERNAL INPUT              │
                    │  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
                    │  │Telegram │  │  Email  │  │  Files  │  │
                    │  └────┬────┘  └────┬────┘  └────┬────┘  │
                    └───────┼────────────┼────────────┼───────┘
                            │            │            │
                            ▼            ▼            ▼
┌───────────────────────────────────────────────────────────────────────┐
│                         INPUT PROCESSING                               │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐   │
│  │  Message Parser │───▶│ Command Router  │───▶│  File Handler   │   │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘   │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
            ▼                   ▼                   ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│   JOB CREATION    │ │  COMMAND HANDLER  │ │  FILE ATTACHMENT  │
│  - Generate ID    │ │  - new/run/ok/no  │ │  - Store in inbox │
│  - Set status     │ │  - status/rerun   │ │  - Parse content  │
│  - Link sender    │ │  - cancel         │ │  - Extract text   │
└─────────┬─────────┘ └─────────┬─────────┘ └─────────┬─────────┘
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│                       TRANSLATION PIPELINE                             │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  PHASE 1: PREPARATION                                            │  │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │  │
│  │  │   KB Sync    │───▶│ KB Retrieve  │───▶│   Context    │       │  │
│  │  │ (Index docs) │    │  (RAG query) │    │   Assembly   │       │  │
│  │  └──────────────┘    └──────────────┘    └──────────────┘       │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                │                                       │
│                                ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  PHASE 2: TRANSLATION                                            │  │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │  │
│  │  │  Intent      │    │   Round 1    │    │   Review 1   │       │  │
│  │  │ Classification│───▶│  (Codex)    │───▶│   (Gemini)   │       │  │
│  │  └──────────────┘    └──────────────┘    └──────────────┘       │  │
│  │                              │                   │               │  │
│  │                              ▼                   ▼               │  │
│  │                       ┌──────────────┐    ┌──────────────┐       │  │
│  │                       │   Round 2    │───▶│   Review 2   │       │  │
│  │                       │  (if needed) │    │  (if needed) │       │  │
│  │                       └──────────────┘    └──────────────┘       │  │
│  │                              │                   │               │  │
│  │                              ▼                   ▼               │  │
│  │                       ┌──────────────┐    ┌──────────────┐       │  │
│  │                       │   Round 3    │───▶│   Final      │       │  │
│  │                       │  (max 3)     │    │   Candidate  │       │  │
│  │                       └──────────────┘    └──────────────┘       │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                │                                       │
│                                ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  PHASE 3: VALIDATION                                             │  │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │  │
│  │  │   Quality    │───▶│  Structure   │───▶│  Purity      │       │  │
│  │  │    Gate      │    │   Fidelity   │    │    Check     │       │  │
│  │  └──────────────┘    └──────────────┘    └──────────────┘       │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                │                                       │
└────────────────────────────────┼───────────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│                          OUTPUT GENERATION                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐             │
│  │   Final      │    │   Review     │    │   Quality    │             │
│  │   .docx      │    │   Brief      │    │   Report     │             │
│  └──────────────┘    └──────────────┘    └──────────────┘             │
│                                                                        │
│                    Output: _VERIFY/{job_id}/                           │
└───────────────────────────────────────────────────────────────────────┘
```

### Data Model Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA MODEL                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────┐         ┌─────────────────────┐            │
│  │        JOBS         │         │       EVENTS        │            │
│  ├─────────────────────┤         ├─────────────────────┤            │
│  │ job_id (PK)         │────────▶│ job_id (FK)         │            │
│  │ sender              │  1:N    │ event_type          │            │
│  │ status              │         │ timestamp           │            │
│  │ task_type           │         │ payload_json        │            │
│  │ company             │         └─────────────────────┘            │
│  │ created_at          │                                            │
│  │ updated_at          │         ┌─────────────────────┐            │
│  └─────────────────────┘         │     JOB_FILES       │            │
│           │                      ├─────────────────────┤            │
│           │ 1:N                  │ job_id (FK)         │            │
│           ▼                      │ file_path           │            │
│  ┌─────────────────────┐         │ file_type           │            │
│  │    JOB_ARTIFACTS    │         │ created_at          │            │
│  ├─────────────────────┤         └─────────────────────┘            │
│  │ job_id (FK)         │                                            │
│  │ artifact_type       │         ┌─────────────────────┐            │
│  │ file_path           │         │     KB_FILES        │            │
│  │ size                │         ├─────────────────────┤            │
│  └─────────────────────┘         │ path (PK)           │            │
│                                  │ section             │            │
│  ┌─────────────────────┐         │ company             │            │
│  │   SENDER_ACTIVE     │         │ file_type           │            │
│  ├─────────────────────┤         │ last_indexed        │            │
│  │ sender (PK)         │         └─────────────────────┘            │
│  │ active_job_id       │                                            │
│  └─────────────────────┘         ┌─────────────────────┐            │
│                                  │    KB_CHUNKS        │            │
│                                  ├─────────────────────┤            │
│                                  │ id (PK)             │            │
│                                  │ kb_file_path (FK)   │            │
│                                  │ chunk_index         │            │
│                                  │ content             │            │
│                                  │ embedding_status    │            │
│                                  └─────────────────────┘            │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    ENTITY RELATIONSHIPS                      │    │
│  │                                                              │    │
│  │  JOB ────1:N────▶ EVENT         (milestones, notifications)  │    │
│  │  JOB ────1:N────▶ JOB_FILE      (input attachments)          │    │
│  │  JOB ────1:N────▶ JOB_ARTIFACT  (output files)               │    │
│  │  SENDER ──1:1───▶ JOB           (active job tracking)        │    │
│  │  KB_FILE ─1:N──▶ KB_CHUNK       (vector index chunks)        │    │
│  │                                                              │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Deployment Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DEPLOYMENT ARCHITECTURE                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                        USER'S MACHINE                                 │    │
│  │                                                                       │    │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐  │    │
│  │  │   Inifity.app   │    │  OpenClaw CLI   │    │   Python venv   │  │    │
│  │  │   (Tauri App)   │    │   (Gateway)     │    │   (Pipeline)    │  │    │
│  │  │                 │    │                 │    │                 │  │    │
│  │  │  - React UI     │    │  - REST API     │    │  - Scripts      │  │    │
│  │  │  - Rust Backend │    │  - Skills       │    │  - Libraries    │  │    │
│  │  │  - SQLite       │    │  - Cron Jobs    │    │  - Dependencies │  │    │
│  │  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘  │    │
│  │           │                      │                      │           │    │
│  │           └──────────────────────┼──────────────────────┘           │    │
│  │                                  │                                   │    │
│  │                    ┌─────────────▼─────────────┐                    │    │
│  │                    │     Local Services        │                    │    │
│  │                    │  ┌─────────────────────┐  │                    │    │
│  │                    │  │   Telegram Bot      │  │                    │    │
│  │                    │  │   (Polling)         │  │                    │    │
│  │                    │  └─────────────────────┘  │                    │    │
│  │                    │  ┌─────────────────────┐  │                    │    │
│  │                    │  │   Email Poller      │  │                    │    │
│  │                    │  │   (IMAP)            │  │                    │    │
│  │                    │  └─────────────────────┘  │                    │    │
│  │                    │  ┌─────────────────────┐  │                    │    │
│  │                    │  │   Run Worker        │  │                    │    │
│  │                    │  │   (Background)      │  │                    │    │
│  │                    │  └─────────────────────┘  │                    │    │
│  │                    └───────────────────────────┘                    │    │
│  │                                                                       │    │
│  └───────────────────────────────────────────────────────────────────────┘    │
│                                    │                                          │
│                                    │ HTTP (localhost)                          │
│                                    ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │                        DOCKER CONTAINERS                                │   │
│  │                                                                         │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐        │   │
│  │  │  ClawRAG        │  │  ChromaDB       │  │  Ollama         │        │   │
│  │  │  Gateway        │  │  (Vector Store) │  │  (Embeddings)   │        │   │
│  │  │  :8080          │  │  :8000          │  │  :11434         │        │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘        │   │
│  │                                                                         │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │                        CLOUD SERVICES (External)                        │   │
│  │                                                                         │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐        │   │
│  │  │  OpenAI API     │  │  Google AI      │  │  Telegram       │        │   │
│  │  │  (GPT-5.2)      │  │  (Gemini)       │  │  Bot API        │        │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘        │   │
│  │                                                                         │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │                        FILE SYSTEM                                      │   │
│  │                                                                         │   │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │   │
│  │  │  OneDrive / iCloud / Local                                       │  │   │
│  │  │                                                                   │  │   │
│  │  │  Translation Task/                                               │  │   │
│  │  │  ├── _INBOX/           (Incoming files)                          │  │   │
│  │  │  │   ├── email/                                                  │  │   │
│  │  │  │   └── telegram/                                               │  │   │
│  │  │  ├── _VERIFY/          (Output for review)                       │  │   │
│  │  │  │   └── {job_id}/                                               │  │   │
│  │  │  ├── _STAGING/         (In progress)                             │  │   │
│  │  │  └── _TRASH/           (Archived)                                │  │   │
│  │  │                                                                   │  │   │
│  │  │  Knowledge Repository/                                           │  │   │
│  │  │  ├── 10_Glossary/                                                │  │   │
│  │  │  ├── 20_Terminology/                                             │  │   │
│  │  │  ├── 30_Reference/                                               │  │   │
│  │  │  └── ...                                                          │  │   │
│  │  │                                                                   │  │   │
│  │  └─────────────────────────────────────────────────────────────────┘  │   │
│  │                                                                         │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Sequence Diagram: Job Execution Flow

```
┌────────┐     ┌────────┐     ┌────────┐     ┌────────┐     ┌────────┐     ┌────────┐
│  User  │     │  Bot   │     │ Router │     │Pipeline│     │  RAG   │     │   AI   │
└───┬────┘     └───┬────┘     └───┬────┘     └───┬────┘     └───┬────┘     └───┬────┘
    │              │              │              │              │              │
    │  "new"       │              │              │              │              │
    │─────────────▶│              │              │              │              │
    │              │  parse cmd   │              │              │              │
    │              │─────────────▶│              │              │              │
    │              │              │  create_job  │              │              │
    │              │              │─────────────▶│              │              │
    │              │              │              │───(DB)──────▶│              │
    │              │              │◀─────────────│              │              │
    │              │              │  job_id      │              │              │
    │              │◀─────────────│              │              │              │
    │  "Job created"│             │              │              │              │
    │◀─────────────│              │              │              │              │
    │              │              │              │              │              │
    │  [attach file]              │              │              │              │
    │─────────────▶│              │              │              │              │
    │              │  save file   │              │              │              │
    │              │─────────────▶│              │              │              │
    │              │              │  link to job │              │              │
    │              │              │─────────────▶│              │              │
    │              │              │              │───(DB)──────▶│              │
    │              │              │◀─────────────│              │              │
    │              │◀─────────────│              │              │              │
    │  "File received"            │              │              │              │
    │◀─────────────│              │              │              │              │
    │              │              │              │              │              │
    │  "run"       │              │              │              │              │
    │─────────────▶│              │              │              │              │
    │              │  parse cmd   │              │              │              │
    │              │─────────────▶│              │              │              │
    │              │              │  start_job   │              │              │
    │              │              │─────────────▶│              │              │
    │              │              │              │              │              │
    │              │              │              │  kb_sync     │              │
    │              │              │              │─────────────▶│              │
    │              │              │              │              │───(index)───▶│
    │              │              │              │◀─────────────│              │
    │              │              │              │              │              │
    │              │              │              │  kb_retrieve │              │
    │              │              │              │─────────────▶│              │
    │              │              │              │              │───(query)───▶│
    │              │              │              │◀─────────────│              │
    │              │              │              │  context     │              │
    │              │              │              │              │              │
    │              │              │              │  translate   │              │
    │              │              │              │─────────────────────────────▶│
    │              │              │              │              │              │
    │              │              │              │◀─────────────────────────────│
    │              │              │              │  result      │              │
    │              │              │              │              │              │
    │              │              │              │  quality_chk │              │
    │              │              │              │─────────────────────────────▶│
    │              │              │              │◀─────────────────────────────│
    │              │              │              │  quality_ok  │              │
    │              │              │              │              │              │
    │              │              │              │  write_artifacts            │
    │              │              │              │───(FS)──────▶│              │
    │              │              │              │              │              │
    │              │              │◀─────────────│              │              │
    │              │◀─────────────│  done        │              │              │
    │              │              │              │              │              │
    │  "Review ready in _VERIFY/{job_id}"       │              │              │
    │◀─────────────│              │              │              │              │
    │              │              │              │              │              │
    ▼              ▼              ▼              ▼              ▼              ▼
```

### State Machine: Job Lifecycle

```
                                    ┌─────────────────┐
                                    │                 │
                                    │    CREATED      │
                                    │   (new cmd)     │
                                    │                 │
                                    └────────┬────────┘
                                             │
                                             │ files/text attached
                                             ▼
┌─────────────────┐                ┌─────────────────┐
│                 │                │                 │
│    COLLECTING   │◀───────────────│   PENDING       │
│  (awaiting run) │   new cmd      │  (ready to run) │
│                 │                │                 │
└────────┬────────┘                └────────┬────────┘
         │                                  │
         │ run cmd                          │ run cmd
         │                                  │
         ▼                                  ▼
┌─────────────────┐                ┌─────────────────┐
│                 │                │                 │
│     RUNNING     │                │     QUEUED      │
│  (executing)    │◀───────────────│  (in worker)    │
│                 │                │                 │
└────────┬────────┘                └─────────────────┘
         │
         │ pipeline complete
         │
         ├──────────────────────────┐
         │                          │
         ▼                          ▼
┌─────────────────┐        ┌─────────────────┐
│                 │        │                 │
│  REVIEW_READY   │        │ NEEDS_ATTENTION │
│  (await verify) │        │  (error/issue)  │
│                 │        │                 │
└────────┬────────┘        └────────┬────────┘
         │                          │
         │ ok cmd                   │ fix + rerun
         │                          │
         ▼                          │
┌─────────────────┐                 │
│                 │                 │
│    VERIFIED     │◀────────────────┘
│   (complete)    │
│                 │
└─────────────────┘

Commands & State Transitions:
───────────────────────────────
  new      → CREATED (or resets to COLLECTING)
  [attach] → COLLECTING (if in CREATED)
  run      → QUEUED → RUNNING
  complete → REVIEW_READY or NEEDS_ATTENTION
  ok       → VERIFIED
  no       → NEEDS_REVISION
  rerun    → QUEUED
  cancel   → CANCELLED
```

### Directory Structure

```
translation/
├── .env.v4.local              # Local configuration
├── .venv/                     # Python virtual environment
├── requirements.txt           # Python dependencies
│
├── scripts/                   # Executable scripts
│   ├── start.sh               # Main service controller
│   ├── setup_openclaw_v4.sh   # OpenClaw setup
│   ├── run_telegram_bot.sh    # Telegram bot launcher
│   ├── run_v4_email_poll.sh   # Email poller
│   ├── run_v4_run_worker.sh   # Background worker
│   └── skill_*.py             # Skill implementations
│
├── skills/                    # OpenClaw skills
│   └── translation-router/
│       └── SKILL.md           # Router skill template
│
├── schemas/                   # JSON schemas
│   ├── job_envelope.json
│   ├── execution_plan.json
│   └── quality_report.json
│
├── tauri-app/                 # Desktop dashboard
│   ├── src/                   # React frontend
│   │   ├── components/
│   │   ├── pages/
│   │   ├── stores/
│   │   └── lib/
│   └── src-tauri/             # Rust backend
│       └── src/
│           └── lib.rs
│
├── tests/                     # Unit tests
│   └── test_*.py
│
└── docs/                      # Documentation
    ├── KB_AND_MEMORY_SYSTEM.md
    └── DEV_AGENT_TEAM.md
```

### Data Flow

```
Input (Telegram/Email)
        │
        ▼
┌───────────────────┐
│  Message Router   │ ─── Parse commands and files
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  Job Dispatcher   │ ─── Create/update jobs in SQLite
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│    KB Sync        │ ─── Index new documents to RAG
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│   KB Retrieve     │ ─── Get relevant context
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Intent Classifier │ ─── Classify task type
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Translation Loop  │ ─── Round 1 → 2 → 3 (if needed)
│ (Codex + Gemini)  │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  Quality Gate     │ ─── Validate output
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  Artifact Writer  │ ─── Write to _VERIFY/{job_id}
└───────────────────┘
```

### Produced Artifacts

Per job, under `_VERIFY/{job_id}/`:

| File | Description |
|------|-------------|
| `Final.docx` | Final translated document |
| `Final-Reflow.docx` | Reformatted version |
| `Review Brief.docx` | Review summary |
| `Change Log.md` | Change tracking |
| `Final.xlsx` | Spreadsheet output (if applicable) |

Internal (`.system/`):
- `execution_plan.json`
- `quality_report.json`
- `openclaw_result.json`
- `Delta Summary.json`
- `Model Scores.json`

---

## API Reference

### Dispatcher CLI

```bash
python -m scripts.openclaw_v4_dispatcher \
  --work-root "/path/to/Translation Task" \
  --kb-root "/path/to/Knowledge Repository" \
  --notify-target "+1234567890" \
  <command> [options]
```

#### Commands

| Command | Description |
|---------|-------------|
| `email-poll` | Poll IMAP for new messages |
| `message-event` | Process incoming message |
| `run-job` | Execute a specific job |
| `kb-sync` | Sync knowledge base |
| `pending-reminder` | Send reminder for pending jobs |
| `approval` | Handle command messages |

### Python API

```python
from scripts.v4_pipeline import create_job
from scripts.v4_kb import sync_kb_with_rag, retrieve_kb_with_fallback
from scripts.openclaw_translation_orchestrator import run as run_translation

# Create job
job_id = create_job(sender="+1234567890", work_root="/path/to/work")

# Sync KB
sync_kb_with_rag(kb_root="/path/to/kb", job_id=job_id)

# Run translation
result = run_translation(job_id=job_id, work_root="/path/to/work")
```

---

## Troubleshooting

### Common Issues

**OpenClaw not responding**
```bash
# Check gateway health
openclaw health --json

# Restart gateway
openclaw gateway --force
```

**Skill not found**
```bash
# Verify skill installation
openclaw skills list | grep translation-router

# Reinstall skill
./scripts/install_openclaw_translation_skill.sh
```

**RAG backend issues**
```bash
# Check ClawRAG health
python -m scripts.skill_clawrag_bridge --base-url "http://127.0.0.1:8080" health

# Check Docker containers
docker ps -a | grep clawrag
```

**Job stuck in queue**
```bash
# Check worker status
cat ~/.openclaw/runtime/translation/pids/worker.pid

# Check logs
tail -f ~/.openclaw/runtime/translation/logs/worker.log
```

### Log Locations

| Log | Path |
|-----|------|
| Telegram Bot | `~/.openclaw/runtime/translation/logs/telegram.log` |
| Worker | `~/.openclaw/runtime/translation/logs/worker.log` |
| OpenClaw | `~/.openclaw/logs/gateway.log` |

### Debug Mode

Enable verbose logging:
```env
OPENCLAW_LOG_LEVEL=DEBUG
```

---

## Development

### Running Tests

```bash
# Run all tests
.venv/bin/python -m unittest discover -s tests -q

# Run specific test
.venv/bin/python -m unittest tests.test_skill_message_router -v
```

### Code Style

- Python 3.8+ with type hints
- Follow PEP 8 conventions
- Use docstrings for public APIs

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

---

## License

MIT License

```
MIT License

Copyright (c) 2024 Translation Automation Contributors

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

- [OpenClaw](https://github.com/anthropics/openclaw) - CLI orchestration framework
- [ClawRAG](https://github.com/anthropics/clawrag) - RAG backend
- [Tauri](https://tauri.app/) - Desktop application framework
- [React](https://react.dev/) - UI framework

---

**Version 1.0.0** | Built with care for the translation community.
