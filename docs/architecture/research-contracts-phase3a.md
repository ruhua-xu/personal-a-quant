# Phase 3A：离线研究模型与身份契约

范围：`aquant/research/{universe,models}.py`，内存对象与 JSON。没有行情读取、SnapshotReader 集成、文件保存、因子计算、排名、指标、Runner、策略执行或交易输出。模型验证通过不是正式研究验收。

## Universe 与身份

- `UniverseDefinition.create(...)` 接受完整 InstrumentId，经过 ChinaEquityRuleBook 校验，并限制为 ETF。不能从 ticker 推断身份。成员按 canonical_key 字典序排序；重复/属性冲突拒绝。
- 定义 Schema 1 身份字段固定为 schema_version、universe_id、as_of、instruments、selection_rules、notes。
- `UniverseSnapshot.from_definition(definition, known_at=..., membership_basis=...)` 先复核定义；精确保留成员和依据。快照身份在上述字段基础上加入 definition_version、known_at、membership_basis。也支持显式完整字段的 `create(...)`；单独加载快照不能证明外部定义文件与其一致，此交叉存储核验留待持久化阶段。
- version 为 `sha256:<hex>`，对规范 JSON UTF-8 字节计算，不包含自身。所有 datetime 必须 aware，规范到 UTC；日期 ISO 编码。严格 JSON 拒绝非有限数、任意对象、Decimal/date 对象、非字符串键和循环引用；嵌套列表保留顺序。
- `create`、`model_validate_json`、`verify` 共享身份字段模型。声明版本与内容不符即失败。调用者必须提供真实 known_at，程序不猜测/倒填。

## ResearchConfig

Schema 1 全部 41 个身份字段明确声明于 `_ResearchConfigIdentity`；加上 parameters_hash，共 42 个序列化字段。默认值展开后参与哈希。关键默认值：63/126 动量窗口、200 趋势窗口、63 波动窗口、252 年化会话数、ddof=1、四因子各 `"0.25"`、DAILY/RAW/close/raw_price_return、每日评分/周一周检查、cash_allowed=true、dataset_union_v1、session_date_end_v1、strict_contiguous_v1、[5,20] 标签窗口、5 个 IC 样本、5 桶、每桶至少 2 项。

调用者必须显式提供身份、版本、top_n、日历成员、RAW 审查引用、三个日期区间、hypothesis、parameter_comparisons、promotion_criteria。后两者仅是 JSON 参数对象清单与审查标准，不执行表达式/搜索。空对照清单明确表示没有额外对照。

- 窗口、top_n 等严格整数，不接受 bool、字符串或 float。动量短窗口小于长窗口；波动窗口至少 2。IC 最小样本和桶数至少 2；单桶最小人数为正。
- 因子名恰为 momentum_3m、momentum_6m、trend_score、volatility。权重只接受十进制字符串；通过 Decimal 解析、Fraction 精确求和，不受全局 Decimal 精度影响；去除尾零、规范负零。拒绝浮点/Decimal 对象、负数、非有限数及不等于 1 的和。
- forward_horizons 是正整数集合，排序去重；日历成员排序但重复项拒绝。参数对照和其他普通 JSON 列表保序。
- research_period、validation_period、test_period 各自含 inclusive start_date/end_date，严格有序且无端点重叠；只接受 date 或 YYYY-MM-DD，不接受 datetime/时间戳。
- score_contract_version 固定 `"1"`，primary_evaluation_sampling 固定 `weekly_decision_dates`。这些是研究契约，不是市场交易规则。
- parameters_hash = `sha256(canonical_json_bytes(全部展开配置))`，不使用 Dataset 专属版本算法。Git SHA、run ID、运行时间、路径不作为配置字段；不允许额外字段。
- EvidenceReference 只保存便携 logical reference_id、content_hash、scope、limitations；reference_id 禁止本机路径/URL。不解析文件，不宣称审查记录真实存在或适用。不得把本机路径/隐私塞进自由文本和 JSON；它们是语义内容而非存储定位器。

