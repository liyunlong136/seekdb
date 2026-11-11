# MiniVectorDB

一个支持 SQL、持久化存储与事务的轻量向量数据库，用于 2026 OceanBase 数据库大赛加赛（题号 600081）。

- 目标运行环境：Linux x86_64 容器，前台运行，通过 stdin/stdout 的 JSONL 协议与测试驱动交互。
- 语言与版本：Python 3.12（仅标准库 + 可选 numpy）。
- 数据目录：`/data`（可用 `VECTORDB_DATA_DIR` 或 `--data-dir` 覆盖）。

## 一、构建与启动

构建镜像：

```bash
docker build -t minivectordb:local .
```

前台启动并保持标准输入打开（不使用 `-d`，不分配 TTY，不暴露端口）：

```bash
docker run -i --rm \
  --network=none --cpus=2 --memory=2g --memory-swap=2g --pids-limit=128 \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --mount type=bind,src=/path/to/data,dst=/data \
  minivectordb:local
```

容器启动后会主动输出一行 `{"type":"ready"}`，随后逐行读取请求、逐行返回响应，每条输出都换行并 flush。
日志只写 stderr。收到 `shutdown` 请求后，程序回滚未提交事务、完成落盘并以退出码 0 退出，之后不再有任何输出。

协议细节见题包根目录的 `STDIO_PROTOCOL.md`；官方自测工具见 `protocol_check/`。

## 二、通信协议

请求（每行一个 JSON 对象）：

```json
{"id": 1, "op": "execute", "sql": "SELECT id FROM t ORDER BY L2_DISTANCE(vec, [0.1,0.2]) LIMIT 10"}
{"id": 2, "op": "execute", "sql": "SELECT id FROM t", "search_mode": "ann"}
{"id": 3, "op": "shutdown"}
```

响应（每行一个 JSON 对象）：

```json
{"id": 1, "ok": true, "columns": ["id"], "rows": [[7]]}
{"id": 3, "ok": true}
```

- `id` 原样回传。
- `ok` 是 JSON 布尔值，不是字符串。
- 失败时 `ok` 为 `false`，并带 `error.code`（语义化错误码，如 `duplicate_key` / `invalid_vector`）。
- `search_mode` 取 `exact`（默认）或 `ann`，只影响向量 TopK 查询。

## 三、SQL 支持范围

| 类别 | 支持 |
| --- | --- |
| 表结构 | `CREATE TABLE`；`BIGINT`、`VARCHAR(n)`、`VECTOR(n)`；每表一个 `BIGINT` 主键，最多一个向量列 |
| 数据操作 | `INSERT`（含多行）、`SELECT`、`UPDATE`、`DELETE`；按主键或条件更新/删除 |
| 条件查询 | `SELECT *`、列投影、`=` `<` `<=` `>` `>=`、`AND`、`ORDER BY`、`LIMIT` |
| 向量检索 | `L2_DISTANCE(col, [..])`、`ORDER BY L2_DISTANCE(...) LIMIT K`、带 `WHERE` 过滤的 TopK |
| 事务 | `BEGIN`、`COMMIT`、`ROLLBACK`；事务内可见自己的修改 |
| 索引 | `CREATE VECTOR INDEX idx ON t(col)`（创建后新增/更新/删除必须正确反映到检索结果） |

不支持的语句（`NULL`、`JOIN`、聚合、子查询、`ALTER TABLE`、外键、嵌套事务、主键更新等）一律返回约定错误码，不会被静默执行为其它含义。

### 语义要点

- `L2_DISTANCE` 返回**欧氏距离**，不是距离平方。例：`[3,4]` 与 `[0,0]` 的距离是 `5`。
- 向量按 **float32** 存储，距离在 float64 上对 float32 存储值计算。输入必须是可表示为有限 float32 的数值；`NaN`、`Infinity`、维度不符、转换溢出都会被拒绝。
- `VARCHAR` 长度按 Unicode 字符数计算，字符串比较按 Unicode 码点顺序，不做语言相关排序。
- `BIGINT` 全程按 int64 精确处理，不经过浮点。
- 过滤后的 TopK 在满足 `WHERE` 的集合内计算，不会先在全表取 K 条再过滤。结果不足 K 条时返回全部符合条件的记录。
- 距离相同时按主键升序；普通 `ORDER BY` 的并列项也按主键升序。
- `exact` 模式即使建了向量索引也返回精确结果。

