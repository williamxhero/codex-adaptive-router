# Codex Adaptive Router

Codex Adaptive Router 1.3.0 是一个面向 Codex 的 Thin Root（薄 Root）调度插件。它把“模型能力、推理强度、执行方式、预期总 token”拆成独立决策：先守住质量门和能力下限，再在 Root 直接执行、子代理和可见任务之间选择完整路线 token 更合理的方案。

它不是一个“遇到关键词就选模型”的 first-match 路由器。Route Plan v3 会显式给出计划目标、dispatch readiness/blocker、递归深度、writer 所有权、handoff 合同和 planned token；Outcome Intelligence v4 则另外记录实际执行者与验收结果。

## 核心能力

- **Thin Root**：Root 保留意图、集成、验收和最终答复；复杂的证据、实现、研究与审计交给有明确边界的 specialist。
- **质量优先**：用户约束、安全、authority/capability floor、质量与隔离要求先于 token 比较。
- **Token-aware routing**：比较 Root 直接执行与“路由 + handoff + worker + 验证 + Root 验收”的预期总 token。只有 Root Sol Medium 足以满足质量、无需隔离/长期执行、也没有 worker-required stage，且节省量达到 Profile v4 门槛时，复杂路线才出现 direct 例外。
- **不静默兜底**：复杂任务需要 worker 而 worker 不可用时，计划返回 `worker_unavailable`；Root 不会悄悄接管。
- **递归调度**：Root 深度 0、子代理深度 1、孙代理深度 2。子代理可顺序多次冻结并重路由剩余工作，但深度 2 不得继续派发。
- **并发和写权**：同一父级默认一个 active specialist；明确独立且全部只读时最多并发 3 个；同一逻辑仓库整棵调度树只有一个 active writer lease。
- **可见任务**：长期、跨项目或明确要求上下文隔离时选择 `visible_task`。只有 Root 能创建；标题格式是 `[AR][MODEL-EFFORT] 简短目标`；成功且质量门通过后才可归档。
- **实际执行证据**：planned role/model/effort/target 与 observed provenance 分开记录，避免把计划当成事实。
- **Outcome Intelligence**：学习只使用边界、scope、verification 和 plan match 合格的实际执行路线；model 与 effort 严格单轴归因，多轴变化标记为 confounded。
- **隐私边界**：本地可保存有来源的 exact token；GitHub evolution batch 只允许 token band/aggregate、HMAC、UUID、计数和枚举字段。

## Model 不等于 Effort

模型决定能力边界，effort 决定合法模型获得多少推理预算。更高 effort 不能让 Luna 获得 Sol 的研究/决策权，也不能让 Terra 独立解决未冻结语义。

| 模型 | 默认职责 | 能力边界 |
| --- | --- | --- |
| Luna | 搜索、数据脉络、日志、已定义测试/扫描/指标 | evidence only |
| Terra | 已冻结规格的复杂实现 | implementation only |
| Sol | 研究、诊断、架构、统计判断、审计、最终验收 | decision / audit |

| 角色 | 默认模型与 effort | 权限 |
| --- | --- | --- |
| `router_code_mapper` | Luna Medium | 只读证据 |
| `router_experiment_runner` | Luna Medium | 只读实验 |
| `router_research_engineer` | Terra High | 冻结规格 writer |
| `router_researcher` | Sol High | 研究与因果判断 |
| `router_quant_researcher` | Sol High | 量化归因与统计结论 |
| `router_architect` | Sol High | 架构、时序、会计和市场语义 |
| `router_adversarial_auditor` | Sol XHigh | 对抗审计 |
| `router_strategy_scout` | Sol XHigh | 开放探索 |

Max 和 Ultra 从不自动出现。它们只来自用户明确约束或已经过人工确认的 policy override。

## 三种执行目标

### `direct`

适合 tiny/bounded、可逆、Root Sol Medium 能完整保证质量的工作，也适用于显著节省完整路线 token 的受限 direct 例外。

### `subagent`

复杂任务的默认方式。每个 delegated stage 在执行前必须 claim lease，并携带严格 agent package：bounded objective、role/model/effort、authority、读写边界、deliverable、verification、freeze/escalation 和 handback 合同。

### `visible_task`

只用于长期、跨项目或上下文隔离任务。高 token 本身不是创建可见任务的理由。Root 负责创建和最终归档；失败、blocked、provisional 或审计未通过的任务保持可见。

## Route Plan v3 与递归 lease

Route Plan v3 是不可变计划。它包含 Profile v4 版本、stage identity/attempt、execution target/mode、depth、access mode、dispatch blocker、writer ownership、handoff contract 和完整路线 token estimate。

运行中的变化进入 lease/observed state，而不是改写计划：

```text
Root (depth 0)
  -> child lease (depth 1)
       -> grandchild lease (depth 2)
       -> freeze mismatch
       -> reroute remaining work with a new plan/lease
  -> Root verification and acceptance
```

