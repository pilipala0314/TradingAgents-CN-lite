"""conftest — 把 a-share-research-agent 根目录注入 sys.path。

pytest 默认不会自动把项目根加入 path；在没有 conftest.py 的情况下，
import 顶层的 `data_pipeline`/`agents` 等会失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
