"""datasource.types — 统一返回值类型定义。

所有 Provider 方法的返回值必须是以下类型之一：
- pd.DataFrame  — 批量/表格数据（ETL 入库场景）
- list[dict]    — 记录列表（实时/聚合场景）
- dict          — 单条记录或聚合结果

设计原则：
- 不引入额外容器类，保持与 pandas / JSON 生态天然兼容
- 类型别名仅供标注用，运行时不强制校验（由路由层 quality check 兜底）
"""
from __future__ import annotations

from typing import Any, Union

import pandas as pd

# 统一返回值类型
DataResult = Union[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]

# method_table 类型：method_name → {provider_name: provider_method_name}
MethodTable = dict[str, dict[str, str]]
