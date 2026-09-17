from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
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
    professional: str = ""
    organization: str = ""
    raw: str = ""


@dataclass
class InspectionDetailData:
    place_no: str = ""
    place_name: str = ""
    address: str = ""
    inspection_time: Optional[datetime] = None
    inspection_unit: str = ""
    fire_management_result: str = ""
    equipment_result: str = ""
    violation_found: Optional[bool] = None
    remarks: str = ""
    raw_text: str = ""


@dataclass
class PlaceData:
    place_no: str = ""
    company_name: str = ""
    sign_name: str = ""
    raw_place_name: str = ""
    address: str = ""
    purpose: str = ""
    business_floors: str = ""
    total_floor_area: str = ""
    use_permit_no: str = ""
    above_ground_floors: str = ""
    below_ground_floors: str = ""
    building_height: str = ""
    manager_name: str = ""
    manager_representative: str = ""
    manager_title: str = ""
    fire_manager_name: str = ""
    fire_manager_title: str = ""
    fire_manager_appointment_date: Optional[datetime] = None
    fire_manager_certificate: str = ""
    fire_plan_date: Optional[datetime] = None
    training_date: Optional[datetime] = None
    training_year: Optional[int] = None
    training_period: str = ""
    flame_retardant_text: str = ""
    equipment_items: list[str] = field(default_factory=list)
    latest_equipment_inspection: Optional[InspectionRecord] = None
    latest_fire_management_inspection: Optional[InspectionRecord] = None
    latest_equipment_report: Optional[ReportRecord] = None

    @property
    def display_name(self) -> str:
        if self.company_name and self.sign_name:
            return f"{self.company_name}({self.sign_name})"
        return self.company_name or self.raw_place_name or self.sign_name

    def to_dict(self):
        return asdict(self)


