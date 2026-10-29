"""质检结果输出契约（qc-2.2）的类型定义——纯 model 层，零组装逻辑。

唯一依赖 indicators 注册表（枚举口径说明引用）；filters/pipeline/report/api/tasks
均可 import 本模块而不引入组装/落盘逻辑（修分层倒置：此前类型定义在 report.py，
filters/root_cause 只能反向依赖 report 层）。

对象树：
QualityResult（聚合根，JSON 顶层）
├── file_info: FileInfo               被检对象静态属性
├── sensors: list[str]                全部通道名
├── config: ConfigInfo → Thresholds   判定标准快照（实际生效阈值）
├── run: RunInfo → StageError         执行上下文（怎么跑的）
├── root_cause: RootCause → RootCauseEvent → Ref  派生分析（为什么有问题，Ref 回指 indicators）
└── indicators: list[IndicatorResult] 检测发现（唯一数据源，默认全量含 pass）
    ├── detail: IndicatorDetail（按指标族 TypedDict 联合）
    └── points: Point（按指标族 TypedDict 联合）
"""
from dataclasses import dataclass, field as _field
from typing import List, Optional, TypedDict, Union


# ===================== 输出契约强类型（qc-2.2） =====================
# detail/points 均按指标族强类型：TypedDict 是静态契约（纯注解、零运行时开销），
# 运行时检测产出直接透传；静态检查器据 code 判窄到具体族。


class _Truncatable(TypedDict, total=False):
    """points 超 200 条截断说明（可选键，出现即表示 points 被截断为前 200 条）。"""
    points_truncated: bool   # points 已截断为前 200 条
    points_total: int        # 截断前完整点位总数


class TimingDropExtra(_Truncatable):
    """时序-掉帧/缺帧（C1/I1/J1/A1）detail。"""
    gap_count: int      # 缺口数
    lost_frames: int    # 累计丢失帧
    drop_ratio: float   # 掉帧率


class TimingJitterExtra(TypedDict):
    """时序-抖动/帧率不稳（C2/I2/J2/A2）detail。"""
    level: str              # 抖动判定档（pass/warning/error）
    cv: float               # 帧间隔变异系数
    dt_mean_ms: float       # 帧间隔均值（毫秒）
    dt_std_ms: float        # 帧间隔标准差（毫秒）


class TimingOrderExtra(_Truncatable):
    """时序-乱序（C3/I3/J3/A3）detail。"""
    count: int              # 乱序条数
    max_backward_ms: float  # 最大回退量（毫秒）


class TimingDelayExtra(TypedDict):
    """时序-延迟尖峰（M5）detail。"""
    mean_ms: float      # 延迟均值（毫秒）
    p95_ms: float       # 延迟 P95（毫秒）
    p99_ms: float       # 延迟 P99（毫秒）
    spike_count: int    # 尖峰次数


class ContentExtra(_Truncatable):
    """内容-数值/图像/字段（I5/I6/I7/J5/J7/A5/A7 等）detail。"""
    count: int              # 事件总数（points 仅前 200 条，count 为全量）
    detail: dict            # 检查级 detail（键随检查变化，见 content_checks 事件定义）


class StructureExtra(TypedDict):
    """结构-文件/通道完整性（M3/M7/M8/C7 等）detail。

    键随结构检查变化（exists/readable/missing/empty/paired/corrupted_episodes/
    schema_drift/...）；完整键集见 structure_filter 各检查的 detail 定义。
    """
    pass


class CoverageExtra(TypedDict):
    """跨模态-时间覆盖（M2）detail。"""
    coverage_ratio: float   # 全部通道时间交集占比
    late_start: int         # 晚启动通道数
    early_end: int          # 早结束通道数


class AlignmentExtra(TypedDict):
    """跨模态-对齐（M1）detail。"""
    topic_pairs: int        # 参与对齐评估的通道对数


class StabilityExtra(TypedDict):
    """跨模态-时间戳稳定性（M6）detail。"""
    topic_count: int        # 评估通道数
    drift_count: int        # 线性漂移通道数
    jump_count: int         # 时钟跳变通道数
    unstable_count: int     # 抖动超标通道数
    anomaly_count: int      # 异常通道总数（drift+jump+unstable）
    drift_topics: list      # 漂移通道明细 [{topic, mode, drift_rate_ms_per_min}]
    jump_topics: list       # 跳变通道明细（同上）
    unstable_topics: list   # 抖动超标通道明细（同上）
    anomaly_topics: list    # 全部异常通道明细（drift+jump+unstable）


