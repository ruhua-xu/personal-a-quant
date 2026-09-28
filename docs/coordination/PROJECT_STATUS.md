# PROJECT_STATUS

维护者：编码角色。下方保留历次交付及初始化记录；历史待审状态不覆盖后续架构结论。

## 当前记录：2026-09-28 Phase 3A 实现交付

| 项目 | 状态 |
| --- | --- |
| 当前任务 | Phase 3A — Universe + ResearchConfig + ResearchRun |
| Status | `READY_FOR_ARCH_REVIEW`；仅离线模型与身份契约，等待架构审查 |
| Active branch | `feat/phase2d-data-versioning`；继续原分支，未修改 main |
| 实现起点 / 开始时 HEAD | `c8f18e4daa88955874596fd8a032fb3709fbc70c` |
| 前置验收 | Phase 2D-B 已由架构角色 ACCEPTED，见本地 ARCH_REVIEW_PHASE_2D_B；不等于正式研究已验收 |
| HEAD SHA（实现已推送、状态文档提交前观察） | `557455854efcf59fbb306ef2597b76c05a610731` |
| 本次实现 commit SHA | `557455854efcf59fbb306ef2597b76c05a610731` — `feat: add offline research contracts` |
| 状态文档提交 | 独立交接提交，不要求文档包含自身最终 SHA |
| push（状态文档提交前观察） | 实现 commit 已推送 `origin/feat/phase2d-data-versioning`，ls-remote 核对 SHA 一致；本状态文档随独立交接提交推送，最终交接 SHA 以实际 HEAD / 远端为准 |
| Blocker | 实现无阻塞；npm 缺失且仅现有 Node 24 可用，Node 20–22 支持环境的 build/lint 验证仍缺失 |
| 架构验收 | `NOT_REVIEWED`；本记录不是 ACCEPTED |
| 下一阶段 | Phase 3B 未开始；Phase 3 正式研究仍 blocked，等待 3A–3D 及真实输入质量审查 |

### 修改文件与交付范围

- `aquant/research/__init__.py`：导出离线研究契约，无启动副作用。
- `aquant/research/universe.py`：CN ETF 完整身份、成员规范化、定义/快照内容版本、UTC、严格 JSON 深拷贝和重核验。
- `aquant/research/models.py`：展开默认配置/精确权重/参数哈希、日期切分、跨对象验证、运行状态/审计元数据与最小输出包装。
- `tests/aquant/research/__init__.py`、`conftest.py`、`test_universe.py`、`test_models.py`、`test_boundaries.py`：合成元数据、身份/参数/状态/篡改/深拷贝测试，独立违规代码检查及无文件/网络访问检查；继承已有断网 fixture。
- `docs/architecture/research-contracts-phase3a.md`：固定 Schema 1 字段、已核对 golden hash、known_at 午夜边界、JSON 生命周期与本阶段限制。
- `docs/coordination/PROJECT_STATUS.md`：本次交接记录；与实现提交区分。

未修改 domain、SecurityScore、MarketRuleBook、data manifest/hash、snapshot/store、Provider、旧 OpenAshare、API、前端、SQLite 或依赖文件。未读行情、未创建真实 Universe/研究数据、未算因子/评分/指标、未运行 Runner；没有进入 3B，没有交易功能。

### 验证证据

环境：Python 3.12.2；Pydantic 2.13.5、pandas 3.0.5、PyArrow 25.0.1、DuckDB 1.5.5、pytest 9.1.1；Node 24.19.0。未安装或升级依赖。

| 检查 | 最终结果 |
| --- | --- |
| `python -m pytest tests/aquant/research -q` | 175 passed，0 failed |
| `python -m pytest tests/aquant -q` | 672 passed，0 failed（含已有 497 项） |
| `tests/test_api_app.py -q` | 64 passed，0 failed；进程内 pytest.main + 离线隔离 |
| `tests/test_us_market.py -q` | 9 passed，0 failed；独立进程 pytest.main + 离线隔离 |
| `npm run build` / `npm run lint` | 已尝试；npm 不在 PATH，未能通过 npm 启动 |
| `node node_modules/next/dist/bin/next build` | 通过；编译、TypeScript、21 页生成成功；Node 24 的补充证据，不是支持版本验证 |
| `node node_modules/eslint/bin/eslint.js app components lib next.config.ts` | 0 errors，10 warnings；同属 Node 24 补充验证 |
| 新增 warning | Python 无 warning；ESLint 10 条与既有基线同类且均在未修改的 app/components 文件；没有新 warning |
| Git 提示 | 新文件暂存时提示 LF 将转换为 CRLF，来自现有 Windows 设置；未修改 Git 配置，不是运行时 warning |
| `git diff --check` | 通过；提交前复核暂存范围与空白错误 |

