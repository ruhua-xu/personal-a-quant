# Phase 2D-B：Immutable Dataset Snapshot

本实现补充 [Phase 2D-A](data-versioning.md) 的持久化缺口，不改变 DatasetManifest
Schema 1、canonical identity、领域模型或 mutable store。正式研究仍须等待架构
Review 验收，不能因代码提交自动进入 Phase 3。

## 接口与布局

新增包 `aquant.data.snapshots`，公开：

- `SnapshotPublisher(root_path).publish(dataset, source, *, start_date, end_date)`：
  输入 MarketDataSet、SourceMetadata 与显式闭区间，返回已发布 DatasetManifest。
- `SnapshotReader(root_path).read(data_version)`：读取完整版本，返回
  `VerifiedSnapshot(manifest, dataset, verified_at)`；另有 data_version 只读属性。
- `snapshot_directory_name(data_version)`：严格版本号到 Windows-safe 目录名的映射。
- `SnapshotError`：输入、I/O 或完整性错误；`SnapshotConflictError` 是发布锁冲突。

```text
<explicit-local-root>/
    .staging/snapshot-<temporary-id>/   # 未发布，reader 永不搜索此目录
    .publish-locks/sha256-<hex>.lock   # 每版本独占发布锁
    snapshots/sha256-<hex>/
        manifest.json
        bars.parquet
```

调用者明确选择本地根目录；不接受 URL、UNC、`latest` 或模糊版本。推荐运行数据
放在已被 Git 忽略的 output/ 下，不提交快照、环境配置或秘密。
data_version 本身仍为 `sha256:<64 lowercase hex>`；只有目录名中的冒号替换为
连字符。没有字符串截断、大小写纠正或自动猜测。

首版只用一个 bars.parquet，避免引入分区调度/增量快照系统。发布/读取整份数据，
没有按 HistoryRequest 筛选、数据库或网络访问。后续大数据集分块需要独立设计。

## 发布契约

1. 重新构造 MarketDataSet 和 SourceMetadata，校验并复制输入，防止 mutable
   DataFrame/嵌套 metadata 或 `model_copy` 绕过校验。不修改调用者对象。
2. start_date/end_date 必须显式提供为 Python date（非 datetime），首日不晚于
   末日；所有 bar 必须在此闭区间。日期不参与隐式过滤，不悄悄丢弃范围外行。
3. 在根目录下独占的 staging 临时目录中重新序列化数据，不链接/引用 mutable
   store 的文件。写入 manifest，使用与正式读取完全相同的核验流程验证 staging。
4. 创建每版本独占锁文件，再检查目标。目标已存在时必须完整验证后才幂等返回原
   manifest；不改写原文件、mtime 或 created_at。目标为空、损坏或冲突即失败。
5. 目标不存在时，使用同一文件系统中的目录 rename 发布完整 staging，不使用
   replace 覆盖目标。同版本的协作发布器受独占锁保护，只有一个可进入发布区。
6. 正常异常会清理本次临时目录及本次取得的锁；不会清理其他版本或其他发布者的锁。
   进程被强制终止可能留下 staging 或锁：staging 不可按版本读取，遗留锁明确失败，
   由人工确认发布者已停止后再处理，不自动过期、接管或覆盖修复。

锁竞争立即抛 SnapshotConflictError，不等待、不调度重试。对方完成后，调用者可
显式重试并获得幂等结果。不同版本的发布互不覆盖。