class EpisodesExtra(_Truncatable):
    """跨模态-切段一致性（M10）detail。"""
    segments: int           # 切段点总数（points 仅前 200 条）


class DedupExtra(TypedDict):
    """去重-重复消息/帧（C4/I4/J4/A4）detail。"""
    duplicate_ratio: float  # 重复占比
    duplicate_count: int    # 重复条数


# 指标检测结果数据联合类型（按 code 判窄到具体族）
IndicatorDetail = Union[TimingDropExtra, TimingJitterExtra, TimingOrderExtra,
                        TimingDelayExtra, ContentExtra, StructureExtra,
                        CoverageExtra, AlignmentExtra, StabilityExtra,
                        EpisodesExtra, DedupExtra]


class FramePoint(TypedDict):
    """逐帧问题定位（内容/结构类指标 point）。"""
    index: int              # 帧索引
    range: list             # [起始帧, 结束帧]
    time: float             # 事件时刻（秒）
    episode: str            # 所属 episode 文件名（lerobot 数据集；非数据集为空）


class GapPoint(TypedDict):
    """掉帧缺口定位（C1 族 point）。"""
    start: float            # 缺口起始（秒）
    end: float              # 缺口结束（秒）
    duration_ms: float      # 缺口时长（毫秒）
    lost_frames: int        # 该缺口丢失帧数
    isolated: bool          # 是否孤立单帧（降级判定）


class OrderPoint(TypedDict):
    """乱序定位（C3 族 point）。"""
    time: float             # 乱序帧时刻（秒）
    backward_ms: float      # 相对前帧回退量（毫秒）


class AlignmentPoint(TypedDict):
    """对齐明细（M1 point，逐通道对）。"""
    topic: str              # 通道名
    p95_delta_ms: float     # 相对参考通道 P95 偏差（毫秒）
    severity: str           # 该通道对对齐档


class StabilityPoint(TypedDict):
    """稳定性明细（M6 point，逐通道）。"""
    topic: str              # 通道名
    mode: str               # drift/jump/unstable
    drift_rate_ms_per_min: float  # 线性漂移率（毫秒/分钟）


class BoundaryPoint(TypedDict):
    """切段点定位（M10 point）。"""
    time: float     # 切段时刻（秒）
    topics: list    # 同窗口异常通道


# 逐点位明细联合类型（按指标族对应）
Point = Union[FramePoint, GapPoint, OrderPoint, AlignmentPoint,
              StabilityPoint, BoundaryPoint]


@dataclass
class IndicatorResult:
    """单条指标质检明细（指标 × 通道，indicators 列表元素）。

    indicator_code/name 契约（重构 v2）：indicator_code 为《数据筛选问题总表》指标编号（"M6"），
    唯一来源 indicators.registry.Indicator 枚举；name 为纯人读中文名（"时间戳漂移/时钟跳变"），
    不再内嵌编号（废除 drift(M6) 复合写法）。
    """
    topic_name: str = ""       # 归属通道名；scope != topic 时为空串
    scope: str = "topic"       # 指标粒度 topic|global：topic=单通道（topic_name 有效）；global=结构级/跨模态（topic_name 恒 ""）
    check: str = ""            # 子检查名（yaml 检测项，与 INDICATOR_ITEMS 键一致）：code 是族级归类，
                               # check 唯一定位（同 code 多条靠它区分，如 M8 的 file_integrity/format_recognition/lerobot_integrity）
    indicator_code: str = ""   # 指标编号（设计表 49 项之一；必填，来自 Indicator 枚举）
    modality: str = ""         # IR 规范模态（data/ir.py TopicInfo.modality）：image/imu/joint_state/
                               # cmd/pose/encoded/other；topic 级=该通道 IR 分类（与 indicator_code
                               # 族前缀 C/I/J/A 互证且更细），global 级（跨模态/结构）空串
    name: str = ""             # 纯人读中文名（枚举 name_zh）；表外自建指标为英文语义名
    level: str = "pass"        # 三档分级 pass|warning|error（指标层取该检查/事件最高档；dedup 的 info 归一为 pass）
    detail: Optional[IndicatorDetail] = None  # 该指标的【汇总统计】：共发生几次/总量/比例/判级依据
                                             # （如掉帧族= {gap_count: 4, lost_frames: 3, drop_ratio: 0.002}，
                                             #  键随指标族变化，见下方 *Extra TypedDict 定义）
    points: Optional[List[Point]] = None      # 该指标的【逐次发生明细】：一行=一次发生（如 4 处掉帧=4 行，
                                             # 每行 start/end/duration_ms/...）；序列化为列式 {cols, rows}，
                                             # 还原=dict(zip(cols,row))；上限 200 行，超出时 detail 带
                                             # points_truncated=true + points_total=真实总数（rows 为抽样）
    valid_count: Optional[int] = None         # 有效帧数（时序类 drop/out_of_order 等）；非时序类（内容/结构/跨模态）为 null
    duration_sec: str = ""                    # 通道时长（秒，字符串，业务端约定）；非时序类空串
    estimated_freq: str = ""                  # 估计频率（Hz，字符串）；非时序类空串

    def to_dict(self) -> dict:
        d = {"topic_name": self.topic_name, "scope": self.scope,
             "check": self.check, "indicator_code": self.indicator_code,
             "modality": self.modality, "name": self.name,
             "level": self.level, "valid_count": self.valid_count,
             "duration_sec": self.duration_sec, "estimated_freq": self.estimated_freq}
        if self.detail is not None:
            d["detail"] = self.detail
        if self.points:
            # 列式编码（契约默认）：[{k:v},...] -> {"cols":[...],"rows":[[v,...],...]}
            # 键写一次、值按列排，raw JSON -46%；还原 = dict(zip(cols, row))；自描述不依赖指标类型
            _cols = list(self.points[0].keys())
            d["points"] = {"cols": _cols,
                           "rows": [[p.get(c) for c in _cols] for p in self.points]}
        return d


