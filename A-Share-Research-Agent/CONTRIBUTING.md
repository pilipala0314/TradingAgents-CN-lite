# Contributing to A-Share Research Agent

感谢你考虑为本项目做贡献。本文档说明开发流程、代码规范与协作约定。

> **TL;DR**：跑测试 → 写代码 → 写测试 → `make lint` → `make test` → 提 PR。

---

## 目录

1. [行为准则](#行为准则)
2. [开发环境](#开发环境)
3. [项目结构](#项目结构)
4. [开发流程](#开发流程)
5. [核心原则](#核心原则)
6. [代码规范](#代码规范)
7. [测试规范](#测试规范)
8. [提 PR 流程](#提-pr-流程)
9. [Issue 报告](#issue-报告)

---

## 行为准则

本项目遵守 [Contributor Covenant](https://www.contributor-covenant.org/) 行为准则。
参与即代表你同意以专业、包容、尊重的方式与社区互动。

---

## 开发环境

### 准备

```bash
# 1. 克隆 & 进入
git clone <repo-url>
cd A-Share-Research-Agent

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 3. 安装开发依赖
pip install -e ".[dev,scheduler,tdx,stealth]"
# 或精简版（只 lint/test）：
pip install -e ".[dev]"

# 4. 复制环境变量
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（任意 OpenAI 兼容服务）

# 5. 启动数据库
make docker-up        # 5 服务
make alembic-up       # 迁移
```

### 验证

```bash
make test             # 全量测试
make lint             # ruff check
make fmt              # ruff format
make test-cov         # 覆盖率报告
```

---

## 项目结构

详见 [README.md § 项目结构](README.md#项目结构)。核心模块：

| 层 | 模块 | 职责 |
|----|------|------|
| L0 协议 | `core/` `models/` | 配置 / DB / LLM 客户端 / Pydantic 契约 / ORM |
| L1 Provider | `data_pipeline/providers/` | 数据源实现（akshare / a_stock_data / 未来 tushare）|
| L2 路由 | `data_pipeline/data_router.py` `data_pipeline/cache/` | 多源路由 + 缓存 + 健康度 |
| L3 编排 | `graph/` `agents/` | LangGraph 6 节点 + 多 Agent |
| L4 业务 | `apps/api/` `apps/worker/` `apps/scheduler/` | FastAPI / Celery / APScheduler |

---

## 开发流程

### 1. 选任务

- 在 [Issues](../../issues) 中找感兴趣的标签：`good first issue` / `help wanted` / `bug` / `enhancement`。
- 没有合适任务？开 issue 描述你想做的功能，等 maintainer 确认后再开 PR。

### 2. 建分支

```bash
git checkout -b feat/your-feature       # 新功能
git checkout -b fix/issue-number-desc   # 修复
git checkout -b docs/your-doc-update    # 文档
```

### 3. 开发

按下面 [核心原则](#核心原则) 和 [代码规范](#代码规范) 写代码。

### 4. 测试

新功能必须带单测（参考 [测试规范](#测试规范)）。

```bash
make test                   # 全量
make test TESTS=tests/test_xxx.py  # 单文件
make test-cov               # 覆盖率
```

### 5. 格式化 & Lint

```bash
make fmt      # ruff format
make lint     # ruff check
```

### 6. 提交

```bash
git add <specific-files>
git commit -m "feat: 简短描述（中文或英文均可）"
```

Commit message 建议遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

- `feat: 新增 xxx 端点`
- `fix: 修复 cache 命中后 fallback 计数错误`
- `docs: 完善 CONTRIBUTING 贡献指南`
- `refactor: 拆分 data_router.py 的 method_table 注册逻辑`
- `test: 补充 cache 单元测试`
- `chore: 升级 pytest 到 9.x`

### 7. 推 & PR

```bash
git push origin feat/your-feature
```

在 GitHub 上开 Pull Request，填写模板（背景 / 改动 / 测试 / 截图）。

---

## 核心原则

### 1. 数据真实（绝不动摇）

> **禁止任何 mock 降级**。

- 端点失败必须显式抛错（`DataFetchError`），由调用方决定是否 strict。
- 单元测试可以用 fake provider（`tests/test_data_router.py` 里的 `_FakeProvider`），
  但 fake **不能用于生产代码路径**。
- 集成测试（标 `integration` 标记）允许打真实外网，但不强制。

### 2. 零硬编码

- 新数据端点必须通过 `DataRouter.register_method()` 或 `method_table=` 注册。
- 不要在 router / facade / agents 里直接 `import akshare` —— 走 provider。
- 配置走 `core/config.py` 的 `settings`，不读 `os.environ` 直读。

### 3. 类型注解

- 公开方法必须有类型注解（参数 + 返回值）。
- 复杂数据结构用 Pydantic model 或 `TypedDict`，别用裸 `dict`。
- `list[dict[str, Any]]` 比 `List[Dict[str, Any]]`（旧风格）优先。

### 4. 失败语义

- `raise DataFetchError(...)` 而不是 `return None` —— 让上层决定如何处理。
- 内部工具函数失败可以 `return None`，但边界（Provider 公开方法）必须 raise。
- 日志用 `logger.warning` / `logger.error` 区分；`print()` 禁止。

### 5. 可观测

- 新端点必须经过 `DataRouter`（享受 cache + stats + timeout + strict）。
- 重要操作加 `logger.info` 含 method / provider / args 关键字段。
- 不要静默 `except: pass`。

---

## 代码规范

### Style

- **Line length**: 110（`pyproject.toml [tool.ruff]`）
- **Python**: 3.10+（`from __future__ import annotations` 必须有）
- **Import order**: ruff isort 自动管（`make fmt`）
- **Naming**:
  - 类名 `PascalCase`
  - 函数/方法/变量 `snake_case`
  - 常量 `UPPER_SNAKE`
  - 私有方法/属性 `_leading_underscore`

### Docstring

- 公开 class / function 必须有 docstring。
- 中文 docstring 与项目现有风格一致（单 `"""` 三引号 + 多行说明）。
- 端点契约（method 名 / 参数 / 返回值 / 失败语义）必须明确写出。

### Logging

```python
# ✅ 好
logger.info("数据路由执行: method=%s provider=%s fallback=%s args=%s", method, candidate, is_fallback, args)

# ❌ 差
logger.info(f"调用 {method}")  # 缺关键字段，难聚合
```

### Async

- I/O 函数用 `async def` + `await`。
- 同步阻塞函数（`akshare` / `requests`）用 `await asyncio.to_thread(...)` 包装。
- 并发用 `asyncio.gather(...)`，不用 `asyncio.wait`（除非真要等全部完成）。

---

## 测试规范

### 文件命名

- `tests/test_<module>.py` —— 测 `agents/stock_agents.py` 写 `tests/test_stock_agents.py`。

### 测试结构

```python
"""<模块> 单元测试 — 一句话目标。"""
from __future__ import annotations

import pytest
from module_under_test import function_or_class


# ── 测试组 1 ──────────────────────────────────────────

def test_xxx_behavior() -> None:
    """一句话描述测试目标。"""
    # arrange
    obj = SomeClass()
    # act
    result = obj.method()
    # assert
    assert result == expected
```

### Async 测试

```python
@pytest.mark.asyncio
async def test_async_xxx() -> None:
    result = await some_async_func()
    assert result is not None
```

（`pyproject.toml` 已开 `asyncio_mode = "auto"`，可以省略 `@pytest.mark.asyncio`，但显式标注更清晰。）

### 覆盖率

- 新增模块：核心路径覆盖率 >= 80%。
- 跑 `make test-cov` 看 `htmlcov/index.html`。

### 集成测试

- 标 `@pytest.mark.integration`，需要外网时用。
- 默认 `make test-fast` 排除，CI 跑全量。

---

## 提 PR 流程

### PR 模板

```markdown
## 改动背景
简述这个 PR 解决什么问题 / 实现什么功能。

## 改动列表
- [ ] feat: xxx
- [ ] refactor: xxx
- [ ] test: xxx

## 测试
- [ ] `make test` 全过
- [ ] `make lint` 无错误
- [ ] 新功能已加单测（覆盖率 >= 80%）

## 截图 / 日志（如适用）

## Checklist
- [ ] 已读 CONTRIBUTING.md
- [ ] 已读 [CHANGELOG.md](CHANGELOG.md) 确认无重复
- [ ] Commit message 遵循 Conventional Commits
```

### Review 流程

1. CI 自动跑 ruff + pytest
2. Maintainer review（一般 1-3 个工作日）
3. 通过后 squash merge 到 main

---

## Issue 报告

### Bug Report

```markdown
## 现象
简述 bug 表现。

## 复现步骤
1. xxx
2. xxx

## 期望
应该怎么。

## 实际
实际怎么。

## 环境
- Python: 3.10.x
- OS: Windows 11 / Ubuntu 22.04
- 分支: main @ commit xxx

## 日志 / 截图
贴关键错误。
```

### Feature Request

```markdown
## 动机
为什么需要这个功能。

## 设计提案
如果已有思路，简述实现路径。

## 替代方案
还有哪些备选。
```

---

## 许可

贡献即代表你同意按 [MIT License](LICENSE) 授权你的代码。
