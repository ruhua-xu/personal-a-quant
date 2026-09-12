# Dataset Manifest 与稳定数据版本（Phase 2D-A）

## 范围与边界

| 层次 | 职责 | 当前状态 |
| --- | --- | --- |
| Mutable Store | Parquet 按证券、频率、复权方式、年份保存，增量写入覆盖同日旧数据；DuckDB 查询当前内容 | Phase 2B 已有，仍然可变 |
| Dataset Manifest | 声明文件摘要、范围、来源及稳定的内容寻址版本号 | Phase 2D-A 的纯模型/哈希契约 |
| Immutable Snapshot | 保留某一版本的不可变文件，验证实际内容，并支持按版本读取 | 留给 Phase 2D-B，本阶段没有实现 |

**当前 `data_version` 可以描述一个版本，但尚不能保证以后还能重新读取旧版本。**
Manifest 不复制、不锁定、不冻结现有 Parquet。Mutable Store 后续 upsert 仍可能
覆盖文件；仅保存清单或通过 `verify_data_version()` 不代表完整解决了可复现性。
真正保留并重读旧版本所需的 snapshot 文件夹、发布流程、实际文件核验和读取器
均属于后续阶段。当前不实现策略、回测、AKQuant、HTTP API 或前端改动。

## Schema 1

代码位于根目录包 `aquant/data/versioning/`，只使用标准库、Pydantic 和现有内部
枚举；不调用 Provider 或 Storage。它不改变 `StrategyRun` 结构，也不让领域层
依赖 versioning/storage。

`SourceMetadata`：

- `provider_name`、`provider_version`：显式提供的非空文本，去除首尾空白。
- `metadata`：JSON 对象，缺省为 `{}`。支持嵌套 dict/list、字符串、整数、有限
  float、bool 和 null；所有 dict key 必须是字符串。
- 禁止 bytes、set、tuple、Decimal、原始 datetime/date、任意 Python 对象、
  NaN/Infinity 和循环引用。时间等特殊类型必须由调用者先显式编码为稳定字符串。
- 不内置 AkShare 假设；未来可声明 `akshare_cn_sina` / `1.18.94` 等来源信息。
  `volume_semantics`、`notes`、`endpoint` 可以作为稳定的来源 metadata。

`DatasetFileEntry`：

- `relative_path`：非空、规范的相对 POSIX 文件路径；拒绝绝对路径、Windows
  drive/反斜杠、冒号、控制字符、空路径分量、`.` 和 `..`。不解析为本机路径，
  不要求文件存在，也不进行 URL 解码。路径字符串大小写有意义。
- `sha256`：文件字节的 64 位小写十六进制摘要，不带 `sha256:` 前缀。
- `row_count`：非负整数，不把 bool、float 或数字字符串自动转为行数。

`DatasetManifest`：

| 字段 | 契约 |
| --- | --- |
| schema_version | 固定字符串 `"1"`，缺省为 `"1"` |
| data_version | `sha256:<64 位小写 hex>`，构造器计算或加载时核验 |
| created_at | 必须 timezone-aware；不参与版本身份 |
| frequency / adjustment | 现有 BarFrequency / AdjustmentMode 枚举 |
| instruments | canonical_key 字符串集合，验证内部市场/交易所标识，去重并排序；不猜证券身份 |
| start_date / end_date | 日期，首日不晚于末日 |
| row_count | 声明的非负整数行数 |
| files | DatasetFileEntry 集合，按 relative_path 排序，重复路径直接失败 |
| source | SourceMetadata，含全部显式 metadata |

空清单可使用 `row_count=0`、空 files 和 instruments。行数、文件摘要和范围均
为调用者声明的 metadata；本阶段不打开 Parquet 来确认实际行数、范围、证券
覆盖或文件内容，也不据此声称物理数据已验证。

## Canonical identity 与 JSON

身份 payload 恰好包含：

```text
schema_version, frequency, adjustment, instruments,
start_date, end_date, row_count,
files[{relative_path, sha256, row_count}],
source{provider_name, provider_version, metadata}
```

规范化步骤：

1. 用同一个 identity schema 验证构造器、公共哈希入口和核验请求。
2. instruments 去重并按字符串排序；files 按 relative_path 排序。
3. 日期显式输出为 `YYYY-MM-DD`，枚举输出内部字符串值。
4. JSON 固定为 UTF-8、`sort_keys=True`、`separators=(",", ":")`、
   `ensure_ascii=False`、`allow_nan=False`，无 BOM、无尾部换行。
5. `data_version = "sha256:" + SHA256(canonical_json_bytes(payload)).hexdigest()`。

`data_version` 自身和 `created_at` 不进入身份。算法不采集绝对根路径、用户名、
机器名、临时目录或随机 UUID，公共 `compute_data_version(identity_fields)`
拒绝这些额外顶层字段。相同相对路径、相同文件字节和相同稳定来源信息，在不同
根目录生成同一版本。调用者也不得把本机私有信息或随机标记塞入 source metadata：
metadata 的全部内容有意参与 identity，系统不会自动剔除其中的任意字符串。

`canonical_json_bytes()` 本身只做严格 JSON 编码，不排序任意列表；证券/文件
列表的规范化由 identity schema 负责。其他 metadata 列表保留顺序，字符串不
做 Unicode 归一化。整数 `1` 与 float `1.0`、`0.0` 与 `-0.0` 保持不同编码。
这是明确的 Python JSON 契约，不宣称实现 RFC 8785/JCS。Schema 1 的固定哈希
测试样本用于发现无意的规则变更；修改规则应显式设计新 schema，不能悄悄漂移。

## 创建、加载与核验

建议使用 `DatasetManifest.create(created_at=..., **identity_fields)`，不手工填
`data_version`；传入该字段会被拒绝。`identity_payload()` 返回脱离原模型的
JSON-compatible 副本，可以交给 `compute_data_version()`。

直接构造 `DatasetManifest(...)` 或调用 `model_validate_json(...)` 时，必须
提供正确版本号；模型会自动核验，格式正确但内容不匹配的任意版本同样失败。
`verify_data_version()` 成功返回 None；身份不匹配或 metadata 非法时抛出
ValueError（包含 Pydantic ValidationError）。它只验证清单身份，不读文件。

模型顶层冻结、集合使用 tuple，来源 metadata 对调用者输入做深拷贝；但 metadata
内部仍是普通 JSON dict/list，并非深度不可变对象。若直接修改嵌套 metadata，
原版本不会自动更新，显式核验会发现差异。Pydantic `model_copy(update=...)` 本身
绕过验证，使用该接口后必须重新验证或用 create 重建，不能把它当作受检 builder。

`StrategyRun.data_version` 可以直接引用 `manifest.data_version`。这只保存引用，
不运行策略，也不意味着对应数据已经具备永久可读的 snapshot。

## 文件 SHA-256 工具

`sha256_file(path, chunk_size=1024*1024)` 以二进制分块读取，仅哈希文件字节，
返回 64 位小写 hex。chunk_size 必须是正整数；文件不存在或读取失败直接报错。
文件路径、修改时间和权限均不参与摘要。该工具不锁文件，也不保证读取期间没有
并发写入；不可变发布和实际内容复核留给 Phase 2D-B。

全部测试离线：文件哈希使用 pytest tmp_path，不生成任何正式 snapshot。