@dataclass
class FileInfo:
    """文件基础信息（业务端「文件基础信息」类，file_info 字段）。"""
    path: str = ""            # 输入路径（文件或数据集目录）
    format: str = ""          # 真实格式：lerobot|parquet|hdf5|ros1_bag|ros2_bag|mcap|mp4|bvh
    size_bytes: Optional[int] = None  # 大小（字节）；文件夹输入=递归聚合总大小
    manufacturer: str = ""    # 厂商（profile.note 首段，；分隔前）；无 profile 为空串

    def to_dict(self) -> dict:
        return {"path": self.path, "format": self.format,
                "size_bytes": self.size_bytes, "manufacturer": self.manufacturer}


@dataclass
class Thresholds:
    """配置判定阈值 6 组（config.thresholds，**实际生效值**=defaults 基线+机型+per-topic 合并后）。

    各组键与 Config 类字段一一对应（dataclasses.asdict 序列化）；pipeline/dedup 组为
    流水线消费的非 dataclass 键（含默认值并入）。
    """
    timing: dict = _field(default_factory=dict)     # TimingConfig 全部字段（36 项）
    content: dict = _field(default_factory=dict)    # ContentConfig 全部字段（69 项）
    sync: dict = _field(default_factory=dict)       # SyncConfig 全部字段（18 项）
    structure: dict = _field(default_factory=dict)  # StructureConfig 全部字段（7 项）
    pipeline: dict = _field(default_factory=dict)   # episode_window_ms/min_topics/merge_sec/min_units（默认+覆盖）
    dedup: dict = _field(default_factory=dict)      # enabled/dedup_ratio_low/dedup_ratio_high（默认+覆盖）

    def to_dict(self) -> dict:
        return {"timing": self.timing, "content": self.content, "sync": self.sync,
                "structure": self.structure, "pipeline": self.pipeline, "dedup": self.dedup}


@dataclass
class ConfigInfo:
    """配置信息（业务端「配置信息」类：frequencies + thresholds）。"""
    frequencies: dict = _field(default_factory=dict)  # 通道→期望/注入频率（profile topics.<名>.expected_freq / 数据驱动 fps）
    thresholds: Thresholds = _field(default_factory=Thresholds)  # 实际生效阈值 6 组

    def to_dict(self) -> dict:
        return {"frequencies": self.frequencies, "thresholds": self.thresholds.to_dict()}


@dataclass
class StageError:
    """阶段级崩溃记录（timing/content 整段失败；伪指标不进 indicators 命名空间）。"""
    stage: str   # 阶段名（timing/content）
    error: str   # 异常信息


