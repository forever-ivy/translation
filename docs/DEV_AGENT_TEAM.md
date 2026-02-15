# 多 Agent 开发协作（本项目）

本项目已在 `.claude/settings.local.json` 配置了 Agent Teams（适配 Claude Code / 多代理协作模式）。下面是每个 agent 的职责边界与推荐用法，目标是让你把一个需求拆成可并行的子任务，并快速收敛到可合并的改动。

## Agent 列表（按用途）

### `pipeline-infra`（入口/路由/状态/DB/KB）
- 适合：消息入口、命令协议、SQLite 状态机、KB sync/retrieve、OpenClaw dispatcher/cron。
- 常见任务：修复 ingest/router、交互 pending_action、归档流程、RAG 同步一致性、runtime 健康检查。

### `translator`（翻译编排/LLM 调用）
- 适合：orchestrator 回合逻辑、prompt 结构、candidate 选择与评分、intent 分类。
- 常见任务：提高质量/收敛、改 prompt、增加/调整模型调用、改 round 策略。

### `format-specialist`（DOCX/XLSX 保真）
- 适合：结构抽取、translation map、写回策略、格式 QA 指标。
- 常见任务：修复表格/编号/样式丢失、合并单元格、行高换行、跨 sheet 处理。

### `qa-engineer`（质量门禁/视觉 QA）
- 适合：质量阈值、markdown 泄漏检测、vision QA、retry 策略、质量报告结构。
- 常见任务：门禁误判/漏判、质量指标改进、增加回归用例。

### `tester`（测试工程）
- 适合：新增/修复单测、回归覆盖、mock 外部依赖、把“线上复现步骤”固化成测试。
- 常见任务：为 bugfix 补测试；为新功能写最小覆盖；维护测试稳定性。

### `kb-memory`（知识库/记忆/归档）
- 适合：Knowledge Repository 结构、reference 归档、company isolation、openclaw-mem 写入/召回、ClawRAG 同步。
- 常见任务：KB 目录迁移、同步策略、检索质量、归档 manifest、记忆抽取规则。

### `security-auditor`（安全审计）
- 适合：附件/下载/base64/类型白名单/大小限制、路径穿越、敏感信息泄露、权限边界。
- 常见任务：对 ingest/router/发送通道做 threat model；提出并落地 hardening 改动。

### `docs-maintainer`（文档维护）
- 适合：README、docs 报告、命令/流程说明、env 模板一致性、对外可读性。
- 常见任务：把实现变更同步到文档；沉淀操作手册/故障排查。

## 推荐协作流程（可直接复制当作你的工作流）

1) 你先用一句话写清楚：目标 + 成功标准 + 失败示例（复现步骤）。
2) 交给 `pipeline-infra` 复现/定位入口与状态机影响面。
3) 并行分工：
   - 需要改 prompt/回合：拉 `translator`
   - 需要改保真写回：拉 `format-specialist`
   - 需要加门禁/视觉：拉 `qa-engineer`
   - 需要补测试：拉 `tester`
   - 涉及 KB/归档/记忆：拉 `kb-memory`
   - 涉及输入安全/限制：拉 `security-auditor`
   - 需要写说明：拉 `docs-maintainer`
4) 最后由你（或 coordinator）做合并决策：只接受“有测试/可验证/可解释”的改动。

## 交接模板（让 agent 输出更可用）

让被分配的 agent 用这个结构返回（强制决策完备）：

- **结论**：一句话说明 root cause / 方案
- **改动点**：列出将改的文件与关键函数
- **边界/回归**：可能破坏的行为、如何避免
- **验证**：最小测试/命令、预期输出

