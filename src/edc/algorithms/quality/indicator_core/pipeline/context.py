"""InternalResult：流水线内部结果的类型化上下文（替代裸 dict + 魔法键）。

设计：
- 六段结果（structure/timing/sync/dedup/content）+ 边界/模式/跳过列表为显式字段
  （根因分析已后置为质检结束后的 indicators 后处理器，不再经 ctx 流转）
- 兼容层：实现 Mapping 协议（.get()/[]/in），旧键名（_topic_modality/_config 等）
  落入 misc，report/annotate 无需改动即可消费
- 阶段函数（runner._stage_*）只读写 ctx 字段；异常进 ctx.errors（不再吞）
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


@dataclass
class InternalResult:
    file: str
    format: str = "unknown"
    error: Optional[str] = None
    mode: str = "deep"
    unpack: bool = True
    elapsed_sec: Dict[str, float] = field(default_factory=dict)
    scenario: Optional[str] = None
    profile: Optional[Dict[str, Any]] = None
    skipped_indicators: List[str] = field(default_factory=list)
    # 六段结果
    structure: Any = None
    timing: Any = None
    sync: Any = None
    dedup: Any = None
    content: Any = None
    episode_boundaries: Any = None
    # 内部附加（原魔法键：_topic_modality/_config/structure_partial/...）
    misc: Dict[str, Any] = field(default_factory=dict)

    # ---- Mapping 兼容层（report/annotate 继续按 dict 消费） ----
    def _resolve(self, key: str) -> Any:
        if key in self.__dataclass_fields__:
            return getattr(self, key)
        return self.misc.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        v = self._resolve(key)
        return default if v is None and key not in self.__dataclass_fields__ and key not in self.misc else v

    def __getitem__(self, key: str) -> Any:
        if key in self.__dataclass_fields__:
            return getattr(self, key)
        return self.misc[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self.__dataclass_fields__:
            setattr(self, key, value)
        else:
            self.misc[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.__dataclass_fields__ or key in self.misc

    def keys(self):
        return list(self.__dataclass_fields__.keys()) + list(self.misc.keys())

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())
