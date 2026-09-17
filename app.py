from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ankuan_config import (
    base_profile_exists,
    base_profile_path,
    config_path,
    load_base_profile,
    load_config,
    reset_config,
    save_current_as_base_profile,
    save_point,
    save_points,
    set_report_counts,
)
from ankuan_manual_flow import ManualSelectionAnKuanAutomation
from ankuan_runtime import get_cursor_position
from parser import (
    InspectionDetailData,
    merge_inspection_detail,
    parse_inspection_record_pdf,
    parse_place_record_pdf,
)
from word_writer import default_output_path, fill_word_template, roc_date

APP_TITLE = "消防安全管理及應變表自動產製工具"

# scope=ankuan: relative to the main AnKuan window
# scope=preview: relative to the report preview window
CALIBRATION_ITEMS = [
    ("place_name", "場所名稱欄位", "ankuan"),
    ("query_button", "查詢資料按鈕", "ankuan"),
    ("condition_tab", "條件設定頁籤", "ankuan"),
    ("result_tab", "查詢結果頁籤", "ankuan"),
    ("safety_tab", "安全查察頁籤", "ankuan"),
    ("place_record_button", "場所紀錄表按鈕", "ankuan"),
    ("report_inspection_count", "最近檢查次數欄", "ankuan"),
    ("report_submission_count", "最近申報次數欄", "ankuan"),
    ("report_confirm_button", "場所紀錄表確認按鈕", "ankuan"),
    ("inspection_record_button", "檢查紀錄表按鈕", "ankuan"),
    ("preview_pdf_button", "預覽 PDF 匯出按鈕", "preview"),
    ("preview_close_button", "預覽結束按鈕", "preview"),
    # 選用：查詢結果清單範圍，只用於OCR候選建議（result_ocr.py）。
    # 沒校正這兩點時，OCR功能自動略過，維持原本純人工選擇流程。
    ("result_grid_top_left", "查詢結果清單－左上角", "ankuan"),
    ("result_grid_bottom_right", "查詢結果清單－右下角", "ankuan"),
]

REQUIRED_BASE_POINTS = [
    "place_name",
    "query_button",
    "safety_tab",
    "place_record_button",
    "report_inspection_count",
    "report_submission_count",
    "report_confirm_button",
    "inspection_record_button",
    "preview_pdf_button",
]

QUICK_ANCHORS = [
    ("place_name", "場所名稱欄位"),
    ("query_button", "查詢資料按鈕"),
    ("safety_tab", "安全查察頁籤"),
]


def _short_status(data) -> list[tuple[str, str]]:
    rows = [
        ("場所名稱", data.display_name or "未讀取"),
        ("場所地址", data.address or "未讀取"),
        ("用途／樓層", f"{data.purpose or '未讀取'} / {data.business_floors or '未讀取'}"),
    ]
    if data.latest_equipment_inspection:
        r = data.latest_equipment_inspection
        rows.append(("消防設備檢查", f"{roc_date(r.date)} / {r.result}"))
    else:
        rows.append(("消防設備檢查", "未找到（Word 會標註待補）"))
    if data.latest_equipment_report:
        r = data.latest_equipment_report
        rows.append(("檢修申報", f"{roc_date(r.received_date)} / {r.period} / {r.result}"))
    else:
        rows.append(("檢修申報", "未找到（Word 會標註待補）"))
    if data.latest_fire_management_inspection:
        r = data.latest_fire_management_inspection
        rows.append(("防火管理檢查", f"{roc_date(r.date)} / {r.result}"))
    else:
        rows.append(("防火管理檢查", "未找到（Word 會標註待補）"))
    return rows