这提供本地协作 API 的不可覆盖与原子可见性，不是有恶意写入者的文件系统沙箱、
WORM 权限系统、分布式锁或断电持久性事务。外部管理员仍可删改文件；Reader 会
对读到的内容重新验证。POSIX rename 可能替换空目录，因此不能去掉发布锁或用
绕过本 API 的进程同时操作目标目录。[Python os.rename 语义](https://docs.python.org/3.12/library/os.html#os.rename)

## Parquet 格式与稳定身份

Snapshot 文件格式 1 与 Manifest Schema 1 是两个不同边界，不修改通用 manifest
schema 的既有合法取值。快照读取器额外要求单个相对路径恰好为 bars.parquet；
任何其他路径、额外文件或不同布局都失败，不会把 manifest 路径拼接后直接打开。

- 列及顺序严格为 HISTORY_COLUMNS：instrument_key、trade_date、open、high、low、
  close、volume。证券列 Arrow string，日期 date32；OHLCV 为实数整数/浮点类型。
- 不统一转换为 float，保留大整数 volume 精度。证券与日期采用固定 Arrow 类型，
  不保存 pandas index、pandas metadata、下载时间、根目录或随机临时标识。
- 文件 metadata 必须包含 `aquant.snapshot.format=1`、`aquant.frequency` 与
  `aquant.adjustment`，并与 manifest 一致。只支持当前 DAILY；保存 RAW/FORWARD/
  BACKWARD 的既有标记，不做复权转换，不替数据来源证明复权价格是否正确。
- Writer 固定 Parquet 2.6、不压缩、不用 dictionary、row group 65536，并保留统计。
  行顺序复用 MarketDataSet 稳定排序；文件 SHA 进入既有 data_version 算法。
- 相同数据类型/值、范围、稳定 SourceMetadata 和写入环境产生同一版本，跨根目录、
  不同行输入顺序或不同 generated_at 不改变版本。来源、数值、类型、范围或复权
  语义改变可产生不同版本。
- 这是内容字节身份，不承诺升级 PyArrow 后仍输出相同字节；不同 Parquet 编码结果
  应获得新版本，旧版本仍按原文件读取。本阶段不引入语义哈希或新依赖锁方案。

SourceMetadata 是调用者显式声明的权威来源记录，全部稳定 metadata 参与哈希。
dataset.provider 可能是 local_parquet，因此不强制等于 source.provider_name。
读取结果 provider 使用 manifest.source.provider_name；generated_at 为原快照
created_at，verified_at 为此次完整核验时间，均 timezone-aware。下载/采集时间若
需保存，应由调用者按已有来源 metadata 契约明确提供，不由 Publisher 猜测。

## 读取与语义核验

Reader 不创建目录，不搜寻其他版本，不访问 Provider 或 working store：

1. 校验严格 data_version、目录映射与本地路径边界；拒绝符号链接、Windows
   reparse point/junction 和共享硬链接文件。
2. 加载并验证 manifest Schema 1、canonical identity，并确认其中版本与请求/
   目录一致。校验固定文件清单及实际目录内容。
3. 对实际 Parquet 字节计算 SHA-256，再从同一内存 buffer 解码，不在验 hash 后
   重新打开路径，避免验证字节与读取字节不同。
4. 校验物理 schema/字段类型、format/frequency/adjustment metadata、Parquet
   footer 行数、实际表行数、file row_count 与总 row_count。缺失值明确失败。
5. 重建 MarketDataSet，验证 canonical key、Python date、数值有限性、OHLC 范围、
   非负 volume、唯一键及排序。实际证券集合必须与 manifest.instruments 完全一致；
   所有日期在声明区间内。
6. 返回带 manifest 和核验时间的 VerifiedSnapshot。对象脱离磁盘，DataFrame 及
   source.metadata 仍可在内存被修改；它们不是深度冻结或签名凭证。再次读取时重新
   验证磁盘，编辑返回对象不能修改旧版本。

逐文件/总行数、证券、日期和 metadata 测试会先重算正确文件 hash 和 data_version，
再验证语义错误被拒绝，避免“仅靠哈希不符”掩盖缺少语义检查的问题。

## 日期与空数据集

声明区间表示允许覆盖范围，不要求每个自然日有行，也不声称交易日完整。首末日期
可以无交易，不强制等于实际最早/最晚 bar；不补行情、不推断停牌或交易日。
manifest.instruments 表示实际出现的证券，不是假定已下载成功的请求名单。

零行输入也必须提供区间。发布一个类型完整、零行的 bars.parquet，instruments
为空、file row_count=total row_count=0；读取返回标准空 MarketDataSet。无对应
版本、缺文件、损坏、未发布 staging 或错误 metadata 都不是合法空结果。

## 验证与限制

所有新增测试位于 tests/aquant/test_snapshots.py，使用现有断网 fixture、合成数据
和 tmp_path。覆盖幂等/跨根目录、来源变化、精度、空数据、篡改、语义不符、非法
路径、实际 Windows junction、硬链接、并发锁、写入/验证/发布失败和旧版本隔离。

首版按整文件读取到内存，适合当前个人日线数据范围，不承诺低内存大规模处理。
不修改 mutable store、旧 API、前端、SQLite 或 Phase 1 模型，不增加 Provider、
下载任务、研究 Runner、策略、回测或交易能力。执行结果和环境限制由
docs/coordination/PROJECT_STATUS.md 记录；架构验收仍是独立步骤。
