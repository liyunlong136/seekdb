# 第六届 OceanBase 数据库大赛 —— 参赛交付仓库

本分支同时携带两条赛线的全部交付物：

| 赛线 | 赛题 | 分值 | 实现位置 |
| --- | --- | --- | --- |
| PowerContext 代码题 | 赛题一至赛题十一 | 240 | `src/powercontext/` |
| 加赛 MiniVectorDB | 题号 600081 | 80 | `main.py`、`minivectordb/` |

## 一、PowerContext 代码题（240 分）

产品代码在 `src/powercontext/`，判题只加载产品的 `src/`。

- 仓库 HTTPS URL：`https://github.com/liyunlong136/seekdb`
- 分支：`competition/powercontext-solutions`
- 提交：见本分支最新 commit 的 40 位 SHA
- 冻结基线：`9db1753434162c0aa7c9f1189a1722e617880a52` 位于本分支历史中

十一道赛题的实现提交（按目录反查）：

| 赛题 | 分值 | 实现文件 |
| --- | --- | --- |
| `setup-00-repository-fork` | 10 | 仓库状态（分支历史含冻结基线） |
| `source-01-table-evidence` | 20 | `src/powercontext/sources/csv_rows.py` |
| `source-02-pi-history` | 20 | `src/powercontext/sources/pi_session.py` |
| `source-03-pdf-retrieval` | 50 | `src/powercontext/sources/pdf_pages.py` |
| `retrieval-01-reranking` | 20 | `src/powercontext/builtin/artifacts/memory/http_reranker.py` |
| `retrieval-02-hybrid-search` | 20 | `src/powercontext/builtin/persistence/oceanbase/memory_index.py` |
| `context-01-evidence-selection` | 20 | `src/powercontext/builtin/runtime/evidence_selection.py` |
| `context-02-inincremental-pack` | 20 | `src/powercontext/builtin/runtime/context_pack.py` |
| `memory-01-source-lifecycle` | 20 | `src/powercontext/builtin/runtime/source_lifecycle.py` |
| `memory-02-embedding-batches` | 20 | `src/powercontext/builtin/inference/coalescing.py` |
| `processing-01-count-trigger` | 20 | `src/powercontext/builtin/runtime/processing_discovery.py` |

自测方式见 `README_PowerContext.md`。

## 二、加赛 MiniVectorDB（80 分，题号 600081）

源码 `minivectordb/`、入口 `main.py`、镜像 `Dockerfile`、Agent 材料
`agent/conversation.json` 与设计说明 `DESIGN.md` 同属本分支的这一次提交。

- 仓库 HTTPS URL：`https://github.com/liyunlong136/seekdb`
- 分支：`competition/powercontext-solutions`
- 提交：同一次 commit

> 若评测端要求分支名严格为 `2026-competition-round2`，该分支同样包含完整
> 的 MiniVectorDB 交付物（同一 commit），SHA 见提测清单。

容器层复验：

```bash
bash verify_docker.sh
```

它会依次做：构建镜像 → 官方握手检查 → 官方全部公开用例 → 带容器约束的协议自测 → 强杀恢复。

## 三、目录说明

| 路径 | 内容 |
| --- | --- |
| `src/powercontext/` | PowerContext 产品代码（十一道赛题的实现） |
| `competition/` | 官方赛题资产与公开自测（未作任何修改） |
| `tests/` | 产品自带测试 |
| `minivectordb/`、`main.py`、`Dockerfile` | MiniVectorDB 实现与镜像 |
| `agent/conversation.json` | MiniVectorDB 的 Agent prompt 材料 |
| `mysqltest/`、`protocol_check/` | 官方判题资产 |
| `README_PowerContext.md` / `README_MiniVectorDB.md` | 两条赛线各自的原始说明 |
