# 知识库（Knowledge Repository）与记忆系统（openclaw-mem）运行报告

本文面向“维护/扩展这个项目的人”，解释知识库、检索、归档、以及跨任务记忆在当前代码中是如何工作的，并给出关键入口与约束点。

## 1. 总览：两套系统分别解决什么问题

### 1) Knowledge Repository（KB）
KB 是**显式、可审计、可回溯**的知识源文件集合（术语表/风格指南/领域知识/参考译文/模板）。它被同步到：

- **本地 SQLite 索引**（`kb_files` / `kb_chunks` / `kb_chunks_fts`），用于离线 BM25 检索；
- **ClawRAG**（可选），用于向量/语义检索（`OPENCLAW_RAG_BACKEND=clawrag`）。

KB 的目标：让每次 run 都能检索到“同客户历史译文、术语决策、风格约束”等，并把其作为模型上下文。

### 2) 记忆（company-scoped，存于本地 SQLite）
跨任务“决策/约束摘要”会写入本地 `state.sqlite` 的 `memories` 表，并且**按 company 严格隔离**（不会跨公司召回）。

记忆的目标：在“KB 文件检索未命中/命中不稳/任务描述很短”的情况下，仍能提供关键约束（且不会串客户）。

## 2. KB 目录结构与语义（目标格式）

KB root 默认是：

- `DEFAULT_KB_ROOT`：`scripts/v4_runtime.py`
- 运行时可通过 `--kb-root` 或环境变量使用不同路径（见 `scripts/v4_pipeline.py` 调用）。

目标结构（强约定：所有可检索文件必须在 `{Company}` 子目录下）：

```
Knowledge Repository/
  00_Glossary/{Company}/          # 术语表（公司隔离）
  10_Style_Guide/{Company}/       # 风格指南（公司隔离）
  20_Domain_Knowledge/{Company}/  # 领域知识（公司隔离）
  30_Reference/         # 参考译文（按客户/项目）
    {Company}/
      {Project}/
        final/          # 最终译文（会被索引/检索）
        source/         # 原文件（默认不索引）
  40_Templates/{Company}/         # 模板（公司隔离）
```

关键点：
- `30_Reference/**/final/**` 是“最重要的可复用资产”（强权重检索）。
- `30_Reference/**/source/**` 默认**不索引**，避免原文（尤其是 Arabic source）污染检索上下文。
- 对于 `00/10/20/40`：如果文件没有放在 `{Company}` 子目录中，会被视为 **unscoped** 并在 sync 时跳过（报告里会列出 `unscoped_skipped_paths`）。

## 3. KB 如何被“索引/同步”

### 3.1 本地索引（SQLite）
入口：`scripts/v4_kb.py:sync_kb()`。

流程：
1) `discover_kb_files(kb_root)` 遍历 KB 文件，过滤：
   - 跳过临时/隐藏文件（`~$`、`.` 开头）
   - 只允许 `KB_SUPPORTED_EXTENSIONS`
   - 跳过 `30_Reference/**/source/**`（默认不索引）
   - 跳过所有不符合 `{Section}/{Company}/...` 结构的文件（公司隔离）
2) 对每个文件：
   - 计算 `sha256`、读取/抽取文本、分 chunk（`kb_chunks`）
   - 写入 `kb_files`（带 `source_group`、chunk_count、indexed_at 等）
3) 建立/更新 FTS5（`kb_chunks_fts`）用于 BM25 检索。

### 3.2 ClawRAG 同步（可选）
入口：`scripts/v4_kb.py:sync_kb_with_rag()`。

关键行为：
- `changed_paths` 不只包括本轮“重建 chunks”的文件，还包括 `metadata_only_paths`（mtime/size 变化但 sha256 不变），避免 RAG 侧漏掉元数据更新。
- 同步会把本地 `removed_paths` 也传给 RAG 做 delete，避免向量库累积陈旧文档。
- 当 `OPENCLAW_RAG_COLLECTION_MODE=auto|per_company` 时，会按 **company 自动路由到不同 collection**（例如 `translation-kb-eventranz`），而不是把所有公司混入同一个向量库。

相关环境变量（RAG）：
- `OPENCLAW_RAG_COLLECTION`：collection base 名（默认 `translation-kb`）。支持占位符：`translation-kb-{company}`。
- `OPENCLAW_RAG_COLLECTION_MODE`：`auto`（默认）/ `shared` / `per_company`。
  - `auto`：当 `OPENCLAW_KB_ISOLATION_MODE=company_strict` 且 job 有 `kb_company` 时，自动启用 `per_company`。

## 4. KB 如何被“检索并注入到 run”

### 4.1 run 前置：sync + retrieve
入口：`scripts/v4_pipeline.py:run_job_pipeline()`。