### 错误码

`duplicate_key`、`unsupported_sql`、`dimension_mismatch`、`invalid_vector`、`no_such_table`、
`no_such_column`、`no_such_index`、`table_already_exists`、`index_already_exists`、
`type_mismatch`、`syntax_error`、`transaction_error`、`constraint_violation`、
`value_out_of_range`、`unsupported_op`、`internal_error`。

## 四、从空数据目录开始的操作示例

```sql
CREATE TABLE docs (id BIGINT PRIMARY KEY, title VARCHAR(64), vec VECTOR(3));
INSERT INTO docs (id, title, vec) VALUES
  (1, 'alpha', [1.0, 0.0, 0.0]),
  (2, 'beta',  [0.0, 1.0, 0.0]),
  (3, 'gamma', [0.0, 0.0, 1.0]);

-- 普通查询
SELECT id, title FROM docs WHERE id = 2;

-- 向量检索：距离 [0.1, 0.1, 0.1] 最近的 2 条
SELECT id, L2_DISTANCE(vec, [0.1, 0.1, 0.1]) AS d
  FROM docs
  ORDER BY L2_DISTANCE(vec, [0.1, 0.1, 0.1])
  LIMIT 2;

-- 带过滤的 TopK：过滤必须先于取 K
SELECT id FROM docs WHERE title = 'alpha'
  ORDER BY L2_DISTANCE(vec, [0.1, 0.1, 0.1]) LIMIT 2;

-- 事务：提交
BEGIN;
INSERT INTO docs (id, title, vec) VALUES (4, 'delta', [1.0, 1.0, 1.0]);
SELECT id FROM docs WHERE id = 4;      -- 事务内可见
COMMIT;

-- 事务：回滚
BEGIN;
DELETE FROM docs WHERE id = 1;
ROLLBACK;
SELECT id FROM docs WHERE id = 1;      -- 恢复为删除前的状态

-- 重启后内容一致：正常关闭（shutdown）后用同一数据目录重新启动，数据仍在。
```

## 五、本地自测

题包自带公开用例（20 个样例、95 条 SQL）与协议自测工具：

```bash
# 握手检查
python3 protocol_check/run.py --image minivectordb:local --handshake-only --output protocol-results/handshake

# 全部公开用例
python3 protocol_check/run.py --image minivectordb:local --output protocol-results/public
```

`protocol_check/run.py` 的进程模式（`--command`）只支持 POSIX；Windows 上可用 `python3 main.py` 手工驱动同样的 JSONL 协议。

## 六、目录结构

```
main.py                 入口：解析数据目录参数并启动协议服务
minivectordb/
  server.py             JSONL 协议层：握手、请求分发、错误响应、shutdown 生命周期
  engine.py             SQL 执行：DDL/DML/SELECT/事务控制、精确与向量化检索路径
  sqlparser.py          SQL 子集解析器（词法 + 语法 → AST）
  core.py               值模型：错误码、列类型、向量语义、行编解码
  store.py              列式落盘存储层：mmap 列文件、WAL、checkpoint、崩溃恢复
  fastpath.py           numpy 加速路径（可用时启用，不可用时自动降级）
agent/conversation.json Agent prompt 学习材料（评审只读这一个文件）
DESIGN.md               设计与验证记录
```

## 七、依赖与合规说明

- 只依赖 Python 标准库；`numpy==2.2.3` 为可选加速依赖，构建期安装，缺失时自动降级。
- **未使用** SQLite / RocksDB 等现成存储或数据库引擎：存储、查询执行、事务与恢复全部自行实现。
- **未调用** Faiss / VSAG / HNSWlib 等现成向量索引库：向量距离与 TopK 自行计算（numpy 仅作通用数学库做批量运算）。
- 约 1000 行实现代码，不依赖选手本地已有的任何未交付文件，镜像可从本仓库源码完整重建。