def _inspection_status(detail: InspectionDetailData) -> list[tuple[str, str]]:
    dt = detail.inspection_time.strftime("%Y/%m/%d %H:%M") if detail.inspection_time else "未讀取"
    return [
        ("檢查場所編號", detail.place_no or "未讀取"),
        ("檢查場所名稱", detail.place_name or "未讀取"),
        ("檢查時間", dt),
        ("防火管理結果", detail.fire_management_result or "未判讀／非本次項目"),
        ("消防設備結果", detail.equipment_result or "未判讀／非本次項目"),
    ]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1020x760")
        self.minsize(900, 650)

        self.automation: ManualSelectionAnKuanAutomation | None = None
        self.pending_query = ""
        self.place_pdf_path: Path | None = None
        self.inspection_pdf_path: Path | None = None
        self.place_data = None
        self.inspection_detail: InspectionDetailData | None = None

        self.manual_place_pdf: Path | None = None
        self.manual_inspection_pdf: Path | None = None
        self.manual_place_data = None
        self.manual_inspection_detail: InspectionDetailData | None = None

        self._calibration_auto: ManualSelectionAnKuanAutomation | None = None
        self._quick_state = None
        self._environment_confirmed = False
        self._build_ui()

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text=APP_TITLE, font=("Microsoft JhengHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text=(
                "先正常登入安管系統。工具只操作既有查詢、報表與預覽畫面，不處理帳密、不連內部 API，"
                "不新增／修改／刪除安管資料。查詢場所與檢查紀錄都由人工選擇。"
            ),
            wraplength=970,
        ).pack(anchor="w", pady=(5, 10))

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)
        self.auto_tab = ttk.Frame(notebook, padding=12)
        self.manual_tab = ttk.Frame(notebook, padding=12)
        self.settings_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.auto_tab, text="安管半自動查詢")
        notebook.add(self.manual_tab, text="手動 PDF 備援")
        notebook.add(self.settings_tab, text="校正／設定")
        self._build_auto_tab()
        self._build_manual_tab()
        self._build_settings_tab()

        self.status_var = tk.StringVar(value="請先登入並開啟安管系統。")
        ttk.Label(root, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

    # ---------- automatic flow UI ----------
    def _build_auto_tab(self):
        search = ttk.Frame(self.auto_tab)
        search.pack(fill="x")
        ttk.Label(search, text="場所名稱關鍵字：").pack(side="left")
        self.query_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.query_var)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 8))
        entry.bind("<Return>", lambda _e: self.search_ankuan())
        ttk.Button(search, text="送出查詢", command=self.search_ankuan).pack(side="left")

        ttk.Label(
            self.auto_tab,
            text=(
                "安管的舊式結果表格不自動讀取，也不使用 CSV。場所與檢查紀錄皆由你在安管畫面人工選擇；"
                "工具負責產生『場所紀錄表』與『消防安全檢查紀錄表』兩份 PDF，再本機解析產生 Word。"
            ),
            wraplength=930,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 10))

        guide = ttk.LabelFrame(self.auto_tab, text="流程", padding=16)
        guide.pack(fill="both", expand=True)
        self.guide_var = tk.StringVar(
            value=(
                "1. 輸入場所名稱關鍵字並送出查詢。\n\n"
                "2. 到安管查詢結果人工選擇正確場所，進入該場所資料。\n\n"
                "3. 回本工具按『我已選好場所，產生場所紀錄表』。工具會套用設定的最近檢查／申報次數，"
                "經預覽匯出 PDF①。\n\n"
                "4. 回安管『安全查察』人工選擇要使用的那一筆檢查紀錄。\n\n"
                "5. 回本工具按『我已選好檢查紀錄，產生第二份 PDF』，經預覽匯出 PDF②。\n\n"
                "6. 兩份 PDF 場所識別相符後才產生 Word。"
            )
        )
        ttk.Label(guide, textvariable=self.guide_var, justify="left", wraplength=880).pack(anchor="nw")

        actions = ttk.Frame(self.auto_tab)
        actions.pack(fill="x", pady=(12, 0))
        self.place_continue_btn = ttk.Button(
            actions,
            text="我已選好場所，產生場所紀錄表",
            command=self.continue_selected_place,
            state="disabled",
        )
        self.place_continue_btn.pack(side="left")
        self.inspection_continue_btn = ttk.Button(
            actions,
            text="我已選好檢查紀錄，產生第二份 PDF",
            command=self.continue_selected_inspection,
            state="disabled",
        )
        self.inspection_continue_btn.pack(side="right")

    def _warn_environment(self, auto: ManualSelectionAnKuanAutomation) -> bool:
        if self._environment_confirmed:
            return True
        env = auto.environment_info()
        scale = env.get("display_scale_percent", 100)
        maximized = env.get("maximized", False)
        if scale == 100 and maximized:
            self._environment_confirmed = True
            return True
        ok = messagebox.askyesno(
            "目前不是標準環境",
            f"基本校正的標準環境為 Windows 顯示縮放 100%＋安管視窗最大化。\n\n"
            f"目前：縮放約 {scale}%；安管視窗{'已最大化' if maximized else '未最大化'}。\n\n"
            "建議先把安管最大化；若縮放不是 100%，可到『校正／設定』執行快速校正。\n"
            "仍要繼續本次操作嗎？",
        )
        if ok:
            self._environment_confirmed = True
        return ok

    def search_ankuan(self):
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning("請輸入關鍵字", "請輸入場所名稱關鍵字，例如「洲際」或「大巨蛋」。")
            return
        self.place_continue_btn.configure(state="disabled")
        self.inspection_continue_btn.configure(state="disabled")
        self.place_pdf_path = None
        self.inspection_pdf_path = None
        self.place_data = None
        self.inspection_detail = None
        self.status_var.set("正在將查詢條件送入安管……")
        self.update_idletasks()
        try:
            self.automation = ManualSelectionAnKuanAutomation().connect()
            if not self._warn_environment(self.automation):
                self.status_var.set("已取消；請調整安管視窗／校正後再試。")
                return
            self.automation.submit_place_name_query(query)
        except Exception as e:
            self.status_var.set("安管查詢未完成。")
            messagebox.showerror("安管查詢未完成", str(e))
            return

        self.pending_query = query
        self.place_continue_btn.configure(state="normal")
        self._offer_ocr_candidates(query)
        self.guide_var.set(
            f"已將「{query}」送至安管查詢。\n\n"
            "現在請在安管結果中人工選擇正確場所並進入場所資料。\n"
            "完成後回本工具按『我已選好場所，產生場所紀錄表』。\n\n"
            "本工具不讀取舊式結果表格，也不會使用『存檔(CSV)』。"
        )
        self.status_var.set(f"查詢「{query}」已送出；請在安管人工選擇場所。")

    def _offer_ocr_candidates(self, query: str) -> None:
        """Best-effort OCR suggestion for the query result row.

        This never clicks anything without an explicit human confirmation,
        and any failure here (uncalibrated grid, OCR engine unavailable,
        zero/ambiguous matches) silently falls back to the existing
        pure-manual flow described in the guide text below.
        """
        try:
            from result_ocr import find_result_candidates
            candidates = find_result_candidates(self.automation, query)
        except Exception:
            candidates = None
        if not candidates:
            return

        if len(candidates) == 1:
            c = candidates[0]
            if messagebox.askyesno(
                "OCR辨識到可能符合的場所",
                f"在查詢結果清單中辨識到這一列：\n\n{c.text}\n\n"
                "這是你要選的場所嗎？按『是』會直接幫你點這一列；"
                "按『否』請自行在安管手動選擇（不會有任何動作）。\n\n"
                "OCR辨識可能有誤，請務必核對內容後再確認。",
            ):
                self.automation.click_absolute(c.x, c.y)
                self.status_var.set("已依OCR辨識結果點擊該列，請確認安管已進入正確場所資料。")
            return

        choice = self._choose_ocr_candidate(candidates)
        if choice is not None:
            self.automation.click_absolute(choice.x, choice.y)
            self.status_var.set("已依你選擇的候選列點擊，請確認安管已進入正確場所資料。")

    def _choose_ocr_candidate(self, candidates):
        """Modal list-picker for multiple OCR candidates. Returns the chosen
        candidate, or None if the user cancelled without picking one."""
        win = tk.Toplevel(self)
        win.title("OCR辨識到多筆可能符合的場所")
        win.transient(self)
        win.grab_set()
        ttk.Label(
            win,
            text="查詢結果清單中辨識到下列多筆可能符合的場所，請選擇正確的一筆：",
            wraplength=440,
            padding=8,
        ).pack(fill="x")
        listbox = tk.Listbox(win, width=70, height=min(10, len(candidates)))
        for c in candidates:
            listbox.insert("end", c.text)
        listbox.pack(fill="both", expand=True, padx=8)

        result: list = [None]

        def confirm():
            sel = listbox.curselection()
            if sel:
                result[0] = candidates[sel[0]]
            win.destroy()

        def cancel():
            win.destroy()

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="取消（自行手動選擇）", command=cancel).pack(side="left")
        ttk.Button(btns, text="確認選這筆並點擊", command=confirm).pack(side="right")
        win.wait_window()
        return result[0]

    def continue_selected_place(self):
        if not self.automation or not self.pending_query:
            messagebox.showwarning("尚未查詢", "請先送出場所名稱查詢。")
            return
        if not messagebox.askyesno(
            "確認已選好場所",
            "請確認你已在安管選好正確場所並進入該場所資料。\n\n"
            "接下來會進入安全查察、產生『場所紀錄表』、填入最近檢查／申報次數，並從預覽器匯出 PDF。\n"
            "不會修改安管資料。\n\n是否繼續？",
        ):
            return

        try:
            self.status_var.set("正在產生場所紀錄表 PDF①……")
            self.update_idletasks()
            self.automation.prepare_selected_place_for_export()
            pdf_path = self.automation.export_place_record()
            data = parse_place_record_pdf(pdf_path)
        except Exception as e:
            self.status_var.set("場所紀錄表未完成。")
            messagebox.showerror("場所紀錄表未完成", str(e))
            return

        selected_ok = messagebox.askyesno(
            "確認場所資料",
            f"場所編號：{data.place_no or '未讀取'}\n"
            f"場所名稱：{data.display_name or '未讀取'}\n"
            f"場所地址：{data.address or '未讀取'}\n"
            f"用途／樓層：{data.purpose or '未讀取'} / {data.business_floors or '未讀取'}\n\n"
            "這就是你要製作的場所嗎？",
        )
        if not selected_ok:
            self.status_var.set("場所未確認；請回安管改選後再產生場所紀錄表。")
            return

        self.place_pdf_path = Path(pdf_path)
        self.place_data = data
        self.inspection_continue_btn.configure(state="normal")
        self.guide_var.set(
            "PDF①『場所紀錄表』已取得並確認。\n\n"
            "現在請回安管的『安全查察』頁，在檢查列表中人工選擇你要使用的那一筆紀錄。\n"
            "請選『檢查紀錄表』所對應的實際檢查紀錄，不要選空表。\n\n"
            "選好後回本工具按『我已選好檢查紀錄，產生第二份 PDF』。"
        )
        self.status_var.set("場所紀錄表 PDF① 已完成；請在安管人工選擇檢查紀錄。")

    def continue_selected_inspection(self):
        if not self.automation or not self.place_data or not self.place_pdf_path:
            messagebox.showwarning("尚未完成第一份 PDF", "請先完成場所紀錄表 PDF①。")
            return
        if not messagebox.askyesno(
            "確認已選好檢查紀錄",
            "請確認你已在安管『安全查察』列表選好要使用的那一筆檢查紀錄。\n\n"
            "接下來會按『檢查紀錄表』並從預覽器匯出 PDF②，不會修改安管資料。\n\n是否繼續？",
        ):
            return

        try:
            self.status_var.set("正在產生消防安全檢查紀錄表 PDF②……")
            self.update_idletasks()
            pdf_path = self.automation.export_inspection_record()
            detail = parse_inspection_record_pdf(pdf_path)
            merged = merge_inspection_detail(self.place_data, detail)
        except Exception as e:
            self.status_var.set("第二份檢查紀錄表未完成。")
            messagebox.showerror("第二份 PDF 未完成", str(e))
            return

        dt = detail.inspection_time.strftime("%Y/%m/%d %H:%M") if detail.inspection_time else "未讀取"
        ok = messagebox.askyesno(
            "確認第二份檢查紀錄",
            f"場所編號：{detail.place_no or '未讀取'}\n"
            f"場所名稱：{detail.place_name or '未讀取'}\n"
            f"檢查時間：{dt}\n"
            f"防火管理結果：{detail.fire_management_result or '未判讀／非本次項目'}\n"
            f"消防設備結果：{detail.equipment_result or '未判讀／非本次項目'}\n\n"
            "兩份 PDF 已通過場所識別檢查。是否以這兩份資料產生 Word？",
        )
        if not ok:
            self.status_var.set("第二份檢查紀錄未確認；請回安管改選後再試。")
            return

        self.inspection_pdf_path = Path(pdf_path)
        self.inspection_detail = detail
        self.place_data = merged
        self._save_word_from_data(self.place_pdf_path, self.place_data)

    def _save_word_from_data(self, pdf_path: Path, data):
        default_path = default_output_path(pdf_path, data)
        out_path = filedialog.asksaveasfilename(
            title="儲存 Word",
            defaultextension=".docx",
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            filetypes=[("Word 文件", "*.docx")],
        )
        if not out_path:
            self.status_var.set("兩份 PDF 已取得；未儲存 Word。")
            return
        try:
            out, _updated = fill_word_template(data, Path(out_path))
        except Exception as e:
            self.status_var.set("Word 產生失敗。")
            messagebox.showerror("產生失敗", f"{e}\n\n{traceback.format_exc()}")
            return

        self.status_var.set(f"完成：{out.name}")
        messagebox.showinfo(
            "完成",
            "Word 已產生。\n\n已使用場所紀錄表＋消防安全檢查紀錄表兩份來源；可確定的安管資料已填入，"
            "事故後才知道的事實仍以紅色【待補：○○】保留。",
        )

    # ---------- manual PDF fallback ----------
    def _build_manual_tab(self):
        ttk.Label(
            self.manual_tab,
            text="若安管預覽／匯出自動化暫時無法使用，可手動存下兩份 PDF，再由本工具本機解析。",
            wraplength=900,
        ).pack(anchor="w", pady=(0, 10))

        row1 = ttk.Frame(self.manual_tab)
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="PDF① 場所紀錄表：", width=20).pack(side="left")
        self.manual_place_path_var = tk.StringVar(value="尚未選擇")
        ttk.Entry(row1, textvariable=self.manual_place_path_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(row1, text="選擇", command=self.choose_manual_place_pdf).pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(self.manual_tab)
        row2.pack(fill="x", pady=3)
        ttk.Label(row2, text="PDF② 檢查紀錄表：", width=20).pack(side="left")
        self.manual_inspection_path_var = tk.StringVar(value="尚未選擇")
        ttk.Entry(row2, textvariable=self.manual_inspection_path_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="選擇", command=self.choose_manual_inspection_pdf).pack(side="left", padx=(8, 0))

        self.manual_tree = ttk.Treeview(self.manual_tab, columns=("field", "value"), show="headings", height=14)
        self.manual_tree.heading("field", text="欄位")
        self.manual_tree.heading("value", text="擷取結果")
        self.manual_tree.column("field", width=180, anchor="w")
        self.manual_tree.column("value", width=690, anchor="w")
        self.manual_tree.pack(fill="both", expand=True, pady=(10, 10))

        self.manual_generate_btn = ttk.Button(
            self.manual_tab,
            text="以兩份 PDF 產生 Word",
            command=self.generate_manual_word,
            state="disabled",
        )
        self.manual_generate_btn.pack(anchor="e")

    def choose_manual_place_pdf(self):
        path = filedialog.askopenfilename(title="選擇場所紀錄表 PDF", filetypes=[("PDF 檔案", "*.pdf")])
        if not path:
            return
        try:
            data = parse_place_record_pdf(path)
        except Exception as e:
            messagebox.showerror("讀取失敗", str(e))
            return
        self.manual_place_pdf = Path(path)
        self.manual_place_data = data
        self.manual_place_path_var.set(path)
        self._refresh_manual_tree()

    def choose_manual_inspection_pdf(self):
        path = filedialog.askopenfilename(title="選擇消防安全檢查紀錄表 PDF", filetypes=[("PDF 檔案", "*.pdf")])
        if not path:
            return
        try:
            detail = parse_inspection_record_pdf(path)
            if self.manual_place_data:
                self.manual_place_data = merge_inspection_detail(self.manual_place_data, detail)
        except Exception as e:
            messagebox.showerror("讀取失敗", str(e))
            return
        self.manual_inspection_pdf = Path(path)
        self.manual_inspection_detail = detail
        self.manual_inspection_path_var.set(path)
        self._refresh_manual_tree()

    def _refresh_manual_tree(self):
        for item in self.manual_tree.get_children():
            self.manual_tree.delete(item)
        if self.manual_place_data:
            for field, value in _short_status(self.manual_place_data):
                self.manual_tree.insert("", "end", values=(field, value))
        if self.manual_inspection_detail:
            for field, value in _inspection_status(self.manual_inspection_detail):
                self.manual_tree.insert("", "end", values=(field, value))
        ready = bool(self.manual_place_pdf and self.manual_place_data and self.manual_inspection_pdf and self.manual_inspection_detail)
        self.manual_generate_btn.configure(state="normal" if ready else "disabled")
        if ready:
            self.status_var.set("兩份 PDF 已讀取，可產生 Word。")

    def generate_manual_word(self):
        if not self.manual_place_pdf or not self.manual_place_data or not self.manual_inspection_detail:
            return
        try:
            data = merge_inspection_detail(self.manual_place_data, self.manual_inspection_detail)
        except Exception as e:
            messagebox.showerror("兩份 PDF 不一致", str(e))
            return
        self._save_word_from_data(self.manual_place_pdf, data)

    # ---------- calibration / settings ----------
    def _build_settings_tab(self):
        ttk.Label(
            self.settings_tab,
            text=(
                "標準環境定為 Windows 顯示縮放 100%＋安管視窗最大化。"
                "ankuan_base_profile.json 是可攜式基本校正；ankuan_config.json 是這台電腦的微調。"
            ),
            wraplength=930,
        ).pack(anchor="w")

        paths = ttk.Frame(self.settings_tab)
        paths.pack(fill="x", pady=(8, 4))
        self.config_path_var = tk.StringVar(value=str(config_path()))
        self.base_path_var = tk.StringVar(value=str(base_profile_path()))
        ttk.Label(paths, text="本機設定：").grid(row=0, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.config_path_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(paths, text="基本校正：").grid(row=1, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.base_path_var, state="readonly").grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(paths, text="開啟資料夾", command=self.open_config_folder).grid(row=0, column=2, rowspan=2, padx=(6, 0))
        paths.columnconfigure(1, weight=1)

        report = ttk.LabelFrame(self.settings_tab, text="場所紀錄表帶入次數", padding=8)
        report.pack(fill="x", pady=(4, 8))
        cfg = load_config()
        self.inspection_count_var = tk.IntVar(value=int(cfg.get("report", {}).get("inspection_history_count", 5)))
        self.submission_count_var = tk.IntVar(value=int(cfg.get("report", {}).get("submission_history_count", 2)))
        ttk.Label(report, text="最近檢查：").pack(side="left")
        ttk.Spinbox(report, from_=1, to=99, width=5, textvariable=self.inspection_count_var).pack(side="left")
        ttk.Label(report, text="次    最近申報：").pack(side="left", padx=(4, 0))
        ttk.Spinbox(report, from_=1, to=99, width=5, textvariable=self.submission_count_var).pack(side="left")
        ttk.Label(report, text="次").pack(side="left")
        ttk.Button(report, text="儲存次數設定", command=self.save_report_count_settings).pack(side="left", padx=(12, 0))
        ttk.Button(report, text="檢查目前環境", command=self.show_environment).pack(side="right")

        body = ttk.Frame(self.settings_tab)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 12))
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="完整校正", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(0, 5))
        for key, label, scope in CALIBRATION_ITEMS:
            ttk.Button(
                left,
                text=f"校正：{label}",
                width=28,
                command=lambda k=key, l=label, s=scope: self.calibrate_point(k, l, s),
            ).pack(fill="x", pady=1)

        ttk.Label(right, text="目前狀態", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(0, 5))
        self.cal_tree = ttk.Treeview(right, columns=("item", "status"), show="headings", height=12)
        self.cal_tree.heading("item", text="項目")
        self.cal_tree.heading("status", text="狀態")
        self.cal_tree.column("item", width=220, anchor="w")
        self.cal_tree.column("status", width=390, anchor="w")
        self.cal_tree.pack(fill="both", expand=True)

        tools1 = ttk.Frame(right)
        tools1.pack(fill="x", pady=(8, 0))
        ttk.Button(tools1, text="重新載入狀態", command=self.refresh_calibration_status).pack(side="left")
        ttk.Button(tools1, text="匯出診斷 TXT", command=self.export_diagnostics).pack(side="left", padx=(8, 0))

        tools2 = ttk.Frame(right)
        tools2.pack(fill="x", pady=(6, 0))
        ttk.Button(tools2, text="將目前校正設為基本校正", command=self.save_as_base_profile).pack(side="left")
        ttk.Button(tools2, text="快速校正（3點）", command=self.quick_calibrate).pack(side="left", padx=(8, 0))
        ttk.Button(tools2, text="恢復基本校正", command=self.reset_settings).pack(side="left", padx=(8, 0))

        ttk.Label(
            right,
            text=(
                "一般安管控制項以主視窗為基準；預覽 PDF／結束按鈕以『預覽』視窗為基準。"
                "建立基本校正後，把 EXE 與 ankuan_base_profile.json 一起複製到其他電腦即可帶入起始定位。"
            ),
            wraplength=580,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 0))
        self.refresh_calibration_status()

    def save_report_count_settings(self):
        try:
            set_report_counts(self.inspection_count_var.get(), self.submission_count_var.get())
        except Exception as e:
            messagebox.showerror("設定失敗", str(e))
            return
        self.refresh_calibration_status()
        self.status_var.set("已儲存場所紀錄表的檢查／申報次數。")

    def show_environment(self):
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
            env = auto.environment_info()
        except Exception as e:
            messagebox.showerror("找不到安管", str(e))
            return
        messagebox.showinfo(
            "目前安管環境",
            f"Windows 顯示縮放：約 {env['display_scale_percent']}%\n"
            f"安管視窗：{'已最大化' if env['maximized'] else '未最大化'}\n"
            f"安管視窗尺寸：{env['window_width']} × {env['window_height']}\n\n"
            "標準基準：100%＋最大化。",
        )

    def refresh_calibration_status(self):
        cfg = load_config()
        self.config_path_var.set(str(config_path()))
        self.base_path_var.set(str(base_profile_path()))
        if hasattr(self, "inspection_count_var"):
            self.inspection_count_var.set(int(cfg.get("report", {}).get("inspection_history_count", 5)))
            self.submission_count_var.set(int(cfg.get("report", {}).get("submission_history_count", 2)))
        if not hasattr(self, "cal_tree"):
            return
        for item in self.cal_tree.get_children():
            self.cal_tree.delete(item)
        points = cfg.get("points", {})
        for key, label, scope in CALIBRATION_ITEMS:
            p = points.get(key)
            if not isinstance(p, dict):
                status = "未校正"
            else:
                status = f"已校正 / {p.get('scope', scope)} / x={p.get('x', 0):.4f}, y={p.get('y', 0):.4f}"
            self.cal_tree.insert("", "end", values=(label, status))
        self.cal_tree.insert("", "end", values=("基本校正檔", "已建立" if base_profile_exists() else "尚未建立"))
        self.cal_tree.insert(
            "", "end",
            values=("場所紀錄表次數", f"檢查 {cfg['report']['inspection_history_count']} 次 / 申報 {cfg['report']['submission_history_count']} 次"),
        )
        self.cal_tree.insert("", "end", values=("結果表處理方式", "人工選擇（不讀表格、不用 CSV）"))

    def calibrate_point(self, key: str, label: str, scope: str):
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
        except Exception as e:
            messagebox.showerror("找不到安管", str(e))
            return
        extra = "\n\n請先讓『預覽』視窗保持開啟。" if scope == "preview" else ""
        ok = messagebox.askokcancel(
            f"校正：{label}",
            f"請先確認畫面包含『{label}』。{extra}\n\n"
            "按『確定』後本工具會縮小；你有 4 秒把滑鼠移到目標控制項中央並停住，不用點擊。",
        )
        if not ok:
            return
        self._calibration_auto = auto
        self.status_var.set(f"校正 {label}：4 秒內把滑鼠移到目標中央……")
        self.iconify()
        self.after(4000, lambda: self._finish_calibration(key, label, scope))

    def _finish_calibration(self, key: str, label: str, scope: str):
        try:
            auto = self._calibration_auto
            if not auto:
                raise RuntimeError("校正期間安管連線已失效。")
            r = auto.get_scope_rect(scope)
            if not r:
                raise RuntimeError("找不到校正目標視窗。" if scope == "ankuan" else "找不到『預覽』視窗，未儲存此次校正。")
            x, y = get_cursor_position()
            if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
                raise RuntimeError("滑鼠不在目標視窗內，未儲存此次校正。")
            width = max(1, r.right - r.left)
            height = max(1, r.bottom - r.top)
            save_point(key, (x - r.left) / width, (y - r.top) / height, scope=scope)
        except Exception as e:
            self.deiconify()
            self.lift()
            self.status_var.set("校正失敗。")
            messagebox.showerror("校正失敗", str(e))
            return
        finally:
            self._calibration_auto = None
        self.deiconify()
        self.lift()
        self.refresh_calibration_status()
        self.status_var.set(f"已校正：{label}")
        messagebox.showinfo("校正完成", f"已記錄『{label}』的相對位置。")

    def save_as_base_profile(self):
        cfg = load_config()
        missing = [
            label for key, label, _scope in CALIBRATION_ITEMS
            if key in REQUIRED_BASE_POINTS and not isinstance(cfg.get("points", {}).get(key), dict)
        ]
        if missing:
            messagebox.showwarning(
                "基本校正尚不完整",
                "請先完成下列必要校正，再把本機設為標準基準：\n\n" + "\n".join(f"• {x}" for x in missing),
            )
            return
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
            env = auto.environment_info()
        except Exception as e:
            messagebox.showerror("找不到安管", str(e))
            return
        if env["display_scale_percent"] != 100 or not env["maximized"]:
            messagebox.showwarning(
                "不是標準環境",
                f"目前縮放約 {env['display_scale_percent']}%，安管視窗{'已' if env['maximized'] else '未'}最大化。\n\n"
                "基本校正只允許在 100%＋最大化的標準環境建立。",
            )
            return
        reference = {
            "display_scale_percent": 100,
            "require_maximized": True,
            "window_width": env["window_width"],
            "window_height": env["window_height"],
        }
        try:
            path = save_current_as_base_profile(reference)
        except Exception as e:
            messagebox.showerror("建立失敗", str(e))
            return
        self.refresh_calibration_status()
        messagebox.showinfo(
            "基本校正已建立",
            f"已建立：\n{path}\n\n之後把 fire_tool.exe 與這個 JSON 一起帶到其他電腦，即可先套用基本定位，再做少量微調。",
        )

    @staticmethod
    def _fit_linear(xs: list[float], ys: list[float]) -> tuple[float, float]:
        if not xs or len(xs) != len(ys):
            return 1.0, 0.0
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        var = sum((x - mx) ** 2 for x in xs)
        if var < 1e-8:
            return 1.0, my - mx
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        a = cov / var
        b = my - a * mx
        return a, b

    def quick_calibrate(self):
        if not base_profile_exists():
            messagebox.showwarning("尚無基本校正", "請先在標準機完成完整校正並建立 ankuan_base_profile.json。")
            return
        base = load_base_profile()
        missing = [label for key, label in QUICK_ANCHORS if not isinstance(base.get("points", {}).get(key), dict)]
        if missing:
            messagebox.showwarning("基本校正缺少錨點", "基本校正缺少：" + "、".join(missing))
            return
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
        except Exception as e:
            messagebox.showerror("找不到安管", str(e))
            return
        self._quick_state = {"auto": auto, "base": base, "index": 0, "captured": {}}
        self._quick_next()

    def _quick_next(self):
        state = self._quick_state
        if not state:
            return
        idx = state["index"]
        if idx >= len(QUICK_ANCHORS):
            self._finish_quick_calibration()
            return
        key, label = QUICK_ANCHORS[idx]
        ok = messagebox.askokcancel(
            f"快速校正 {idx + 1}/{len(QUICK_ANCHORS)}",
            f"請讓安管顯示『{label}』。\n\n按確定後 4 秒內把滑鼠移到該控制項中央並停住。",
        )
        if not ok:
            self._quick_state = None
            return
        self.iconify()
        self.after(4000, lambda: self._quick_capture(key, label))

    def _quick_capture(self, key: str, label: str):
        try:
            state = self._quick_state
            if not state:
                return
            auto = state["auto"]
            r = auto.get_scope_rect("ankuan")
            if not r:
                raise RuntimeError("找不到安管主視窗。")
            x, y = get_cursor_position()
            if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
                raise RuntimeError("滑鼠不在安管主視窗內。")
            width = max(1, r.right - r.left)
            height = max(1, r.bottom - r.top)
            state["captured"][key] = {"x": (x - r.left) / width, "y": (y - r.top) / height}
            state["index"] += 1
        except Exception as e:
            self.deiconify()
            self.lift()
            self._quick_state = None
            messagebox.showerror("快速校正失敗", str(e))
            return
        self.deiconify()
        self.lift()
        self.after(120, self._quick_next)

    def _finish_quick_calibration(self):
        state = self._quick_state
        self._quick_state = None
        if not state:
            return
        base_points = state["base"].get("points", {})
        captured = state["captured"]
        bx, nx, by, ny = [], [], [], []
        for key, _label in QUICK_ANCHORS:
            bp = base_points.get(key)
            cp = captured.get(key)
            if not isinstance(bp, dict) or not isinstance(cp, dict):
                continue
            bx.append(float(bp["x"]))
            nx.append(float(cp["x"]))
            by.append(float(bp["y"]))
            ny.append(float(cp["y"]))
        ax, cx = self._fit_linear(bx, nx)
        ay, cy = self._fit_linear(by, ny)

        transformed = {}
        for key, p in base_points.items():
            if not isinstance(p, dict) or "x" not in p or "y" not in p:
                continue
            scope = p.get("scope", "ankuan")
            if scope == "ankuan":
                x = min(1.0, max(0.0, ax * float(p["x"]) + cx))
                y = min(1.0, max(0.0, ay * float(p["y"]) + cy))
            else:
                x, y = float(p["x"]), float(p["y"])
            transformed[key] = {"x": x, "y": y, "scope": scope}
        try:
            save_points(transformed)
        except Exception as e:
            messagebox.showerror("快速校正失敗", str(e))
            return
        self.refresh_calibration_status()
        messagebox.showinfo(
            "快速校正完成",
            "已用場所名稱、查詢按鈕、安全查察 3 個錨點修正主視窗的基本校正；預覽視窗定位沿用基本校正。\n\n"
            "若仍有單一按鈕偏移，可再對該項做完整校正。",
        )

    def open_config_folder(self):
        try:
            load_config()
            os.startfile(str(config_path().parent))
        except Exception as e:
            messagebox.showerror("無法開啟", str(e))

    def export_diagnostics(self):
        path = filedialog.asksaveasfilename(
            title="匯出安管控制項診斷",
            defaultextension=".txt",
            initialfile="ankuan_controls_diagnostic.txt",
            filetypes=[("文字檔", "*.txt")],
        )
        if not path:
            return
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
            out = auto.export_diagnostics(Path(path))
        except Exception as e:
            messagebox.showerror("診斷失敗", str(e))
            return
        messagebox.showinfo("完成", f"診斷檔已輸出：\n{out}\n\n欄位資料內容預設遮蔽。")

    def reset_settings(self):
        message = (
            "確定將這台電腦的微調恢復成『基本校正』？"
            if base_profile_exists()
            else "目前尚無基本校正檔。確定清除本機校正並恢復安全預設值？"
        )
        if not messagebox.askyesno("恢復基本校正", message):
            return
        try:
            reset_config()
        except Exception as e:
            messagebox.showerror("恢復失敗", str(e))
            return
        self.refresh_calibration_status()
        self.status_var.set("已恢復基本校正／預設設定。")


def main():
    app = App()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists() and path.suffix.lower() == ".pdf":
            app.after(150, lambda: app._load_command_line_pdf(path))
    app.mainloop()


# Backward-compatible command-line open: treat the supplied PDF as PDF①.
def _load_command_line_pdf(self, path: Path):
    try:
        data = parse_place_record_pdf(path)
    except Exception as e:
        messagebox.showerror("讀取失敗", str(e))
        return
    self.manual_place_pdf = path
    self.manual_place_data = data
    self.manual_place_path_var.set(str(path))
    self._refresh_manual_tree()


App._load_command_line_pdf = _load_command_line_pdf


if __name__ == "__main__":
    main()
