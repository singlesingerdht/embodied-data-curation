"""Build the editable EDC research deck and bilingual slide previews.

Documentation tooling only; no EDC runtime dependencies are changed.
Requires python-pptx, Pillow, PyMuPDF, LibreOffice and WenQuanYi Zen Hei.
Run: python docs/presentations/build_deck.py --render-dir /path/to/previews
Paper results are attributed in slide footnotes and speaker notes.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFont
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_TICK_MARK
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt


ROOT = Path(__file__).resolve().parents[2]
W, H = 1600, 900
FONT = "WenQuanYi Zen Hei"
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
INK, MUTED = "19394E", "647B90"
TEAL, BLUE, AMBER, RED = "098F85", "3267D6", "B57121", "BC5555"
PALE, BLUE_BG, TEAL_BG, WARM, BORDER = "F5F8FA", "EFF4FF", "EAF7F4", "FFF8EE", "DCE7ED"
STEAM = "https://arxiv.org/html/2606.29834v2"
ROBODROP = "https://arxiv.org/html/2609.10021v1"
FLORA = "https://arxiv.org/abs/2610.31779"


def u(px):
    return Inches(px / 100)


def rgb(hex_color):
    return RGBColor.from_string(hex_color)


def wrap(text, width, size):
    font = ImageFont.truetype(FONT_PATH, size * 4)
    result = []
    for raw in text.split("\n"):
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9%._/+\-]*|\s+|.", raw)
        line = ""
        for token in tokens:
            if line and font.getlength(line + token) / 4 > width - 5:
                result.append(line.rstrip())
                line = token.lstrip()
            else:
                line += token
        result.append(line)
    return result


class Deck:
    def __init__(self):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = u(W), u(H)
        self.prs.core_properties.title = "EDC — 从可信表达，到目标对齐"
        self.prs.core_properties.subject = "框架能力、论文支撑与自主数据整理路线"
        self.prs.core_properties.author = "EDC"
        self.slide = None

    def rect(self, x, y, w, h, fill="FFFFFF", line=BORDER, radius=True):
        kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
        s = self.slide.shapes.add_shape(kind, u(x), u(y), u(w), u(h))
        if radius:
            s.adjustments[0] = 0.035
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(fill)
        if line:
            s.line.color.rgb = rgb(line)
            s.line.width = Pt(0.85)
        else:
            s.line.fill.background()
        s._element.spPr.append(OxmlElement("a:effectLst"))
        for effect in s._element.xpath("./p:style/a:effectRef"):
            effect.set("idx", "0")
        return s

    def text(self, value, x, y, w, h, size=24, color=INK, bold=False,
             align=PP_ALIGN.LEFT, url=None):
        lines = wrap(value, w, size)
        needed = len(lines) * size * 1.24
        if needed > h + 4:
            raise ValueError(f"Text overflow on slide {len(self.prs.slides)}: {value!r} ({needed} > {h})")
        box = self.slide.shapes.add_textbox(u(x), u(y), u(w), u(h))
        tf = box.text_frame
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.vertical_anchor = MSO_ANCHOR.TOP
        tf._txBody.bodyPr.set("anchorCtr", "0")
        for i, line in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            p.space_before = p.space_after = Pt(0)
            p.line_spacing = 1.15
            r = p.add_run()
            r.text = line
            r.font.name, r.font.size = FONT, Pt(size * 0.72)
            r.font.bold, r.font.color.rgb = bold, rgb(color)
            ea = OxmlElement("a:ea")
            ea.set("typeface", FONT)
            r._r.get_or_add_rPr().append(ea)
            if url:
                r.hyperlink.address = url
        return box

    def line(self, x1, y1, x2, y2, color=BORDER, width=1.2, arrow=False):
        s = self.slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, u(x1), u(y1), u(x2), u(y2))
        s.line.color.rgb, s.line.width = rgb(color), Pt(width)
        s._element.spPr.append(OxmlElement("a:effectLst"))
        for effect in s._element.xpath("./p:style/a:effectRef"):
            effect.set("idx", "0")
        if arrow:
            end = OxmlElement("a:tailEnd")
            end.set("type", "triangle")
            s.line._get_or_add_ln().append(end)
        return s

    def pill(self, value, x, y, w, color=TEAL, fill=TEAL_BG, size=18):
        self.rect(x, y, w, 34, fill, None)
        self.text(value, x+8, y+5, w-16, 28, size, color, align=PP_ALIGN.CENTER)

    def header(self, kicker, title, subtitle, page, state):
        self.slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        self.slide.background.fill.solid()
        self.slide.background.fill.fore_color.rgb = rgb("FFFFFF")
        self.line(64, 47, 92, 47, TEAL, 2)
        self.text(kicker, 105, 33, 1180, 32, 17, TEAL)
        self.text(title, 64, 79, 1350, 68, 42, bold=True)
        self.text(subtitle, 64, 150, 1440, 58, 22, MUTED)
        self.text(f"{page:02d} / 06", 1420, 37, 116, 30, 18, MUTED, align=PP_ALIGN.RIGHT)
        self.pill(state, 1366, 92, 170, size=17)

    def footer(self, source, scope, url=None):
        self.line(64, 821, 1536, 821)
        self.text(source, 64, 835, 1470, 26, 15, MUTED, url=url)
        self.text(scope, 64, 862, 1470, 26, 15, MUTED)
        self.slide.notes_slide.notes_text_frame.text = source + "\n" + scope + ("\n" + url if url else "")

    def step(self, n, title, body, x, y, w, h=160, fill="FFFFFF"):
        self.rect(x, y, w, h, fill)
        self.rect(x+22, y+24, 30, 30, TEAL, None)
        self.text(str(n), x+27, y+25, 20, 28, 19, "FFFFFF", align=PP_ALIGN.CENTER)
        self.text(title, x+65, y+23, w-88, 68, 25, bold=True)
        self.text(body, x+24, y+85, w-48, h-96, 23, MUTED)

    def chart(self, x, y, w, h, categories, values):
        data = CategoryChartData()
        data.categories = categories
        data.add_series("平均成功率", values)
        chart = self.slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,
                                           u(x), u(y), u(w), u(h), data).chart
        chart.has_legend = False
        chart.has_title = False
        chart.font.name, chart.font.size = FONT, Pt(14)
        chart.font.color.rgb = rgb(MUTED)
        axis = chart.value_axis
        axis.minimum_scale, axis.maximum_scale, axis.major_unit = 0, 100, 25
        axis.has_major_gridlines = True
        axis.major_gridlines.format.line.color.rgb = rgb(BORDER)
        axis.tick_labels.number_format = '0"%"'
        axis.tick_labels.font.size = Pt(12)
        axis.major_tick_mark = axis.minor_tick_mark = XL_TICK_MARK.NONE
        axis.format.line.fill.background()
        chart.category_axis.major_tick_mark = chart.category_axis.minor_tick_mark = XL_TICK_MARK.NONE
        chart.category_axis.format.line.fill.background()
        chart.category_axis.tick_labels.font.size = Pt(14)
        plot = chart.plots[0]
        plot.gap_width = 125
        plot.has_data_labels = True
        labels = plot.data_labels
        labels.position = XL_LABEL_POSITION.OUTSIDE_END
        labels.show_value = True
        labels.number_format = '0.0"%"'
        labels.font.name, labels.font.size, labels.font.bold = FONT, Pt(17), True
        labels.font.color.rgb = rgb(INK)
        for i, point in enumerate(chart.series[0].points):
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = rgb("B8C9DA" if i == 0 else TEAL)
            point.format.line.fill.background()


def overview(d, english=False):
    if english:
        d.header("EDC / FROM TRUSTWORTHY DATA TO GOAL-ALIGNED LEARNING",
                 "Let user goals drive how data evolves",
                 "Reliable data → human experience as learning signals → model feedback that guides selection",
                 1, "Framework vision")
    else:
        d.header("EDC / 从可信表达，到目标对齐", "让需求决定数据如何进化",
                 "数据可被正确解释 → 人的任务理解成为学习信号 → 模型反馈连接学习与人的目标", 1, "框架愿景")
    for x, w, fill in [(64, 220, "FFFFFF"), (314, 470, "FFFFFF"), (814, 320, WARM), (1164, 372, "FFFFFF")]:
        d.rect(x, 220, w, 442, fill)
    d.text("User needs" if english else "用户需求", 86, 244, 178, 38, 25, bold=True)
    user_items = [("Task", "What to achieve?"), ("Target policy", "Who is learning?"), ("Quality & budget", "What must be met?")] if english else [
        ("目标任务", "希望完成什么"), ("目标策略", "希望谁来学习"), ("质量与预算", "必须满足哪些条件")]
    for i, (label, desc) in enumerate(user_items):
        y = 305 + 100*i
        d.text(label, 86, y, 178, 39, 23, bold=True)
        d.text(desc, 86, y+40, 178, 52, 19, MUTED)
        if i < 2:
            d.line(86, y+84, 260, y+84)
    d.pill("Start from goals" if english else "以需求确定整理方向", 82, 611, 184, BLUE, BLUE_BG, 17)
    d.text("Three layers of assessment" if english else "三层递进的数据评估", 338, 244, 420, 38, 25, bold=True)
    layers = [
        ("01  Data quality", "Available", "Trustworthy inputs across robots", "Shared checks; explicit data meaning", BLUE, BLUE_BG),
        ("02  Task value", "Planned", "Turn task understanding into signals", "Task progress, key actions and recovery", TEAL, TEAL_BG),
        ("03  Policy value", "Planned", "Bridge human goals and model learning", "Use model feedback to guide selection", TEAL, TEAL_BG),
    ] if english else [
        ("01  数据质量 · Data-centric", "当前能力", "让不同机器人的数据可被正确解释", "统一输入，复用质检；确保表达可信", BLUE, BLUE_BG),
        ("02  任务价值 · Task-aware", "研究待接入", "把人类理解，转成机器的学习信号", "发现关键动作、任务进展与纠错经验", TEAL, TEAL_BG),
        ("03  策略价值 · Policy-aware", "研究待接入", "用模型反馈，连接人的目标与机器学习", "判断这些经验是否帮助目标模型学习", TEAL, TEAL_BG),
    ]
    for i, (label, status, main, detail, color, fill) in enumerate(layers):
        y = 299 + 114*i
        d.rect(334, y, 430, 104, fill, None)
        d.text(label, 350, y+10, 294, 30, 20, color, True)
        d.text(status, 643, y+12, 106, 27, 16, color, align=PP_ALIGN.RIGHT)
        d.text(main, 350, y+42, 398, 32, 21, bold=True)
        d.text(detail, 350, y+75, 398, 27, 18, MUTED)
    d.text("Adapt the curation" if english else "自主选择与数据改进", 838, 244, 272, 38, 25, bold=True)
    d.pill("Long-term goal" if english else "长期目标 · 待实现", 838, 289, 236, AMBER, "FBEFD9", 18)
    future = [("Update selection rules", "Task needs and model feedback"), ("Choose methods", "Algorithms, parameters and order"), ("Improve the data", "Annotate, edit, then recheck")] if english else [
        ("制定与调整规则", "把需求转成可检查的选择依据"), ("选择与调整算法", "方法组合、参数与执行顺序"), ("自动改进数据", "标注、编辑、增强，再检查质量")]
    for i, (label, desc) in enumerate(future):
        y = 340 + 88*i
        d.text(label, 838, y, 272, 34, 23, bold=True)
        d.text(desc, 838, y+38, 272, 46, 18, MUTED)
    d.text("Develop and test algorithms" if english else "进一步探索新算法的生成与验证", 838, 617, 272, 39, 18, AMBER)
    d.text("Desired outcomes" if english else "目标产出：可靠且值得学", 1188, 244, 324, 40, 25, bold=True)
    outcomes = [("Reliable records", "Meet required quality conditions"), ("Useful experience", "Preserve task-relevant supervision"), ("Better-matched data", "Test value for the target policy")] if english else [
        ("满足质量要求", "数据能够正确表达观察与动作"), ("保留有用经验", "任务推进与纠错都有学习价值"), ("匹配目标模型", "通过学习反馈与任务表现检验")]
    for i, (label, desc) in enumerate(outcomes):
        y = 309 + 88*i
        d.text(label, 1188, y, 324, 35, 23, bold=True)
        d.text(desc, 1188, y+39, 324, 32, 19, MUTED)
    d.line(1188, 577, 1512, 577)
    d.text("Data + evidence + feedback" if english else "数据引用＋判断证据＋效果反馈", 1188, 600, 324, 40, 21, TEAL, True)
    for x1, x2 in [(289, 308), (789, 808), (1139, 1158)]:
        d.line(x1, 444, x2, 444, MUTED, 1.6, True)
    d.line(1350, 670, 1350, 715, TEAL, 1.6)
    d.line(1350, 715, 549, 715, TEAL, 1.6)
    d.line(549, 715, 549, 670, TEAL, 1.6, True)
    d.rect(595, 690, 720, 50, "FFFFFF", None)
    d.text("Feedback refines the next round of curation" if english else "以任务表现与训练反馈，驱动下一轮数据整理", 615, 699, 680, 36, 22, TEAL, True, PP_ALIGN.CENTER)
    d.rect(64, 753, 1472, 49, PALE, None)
    d.text("Available today: data ingestion · nine quality checks · evidence · selected-data export" if english else
           "当前工程基础：多格式与多机器人接入  ·  九项质量检查  ·  证据记录  ·  筛选导出", 88, 764, 1424, 34, 22, BLUE)
    d.footer("Research: STEAM · MGT-QV · RoboDrop; DemoTempo / FLORA inform further work." if english else
             "研究支撑：STEAM、MGT-QV、RoboDrop；数据改进与自动标注参考 DemoTempo、FLORA 等方向。",
             "Users choose algorithms today. Research integration and autonomous iteration remain future work." if english else
             "状态说明：当前由用户选择算法与参数；价值方法、自动规则调整和完整自主循环仍待接入与验证。")


def quality(d):
    d.header("DATA-CENTRIC / 可信表达", "换一个机器人，复用一套质检方法",
             "先让数据能够被机器正确读取与解释，再讨论其中有什么值得学习。", 2, "当前框架能力")
    d.step(1, "不同机器人与记录方式", "X1 Pro · MCAP\n乐聚 KUAVO · ROS bag\n珞石双臂 · LeRobot", 64, 230, 442, 225)
    d.step(2, "用配置说清数据含义", "对应哪个部件、什么物理量\n使用什么单位、哪一种时间\n本次检查哪些输入", 579, 230, 442, 225, BLUE_BG)
    d.step(3, "复用检查与证据记录", "相同的算法输入规则\n各机器人的适用参数\n问题位置、原因与来源", 1094, 230, 442, 225)
    d.line(520, 342, 563, 342, MUTED, 1.5, True)
    d.line(1034, 342, 1078, 342, MUTED, 1.5, True)
    d.rect(64, 485, 710, 290)
    d.text("为什么必须先检查？", 90, 508, 658, 42, 28, bold=True)
    for y, title, desc in [(567, "输入缺失", "训练所需的观察或动作不存在"), (631, "数值无法解释", "NaN、错误单位等破坏训练输入"), (695, "观察与动作错配", "记录表达了错误的操作关系")]:
        d.text(title, 90, y, 186, 35, 23, BLUE, True)
        d.text(desc, 286, y, 457, 58, 22, MUTED)
    d.rect(800, 485, 736, 290, TEAL_BG)
    d.text("复用的方法，明确的边界", 826, 508, 680, 42, 28, bold=True)
    d.text("九项质量检查 → 原始指标与证据 → 筛选导出", 826, 568, 680, 42, 25, TEAL, True)
    d.text("复用检查逻辑；机器人语义、限值与时钟仍需确认。\n通过表示满足已检查条件，策略收益另行验证。", 826, 632, 674, 104, 24, MUTED)
    d.footer("工程依据：仓库机器人配置、docs/how-to/DATA_AND_ROBOT.zh-CN.md、docs/reference/ALGORITHMS.zh-CN.md。",
             "不把能打开文件等同于完整支持；当前配置的适用范围见专题。原始数据只读，输出保留来源。")


def steam(d):
    d.header("TASK-AWARE / STEAM", "把人类理解，变成机器的学习信号",
             "专家示教提供操作经验；自监督进度建模把经验转成细粒度的训练监督。", 3, "论文方法与证据")
    d.step(1, "专家经验提供参考", "语言任务＋专家示教帧对\n由相对时间关系构造监督", 64, 230, 462, 178)
    d.step(2, "学习局部任务进展", "时间预测集成 → 帧级优势\n减少逐帧人工进度标注", 569, 230, 462, 178, TEAL_BG)
    d.step(3, "信号用于策略学习", "评估混合质量轨迹\n结合 CFGRL 改进策略", 1074, 230, 462, 178)
    d.line(535, 318, 559, 318, MUTED, 1.5, True)
    d.line(1040, 318, 1064, 318, MUTED, 1.5, True)
    d.rect(64, 436, 650, 355)
    d.text("一次失败，不等于没有可学经验", 90, 462, 600, 42, 27, bold=True)
    for x, label, fill, color in [(90, "抓偏", "FCEFEF", RED), (291, "调整", WARM, AMBER), (492, "抓取成功", TEAL_BG, TEAL)]:
        d.rect(x, 529, 172, 65, fill, None)
        d.text(label, x+10, 548, 152, 37, 25, color, True, PP_ALIGN.CENTER)
    d.line(270, 562, 285, 562, MUTED, 1.5, True)
    d.line(471, 562, 486, 562, MUTED, 1.5, True)
    d.text("发现有用操作与纠错经验\n让任务理解服务于策略能力提升", 90, 625, 598, 90, 27, TEAL, True)
    d.text("过程为概念示例；接近人工判断与学习收益分别验证。", 90, 744, 598, 30, 18, MUTED)
    d.rect(742, 436, 794, 355)
    d.text("四项真实机器人任务 · 平均成功率", 770, 462, 738, 40, 26, bold=True)
    d.chart(780, 511, 716, 230, ["最佳基线", "STEAM + CFGRL"], [58.8, 83.5])
    d.pill("论文组合方法：+24.7 个百分点", 902, 748, 482, TEAL, TEAL_BG, 20)
    d.footer("来源：STEAM, Self-Supervised Temporal Ensemble Advantage Modeling for Real-World Robot Learning · arXiv:2606.29834v2。",
             "数值为论文组合训练方法结果，不能归因于单独筛选；STEAM 尚未集成到 EDC。", STEAM)


def local_curation(d):
    d.header("TASK-AWARE / MGT-QV 与局部治理", "局部问题，用局部证据治理",
             "把任务理解落实到可核查的片段与完整训练窗口，保留有用经验及判断依据。", 4, "研究稿与接入方向")
    d.step(1, "定位任务阶段", "任务语义与操作顺序\n定位停滞、失败等候选区间", 64, 230, 462, 178)
    d.step(2, "核验多模态证据", "视觉、关节与末端反馈\n结合上下文确认问题", 569, 230, 462, 178, TEAL_BG)
    d.step(3, "形成局部处理依据", "保留有效片段\n明确排除原因与待复核证据", 1074, 230, 462, 178)
    d.line(535, 318, 559, 318, MUTED, 1.5, True)
    d.line(1040, 318, 1064, 318, MUTED, 1.5, True)
    d.rect(64, 436, 810, 355)
    d.text("检查完整训练窗口：观察历史＋未来动作", 90, 462, 758, 42, 27, bold=True)
    d.rect(491, 541, 100, 179, "FCEFEF", None, False)
    d.text("已确认的问题区间", 443, 514, 216, 28, 18, RED, align=PP_ALIGN.CENTER)
    d.line(168, 553, 808, 553, MUTED, 1, True)
    for label, x, y, status, color in [("A", 198, 580, "保留", TEAL), ("B", 386, 634, "排除", RED), ("C", 620, 688, "保留", TEAL)]:
        d.text(label, 112, y-4, 50, 32, 22, bold=True)
        d.rect(x, y, 52, 25, BLUE, None, False)
        d.rect(x+52, y, 110, 25, "B6CDEE", None, False)
        d.text(status, 791, y-3, 58, 31, 18, color)
    d.text("深色：观察历史    浅色：未来动作    B 的动作窗口与问题相交", 90, 750, 754, 30, 18, MUTED)
    d.rect(902, 436, 634, 355, PALE)
    d.text("这为框架补上什么？", 930, 462, 578, 42, 27, bold=True)
    d.text("从整条取舍，推进到局部经验的保留。\n将判断关联到源片段与完整训练窗口。\n让人能复核，后续变换能追溯。", 930, 528, 572, 152, 25, INK)
    d.text("EDC 接入时需验证来源映射、上下文完整性\n和变换后质量，再检验是否改善策略学习。", 930, 701, 572, 68, 21, MUTED)
    d.footer("依据：MGT-QV 研究稿介绍与用户提供的研究材料；窗口图为概念示意，未展示尚未核对原稿的实验数字。",
             "局部治理为待接入研究方向；当前 EDC 不提供任意片段调度，不能将图示视为已交付能力。")


def robodrop(d):
    d.header("POLICY-AWARE / ROBODROP", "用模型反馈，连接人的目标与机器的学习",
             "从“这段经验有用吗”，进一步问“它是否推动当前模型朝目标学习”。", 5, "论文方法与证据")
    d.step(1, "人的目标与参考经验", "任务要求\n干净参考数据", 64, 230, 342, 178)
    d.step(2, "目标模型的训练反馈", "预热训练中提取梯度\n语义与视觉匹配参考", 441, 230, 342, 178)
    d.step(3, "比较局部学习方向", "候选与参考的梯度相容性\n作为数据价值代理", 818, 230, 342, 178, TEAL_BG)
    d.step(4, "选择并验证数据", "样本分数聚合到轨迹\n训练与任务评测验证", 1195, 230, 341, 178)
    for a, b in [(415, 432), (792, 809), (1169, 1186)]:
        d.line(a, 318, b, 318, MUTED, 1.5, True)
    d.rect(64, 436, 650, 355)
    d.text("连接人类理解与机器学习的桥梁", 90, 462, 598, 42, 27, bold=True)
    d.text("人提供目标与参考经验", 90, 530, 598, 38, 27, BLUE, True)
    d.text("模型反馈哪些数据契合当前学习方向", 90, 587, 598, 42, 26, TEAL, True)
    d.text("据此调整选择，再用任务表现检验", 90, 644, 598, 42, 26, bold=True)
    d.text("减少逐批真机筛选评估的需求，最终收益仍需实测。", 90, 744, 598, 30, 18, MUTED)
    d.rect(742, 436, 794, 355)
    d.text("真实机器人实验 · 平均任务成功率", 770, 462, 738, 40, 26, bold=True)
    d.chart(780, 511, 716, 230, ["未筛选", "RoboDrop 筛选"], [35.0, 67.5])
    d.pill("论文所报告条件：+32.5 个百分点", 902, 748, 482, TEAL, TEAL_BG, 20)
    d.footer("来源：RoboDrop, Curating VLA Post-Training Data via Local Gradient Compatibility · arXiv:2609.10021v1。",
             "相容性是代理分数，不等于真实收益或完整意图理解；需要干净参考与预热计算，方法尚未集成到 EDC。", ROBODROP)


def autonomy(d):
    d.header("RESEARCH ROADMAP / 持续自主改进", "论文提供能力，框架将它们连接成改进循环",
             "长期目标：用户给出任务与模型需求，框架自主选择路线，并用独立反馈验证每次改进。", 6, "长期目标 · 待实现")
    xs = [64, 366, 668, 970, 1272]
    stages = [("目标与要求", "质量约束\n任务、模型与预算"), ("标注与评估", "任务理解信号\n模型学习反馈"), ("选择或改进", "筛选、编辑、增强\n保留来源与证据"), ("独立检验", "质量复检\n人工与策略评测"), ("更新整理路线", "调整规则与算法\n验证候选新方法")]
    for i, ((title, body), x) in enumerate(zip(stages, xs)):
        d.step(i+1, title, body, x, 230, 264, 184, TEAL_BG if i == 3 else "FFFFFF")
        if i < 4:
            d.line(x+273, 323, x+292, 323, MUTED, 1.5, True)
    d.line(1404, 424, 1404, 455, TEAL, 1.4)
    d.line(1404, 455, 196, 455, TEAL, 1.4)
    d.line(196, 455, 196, 424, TEAL, 1.4, True)
    d.rect(64, 487, 710, 301)
    d.text("各研究支撑循环中的一部分", 90, 513, 658, 40, 27, bold=True)
    d.text("STEAM / MGT-QV    任务信号与局部治理\nRoboDrop                 模型相关的数据选择\nDemoTempo / FLORA  数据改进与自动标注\nEDC                         统一执行、证据与质量复检", 90, 578, 658, 170, 24, MUTED)
    d.rect(800, 487, 736, 301, WARM)
    d.text("怎样证明框架确实在改进？", 826, 513, 684, 40, 27, bold=True)
    d.text("独立复核：缺陷是否更少，有用经验是否保留？\n训练对照：全量、等量随机、EDC 子集比较。\n逐层验证：质量 → 任务价值 → 策略价值。", 826, 578, 680, 138, 24, INK)
    d.text("不以放宽质量要求制造提升；最终测试与规则调整分开。", 826, 748, 680, 29, 18, AMBER)
    d.footer("研究来源与接入边界见 docs/explanation/QUALITY_VALIDATION.zh-CN.md；FLORA 为外部研究参考，DemoTempo 按已有材料介绍。",
             "现有论文分别支持部分环节；自动调整路线、生成新算法与完整自主循环，仍需框架级集成和验证。")


def render(pptx, destination, profile):
    subprocess.run(["libreoffice", f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", "pdf",
                    "--outdir", str(destination), str(pptx)], check=True, capture_output=True, text=True)
    import pymupdf
    pdf = pymupdf.open(destination / (pptx.stem + ".pdf"))
    images = []
    for i, page in enumerate(pdf):
        path = destination / f"{pptx.stem}-{i+1:02d}.png"
        page.get_pixmap(matrix=pymupdf.Matrix(2400 / page.rect.width, 2400 / page.rect.width), alpha=False).save(path)
        images.append(path)
    pdf.close()
    return images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-dir", type=Path, required=True)
    args = parser.parse_args()
    args.render_dir.mkdir(parents=True, exist_ok=True)
    output = Path(__file__).parent / "edc-framework.zh-CN.pptx"
    d = Deck()
    for build in [overview, quality, steam, local_curation, robodrop, autonomy]:
        build(d)
    d.prs.save(output)
    en = Deck()
    overview(en, english=True)
    english_cover = args.render_dir / "edc-overview.en.pptx"
    en.prs.save(english_cover)
    with tempfile.TemporaryDirectory(prefix="edc-slides-render-") as tmp:
        profile = Path(tmp) / "lo"
        pages = render(output, args.render_dir, profile)
        en_pages = render(english_cover, args.render_dir, profile)
    # README overview assets are maintained independently from slide previews.
    (args.render_dir / "edc-slide-cover.zh-CN.png").write_bytes(pages[0].read_bytes())
    (args.render_dir / "edc-slide-cover.en.png").write_bytes(en_pages[0].read_bytes())
    sheet = Image.new("RGB", (1648, 1462), "#DFE8ED")
    for i, p in enumerate(pages):
        with Image.open(p) as img:
            img.thumbnail((800, 450), Image.Resampling.LANCZOS)
            sheet.paste(img, (16+(i % 2)*816, 16+(i//2)*482))
    sheet.save(args.render_dir / "edc-deck-preview.png")
    print(f"Built {len(pages)} editable slides: {output}")
    print(f"Bilingual slide covers: {args.render_dir}")
    print(f"Preview: {args.render_dir / 'edc-deck-preview.png'}")


if __name__ == "__main__":
    main()
