# 接入新算法（样板：关节跳变）

新算法照这个文件夹写：样板 `joint_jump/` 查关节跳变，带上后 `edc batch` 的报告多一列“关节跳变”，并参与整条结论。阈值只作演示，没有实测依据。

## 查什么

每条手臂的关节角，相邻两帧变化最大的那个关节：超过 `exclude_rad`（默认 0.5 rad）判排除；有帧超过 `review_rad`（默认 0.2 rad）判复核；否则通过。阈值写在机器人 yaml 的 `checks.team_example__joint_jump`。

## 文件

- `joint_jump/algorithm.py`：接框架。`spec` 写名字、参数；`check()` 按类型找手臂、读关节角（已是 rad）、调用 method、把结果写成检查项 `Item`，并给格子写一句总结。
- `joint_jump/method.py`：方法本体，只做计算，不认识框架。

## 从零写一个新算法

复制 `joint_jump/` 改名（如 `my_check/`），照下面改。

**结构**：`algorithm.py` 最少这些；`method.py` 只放计算（输入数组和阈值，输出数字和结论，不 import edc）。

```python
from edc.algorithms.base import QualityAlgorithm, Item
from edc.algorithms.registry import AlgorithmSpec
from .method import compute                        # method.py 里的计算函数

class Algorithm(QualityAlgorithm):
    spec = AlgorithmSpec('团队__算法', '0.1.0', '报告表头的中文名', '作者', '一句话说明',
                         presets=('quality_cpu',))   # 随 edc batch 跑
    def check(self, episode, params):
        ...                                        # 找通道 → 读数据 → compute → 写成 Item
        return [Item('左臂关节', verdict='PASS', reason='...')]

plugin = Algorithm()                               # 框架按 plugin 这个名字找算法
```

**输入**：`check(episode, params)` 拿到两样。
- `params`：参数。默认值写在 `spec` 的 `parameter_schema`，用户在机器人 yaml 的 `checks.<id>` 下改。
- `episode`：一条数据。
  - `episode.channels`：所有通道 `{名字: 通道}`。按类型挑，不写死名字：`c.modality`（`joint_space` 关节、`pose` 位姿、`rgb` 图像等）、`c.source_kind`（如 `feedback` 反馈）、`c.fields`（如 `position`）。
  - `episode.read_signal(名字, 字段)`：数值数组（帧数 × 维度），已换成国际单位（rad、m、s）。
  - `episode.iter_frames(名字)`：逐帧读图像。
  - `episode.raw(名字)`：原始记录，如帧数 `count`、时间戳 `acquisition_timestamps_ns`。
  - `episode.profile`：机型信息（来自机器人 yaml），`profile['labels']` 是通道的中文名。

**输出**：返回 `Item` 列表，每个被查的对象（一条手臂、一个相机）一个。
- `target`：查的对象，报告里显示，如“左臂关节”；
- `verdict`：`PASS` 通过 / `REVIEW` 复核 / `SALVAGE` 可救 / `EXCLUDE` 排除；
- `reason`：一句话原因，不是 PASS 时必须写；
- 可选：`metrics` 判断用的数字，`evidence` 出问题的位置（如 `[{'time_s': 12.4}]`），`remedy` 可救时怎么修；
- 没法查（如没有这类数据）：`Item('本条', status=NOT_APPLICABLE, reason='原因')`，不写 verdict。

想在报告格子里写一句总结，改为返回 `AlgorithmResult(self.spec.id, items, '一句话')`。整条数据的结论由框架按各项结论统一汇总，算法不用管。

## 需要新的安装包

以 torch 为例（numpy 框架自带，不用管）：
1. `spec` 里加 `dependencies=('torch',)`，写 import 时的名字。框架运行前检查，缺了只有这一列显示“没跑完”、原因“缺依赖 torch”，其他算法照常跑。
2. `import torch` 写在函数里面，不写在文件开头；否则缺包时加载插件就报错，整条命令都跑不起来。
3. 装进项目环境：`.venv/bin/pip install torch`（这里写安装名，可能和 import 名不同，如 `opencv-python-headless` 对应 `cv2`）。`.venv` 是大家共用的，装之前跟组里说一声，并记下哪个算法装了哪些包；不用了照记录 `.venv/bin/pip uninstall`。

## 试用（在项目目录下执行，不用安装）

命令前加 `EDC_PLUGINS=插件文件夹`，框架就直接读这个文件夹的 `algorithm.py`；改了代码下次运行就生效。只影响带这个开关的命令，别人的 `edc batch` 不受影响。多个插件用冒号隔开。

```bash
# 确认找到：列表最后多一行“team_example__joint_jump  关节跳变”
EDC_PLUGINS=examples/plugins/algorithm/joint_jump .venv/bin/edc algorithms

# 带插件跑：报告多一列“关节跳变”
EDC_PLUGINS=examples/plugins/algorithm/joint_jump .venv/bin/edc batch 数据 --robot configs/robots/机型.yaml --output reports/名字.html
```

不想要了：不加前缀就不跑，直接删文件夹即可，`.venv` 里没有留任何东西。

定型后要长期给大家用：转正。

改阈值：在机器人 yaml 末尾加下面几行（yaml 改过要重新签名，试用时可复制一份放在同一文件夹里改）：

```yaml
checks:
  team_example__joint_jump:
    review_rad: 0.1
```

不想某个机型跑它：yaml 里写 `checks.skip: [team_example__joint_jump]`。不想进日常 batch、只手动跑：去掉 `spec` 的 `presets`，用 `edc run 数据 --robot 机型 --algorithm team_example__joint_jump`。

## 转正（成为内置算法）

1. 文件夹改名并搬进 `src/edc/algorithms/quality/algorithms/`，如 `Joint_Jump/`；
2. `spec` 的 id 改成和文件夹同名（`Joint_Jump`），yaml 里 `checks.` 下的名字跟着改；
3. 在 `quality/adapters/cpu.py` 的 `ORDER` 里加上它；
4. 不再加 `EDC_PLUGINS` 前缀，删掉原插件文件夹。

转正后它成了 edc 自己的代码（`ORDER` 里写着它），不需要任何登记或前缀。类名已经是 `Algorithm`、导入 method 用的是相对路径，所以代码不用改。
