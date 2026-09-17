from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor

from parser import PlaceData

PENDING_RE = re.compile(r"(【待補：[^】]+】)")


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def roc_year(year: int) -> int:
    return year - 1911 if year >= 1912 else year


def roc_date(dt: datetime) -> str:
    return f"{roc_year(dt.year)}年{dt.month}月{dt.day}日"


def _result_phrase(result: str) -> str:
    if result in ("合格", "符合"):
        return "合格"
    if result in ("不合格", "不符合"):
        return "不合格"
    return result


def _format_area(value: str) -> str:
    try:
        return f"{float(value.replace(',', '')):,.0f}"
    except Exception:
        return value


def _write_cell(cell, text: str):
    if not cell.paragraphs:
        p = cell.add_paragraph()
    else:
        p = cell.paragraphs[0]
    for run in p.runs:
        run.text = ""

    for part in PENDING_RE.split(text):
        if not part:
            continue
        run = p.add_run(part)
        run.font.name = "標楷體"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "標楷體")
        if part.startswith("【待補："):
            run.bold = True
            run.font.color.rgb = RGBColor(192, 0, 0)

    for extra_p in cell.paragraphs[1:]:
        for run in extra_p.runs:
            run.text = ""


def _equipment_summary(items: list[str]) -> str:
    preferred = [
        "滅火器",
        "室內消防栓設備",
        "自動撒水設備",
        "火警自動警報設備",
        "緊急廣播設備",
        "出口標示燈",
        "避難方向指示燈",
        "緊急照明設備",
        "連結送水管",
        "室內排煙設備",
    ]
    found = [item for item in preferred if item in items]
    return "、".join(found) + ("等設備" if found else "")


def build_prefill_text(data: PlaceData) -> dict[str, str]:
    fields: dict[str, str] = {}

    if data.display_name:
        fields["場所名稱"] = data.display_name
    if data.address:
        fields["場所地址"] = data.address

    if data.purpose or data.business_floors:
        fields["用途及營業樓層"] = (
            f"用途為{data.purpose or '【待補：用途】'}；"
            f"營業樓層{data.business_floors or '【待補：樓層】'}。"
        )

    building_bits: list[str] = []
    if data.above_ground_floors and data.below_ground_floors:
        building_bits.append(f"地上{data.above_ground_floors}層、地下{data.below_ground_floors}層")
    if data.building_height:
        building_bits.append(f"高度{data.building_height}公尺")
    if data.total_floor_area:
        building_bits.append(f"總樓地板面積{_format_area(data.total_floor_area)}㎡")
    if data.use_permit_no:
        building_bits.append(f"使用執照{data.use_permit_no}")
    if building_bits:
        fields["建築物規模"] = "；".join(building_bits) + "。"

    if data.manager_name:
        text = data.manager_name
        if data.manager_representative:
            text += f"，代表人{data.manager_representative}"
        fields["管理權人"] = text + "。"

    if data.fire_manager_name:
        text = data.fire_manager_name
        if data.fire_manager_title:
            text += f"，職稱{data.fire_manager_title}"
        if data.fire_manager_appointment_date:
            text += f"，於{roc_date(data.fire_manager_appointment_date)}任用"
        fields["防火管理人"] = text + "。"

    if data.equipment_items:
        summary = _equipment_summary(data.equipment_items)
        if summary:
            fields["主要消防安全設備"] = f"設有{summary}。"

    if data.latest_equipment_inspection:
        record = data.latest_equipment_inspection
        fields["最近消防設備檢查"] = (
            f"最近一次於{roc_date(record.date)}辦理消防設備檢查，"
            f"結果{_result_phrase(record.result)}。"
        )

    if data.latest_equipment_report:
        record = data.latest_equipment_report
        fields["最近檢修申報"] = (
            f"最近一次於{roc_date(record.received_date)}辦理"
            f"{roc_year(record.year)}年{record.period}消防安全設備檢修申報，"
            f"結果{_result_phrase(record.result)}。"
        )

    if data.fire_plan_date:
        fields["消防防護計畫"] = f"消防防護計畫於{roc_date(data.fire_plan_date)}制定（變更）。"

    if data.latest_fire_management_inspection:
        record = data.latest_fire_management_inspection
        fields["最近防火管理檢查"] = (
            f"最近一次於{roc_date(record.date)}辦理防火管理檢查，"
            f"結果{_result_phrase(record.result)}。"
        )

    if data.training_date:
        fields["自衛消防編組訓練"] = f"最近一次於{roc_date(data.training_date)}辦理自衛消防編組訓練。"

    if data.flame_retardant_text:
        fields["防焰物品"] = data.flame_retardant_text.rstrip("。") + "。"

    return fields


def fill_word_template(
    data: PlaceData,
    output_path: str | Path,
    template_path: str | Path | None = None,
) -> tuple[Path, list[str]]:
    template_path = Path(template_path) if template_path else resource_path("resources/template.docx")
    output_path = Path(output_path)

    doc = Document(template_path)
    if not doc.tables:
        raise ValueError("Word 樣板內找不到表格。")
    table = doc.tables[0]
    fields = build_prefill_text(data)
    updated: list[str] = []

    for row in table.rows:
        cells = row.cells
        if len(cells) < 2:
            continue
        item = cells[0].text.strip()
        if not item or item == "項目" or item.startswith(("一、", "二、", "三、", "四、")):
            continue

        if item in fields:
            _write_cell(cells[1], fields[item])
            updated.append(item)
        else:
            _write_cell(cells[1], cells[1].text.strip())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path, updated


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().rstrip(".")
    return name[:80] or "場所"


def default_output_path(pdf_path: str | Path, data: PlaceData) -> Path:
    pdf_path = Path(pdf_path)
    stem = _sanitize_filename(data.sign_name or data.company_name or data.raw_place_name)
    today = datetime.now()
    date_part = f"{roc_year(today.year)}{today.month:02d}{today.day:02d}"
    return pdf_path.with_name(f"{stem}_消防安全管理及應變執行情形表_{date_part}.docx")
