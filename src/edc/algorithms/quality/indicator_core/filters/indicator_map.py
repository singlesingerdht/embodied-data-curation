"""兼容 shim：指标↔参数映射已迁移至 indicators/params.py（code 口径）。

保留本模块以兼容旧 import 路径；新代码请 import edc.algorithms.quality.indicator_core.indicators.params。
"""
from ..indicators.params import (  # noqa: F401
    all_indicator_params, validate_mapping, config_dump,
    system_params, INDICATOR_ITEMS, PIPELINE_INDICATOR_PARAMS,
)
