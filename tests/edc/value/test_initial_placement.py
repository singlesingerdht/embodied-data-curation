"""首帧摆放：透视九宫格和报警规则（不加载模型）。"""
from edc.algorithms.value.algorithms.Initial_Placement_Assessment import method

CORNERS = ((.2, .3), (.8, .3))


def test_perspective_grid_cells():
    matrix = method.make_grid(CORNERS, 1.10, 1.25)
    assert method.to_cell(matrix, .5, .32) == (1, 0)   # 远边中间 -> 中上
    assert method.to_cell(matrix, .25, .35) == (0, 0)  # 远边左角附近 -> 左上
    assert method.to_cell(matrix, .5, 1.0) == (1, 2)   # 画面底部中间 -> 中下


def judge(monkeypatch, positions):
    """positions: {英文名: 框底边中点 (x, y)}；SOP 都要求在中上。"""
    import numpy as np
    monkeypatch.setattr(method, 'table_mask', lambda m, img: np.ones((10, 10), bool))
    monkeypatch.setattr(method, 'far_corners', lambda mask: CORNERS)
    monkeypatch.setattr(method, 'detect', lambda m, img, names: {
        n: (.9, [x - .01, y - .05, x + .01, y]) for n, (x, y) in positions.items() if n in names})
    names = {f'{n}位置': {'en': n, 'flat': False} for n in positions}
    return method.check(None, None, {k: '中上' for k in names}, names, 1.10, 1.25)


def test_two_cells_off_alarms(monkeypatch):
    objects, alarm = judge(monkeypatch, {'cup': (.5, 1.0)})
    assert alarm and objects[0]['actual_cell'] == '中下' and objects[0]['result'] == '报警'


def test_one_object_one_cell_off_passes_but_two_alarm(monkeypatch):
    assert not judge(monkeypatch, {'cup': (.25, .35)})[1]
    assert judge(monkeypatch, {'cup': (.25, .35), 'bowl': (.75, .35)})[1]


def test_flat_and_duplicate_names_are_not_judged(monkeypatch):
    monkeypatch.setattr(method, 'table_mask', lambda m, img: None)
    names = {'布位置': {'en': 'cloth', 'flat': True}, '碗1位置': {'en': 'bowl', 'flat': False},
             '碗2位置': {'en': 'bowl', 'flat': False}}
    assert method.check(None, None, {k: '中上' for k in names}, names, 1.1, 1.25) == (None, False)
