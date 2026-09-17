from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ankuan_config import config_path, load_config, reset_config, save_point
from ankuan_manual_flow import ManualSelectionAnKuanAutomation
from ankuan_runtime import get_cursor_position
from parser import parse_place_record_pdf
from word_writer import default_output_path, fill_word_template, roc_date

APP_TITLE = "消防安全管理及應變表自動產製工具"

# 查詢結果表格不再自動讀取，也不再使用 CSV fallback。
# 場所選擇由使用者在安管結果畫面完成。
CALIBRATION_ITEMS = [
    ("place_name", "場所名稱欄位"),
    ("query_button", "查詢資料按鈕"),
    ("condition_tab", "條件設定頁籤"),
    ("result_tab", "查詢結果頁籤"),
    ("safety_tab", "安全查察頁籤"),
    ("place_record_button", "場所紀錄表按鈕"),
    ("report_confirm_button", "報表選項確認按鈕"),
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("960x700")
        self.minsize(840, 590)
        self.automation: ManualSelectionAnKuanAutomation | None = None
        self.pending_query = ""
        self.pdf_path: Path | None = None
        self.data = None
        self._calibration_auto: ManualSelectionAnKuanAutomation | None = None
        self._build_ui()

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text=APP_TITLE, font=("Microsoft JhengHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text=(
                "先正常登入安管系統。工具只操作既有安管畫面的查詢與報表匯出，"
                "不處理帳密、不連內部 API；舊式控制項可用「校正／設定」微調。"
            ),
            wraplength=920,
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
                "程式只負責把關鍵字正確輸入安管並送出查詢。查詢結果的舊式表格不再自動讀取，"
                "也不使用「存檔(CSV)」。請直接在安管畫面人工選擇正確場所。"
            ),
            wraplength=870,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 10))

        guide = ttk.LabelFrame(self.auto_tab, text="查詢後請依序操作", padding=16)
        guide.pack(fill="both", expand=True)
        self.guide_var = tk.StringVar(
            value=(
                "1. 在上方輸入場所名稱關鍵字並按「送出查詢」。\n\n"
                "2. 程式會切到安管的查詢結果畫面。\n\n"
                "3. 請在安管結果表格中人工點選正確場所，讓該場所成為目前選取的資料。\n\n"
                "4. 回到本工具，按下方「我已在安管選好場所，繼續」。\n\n"
                "5. 工具才會進入安全查察、匯出場所紀錄表；PDF 解析後會再顯示場所名稱與地址讓你確認。"
            )
        )
        ttk.Label(guide, textvariable=self.guide_var, justify="left", wraplength=820).pack(anchor="nw")

        actions = ttk.Frame(self.auto_tab)
        actions.pack(fill="x", pady=(12, 0))
        self.continue_btn = ttk.Button(
            actions,
            text="我已在安管選好場所，繼續",
            command=self.continue_selected_place,
            state="disabled",
        )
        self.continue_btn.pack(side="right")

    def _build_manual_tab(self):
        ttk.Label(
            self.manual_tab,
            text="如果安管介面自動化暫時無法辨識，可手動匯出「場所紀錄表」PDF，再由本工具產生 Word。",
            wraplength=880,
        ).pack(anchor="w", pady=(0, 10))

        row = ttk.Frame(self.manual_tab)
        row.pack(fill="x")
        self.path_var = tk.StringVar(value="尚未選擇 PDF")
        ttk.Entry(row, textvariable=self.path_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="選擇 PDF", command=self.choose_pdf).pack(side="left", padx=(8, 0))

        self.manual_tree = ttk.Treeview(
            self.manual_tab,
            columns=("field", "value"),
            show="headings",
            height=10,
        )
        self.manual_tree.heading("field", text="欄位")
        self.manual_tree.heading("value", text="擷取結果")
        self.manual_tree.column("field", width=170, anchor="w")
        self.manual_tree.column("value", width=640, anchor="w")
        self.manual_tree.pack(fill="both", expand=True, pady=(10, 10))

        self.manual_generate_btn = ttk.Button(
            self.manual_tab,
            text="產生 Word",
            command=self.generate_manual_word,
            state="disabled",
        )
        self.manual_generate_btn.pack(anchor="e")

    def _build_settings_tab(self):
        ttk.Label(
            self.settings_tab,
            text=(
                "校正點會存成相對於安管視窗的位置，不是固定螢幕座標。"
                "校正資料寫入 EXE 同資料夾的 ankuan_config.json；微調位置不需重新打包。"
            ),
            wraplength=880,
        ).pack(anchor="w")

        self.config_path_var = tk.StringVar(value=str(config_path()))
        path_row = ttk.Frame(self.settings_tab)
        path_row.pack(fill="x", pady=(8, 8))
        ttk.Label(path_row, text="設定檔：").pack(side="left")
        ttk.Entry(path_row, textvariable=self.config_path_var, state="readonly").pack(side="left", fill="x", expand=True, padx=(6, 8))
        ttk.Button(path_row, text="開啟資料夾", command=self.open_config_folder).pack(side="left")

        body = ttk.Frame(self.settings_tab)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 12))
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="校正控制項", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(0, 5))
        for key, label in CALIBRATION_ITEMS:
            ttk.Button(left, text=f"校正：{label}", width=26, command=lambda k=key, l=label: self.calibrate_point(k, l)).pack(fill="x", pady=2)

        ttk.Label(right, text="目前狀態", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(0, 5))
        self.cal_tree = ttk.Treeview(right, columns=("item", "status"), show="headings", height=10)
        self.cal_tree.heading("item", text="項目")
        self.cal_tree.heading("status", text="狀態")
        self.cal_tree.column("item", width=220, anchor="w")
        self.cal_tree.column("status", width=300, anchor="w")
        self.cal_tree.pack(fill="both", expand=True)

        tools = ttk.Frame(right)
        tools.pack(fill="x", pady=(8, 0))
        ttk.Button(tools, text="重新載入狀態", command=self.refresh_calibration_status).pack(side="left")
        ttk.Button(tools, text="匯出診斷 TXT", command=self.export_diagnostics).pack(side="left", padx=(8, 0))
        ttk.Button(tools, text="恢復預設值", command=self.reset_settings).pack(side="left", padx=(8, 0))

        ttk.Label(
            right,
            text=(
                "校正方式：先把安管停在正確畫面，按校正後有 4 秒，"
                "把滑鼠移到目標控制項中央並停住，不用點擊。"
                "「報表選項確認按鈕」需先人工開啟該選項視窗再校正。"
            ),
            wraplength=520,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 0))
        self.refresh_calibration_status()

    # ---------- AnKuan semi-automatic flow ----------
    def search_ankuan(self):
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning("請輸入關鍵字", "請輸入場所名稱關鍵字，例如「洲際」或「大巨蛋」。")
            return

        self.continue_btn.configure(state="disabled")
        self.status_var.set("正在將查詢條件送入安管……")
        self.update_idletasks()
        try:
            self.automation = ManualSelectionAnKuanAutomation().connect()
            self.automation.submit_place_name_query(query)
        except Exception as e:
            self.status_var.set("安管查詢未完成。")
            messagebox.showerror("安管查詢未完成", str(e))
            return

        self.pending_query = query
        self.continue_btn.configure(state="normal")
        self.guide_var.set(
            f"已將「{query}」送至安管查詢。\n\n"
            "現在請直接在安管的查詢結果表格中人工選擇正確場所。\n"
            "選好後不要再按本工具的查詢；回到這裡按「我已在安管選好場所，繼續」。\n\n"
            "本工具不會讀取舊式結果表格，也不會按「存檔(CSV)」。"
        )
        self.status_var.set(f"查詢「{query}」已送出；請在安管人工選擇場所。")

    def continue_selected_place(self):
        if not self.automation or not self.pending_query:
            messagebox.showwarning("尚未查詢", "請先送出場所名稱查詢。")
            return

        if not messagebox.askyesno(
            "確認已選好場所",
            "請確認你已在安管查詢結果表格中點選正確場所。\n\n"
            "接下來只會執行安全查察／場所紀錄表的讀取與報表產出，不會修改安管資料。\n\n"
            "是否繼續？",
        ):
            return

        try:
            self.status_var.set("正在從目前選取的安管場所匯出場所紀錄表……")
            self.update_idletasks()
            self.automation.prepare_selected_place_for_export()
            pdf_path = self.automation.export_place_record()
            self.status_var.set("場所紀錄表已取得，正在解析……")
            self.update_idletasks()
            data = parse_place_record_pdf(pdf_path)
        except Exception as e:
            self.status_var.set("自動匯出未完成。")
            messagebox.showerror("自動匯出未完成", str(e))
            return

        selected_ok = messagebox.askyesno(
            "確認場所資料",
            f"場所名稱：{data.display_name or '未讀取'}\n"
            f"場所地址：{data.address or '未讀取'}\n"
            f"用途／樓層：{data.purpose or '未讀取'} / {data.business_floors or '未讀取'}\n\n"
            "這就是你要製作的場所嗎？\n\n"
            "若不是，請按「否」，回安管改選正確場所後可再按一次「我已在安管選好場所，繼續」。",
        )
        if not selected_ok:
            self.status_var.set("場所未確認；請回安管改選後再繼續。")
            return

        self._save_word_from_data(pdf_path, data)

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
            self.status_var.set("已取得場所資料；未儲存 Word。")
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
            "Word 已產生。\n\n安管可取得的資料已自動填入；事故後才知道的事實以紅色【待補：○○】保留完整句型。",
        )

    # ---------- calibration / settings ----------
    def refresh_calibration_status(self):
        cfg = load_config()
        self.config_path_var.set(str(config_path()))
        if not hasattr(self, "cal_tree"):
            return
        for item in self.cal_tree.get_children():
            self.cal_tree.delete(item)
        points = cfg.get("points", {})
        for key, label in CALIBRATION_ITEMS:
            p = points.get(key)
            status = "未校正" if not p else f"已校正  x={p.get('x', 0):.4f}, y={p.get('y', 0):.4f}"
            self.cal_tree.insert("", "end", values=(label, status))
        val = cfg.get("validation", {})
        self.cal_tree.insert("", "end", values=("輸入讀回驗證", "啟用" if val.get("require_input_echo", True) else "停用"))
        self.cal_tree.insert("", "end", values=("結果表處理方式", "人工選擇（不讀表格、不用 CSV）"))

    def calibrate_point(self, key: str, label: str):
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
        except Exception as e:
            messagebox.showerror("找不到安管", str(e))
            return
        ok = messagebox.askokcancel(
            f"校正：{label}",
            f"請先確認安管目前顯示包含「{label}」的正確畫面。\n\n"
            "按「確定」後，本工具會縮小。你有 4 秒把滑鼠移到目標控制項的中央並停住，不用點擊。\n"
            "4 秒後會自動記錄相對位置。",
        )
        if not ok:
            return
        self._calibration_auto = auto
        self.status_var.set(f"校正 {label}：4 秒內把滑鼠移到目標中央……")
        self.iconify()
        self.after(4000, lambda: self._finish_calibration(key, label))

    def _finish_calibration(self, key: str, label: str):
        try:
            auto = self._calibration_auto
            if not auto or not auto.window:
                raise RuntimeError("校正期間安管連線已失效。")
            x, y = get_cursor_position()
            r = auto.window.rectangle()
            if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
                raise RuntimeError("滑鼠不在安管視窗內，未儲存此次校正。")
            width = max(1, r.right - r.left)
            height = max(1, r.bottom - r.top)
            save_point(key, (x - r.left) / width, (y - r.top) / height)
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
        messagebox.showinfo("校正完成", f"已記錄「{label}」的相對位置。\n之後微調 ankuan_config.json 不需要重新打包 EXE。")

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
        messagebox.showinfo("完成", f"診斷檔已輸出：\n{out}\n\n欄位資料內容預設遮蔽，只保留控制項結構與安全介面文字。")

    def reset_settings(self):
        if not messagebox.askyesno("恢復預設值", "確定清除所有安管校正點並恢復預設設定？"):
            return
        try:
            reset_config()
        except Exception as e:
            messagebox.showerror("恢復失敗", str(e))
            return
        self.refresh_calibration_status()
        self.status_var.set("已恢復安管預設設定。")

    # ---------- manual PDF ----------
    def choose_pdf(self):
        path = filedialog.askopenfilename(title="選擇場所紀錄表 PDF", filetypes=[("PDF 檔案", "*.pdf")])
        if path:
            self.load_pdf(Path(path))

    def load_pdf(self, path: Path):
        try:
            self.status_var.set("讀取 PDF 中……")
            self.update_idletasks()
            data = parse_place_record_pdf(path)
        except Exception as e:
            self.data = None
            self.manual_generate_btn.configure(state="disabled")
            self.status_var.set("讀取失敗。")
            messagebox.showerror("讀取失敗", str(e))
            return

        self.pdf_path = path
        self.data = data
        self.path_var.set(str(path))
        for item in self.manual_tree.get_children():
            self.manual_tree.delete(item)
        for field, value in _short_status(data):
            self.manual_tree.insert("", "end", values=(field, value))
        self.manual_generate_btn.configure(state="normal")
        self.status_var.set("PDF 讀取完成，可產生 Word。")

    def generate_manual_word(self):
        if not self.pdf_path or not self.data:
            return
        self._save_word_from_data(self.pdf_path, self.data)


def main():
    app = App()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists() and path.suffix.lower() == ".pdf":
            app.after(150, lambda: app.load_pdf(path))
    app.mainloop()


if __name__ == "__main__":
    main()