同一父级的独立只读并发必须在 Route Plan 中显式声明 2–3 个槽位，并在 claim 时为每项提供不同的 independence key；普通只读 stage 默认串行。writer exclusivity 使用仓库 HMAC，而不是保存原始路径。lease 不使用墙钟自动 TTL，避免长任务、睡眠或恢复导致 writer 被错误回收。

## 量化研究示例

高不确定量化归因不会由 Luna/Terra 独立下结论：

```text
router_quant_researcher / Sol High：冻结研究问题与判断标准
  -> router_code_mapper 或 router_experiment_runner / Luna Medium：证据与指标
  -> router_research_engineer / Terra High：仅在协议已冻结时实现
  -> router_quant_researcher / Sol High：归因、统计判断与结论
  -> router_adversarial_auditor / Sol XHigh：高影响、冲突或异常优秀结果审计
```

交易时序、fill、T+1、停牌、涨跌停、连续合约、保证金和会计语义仍交给 `router_architect`。

运行中发现 exceptional-positive 结果时，Router 会追加一个不改写原 Route Plan 的可 claim Sol XHigh audit follow-up；审计 outcome 必须绑定已完成、实际观测且全门通过的 audit lease。

## Outcome Intelligence v4

本地 outcome 记录：

- dispatch target 与 delegation depth；
- observed role/model/effort/target 及 plan match；
- boundary、scope、verification、archive 状态；
- stage lease HMAC 和状态；
- model/effort/context/tool-data/execution failure axis；
- 有稳定来源时的 exact input/output/total token。

当前稳定 Hook 表面并不保证每个子代理都提供 effort 和 token usage。插件不会解析不稳定 transcript 伪造 exact token：有 Codex/provider usage 或精确调用方报告就记录实际值，没有就保留 unknown。估算值只属于 Route Plan，永远不写进 actual token 字段。

学习只使用 observed route 的唯一 primary stage。模型证据必须固定 role、effort、execution target、depth、stage 与 task class；effort 证据必须固定 role、model、execution target、depth、stage 与 task class。context/tool data 缺失、worker unavailable、lease conflict、scope/boundary 失败、plan deviation 未解释或多轴变化都不会产生自动建议，也不能计入可比较 shadow 结果。

Policy 不能自动晋升。候选仍需重复的客观证据、跨 session 支持、非强制 shadow evaluation，以及用户明确确认。

## 隐私与 GitHub evolution

本地身份使用私有 salt + HMAC。同步到 GitHub 前会做显式 v4 projection，拒绝：

- raw prompt、路径、代码、日志；
- tool input/output、assistant message、transcript；
- 标题、objective、credentials、secrets；
- exact local token 和 planned token 数字。

公开批次只保留 token band/aggregate、HMAC、枚举、UUID、sequence、计数和时间。v1-v3 evidence、旧 batches/manifests/metrics 只读兼容，不重写既有 CRLF/LF 字节或 hash chain。新文本和演进产物显式使用 LF。

## 安装

插件安装后，如需把 namespaced custom agents 安装到用户级 Codex 配置：

```powershell
python scripts\install_user_layer.py
```

如需同时把新 Root 默认设为 Sol Medium：

```powershell
python scripts\install_user_layer.py --set-root-model
```

安装器先做时间戳备份，只管理 `router_*.toml` 和自己的配置片段。发布流程使用 Codex plugin cachebuster 重新安装 personal 插件，不直接修改 marketplace。

## 验证

```powershell
python -m pip install jsonschema ruff
python -m unittest discover -s tests -v
python -m ruff check .
python scripts\validate_router.py
python scripts\validate_evolution.py
python scripts\validate_agent_packages.py
git diff --check
```

CI 同时执行 unittest、Ruff、router/evolution validator、agent package contract、lifecycle smoke 和 diff-check。

## 目录

```text
.codex-plugin/plugin.json       插件 1.3.0 manifest
profiles/                       Profile v4 generic / quant
templates/agents/               八个 namespaced specialist package
scripts/router_core.py          planner、dispatch/lease、Outcome Intelligence
scripts/router_mcp.py           Route Plan 与 stage lifecycle MCP
evolution-data/schemas/         public Evidence v2/v3/v4 schema
tests/                          legacy、Windows、privacy、递归与 token 回归
docs/adr/                       能力预算与 Thin Root 决策记录
```

## 当前限制

- 普通 Hook 不能热切换已运行主线程的模型；Root 的全局默认仍由用户层配置决定。
- caller depth/Root-only 是调度不变量与审计边界，不是独立的安全认证系统。
- hook-only 生命周期可能观察不到 effort 或 exact token；这时 provenance 明确为 unknown。
- visible task 的创建/归档由 Codex App 工具执行，Router 负责规划、标题、eligibility 与审计合同。
- Max/Ultra 不自动选择；策略变更始终需要人工确认。
