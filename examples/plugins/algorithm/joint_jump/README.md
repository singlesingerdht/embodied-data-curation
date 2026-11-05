# 关节跳变（示例插件）

新算法的样板：照着它写，装上后 `edc batch` 的报告多一列“关节跳变”，并参与整条结论。阈值只作演示，没有实测依据。

## 查什么

每条手臂的关节角，相邻两帧变化最大的那个关节：超过 `exclude_rad`（默认 0.5 rad）判排除；有帧超过 `review_rad`（默认 0.2 rad）判复核；否则通过。阈值写在机器人 yaml 的 `checks.team_example__joint_jump`。

## 文件

- `algorithm.py`：接框架。`spec` 写名字、参数；`check()` 按类型找手臂、读关节角（已是 rad）、调用 method、把结果写成检查项 `Item`，并给格子写一句总结。
- `method.py`：方法本体，只做计算，不认识框架。
- `../pyproject.toml`：登记一行，`edc` 按它找到算法。

## 装、卸

```bash
pip install -e examples/plugins/algorithm     # 装上，edc batch 自动多跑这一项
pip uninstall edc-example-algorithm           # 卸掉
```

## 转正（成为内置算法）

1. 文件夹改名并搬进 `src/edc/algorithms/quality/algorithms/`，如 `Joint_Jump/`；
2. `spec` 的 id 改成和文件夹同名（`Joint_Jump`），yaml 里 `checks.` 下的名字跟着改；
3. 在 `quality/adapters/cpu.py` 的 `ORDER` 里加上它；
4. 删掉插件的 `pyproject.toml` 登记，卸掉插件包。

类名已经是 `Algorithm`、导入 method 用的是相对路径，所以代码不用改。