核心步骤：
1) `sync_kb_with_rag(...)`：先保证 KB 索引/RAG 可用。
2) `retrieve_kb_with_fallback(...)`：
   - clawrag 先召回（默认 `top_k=20`；在 `per_company` 模式下仅查询该公司的 collection）
   - 本地 BM25/FTS 再召回（默认 `top_k=12`）
   - 合并去重后做统一重排（merge + rerank），而不是“远端命中直接覆盖本地”
   - 当 clawrag 不可用时自动回退为本地-only（`rag_fallback_local` flag）
3) `knowledge_context`（hits 列表）写入 `meta`，作为 `openclaw_translation_orchestrator.run()` 的输入上下文。

相关环境变量（rerank）：
- `OPENCLAW_KB_RERANK_FINAL_K`（默认 `12`）
- `OPENCLAW_KB_RERANK_GLOSSARY_MIN`（默认 `3`，命中 glossary 时保底条数）
- `OPENCLAW_KB_RERANK_TERMINOLOGY_GLOSSARY_RATIO`（默认 `0.4`，术语任务 glossary 占比）

### 4.2 公司级隔离（当前默认：`company_strict`）
通过 `OPENCLAW_KB_ISOLATION_MODE` 控制检索隔离策略（默认 `company_strict`），并依赖 `jobs.kb_company`：

- `company_strict`（默认）：所有 KB 命中都必须属于该公司目录（`{Section}/{Company}/...`），包含 `00/10/20/30/40` 全量隔离。
- `reference_only`：仅对 `30_Reference/` 做公司隔离（兼容旧行为；不推荐）。
- `all`：只允许 `30_Reference/{Company}/...`（最严格但会屏蔽 style/domain/templates）。

公司选择：
- `run`/`rerun`/`ok` 时若 `kb_company` 为空，会列出 KB 中已存在的公司目录（跨 `00/10/20/30/40` 的并集）作为菜单，用户数字选择后继续执行（见 `scripts/skill_approval.py`）。

## 5. “已完成任务如何归档到知识库”（参考译文归档）

原则：**人工确认 OK 后才归档**，且归档的是“用户上传的最终文件”，不是模型输出物。

### 5.1 FinalUploads 的来源
- 当 job 处于 post-run（`review_ready/needs_attention`）时，用户可以上传最终文件附件。
- 这些附件会被保存到：`_VERIFY/{job_id}/FinalUploads/`
- 并记录到 `job_interactions.final_uploads_json`。

### 5.2 ok 的归档动作
入口：`scripts/skill_approval.py` 的 `ok` 分支。

归档位置（硬约定）：
- `Knowledge Repository/30_Reference/{Company}/{Project}/final/`
- 同时写入 `reference_manifest.json`（包含 job_id、sha256、来源路径等）。

注意：
- 如果没有 final uploads，`ok` 只会把 job 标记为 `verified`（不会自动写入 reference）。
- 可通过 `OPENCLAW_ARCHIVE_REQUIRE_FINAL_UPLOAD=1` 强制要求先上传 final 才能 ok。

## 6. 记忆系统：把“真正决策”写入本地 SQLite（company-scoped）

入口：`scripts/v4_pipeline.py:_store_job_memory()`。

写入内容包含：
- Company / Task / Job / Type
- `Change Log.md` 中的 bullet points（作为“Decisions”）
- 本轮 KB 命中来源摘要（`KB sources`）
- 收敛轮数（Convergence）

写入方式：
- `scripts/v4_runtime.py:add_memory()` 写入 `memories` 表（company 隔离）。

召回方式（run 前）：
- `scripts/v4_runtime.py:search_memories()`（company 隔离 + FTS/BM25 优先）
- 召回结果作为 `cross_job_memories` 注入到 orchestrator 的 meta（见 `scripts/v4_pipeline.py`）。

## 7. status 设计的作用（任务状态机）

状态存放于 SQLite：`jobs.status`（见 `scripts/v4_runtime.py` schema）。

status 的作用：
1) 命令门禁：决定 `run/rerun/ok/no` 是否允许（`scripts/skill_approval.py`）。
2) 用户引导：`status` 输出 card，告诉你“下一步发什么命令”（`scripts/skill_status_card.py`）。
3) 交互安全：pending_action（公司选择/附件去向选择）通过 `job_interactions` 持久化，避免多人并行时丢上下文。

新版 status card 会额外展示：
- Pending action + expires_at
- Final uploads 数量
- Archived: yes/no

## 8. 常见失败模式与防护点

- 参考译文 `source/` 默认不索引：避免原文污染检索。
- ClawRAG 返回路径过滤：只允许 `kb_root` 下且文件存在的命中，避免 KB 重构后的陈旧路径命中。
- post-run 附件不再自动当作 FinalUploads：需要用户明确选择目的地，避免把“新任务附件”误归档到旧任务。