API / 美股沿用进程内隔离：关闭默认行情预热，外部 socket/DNS 禁用，requests 抛离线 ConnectionError，只允许 Windows asyncio 所需 loopback；保留原测试 mock，未访问实时网站，未改旧测试或提交本机辅助配置。

首轮新测试校准了完整字段数、canonical_key 字典序及独立计算的固定摘要；无 I/O 测试预加载标准库时区资源后再封锁文件调用，不 mock 市场规则。最终全部通过。没有为通过回归修改既有业务逻辑。

### Review 重点与限制

- 三类身份都共享创建/加载/复核的规范化逻辑。固定摘要另以手工展开 JSON + 标准库 SHA 核对；未复用 Dataset 专属版本算法。
- 评分起点为 research_period.start_date 在 RuleBook 时区的午夜，prospective known_at 等于允许、晚 1 微秒拒绝。retrospective_manual 仍保留事后选池限制。
- 配置单独验证不代表完成跨对象验证，必须显式调用 `validate_research_inputs`；该入口也不核验实际 manifest、数据质量或证据文件。
- 模型顶层 frozen 并非深度不可变；JSON 输出前、加载时及显式 verify 会重核验。没有文件保存/实验发布功能。
- Git SHA、运行版本、许可/审查引用均为调用者声明；没有自动环境探测、内容读取或真实性背书。FAILED/未完成与成功分开，test 已开始即标消耗。
- FactorSnapshot / ResearchResult 仅最小 schema；缺分数/统计保留 None，promotion_level 最高 1，不自动形成研究成果或晋级结论。
- 当前 Node 超出项目 `>=20 <23` 范围；待在 Node 20–22 复核，不把本次补充构建写成支持环境验收。无关 10 条 ESLint warning 未修复。
- 既有 `AGENTS.md`、未跟踪 CURRENT_TASK、ETF 架构参考、2D-B 审查文件保持内容不变，未纳入本次提交；CURRENT_TASK 由架构角色维护，编码角色不改任务范围或状态。
- 完成推送后停止，仅交架构 Review，不自行进入 Phase 3B。

## 历史记录：2026-09-28 Phase 2D-B 实现交付

| 项目 | 状态 |
| --- | --- |
| 当前任务 | Phase 2D-B — Immutable Dataset Snapshot |
| Status | `READY_FOR_ARCH_REVIEW`；实现及 Python 验证完成，尚未架构验收 |
| Active branch | `feat/phase2d-data-versioning` |
| 架构/实现起点 | `d691393dde90ee0299966d13ac0069774354bfd9` |
| HEAD SHA（实现已推送、状态文档提交前观察） | `02d975f932bd050dd8f74b964c96ea365fd6154f` |
| 本次实现 commit SHA | `02d975f932bd050dd8f74b964c96ea365fd6154f` — `feat: add immutable dataset snapshots` |
| 状态文档提交 | 独立交接提交；不要求文档包含自身最终 SHA |
| push（状态文档提交前观察） | 实现 commit 已推送至 `origin/feat/phase2d-data-versioning` 并核对远端 SHA；本记录随独立交接提交推送，最终交接 SHA 以实际 Git HEAD/远端为准 |
| Blocker | 实现无阻塞；环境缺少 npm，frontend build/lint 未能执行，需 Review 知悉验证缺口 |
| 架构验收 | `NOT_REVIEWED` |
| 是否进入下一阶段 | 否；Phase 3 正式研究继续 blocked，等待架构验收和后续任务授权 |

### 本次修改文件与实现摘要

- `aquant/data/snapshots/__init__.py`：导出快照发布、读取、结果与异常契约。
- `aquant/data/snapshots/store.py`：staging 完整校验、按版本独占发布、Windows-safe 目录、已有版本幂等验证、文件与行情语义检查、已核验版本上下文。
- `tests/aquant/test_snapshots.py`：96 项离线测试，覆盖空数据、幂等、跨根目录、篡改、错误语义、发布失败/并发、实际 Windows junction/硬链接与 mutable store 隔离。
- `docs/architecture/immutable-dataset-snapshots.md`：记录首版接口、单文件布局、日期/空数据约定、错误及并发/持久性限制。
- `docs/coordination/PROJECT_STATUS.md`：本交接记录。

