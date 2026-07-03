# -*- coding: utf-8 -*-
"""Generate PNG diagrams embedded in the beginner project report DOCX."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "project_report_diagrams"
OUT.mkdir(exist_ok=True)


def _font_path() -> str | None:
    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        candidate = Path(path)
        if candidate.exists():
            return str(candidate)
    return None


FONT_PATH = _font_path()


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if FONT_PATH:
        return ImageFont.truetype(FONT_PATH, size=size)
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, text: str, font_obj, width: int) -> list[str]:
    lines: list[str] = []
    for block in text.split("\n"):
        current = ""
        for char in block:
            trial = current + char
            if draw.textbbox((0, 0), trial, font=font_obj)[2] <= width or not current:
                current = trial
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
    return lines


def centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font_obj, fill=(20, 44, 75)) -> None:
    x1, y1, x2, y2 = box
    lines = wrap(draw, text, font_obj, x2 - x1 - 26)
    line_height = font_obj.size + 6 if hasattr(font_obj, "size") else 20
    y = y1 + max(0, (y2 - y1 - line_height * len(lines)) // 2)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_obj)
        x = x1 + (x2 - x1 - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font_obj, fill=fill)
        y += line_height


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color=(45, 90, 140), width=4) -> None:
    draw.line([start, end], fill=color, width=width)
    x1, y1 = start
    x2, y2 = end
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 14
    points = [
        (x2, y2),
        (x2 - size * math.cos(angle - math.pi / 6), y2 - size * math.sin(angle - math.pi / 6)),
        (x2 - size * math.cos(angle + math.pi / 6), y2 - size * math.sin(angle + math.pi / 6)),
    ]
    draw.polygon(points, fill=color)


def title(draw: ImageDraw.ImageDraw, text: str, width: int = 1500) -> None:
    draw.text((50, 34), text, font=font(34), fill=(11, 37, 69))
    draw.line((50, 84, width - 50, 84), fill=(190, 205, 224), width=3)


def vertical_flow(name: str, heading: str, boxes: list[str]) -> None:
    width = 1500
    height = 160 + len(boxes) * 112
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    title(draw, heading, width)
    previous_bottom = None
    for index, text in enumerate(boxes):
        x1, x2 = 290, 1210
        y1 = 125 + index * 112
        y2 = y1 + 72
        draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=(234, 242, 251), outline=(76, 125, 176), width=3)
        centered(draw, (x1, y1, x2, y2), text, font(23))
        if previous_bottom is not None:
            arrow(draw, ((x1 + x2) // 2, previous_bottom + 8), ((x1 + x2) // 2, y1 - 10))
        previous_bottom = y2
    img.save(OUT / name)


def grid_flow(name: str, heading: str, boxes: list[str], columns: int = 2) -> None:
    img = Image.new("RGB", (1500, 860), "white")
    draw = ImageDraw.Draw(img)
    title(draw, heading)
    coords = []
    box_w, box_h = 530, 94
    for index, text in enumerate(boxes):
        row, col = divmod(index, columns)
        x1 = 120 + col * 720
        y1 = 135 + row * 165
        x2, y2 = x1 + box_w, y1 + box_h
        coords.append((x1, y1, x2, y2))
        fill = (234, 246, 239) if "输出" in text or "报告" in text else (234, 242, 251)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=fill, outline=(76, 125, 176), width=3)
        centered(draw, (x1, y1, x2, y2), text, font(21))
    for i in range(len(coords) - 1):
        x1, y1, x2, y2 = coords[i]
        nx1, ny1, nx2, ny2 = coords[i + 1]
        if i % columns == columns - 1:
            arrow(draw, ((x1 + x2) // 2, y2 + 8), ((nx1 + nx2) // 2, ny1 - 10))
        else:
            arrow(draw, (x2 + 10, (y1 + y2) // 2), (nx1 - 10, (ny1 + ny2) // 2))
    img.save(OUT / name)


def program_graph() -> None:
    img = Image.new("RGB", (1500, 900), "white")
    draw = ImageDraw.Draw(img)
    title(draw, "图示：Program Graph 把程序证据放进同一张异构图")
    center = (565, 315, 935, 535)
    draw.rounded_rectangle(center, radius=26, fill=(234, 242, 251), outline=(45, 90, 140), width=4)
    centered(draw, center, "Program Graph\n函数 / 调用 / 控制流 / 数据流", font(25))
    nodes = [
        ((90, 160, 380, 250), "AST\n函数、类、语句"),
        ((90, 430, 380, 520), "CFG\n分支、循环、异常路径"),
        ((1060, 160, 1410, 250), "Call Graph\n调用边与 import alias"),
        ((1060, 430, 1410, 520), "Data-flow\n变量流动与跨函数传参"),
        ((320, 680, 630, 780), "Pytest Trace\n失败/通过测试覆盖"),
        ((880, 680, 1190, 780), "Static Rules\n边界、类型、API misuse"),
    ]
    for box, text in nodes:
        draw.rounded_rectangle(box, radius=18, fill=(244, 246, 249), outline=(120, 150, 180), width=3)
        centered(draw, box, text, font(20))
        arrow(draw, ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), ((center[0] + center[2]) // 2, (center[1] + center[3]) // 2), width=3)
    img.save(OUT / "program_graph.png")


def score_fusion() -> None:
    img = Image.new("RGB", (1500, 850), "white")
    draw = ImageDraw.Draw(img)
    title(draw, "图示：FinalScore 是多个证据的融合，不是单一分数")
    inputs = ["SBFL\n失败测试覆盖", "GraphScore\n调用/数据/控制证据", "StaticRuleScore\n规则命中", "Semantic/LLMScore\n失败信息语义", "Risk Penalty\n补丁风险"]
    for i, text in enumerate(inputs):
        box = (65 + i * 285, 150, 310 + i * 285, 265)
        draw.rounded_rectangle(box, radius=18, fill=(244, 246, 249), outline=(120, 150, 180), width=3)
        centered(draw, box, text, font(20))
        arrow(draw, ((box[0] + box[2]) // 2, box[3] + 8), (750, 410), width=3)
    final = (515, 420, 985, 545)
    topk = (515, 650, 985, 765)
    draw.rounded_rectangle(final, radius=22, fill=(234, 242, 251), outline=(45, 90, 140), width=4)
    centered(draw, final, "FinalScore\n综合排序分数", font(27))
    draw.rounded_rectangle(topk, radius=22, fill=(234, 246, 239), outline=(56, 120, 90), width=4)
    centered(draw, topk, "Top-k suspicious\n候选缺陷函数", font(26), fill=(20, 80, 55))
    arrow(draw, (750, 552), (750, 638), width=4)
    img.save(OUT / "score_fusion.png")


def main() -> None:
    vertical_flow(
        "architecture.png",
        "图示：从仓库到修复报告的端到端 Agent 流程",
        [
            "输入：repo / failing tests / benchmark template",
            "Repo Parser：读取文件、函数、类、导入关系",
            "Program Graph：AST + CFG + Call Graph + Data-flow",
            "Fault Localization：SBFL + GraphScore + StaticRuleScore + LLMScore",
            "Patch Search：Top-k 函数内生成候选补丁",
            "Sandbox：隔离运行 pytest，收集执行反馈",
            "Reflection：失败归因后继续生成 refined patch",
            "输出：定位排名、补丁、测试结果、评估报告",
        ],
    )
    program_graph()
    score_fusion()
    grid_flow(
        "repair_loop.png",
        "图示：自动修复不是一次生成，而是搜索与反馈闭环",
        [
            "Top-k suspicious functions",
            "生成多个 patch candidates",
            "AST / scope / risk 校验",
            "Beam Search 排序与去重",
            "Sandbox 执行 pytest",
            "成功：输出补丁与证据",
            "失败：提取 stdout / stderr / traceback",
            "Reflection 生成 refined candidates",
        ],
    )
    grid_flow(
        "benchmark_loop.png",
        "图示：Benchmark 让项目结果可复现、可比较、可写进简历",
        [
            "GitHub raw source / toy cases",
            "Template 定义 mutation 和 tests",
            "Materializer 生成可运行 repo",
            "Benchmark Runner 执行定位与修复",
            "Metrics 统计 Top-1 / MAP / Patch Success",
            "Ablation 证明模块贡献",
            "Quality Gate 防止虚高结果",
            "Showcase Report 汇总简历证据",
        ],
    )
    print(OUT)


if __name__ == "__main__":
    main()
