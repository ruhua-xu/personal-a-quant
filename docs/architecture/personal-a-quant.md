# personal-a-quant 架构基线

## 1. 文档状态与范围

本项目从 OpenAshare baseline commit `c830c27c9a431c867a57fae921e360e05daee44a` 接管，继续使用其 FastAPI、Next.js 和现有分析能力作为应用底座。

Phase 0.5 只建立项目归属、工程边界和后续模块规划，不重构现有业务模型，不接入 AKQuant，不增加自动交易，也不删除 OpenAshare 功能。现有 `api/schemas.py`、`api/services.py`、`ashare/`、`app/` 和 `components/` 在本阶段保持不变。

## 2. 产品目标与安全边界

personal-a-quant 是个人使用的多市场量化研究和手动交易辅助系统。第一阶段主要支持 A 股并优先研究 ETF，由用户在券商 App 中人工执行买卖；系统不自动下单、不自动撤单、不操作券商客户端，也不进行高频交易。

目标产品流程为：

```text
Market Data
→ Research
→ Strategy
→ Backtest
→ SecurityScore
→ TargetPosition
→ Risk Check
→ ManualOrderPlan
→ Human Execution
→ ManualExecution
→ Reconciliation
→ Review
```

`Strategy` 只能产生策略结果，不能调用执行模块。`ManualOrderPlan` 必须在 `Risk Check` 之后生成，并且只用于提示人工操作。真实成交由用户完成后录入为 `ManualExecution`，再参与对账和复盘。

新闻、券商研报、社交媒体与 LLM 输出仅作为研究材料，不能单独产生买入订单计划。系统默认约束为 `allow_news_only_trade = false`。

## 3. 当前可复用的 OpenAshare 能力

| 能力 | 当前实现 | 在 personal-a-quant 中的定位 |
| --- | --- | --- |
| FastAPI | `api/main.py` 提供应用入口、生命周期、中间件和 API；`api/schemas.py`、`api/services.py` 承载现有契约和服务 | 继续作为后端应用层和 HTTP 边界，后续通过 service 调用 `aquant/` 用例 |
| Next.js | `app/` 使用 App Router，`components/` 提供工作台和交互组件 | 继续作为研究、组合、风险提示和人工订单计划的 UI |
| 股票搜索 | `ashare/search.py`、`ashare/stock_pool.py` 以及现有搜索 API/UI 支持代码、名称和市场识别 | 复用为证券发现入口；后续通过规范化的证券标识衔接多市场领域模型 |
| K 线图 | `components/candlestick-chart.tsx`、`components/research-chart.tsx` 使用 `lightweight-charts` 展示行情 | 复用为研究和回测结果的可视化基础 |
| Portfolio UI | `/portfolio` 跳转到 `work` 的 portfolio context，现有工作区提供持仓录入、分析和展示 | 保留为组合工作台，后续逐步接入目标仓位、实际持仓和对账用例 |
| SQLite | `api/services.py`、`api/main.py` 和 `ashare/monitor.py` 已有本地 SQLite 存储 | 第一阶段可用于个人本地状态；研究数据版本和领域持久化需通过明确 repository 边界管理 |
| 美股基础支持 | `ashare/stock_pool.py`、`ashare/data.py` 已支持 `US.*` 标识、yfinance 主源和 Finnhub 备选，`tests/test_us_market.py` 覆盖关键路径 | 作为未来美股扩展的现有基础，不代表通用领域可以依赖当前代码格式或供应商实现 |
| API client | `lib/api.ts` 集中处理前后端请求，`lib/types.ts` 保存共享的前端契约 | 继续作为前端访问后端的统一边界；API 变更必须与 Python schema 同步 |
| 现有测试 | `tests/` 覆盖 FastAPI、搜索、市场标识、监控、Agent、Credits、Supabase 与美股等能力 | 作为接管后的回归安全网；新增核心领域能力需要独立、无实时网络依赖的测试 |

## 4. 暂时保留但不是量化核心的能力

以下能力来自 OpenAshare，当前阶段不删除、不重写，后续按产品价值独立评估：

