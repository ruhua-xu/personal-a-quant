# AGENTS.md

## Project

`personal-a-quant` 是一个个人使用的多市场量化研究和手动交易辅助系统，基于 OpenAshare 的 `FastAPI + Next.js` 工程底座继续开发。

第一阶段的边界：

- 主要支持 A 股，优先研究 ETF。
- 用户在券商 App 中人工执行买卖。
- 不进行券商自动下单，不进行高频交易。

未来演进方向：支持美股、多个市场、多个账户和多币种。通用设计必须为这些扩展保留明确边界，但不得为了未来需求提前进行无必要的大规模重构。

核心产品流程：

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

## Repository Areas

- `api/`：FastAPI 入口、schema 和 service 层。
- `app/`：Next.js App Router 页面。
- `components/`：前端 UI 组件。
- `lib/`：前端 API client 与共享 TypeScript 类型。
- `ashare/`：继承自 OpenAshare 的分析、监控和数据模块。
- `aquant/`：personal-a-quant 后续新增的领域、市场、策略、回测、风控和手动执行模块；当前阶段按根目录 Python package 组织。
- `scripts/`：本地运行辅助脚本。
- `tests/`：后端和领域测试。

## Mandatory Trading Safety

第一阶段禁止实现、接入或变相执行以下能力：

- 自动券商下单。
- 自动撤单。
- 模拟点击券商客户端。
- 自动输入交易密码。
- 绕过验证码或其他人工确认机制。
- 使用未授权的证券交易接口。

策略层不能直接调用任何交易执行模块。系统最多生成经过风控检查的 `ManualOrderPlan`，最终交易动作必须由人类在券商 App 中完成，并以 `ManualExecution` 记录实际执行结果。

## News and AI Safety

新闻、券商研报、社交媒体或 LLM 输出都只能作为研究证据，不能单独产生买入订单计划。默认且强制保持：

```text
allow_news_only_trade = false
```

任何订单计划都必须同时经过结构化策略、可复现数据和风险检查；AI 输出不得绕过这些边界。

## Multi-market Rules

通用领域代码不得写死以下 A 股特有假设：

- 六位证券代码。
- `CNY`。
- 北京时间。
- 100 股整数倍。
- A 股 T+1。
- A 股涨跌停规则。

代码格式、币种、时区、最小交易单位、结算规则和价格限制必须由具体市场的 `MarketRuleBook` 提供。业务领域模型只能依赖稳定的内部接口，不能依赖某个市场或数据供应商的实现细节。

## Data and Reproducibility

- 所有正式策略研究和回测必须使用可复现的本地历史数据，并记录数据版本、时间范围和来源。
- 网络数据源不能直接成为回测的唯一输入；网络数据应先经 Adapter 获取、校验并落入可复现的数据集。
- 单元测试禁止依赖实时网络。网络 Adapter 使用 mock、fixture 或预先保存的样本验证。

## Development Rules

- 优先小而明确的改动，不进行无必要的大规模重构。
- 保留现有 OpenAshare 产品能力，包括股票分析、新闻、热点、Portfolio 和 Agent；除非任务明确要求，不得删除或破坏这些路径。
- 改动 API contract 时，必须同步更新 `api/schemas.py` 与 `lib/types.ts`，并补充契约测试。
- 核心功能必须有测试；修复缺陷时应增加能够复现问题的回归测试。
- 领域逻辑应放在后端既有 service 边界或未来 `aquant/` 模块中，不要嵌入前端组件。
- AkShare、AKQuant、yfinance 和 Finnhub 只能通过 Adapter 接入；领域模型不得导入这些第三方库。
- 不提交真实 API Key、Token、`.env`、券商账号、交易密码或任何本机私有配置。
- 不在仓库文档中记录私有本地 skill 名称、API Key、本地 agent wiring 或机器专属构建产物。

## Information Sources

对时效敏感的新闻、公告、券商研报、政策、交易规则和市场事件，优先使用权威且适合金融信息的来源。返回事实应标注来源；基于事实形成的判断必须明确标为推断。

## Validation

至少运行与改动相关的测试。项目基线命令为：

```bash
python -m pytest tests/test_api_app.py -q
python -m pytest tests/test_us_market.py -q
npm run build
npm run lint
```
