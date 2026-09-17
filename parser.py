from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


DATE_RE = re.compile(r"(?P<date>20\d{2}/\d{1,2}/\d{1,2})")


@dataclass
class InspectionRecord:
    date: datetime
    category: str
    result: str
    raw: str = ""


@dataclass
class ReportRecord:
    received_date: datetime
    year: int
    period: str
    result: str
    raw: str = ""


@dataclass
class PlaceData:
    company_name: str = ""
    sign_name: str = ""
    raw_place_name: str = ""
    address: str = ""
    fire_plan_date: Optional[datetime] = None
    training_date: Optional[datetime] = None
    training_year: Optional[int] = None
    training_period: str = ""
    flame_retardant_text: str = ""
    latest_equipment_inspection: Optional[InspectionRecord] = None
    latest_equipment_report: Optional[ReportRecord] = None

    @property
    def display_name(self) -> str:
        if self.company_name and self.sign_name:
            return f"{self.company_name}({self.sign_name})"
        return self.company_name or self.raw_place_name or self.sign_name

    def to_dict(self):
        d = asdict(self)
        return d


def _parse_date(value: str) -> Optional[datetime]:
    value = value.strip()
    for fmt in ("%Y/%m/%d", "%Y/%m/%-d", "%Y/%-m/%-d"):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, OSError):
            continue
    # Portable fallback for Windows/POSIX.
    m = re.fullmatch(r"(20\d{2})/(\d{1,2})/(\d{1,2})", value)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def extract_pdf_text(pdf_path: str | Path) -> str:
    pdf_path = Path(pdf_path)
    with fitz.open(pdf_path) as doc:
        pages = [page.get_text("text") for page in doc]
    return "\n".join(pages).replace("\r\n", "\n").replace("\r", "\n")


def _first_match(pattern: str, text: str, flags=0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def _extract_company_and_sign(text: str) -> tuple[str, str]:
    # 標準場所紀錄表通常把「公司商號」與「市招」放在同一行。
    m = re.search(r"公司商號\s*:\s*(.*?)\s+市招\s*:\s*([^\n]*)", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", ""


def _extract_fire_plan_fields(text: str):
    line = ""
    for ln in text.splitlines():
        if "防護計畫書製定(變更)日期" in ln or "防護計畫書製定（變更）日期" in ln:
            line = ln.strip()
            break
    if not line:
        return None, None, None, ""

    plan = _first_match(r"防護計畫書製定[\(（]變更[\)）]日期\s*:\s*(20\d{2}/\d{1,2}/\d{1,2})", line)
    training = _first_match(r"組訓日期\s*:\s*(20\d{2}/\d{1,2}/\d{1,2})", line)
    year = _first_match(r"年度\s*:\s*(20\d{2}|1\d{2})", line)
    period = _first_match(r"期別\s*:\s*([^\s]+)", line)

    plan_date = _parse_date(plan) if plan else None
    training_date = _parse_date(training) if training else None
    training_year = int(year) if year else None
    return plan_date, training_date, training_year, period


def _extract_flame_retardant(text: str) -> str:
    m = re.search(r"防焰物品\s*(.*?)\s*消防安全設備", text, re.S)
    if not m:
        return ""
    block = " ".join(ln.strip() for ln in m.group(1).splitlines() if ln.strip())
    block = block.strip().strip("()（） ")
    return block


def _date_blocks(section: str):
    matches = list(DATE_RE.finditer(section))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        date = _parse_date(m.group("date"))
        if date:
            yield date, section[start:end].strip()


def _extract_latest_equipment_inspection(text: str) -> Optional[InspectionRecord]:
    m = re.search(r"安全查察(.*?)(?:檢修申報|\Z)", text, re.S)
    if not m:
        return None
    section = m.group(1)
    records: list[InspectionRecord] = []
    for date, block in _date_blocks(section):
        # 固定表格中，「消防設備 合格」會落在同一筆查察區塊。
        if "消防設備" not in block:
            continue
        result = ""
        # 避免把「宣導活動 完成」等其他狀態當成設備查察結果。
        mm = re.search(r"消防設備\s*(合格|不合格|符合|不符合)", block)
        if mm:
            result = mm.group(1)
        else:
            for candidate in ("不合格", "合格", "不符合", "符合"):
                if candidate in block:
                    result = candidate
                    break
        if result:
            records.append(InspectionRecord(date=date, category="消防設備", result=result, raw=block))
    return max(records, key=lambda r: r.date) if records else None


def _extract_latest_equipment_report(text: str) -> Optional[ReportRecord]:
    m = re.search(r"檢修申報(.*)\Z", text, re.S)
    if not m:
        return None
    section = m.group(1)
    records: list[ReportRecord] = []
    for date, block in _date_blocks(section):
        # 主申報列一定會包含年度與期別；「複查日期」區塊不會。
        ym = re.search(r"\b(20\d{2}|1\d{2})\s*(上半年|下半年)\b", block)
        if not ym:
            lines = [x.strip() for x in block.splitlines() if x.strip()]
            for idx, line in enumerate(lines[:-1]):
                if re.fullmatch(r"20\d{2}|1\d{2}", line) and lines[idx + 1] in ("上半年", "下半年"):
                    ym = re.match(r"(.*)", f"{line}{lines[idx + 1]}")
                    year = int(line)
                    period = lines[idx + 1]
                    break
            else:
                continue
        else:
            year = int(ym.group(1))
            period = ym.group(2)

        result = ""
        for candidate in ("不符合", "符合", "不合格", "合格"):
            if candidate in block:
                result = candidate
                break
        if not result:
            continue
        records.append(ReportRecord(received_date=date, year=year, period=period, result=result, raw=block))
    return max(records, key=lambda r: r.received_date) if records else None


def parse_place_record_pdf(pdf_path: str | Path) -> PlaceData:
    text = extract_pdf_text(pdf_path)
    if "臺北市政府消防局場所紀錄表" not in text and "場所紀錄表" not in text:
        raise ValueError("PDF 格式不符：未找到「臺北市政府消防局場所紀錄表」標題。")

    company_name, sign_name = _extract_company_and_sign(text)
    raw_place_name = _first_match(r"場所名稱\s*:\s*([^\n]*)", text)
    address = _first_match(r"場所地址\s*:\s*([^\n]*)", text)
    plan_date, training_date, training_year, training_period = _extract_fire_plan_fields(text)

    data = PlaceData(
        company_name=company_name,
        sign_name=sign_name,
        raw_place_name=raw_place_name,
        address=address,
        fire_plan_date=plan_date,
        training_date=training_date,
        training_year=training_year,
        training_period=training_period,
        flame_retardant_text=_extract_flame_retardant(text),
        latest_equipment_inspection=_extract_latest_equipment_inspection(text),
        latest_equipment_report=_extract_latest_equipment_report(text),
    )

    if not data.display_name:
        raise ValueError("無法從 PDF 讀取場所名稱。")
    if not data.address:
        raise ValueError("無法從 PDF 讀取場所地址。")
    return data