没有修改现有 Manifest Schema 1/hash 算法、MarketDataSet、domain、MarketRuleBook、mutable store、Provider、FastAPI、前端或 SQLite。没有执行真实数据下载，没有进入 Phase 3。

### 验证结果

环境：Python 3.12.2；pandas 3.0.5、PyArrow 25.0.1、DuckDB 1.5.5、Pydantic 2.13.5、pytest 9.1.1。使用已有虚拟环境，无新增依赖或版本升级。

| 检查 | 结果 |
| --- | --- |
| `python -m pytest tests/aquant/test_snapshots.py -q` | 96 passed，0 failed |
| `python -m pytest tests/aquant -q` | 497 passed，0 failed（含原有 401 项） |
| `tests/test_api_app.py -q` | 64 passed，0 failed；进程内 pytest.main + 临时离线隔离，见下文 |
| `tests/test_us_market.py -q` | 9 passed，0 failed；进程内 pytest.main + 临时离线隔离，见下文 |
| `npm run build` | 已尝试；PowerShell 找不到 npm，未进入项目构建 |
| `npm run lint` | 已尝试；PowerShell 找不到 npm，未进入 ESLint |
| 新增 warning | 本次 Python 测试输出无 warning；未运行构建/ESLint，不能判断其 warning |
| Git 提示 | 暂存新文件时提示 LF 将转换为 CRLF，来自现有 Windows 换行设置；未修改 Git 配置，不属于运行时 warning |
| 文档与 diff | 已检查变更范围及空白错误；提交前再次核对暂存文件 |

API / 美股原工程回归使用 pytest.main 运行对应原测试文件，并在进程内提供网络隔离：禁止外部 socket/HTTP，requests 抛离线 ConnectionError，关闭默认行情预热（WARMUP_STOCK_LIMIT=0）；API 测试仅保留 Windows asyncio 内部所需的 loopback socket。测试内已有 mock 继续生效。未修改旧测试或业务代码，未访问实时网络，未提交本机测试辅助配置。无关 baseline warning 未修改。

### Review 注意事项与保留项

- 当前快照格式 1 使用一个 bars.parquet，完整缓冲后验证并解码；不是分区/低内存或断电持久性方案。
- 并发发布通过每版本独占锁协调；冲突立即失败，遗留锁需人工核查，不自动接管或修复目标。禁止绕过 API 同时更改发布目录。
- 返回的 MarketDataSet/DataFrame 和 source.metadata 不是深度冻结对象；核验上下文只证明本次读取，磁盘独立保存，重读会再次验证。
- 缺少 npm 是前端验证环境限制，不通过修改项目绕过，也不宣称 build/lint 已通过。
- 既有未提交 `AGENTS.md`、未跟踪 `docs/coordination/CURRENT_TASK.md` 与 `docs/architecture/etf-research-lab.md` 原样保留，不纳入本次提交。CURRENT_TASK 保持 OPEN，由架构 / Review 角色维护后续状态。
- 状态为待审，不是验收通过；交付后停止，等待 Review。

## 历史记录：2026-09-28 协调状态更新

| 项目 | 状态 |
| --- | --- |
| 本次工作 | 按用户明确要求仅更新协调状态；当前任务仍为 Phase 2D-B — Immutable Dataset Snapshot |
| Status | `READY_FOR_IMPLEMENTATION`；未开始实现，不标记 `READY_FOR_ARCH_REVIEW` |
| Blocker | 已解除；任务未开放的协调阻塞已解除 |
| CURRENT_TASK 当前状态 | `OPEN` |
| Phase 2D-B | `NOT_STARTED` |
| 架构验收 | `NOT_REVIEWED` |
| Active branch | `feat/phase2d-data-versioning` |
| Baseline/HEAD（本次协调更新前核对） | `d691393dde90ee0299966d13ac0069774354bfd9` |
| 最新 commit | `feat: add dataset manifest and stable data version` |
| 分支跟踪 | `origin/feat/phase2d-data-versioning`；本地跟踪引用显示一致，本次未 fetch 核验远端 |
| 本次实现 commit SHA | 无；未实施 |
| 本次状态文档 commit SHA | 无；本次仅保存本地协调状态更新 |
| push | 未执行；本次没有可交付的实现提交 |
| 本次修改文件 | 仅 `docs/coordination/CURRENT_TASK.md`、`docs/coordination/PROJECT_STATUS.md` |
| 是否进入下一阶段 | 否 |
| Phase 3 正式研究 | `blocked`，仍等待 Phase 2D-B 架构验收 |

### Blocker 解除说明