- Agent：保留研究问答与现有服务边界，但不能绕过策略、回测和风控直接生成交易执行动作。
- 新闻：保留新闻聚合和展示，只作为研究证据。
- Hotspots：保留热点发现和展示，不作为单一交易信号。
- Supabase：保留现有认证、云端工作区与相关配置能力，不把它设为本地量化研究的强依赖。
- Credits：保留当前计费/额度相关代码，不让额度模型渗入量化领域模型。
- Waitlist：保留等待名单和管理页面，不纳入量化核心流程。
- Cloud deployment：保留现有部署配置和云端路径，同时保证个人本地运行仍是有效场景。

## 5. 计划新增的模块

当前仓库采用根目录 Python package 风格。第一阶段新增模块使用根目录 `aquant/`，不强制迁移到 `src/aquant/`，避免无意义的大规模目录重构。

```text
aquant/
    domain/
    markets/
        china/
        usa/
    data/
        providers/
    strategies/
    portfolio/
    backtest/
    risk/
    manual_execution/
    reporting/
    statement_importers/
```

各模块的预期职责：

- `domain/`：证券标识、货币、时间、账户、订单计划、成交、对账等稳定领域对象和端口；不导入外部数据或量化库。
- `markets/`：市场规则接口及具体 `MarketRuleBook`。`china/` 和 `usa/` 分别承载交易时区、交易单位、结算和价格限制等市场规则。
- `data/providers/`：行情与基础数据 Adapter，以及本地可复现数据集的读取、版本和质量检查边界。
- `strategies/`：产生结构化策略信号与目标，不依赖交易执行，也不直接访问券商。
- `portfolio/`：账户、持仓、现金、目标仓位和估值用例；币种必须显式表达。
- `backtest/`：只消费版本明确的本地历史数据，产生可复现结果和运行元数据。
- `risk/`：组合和订单计划的风险规则；在生成 `ManualOrderPlan` 前形成显式检查结果。
- `manual_execution/`：生成供人查看的订单计划、记录人工成交并支持对账；不包含券商自动操作能力。
- `reporting/`：研究、策略、回测、风险和复盘报告的组装与导出。
- `statement_importers/`：通过显式 Adapter 导入人工提供的券商对账单，不保存账号凭证。

## 6. 多市场领域约束

通用领域代码不得假设证券代码一定是 A 股六位数字，也不得写死 `CNY`、北京时间、100 股整数倍、A 股 T+1 或 A 股涨跌停。

这些差异必须由具体 `MarketRuleBook` 表达，至少覆盖：

- 证券标识解析与格式化。
- 交易所日历和时区。
- 计价与结算币种。
- 最小交易单位和数量步长。
- 结算与可卖规则。
- 价格限制和价格步长。

领域服务依赖 `MarketRuleBook` 抽象，不通过 `if market == ...` 将各市场规则散落到通用代码中。

## 7. 外部依赖与 Adapter 原则

- AkShare 通过 `aquant/data/providers/` 下的 Adapter 调用。
- AKQuant 未来作为 Python 依赖，通过回测/研究 Adapter 调用；不复制其源码，也不让领域模型继承其类型。
- yfinance 和 Finnhub 通过行情 Adapter 调用；现有 `ashare/data.py` 可在迁移期保留并由兼容层使用。
- 业务领域模型不得导入 AkShare、AKQuant、yfinance、Finnhub 或它们的响应对象。
- Adapter 负责第三方标识映射、请求、重试、响应校验和错误转换，对领域层暴露稳定的内部类型。

## 8. 数据可复现性

所有正式策略研究与回测必须基于本地、可复现的历史数据。数据集至少记录供应商、抓取时间、覆盖区间、复权方式、schema 版本和内容校验信息。

在线数据可以用于抓取、更新和交互式研究，但不能直接成为回测的唯一输入。进入回测前必须固化为版本明确的本地数据集。单元测试使用 fixture、fake 或已保存样本，不访问实时网络。

## 9. 演进原则

- 以小步、可测试的方式在现有工程旁建立 `aquant/`，不先搬迁 OpenAshare 模块。
- 通过 Adapter 和应用 service 连接新旧模块，保持现有页面和 API 可用。
- API contract 变化时同步更新 `api/schemas.py` 与 `lib/types.ts`。
- 核心功能必须有单元测试和必要的契约测试。
- 不提交 `.env`、真实 API Key、Token、券商账号、交易密码或本机私有配置。
- 任何未来功能都必须遵守人工执行边界；不得把“手动交易辅助”扩展为自动交易。
