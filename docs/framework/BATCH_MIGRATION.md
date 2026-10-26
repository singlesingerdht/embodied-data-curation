# 批处理与使用边界

公共命令使用 edc，安装包为 embodied-data-curation；总配置见 [CONFIGURATION](CONFIGURATION.md)。批处理转发命令只支持 workers=1；不接受 ndjson 作为恢复记录。恢复必须依据 catalog.json 中内容、参数、版本及证据匹配的 run。

任意片段调度、分布式处理和非 identity 变换尚未支持；未知单位不能用来运行依赖具体物理量的算法。各格式、机器人与方法的支持分别验收。结果协议保留 dqs_record_v1 等固定标识，历史证据不改写。