@dataclass
class RunInfo:
    """
        本次质检运行元信息（run 节点）——执行上下文，回答"这次怎么跑的"。
    """
    mode: str = ""
    """质检模式 fast|deep。
        fast=初筛（不解包：无数值内容/图像二层/字段采样）；deep=精筛（含 fast 全部+解包类）。
    """

    skipped_indicators: list = _field(default_factory=list)
    """被跳过的指标 code 列表（如 ["I5","I6","J5","M7"]）。
    来源=mode 限制 + profile indicators:{code:false} 开关；与 indicators 互补：
    出现在这里的 code 一定不在 indicators 中（"没检"），反之是"检了"（含 pass）。"""

    errors: list = _field(default_factory=list)
    """阶段级崩溃 List[StageError]（stage+error）。
    timing/content 整段失败时记录——伪指标不进 indicators（不污染 code 命名空间），
    但出现即表示对应段的 indicators 缺失不可信；正常运行为空列表。"""

    event_count: int = 0
    """内部事件点总数（timing/sync/content/structure 各段原始事件合计）。
    口径注意：一条指标可对应 N 个事件点（如一个通道 50 个缺口=1 条 C1 记录 50 个 point），
    因此 event_count ≥ len(indicators)，两者不可互换、不可相除。"""

    elapsed_sec: Optional[float] = None
    """检测总耗时（秒，含全部阶段；不含报告落盘 IO）。性能监控/成本核算用。"""

    def to_dict(self) -> dict:
        return {"mode": self.mode, "skipped_indicators": self.skipped_indicators,
                "errors": [{"stage": e.stage, "error": e.error} for e in self.errors],
                "event_count": self.event_count, "elapsed_sec": self.elapsed_sec}


@dataclass
class Ref:
    """指向 indicators 具体记录的关联引用（根因事件来源）。"""
    indicator_code: str       # 指标编号（与 IndicatorResult.indicator_code 对齐）
    topic_name: str           # 通道名
    point_index: Optional[int]  # 该指标 points 的第几条（0 基）；None = 指标级（无 points）


@dataclass
class RootCauseEvent:
    """根因事件（root_cause.events 元素）。"""
    kind: str                # 系统级: correlated_gap | periodic_gaps | drift_attributed
                             # 一级族归因: <code>_anomaly（如 j5_anomaly/j7_anomaly，见 filters/root_cause._CAUSE_RULES）
    time: Optional[float] = None  # 事件时刻（秒）；族归因无单一时刻为 None
    detail: dict = _field(default_factory=dict)   # 按 kind 的事件细节（时长/周期/归因等）
    sources: list = _field(default_factory=list)  # List[Ref]：贡献该事件的 indicators 点位/记录


@dataclass
class RootCause:
    """根因关联分析结果（顶层 root_cause 节点，质检结束后的纯后处理产出）。"""
    verdict: str = ""             # 综合归因文本
    events: list = _field(default_factory=list)   # List[RootCauseEvent]

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "events": [{
            "kind": e.kind, "time": e.time, "detail": e.detail,
            "sources": [{"indicator_code": s.indicator_code, "topic_name": s.topic_name,
                         "point_index": s.point_index} for s in e.sources]}
            for e in self.events]}


@dataclass
class QualityResult:
    """质检结果（业务端类「质检结果」，JSON 顶层结构 qc-2.2）。

    对外程序级结果 = 本类序列化：file_info → sensors → config → run → root_cause → indicators。
    顶层不含内部检测段（timing/sync/content 等）——细节经 indicators 的 detail/points 展平；
    内部段结构见 qc-1.1（不对外）。indicators 为唯一数据源（默认全量含 pass，自解释）；
    聚合统计由消费端基于 indicators 现算（算子不产出派生统计）。
    """
    schema_version: str = "qc-2.2"  # 输出契约版本（统计/数据库按版本兼容；qc-1.1 仅内部段结构）
    file_info: FileInfo = _field(default_factory=FileInfo)   # 文件基础信息（路径/格式/大小/厂商）
    sensors: list = _field(default_factory=list)             # 全部通道名（含未检测的传感器）
    config: ConfigInfo = _field(default_factory=ConfigInfo)  # 配置：frequencies + thresholds 6 组（实际生效值）
    run: RunInfo = _field(default_factory=RunInfo)           # 运行元信息（mode/skipped/errors/event_count/elapsed_sec）
    root_cause: RootCause = _field(default_factory=RootCause)  # 根因关联分析（质检后处理产出，sources 关联 indicators）
    indicators: list = _field(default_factory=list)          # List[IndicatorResult] 明细（唯一数据源，默认全量）
    saved_files: list = _field(default_factory=list)         # 落盘 JSON 文件路径（save=True 时）

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "file_info": self.file_info.to_dict(),
            "sensors": self.sensors,
            "config": self.config.to_dict(),
            "run": self.run.to_dict(),
            "root_cause": self.root_cause.to_dict(),
            "indicators": [z.to_dict() for z in self.indicators],
            "saved_files": self.saved_files,
        }