用户已明确授权本次将 CURRENT_TASK 的任务状态从 `SPECIFIED_NOT_STARTED` 改为 `OPEN`。原“任务未开放”协调阻塞已解除，当前 Status 为 `READY_FOR_IMPLEMENTATION`。任务名称、范围、契约与验收要求不变。

本次只更新协调状态，不运行测试、不开始 Phase 2D-B 实现；完成后停止。此状态不代表实现完成或架构验收通过；Phase 3 正式研究仍受原门禁约束。后续收到实施指令时重新读取现场任务与 Git 状态。

### 工作区与保留项（写入本记录前）

- 已跟踪但未提交：`AGENTS.md`，diff 仅为既有 Coordination Protocol 追加内容；本次不修改、不提交。
- 既有未跟踪文件：`docs/coordination/CURRENT_TASK.md`、`docs/coordination/PROJECT_STATUS.md`、`docs/architecture/etf-research-lab.md`。
- 暂存区为空；未发现未提交的业务代码。工作区不是干净状态，但这些文件与初始化记录一致，不能顺带清理或提交。
- 本次仅修改 CURRENT_TASK 的任务状态及本状态文件的当前记录，保留历史初始化内容；AGENTS、架构文档及既有业务代码保持原样。

### 验证、warning 与未执行项

- 已读取：AGENTS、CURRENT_TASK、PROJECT_STATUS，以及任务引用的 personal-a-quant、data-versioning、etf-research-lab 架构文档。
- 已检查：`git status`、当前 branch、`git branch -vv`、完整 HEAD SHA、最新 commit、工作区 diff、暂存区与未跟踪文件。
- 任务单元测试、aquant 回归、API / 美股回归：未运行；用户明确要求不运行测试、不开始代码实现。
- build / lint：未运行；本次仅协调状态更新，不声称通过或当前环境可用。
- 新增运行时 warning：未知；本次没有运行测试或构建。
- 按用户授权仅将任务状态改为 OPEN；未修改业务代码、任务范围、契约或验收要求，未开始 Phase 2D-B；未 commit、未 push。

## 历史记录：协作机制初始化

| 项目 | 状态 |
| --- | --- |
| 本次工作 | 建立跨聊天协作机制，仅本地文档 |
| 协作机制 | `LOCAL_DOCS_SAVED` |
| Phase 2D-B | `NOT_STARTED` |
| 架构验收 | `NOT_REVIEWED` |
| branch | `feat/phase2d-data-versioning` |
| HEAD SHA（初始化检查时） | `d691393dde90ee0299966d13ac0069774354bfd9` |
| 架构基线 | `d691393dde90ee0299966d13ac0069774354bfd9` |
| 本次 commit SHA | 无；按用户要求不提交 |
| push | 未执行；按用户要求仅保存本地 |
| 是否进入下一阶段 | 否 |
| Phase 3 正式研究 | `blocked`，等待 Phase 2D-B 架构验收 |

## 本次修改文件

- `AGENTS.md`：追加 Coordination Protocol，保留原有规则。
- `docs/coordination/CURRENT_TASK.md`：定义 Phase 2D-B 范围、验收与阶段门禁。
- `docs/coordination/PROJECT_STATUS.md`：初始化交接记录。

## 工作区与保留项

初始化检查未发现未提交的业务代码。已有未跟踪文件 `docs/architecture/etf-research-lab.md`，原样保留，不属于本次修改与后续默认提交范围。后续执行前必须重新检查工作区，不以此记录代替实际 Git 状态。

## 验证记录

- 文档检查：核对基线、任务范围、角色职责、状态字段与 Phase 3 门禁；检查本次 diff 和现有架构文档未被修改。
- 业务测试：未运行；本次仅协作文档，不包含实现变更。
- build：未运行；本次仅协作文档。
- lint：未运行；本次仅协作文档。
- 新增 warning：未执行测试/build/lint，运行时 warning 状态未知；未发现新增文档冲突。
- blockers：协作文档初始化无阻塞；Phase 2D-B 尚未实现及验收，阻塞 Phase 3 正式研究。

## 后续编码交付要求

每次交付用实际结果更新 branch、检查时 HEAD SHA、实现 commit SHA、修改文件、测试命令及结果、build/lint 状态、新增 warning、blockers、是否进入下一阶段、commit/push 状态。不得填报未运行的测试通过。

实现完成且验证通过时使用 `READY_FOR_ARCH_REVIEW`，随后按用户授权和 Coordination Protocol 交付并停止。未完成、验证失败或推送受阻需明确说明；待审不等于验收通过，不自行进入下一 Phase。