固定基准（测试中人工 fixture，经独立标准库 JSON/SHA 计算核对）：定义 `sha256:8d47a49e53ba06efad22e3091fce0ab18e15d8f68c11ba61661be14974b2a190`，快照 `sha256:7fdc0ab8a16ee742b939fe64b3a9436f2edc0e9374d070942632c09abbe00b07`，配置 `sha256:437ac8fc11fc4c2ed7101b62c40b88e33e6b8cb003c3fd001b02eec301690c99`。更改身份 schema 时必须显式重新审查，不能随意更新 fixture 掩盖变化。

## 跨对象入口

`validate_research_inputs(config, universe)` 为纯函数，先重新验证两个模型，再检查 universe_id/version、一致的完整日历成员、非空池、top_n 不超过完整人数。缺行情不是删池理由；本阶段不读数据。

prospective_frozen 的保守评分起点定义为 research_period.start_date 在 **ChinaEquityRuleBook.timezone 的 00:00:00**；known_at 必须 <= 此时刻，等于允许，晚 1 微秒也拒绝。此处是预先冻结截止边界，不是生成信号时的日末 as_of，也不是交易时段判断。retrospective_manual 保留其事后人工选池标识，不自动变成 PIT/OOS 或晋级凭据。

实际 manifest 的版本/覆盖/RAW 适用性、真实数据质量与审查文件内容不在此函数验证范围，留待 Phase 3D 和正式输入审查。

## ResearchRun 与最小输出包装

- ResearchRun 与既有 StrategyRun 并存；完整 40 位小写 Git SHA 由调用者提供，git_commit_source 固定 caller_supplied，不能宣称自动验证了运行代码。
- runtime_versions 为调用者提供的非空版本字典。不探测 Git/环境、不启动运行。正式运行器将负责完整记录实际相关依赖，3A 不把声明当实测证据。
- CREATED 只有 created_at；RUNNING 必须 started_at、不得 completed_at；COMPLETED 必须二者且无 error；FAILED 必须 completed_at 和明确 code/message。输入验证阶段失败允许没有 started_at；其余时间依次不倒退。失败/未完成不会被转换为成功。
- evaluation_stage、test_release_ref、test_consumed 属于运行审计，不改变 parameters_hash。test 阶段要求许可引用；已开始的 test（包括中途 FAILED）必须标已消耗。许可内容、单次使用与阶段化读取在后续 Runner 落实。
- FactorSnapshot 最小包装只存 run ID、signal_date、aware as_of、完整证券及可选的原有 SecurityScore。不存在分数时为 None；有分数时要求有限且位于 [0,1]、证券/截止时间与包装一致；保持 Decimal、UTC，不修改 domain。四因子/percentile 表及资格计算留后续阶段，不造占位数值。
- ResearchResult 仅保留身份引用、RAW 价格语义、artifact 逻辑引用、可空 coverage_summary/audit_checks、quality_flags、必填非空 limitations、0–1 promotion_level；没有产物时不填虚构统计。它不是已完成实验的证据，也不会自行授予晋级资格。

## 复制、重核验与边界

入口深拷贝可变 JSON；顶层 frozen 不代表深度不可变。嵌套 dict/list 仍可被修改，`verify()` 返回重新验证的独立对象，version/hash 不一致则拒绝；`model_dump_json()` 会先 verify，再序列化。`model_validate_json()` 加载时再次验证。调用者不能把 `model_construct` / `model_copy(update=...)` 当验证入口，正式边界必须复核。

没有文件 save/load 方法；上述加载仅是 JSON 文本解析。测试断网，并对导入依赖、动态导入、文件调用做静态检查，以独立违规代码片段验证检查器有效；运行时同时禁止 file/socket/subprocess/env 访问，验证模型完整生命周期。系统导入代码/时区数据库的正常加载不属于行情或研究文件访问。

阶段止于 READY_FOR_ARCH_REVIEW，不包含 Phase 3B。现有 domain、MarketRuleBook、manifest/hash、snapshot/store、Provider、API 与前端均不修改。
