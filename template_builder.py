from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

OUT = Path(__file__).parent / "resources" / "template.docx"
TITLE = "施工中場所火災－消防安全管理及應變執行情形表"
NOTE = "註：紅色【待補：○○】為事故發生後依現場具體事實補填；其餘資料依安管系統匯出資料更新。"

ROWS = [
    ("HEADER", "項目", "執行情形"),
    ("SECTION", "一、建築物概要", ""),
    ("ROW", "場所名稱", "【待補：場所名稱】"),
    ("ROW", "場所地址", "【待補：場所地址】"),
    ("ROW", "用途及營業樓層", "用途為【待補：用途】；營業樓層【待補：樓層】。"),
    ("ROW", "建築物規模", "地上【待補：樓層】層、地下【待補：樓層】層，高度【待補：高度】公尺；總樓地板面積【待補：面積】㎡。"),
    ("ROW", "管理權人", "【待補：管理權人】。"),
    ("ROW", "防火管理人", "【待補：姓名】，職稱【待補：職稱】，於【待補：日期】任用。"),
    ("SECTION", "二、消防安全設備情形", ""),
    ("ROW", "主要消防安全設備", "設有【待補：主要設備】。"),
    ("ROW", "最近消防設備檢查", "最近一次於【待補：日期】辦理消防設備檢查，結果【待補：結果】。"),
    ("ROW", "最近檢修申報", "最近一次於【待補：日期】辦理【待補：期別】消防安全設備檢修申報，結果【待補：結果】。"),
    ("SECTION", "三、消防管理情形", ""),
    ("ROW", "消防防護計畫", "消防防護計畫於【待補：日期】制定（變更）。"),
    ("ROW", "最近防火管理檢查", "最近一次於【待補：日期】辦理防火管理檢查，結果【待補：結果】。"),
    ("ROW", "自衛消防編組訓練", "最近一次於【待補：日期】辦理自衛消防編組訓練。"),
    ("ROW", "防焰物品", "【待補：防焰物品情形】。"),
    ("SECTION", "四、火災事故及應變情形", ""),
    ("ROW", "火災時間", "火災發生於【待補：日期】 【待補：時間】。"),
    ("ROW", "火災位置", "起火位置為【待補：樓層】之【待補：區域】。"),
    ("ROW", "起火原因", "初步研判為【待補：原因】；實際原因由火災調查單位調查中。"),
    ("ROW", "消防設備動作", "【待補：設備】於火災時【待補：有／無】動作；火警自動警報設備於【待補：時間】發報。"),
    ("ROW", "119通報及初期滅火", "現場人員於【待補：時間】通報119，並使用【待補：設備】進行初期滅火。"),
    ("ROW", "人員疏散及避難引導", "由自衛消防編組人員引導【待補：人數】人疏散至【待補：地點】。"),
]


def set_run_font(run, size_pt=9.2, bold=None):
    run.font.name = "標楷體"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "標楷體")
    run.font.size = Pt(size_pt)
    run.bold = bold


def _shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _cell_margins(cell, vertical=45, horizontal=65):
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for tag, value in (
        ("top", vertical),
        ("bottom", vertical),
        ("start", horizontal),
        ("end", horizontal),
    ):
        el = OxmlElement(f"w:{tag}")
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tc_pr.append(mar)


def build_template(path=OUT):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(1.15)
    sec.bottom_margin = Cm(1.05)
    sec.left_margin = Cm(1.15)
    sec.right_margin = Cm(1.15)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(TITLE)
    set_run_font(run, 15.5, True)

    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    for row_type, left, right in ROWS:
        cells = table.add_row().cells
        if row_type == "SECTION":
            cell = cells[0].merge(cells[1])
            _shade(cell, "E7E6E6")
            _cell_margins(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(left)
            set_run_font(run, 10, True)
            continue

        cells[0].width = Cm(4.0)
        cells[1].width = Cm(14.5)
        for cell in cells:
            _cell_margins(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            cell.paragraphs[0].paragraph_format.space_before = Pt(0)
            cell.paragraphs[0].paragraph_format.space_after = Pt(0)

        if row_type == "HEADER":
            for cell, text in zip(cells, (left, right)):
                _shade(cell, "D9E2F3")
                p = cell.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = p.add_run(text)
                set_run_font(run, 10, True)
        else:
            p = cells[0].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(left)
            set_run_font(run, 9.2, True)

            p = cells[1].paragraphs[0]
            run = p.add_run(right)
            set_run_font(run, 9.2)

    table.columns[0].width = Cm(4.0)
    table.columns[1].width = Cm(14.5)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(NOTE)
    set_run_font(run, 8)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


if __name__ == "__main__":
    print(build_template())