def _parse_date(value: str) -> Optional[datetime]:
    m = re.fullmatch(r"(20\d{2})/(\d{1,2})/(\d{1,2})", value.strip())
    if not m:
        return None
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _parse_datetime(value: str) -> Optional[datetime]:
    value = value.strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
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
    m = re.search(r"公司商號\s*:\s*(.*?)\s+市招\s*:\s*([^\n]*)", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", ""


def _extract_fire_plan_fields(text: str):
    for line in text.splitlines():
        if "防護計畫書製定(變更)日期" in line or "防護計畫書製定（變更）日期" in line:
            plan = _first_match(r"防護計畫書製定[\(（]變更[\)）]日期\s*:\s*(20\d{2}/\d{1,2}/\d{1,2})", line)
            training = _first_match(r"組訓日期\s*:\s*(20\d{2}/\d{1,2}/\d{1,2})", line)
            year = _first_match(r"年度\s*:\s*(20\d{2}|1\d{2})", line)
            period = _first_match(r"期別\s*:\s*([^\s]+)", line)
            return (
                _parse_date(plan) if plan else None,
                _parse_date(training) if training else None,
                int(year) if year else None,
                period,
            )
    return None, None, None, ""


def _extract_flame_retardant(text: str) -> str:
    m = re.search(r"防焰物品\s*(.*?)\s*消防安全設備", text, re.S)
    if not m:
        return ""
    block = " ".join(line.strip() for line in m.group(1).splitlines() if line.strip())
    return block.strip().strip("()（） ")


def _date_blocks(section: str):
    matches = list(DATE_RE.finditer(section))
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        date = _parse_date(match.group("date"))
        if date:
            yield date, section[start:end].strip()


def _extract_latest_inspection(text: str, category: str) -> Optional[InspectionRecord]:
    m = re.search(r"安全查察(.*?)(?:檢修申報|\Z)", text, re.S)
    if not m:
        return None
    records: list[InspectionRecord] = []
    for date, block in _date_blocks(m.group(1)):
        if category not in block:
            continue
        mm = re.search(re.escape(category) + r"\s*(合格|不合格|符合|不符合)", block)
        result = mm.group(1) if mm else ""
        if result:
            records.append(InspectionRecord(date=date, category=category, result=result, raw=block))
    return max(records, key=lambda r: r.date) if records else None


def _extract_latest_equipment_report(text: str) -> Optional[ReportRecord]:
    m = re.search(r"檢修申報(.*)\Z", text, re.S)
    if not m:
        return None

    records: list[ReportRecord] = []
    for date, block in _date_blocks(m.group(1)):
        ym = re.search(r"\b(20\d{2}|1\d{2})\s*(上半年|下半年)\b", block)
        if not ym:
            continue
        year = int(ym.group(1))
        period = ym.group(2)
        result = next((x for x in ("不符合", "符合", "不合格", "合格") if x in block), "")
        if not result:
            continue

        professional = ""
        organization = ""
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        try:
            idx = next(i for i, x in enumerate(lines) if x in ("符合", "不符合", "合格", "不合格"))
            if idx + 1 < len(lines):
                professional = lines[idx + 1]
            if idx + 2 < len(lines):
                cert_org = lines[idx + 2]
                mm_org = re.search(r"(?:消師證字第\S+號)?(.*)", cert_org)
                if mm_org:
                    organization = mm_org.group(1).strip()
        except StopIteration:
            pass

        records.append(
            ReportRecord(
                received_date=date,
                year=year,
                period=period,
                result=result,
                professional=professional,
                organization=organization,
                raw=block,
            )
        )
    return max(records, key=lambda r: r.received_date) if records else None


def _extract_equipment_items(text: str) -> list[str]:
    m = re.search(r"消防安全設備\s*\n(?:檢查項目\s*\n?說明\s*\n)?(.*?)\n安全查察", text, re.S)
    if not m:
        return []
    items: list[str] = []
    for line in m.group(1).splitlines():
        value = re.sub(r"\s+", "", line)
        if not value or value in ("檢查項目", "說明"):
            continue
        if value not in items:
            items.append(value)
    return items


def _extract_building(text: str) -> tuple[str, str, str]:
    m = re.search(
        r"建物資料\s*\n建物名稱\s*\n地上樓層\s*\n地下樓層\s*\n建物高度\s*\n"
        r"[^\n]+\n(\d+)\n(\d+)\n([0-9.]+)",
        text,
    )
    return (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")


def parse_place_record_pdf(pdf_path: str | Path) -> PlaceData:
    text = extract_pdf_text(pdf_path)
    if "臺北市政府消防局場所紀錄表" not in text and "場所紀錄表" not in text:
        raise ValueError("PDF 格式不符：未找到「臺北市政府消防局場所紀錄表」標題。")

    company_name, sign_name = _extract_company_and_sign(text)
    plan_date, training_date, training_year, training_period = _extract_fire_plan_fields(text)
    above_ground, below_ground, building_height = _extract_building(text)

    manager_line = _first_match(r"管理權人\s*\n([^\n]+)", text)
    fire_manager_line = _first_match(r"防火管理人\s*\n([^\n]+)", text)
    fire_manager_date_text = _first_match(r"任用日期\s*:\s*(20\d{2}/\d{1,2}/\d{1,2})", fire_manager_line)

    data = PlaceData(
        place_no=_first_match(r"場所編號\s*:\s*(\d+)", text),
        company_name=company_name,
        sign_name=sign_name,
        raw_place_name=_first_match(r"場所名稱\s*:\s*([^\n]*)", text),
        address=_first_match(r"場所地址\s*:\s*([^\n]*)", text),
        purpose=_first_match(r"用途名稱\s*:\s*([^\s\n]+)", text),
        business_floors=_first_match(r"營業樓層\s*:\s*([^\s\n]+)", text),
        total_floor_area=_first_match(r"總樓地板面積\s*:\s*([0-9,.]+)", text),
        use_permit_no=_first_match(r"使照號碼\s*:\s*([^\s\n]+)", text),
        above_ground_floors=above_ground,
        below_ground_floors=below_ground,
        building_height=building_height,
        manager_name=_first_match(r"姓名\(法人\)\s*:\s*(.*?)\s+證號", manager_line),
        manager_representative=_first_match(r"代表人\s*:\s*([^\s]+)", manager_line),
        manager_title=_first_match(r"職稱\s*:\s*([^\s]+)", manager_line),
        fire_manager_name=_first_match(r"姓名\s*:\s*([^\s]+)", fire_manager_line),
        fire_manager_title=_first_match(r"職稱\s*:\s*([^\s]+)", fire_manager_line),
        fire_manager_appointment_date=_parse_date(fire_manager_date_text) if fire_manager_date_text else None,
        fire_manager_certificate=_first_match(r"證書文號\s*:\s*([^\s\n]+)", text),
        fire_plan_date=plan_date,
        training_date=training_date,
        training_year=training_year,
        training_period=training_period,
        flame_retardant_text=_extract_flame_retardant(text),
        equipment_items=_extract_equipment_items(text),
        latest_equipment_inspection=_extract_latest_inspection(text, "消防設備"),
        latest_fire_management_inspection=_extract_latest_inspection(text, "防火管理"),
        latest_equipment_report=_extract_latest_equipment_report(text),
    )

    if not data.display_name:
        raise ValueError("無法從 PDF 讀取場所名稱。")
    if not data.address:
        raise ValueError("無法從 PDF 讀取場所地址。")
    return data


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _slice_compact(compact: str, start_key: str, end_key: str) -> str:
    start = compact.find(start_key)
    if start < 0:
        return ""
    end = compact.find(end_key, start + len(start_key))
    if end < 0:
        end = len(compact)
    return compact[start:end]


def parse_inspection_record_pdf(pdf_path: str | Path) -> InspectionDetailData:
    """Parse the one-page 臺北市政府消防局消防安全檢查紀錄表.

    The detailed form is used as a second source.  Only deterministic header and
    checkbox signals are interpreted; ambiguous unchecked equipment rows are not
    guessed.
    """
    text = extract_pdf_text(pdf_path)
    compact = _compact(text)
    if "消防安全檢查紀錄表" not in compact:
        raise ValueError("PDF 格式不符：未找到「消防安全檢查紀錄表」標題。")

    inspection_time_text = _first_match(
        r"檢查時間\s*(20\d{2}/\d{1,2}/\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
        text,
    )
    place_name = _first_match(r"場所名稱\s+(.+?)\s+用途\s+", text)
    address = _first_match(r"場所地址\s+(.+?)\s+管理權人\s+", text)
    unit = _first_match(r"檢查單位\s+([^\s]+)", text)

    fire_block = _slice_compact(compact, "防火管理", "檢修申報")
    fire_result = ""
    if "■不符合" in fire_block:
        fire_result = "不合格"
    elif "■符合" in fire_block:
        fire_result = "合格"

    equipment_block = _slice_compact(compact, "滅火器", "違規查報")
    equipment_result = ""
    if "■不符合" in equipment_block:
        equipment_result = "不合格"
    elif "■符合" in equipment_block:
        equipment_result = "合格"

    violation_block = _slice_compact(compact, "違規查報", "備註")
    violation_found: Optional[bool] = None
    if "■有發現" in violation_block:
        violation_found = True
    elif "■未發現" in violation_block:
        violation_found = False

    remarks_block = _slice_compact(compact, "備註", "簽名")
    remarks = remarks_block.removeprefix("備註")

    data = InspectionDetailData(
        place_no=_first_match(r"場所編號\s*[:：]?\s*(\d+)", text),
        place_name=place_name,
        address=address,
        inspection_time=_parse_datetime(inspection_time_text) if inspection_time_text else None,
        inspection_unit=unit,
        fire_management_result=fire_result,
        equipment_result=equipment_result,
        violation_found=violation_found,
        remarks=remarks,
        raw_text=text,
    )
    if not data.place_no and not data.place_name and not data.address:
        raise ValueError("消防安全檢查紀錄表無法讀取場所識別資料。")
    return data


def _norm_identity(value: str) -> str:
    return re.sub(r"[\s()（）\-－]", "", value or "").lower()


def merge_inspection_detail(place: PlaceData, detail: InspectionDetailData) -> PlaceData:
    """Validate that both PDFs are the same place and merge confident results."""
    if place.place_no and detail.place_no and place.place_no != detail.place_no:
        raise ValueError(
            f"兩份 PDF 的場所編號不同：場所紀錄表={place.place_no}，檢查紀錄表={detail.place_no}。"
        )
    if place.address and detail.address:
        a = _norm_identity(place.address)
        b = _norm_identity(detail.address)
        if a and b and a != b and a not in b and b not in a:
            raise ValueError("兩份 PDF 的場所地址不同，請確認檢查紀錄是否選對場所。")

    if detail.inspection_time and detail.fire_management_result:
        current = place.latest_fire_management_inspection
        if current is None or detail.inspection_time >= current.date:
            place.latest_fire_management_inspection = InspectionRecord(
                date=detail.inspection_time,
                category="防火管理",
                result=detail.fire_management_result,
                raw=detail.raw_text,
            )

    if detail.inspection_time and detail.equipment_result:
        current = place.latest_equipment_inspection
        if current is None or detail.inspection_time >= current.date:
            place.latest_equipment_inspection = InspectionRecord(
                date=detail.inspection_time,
                category="消防設備",
                result=detail.equipment_result,
                raw=detail.raw_text,
            )
    return place
