from __future__ import annotations

import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ankuan_config import (
    CALIBRATION_ITEMS,
    base_profile_exists,
    get_base_profile_summary,
    load_config,
    reset_config,
    save_current_as_base_profile,
    save_point,
    set_report_counts,
)
from ankuan_manual_flow import ManualSelectionAnKuanAutomation
from ankuan_runtime import get_cursor_position
from parser import parse_inspection_record_pdf, parse_place_record_pdf
from word_writer import default_output_path, fill_word_template, roc_date

APP_TITLE = "消防安全管理及應變表自動產製工具 v0.4.2 TEST3"
BUILD_NOTE = "v0.4.2-test3｜PDF 一律先存桌面｜內建校正｜OCR 狀態可視化｜PDF 匯出修正"

# scope=ankuan: relative to the main AnKuan window
# scope=preview: relative to the report preview window
POINT_SCOPES = {
    "preview_pdf_button": "preview",
    "preview_close_button": "preview",
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1040x760")
        self.minsize(960, 680)
        self.option_add("*Font", ("Microsoft JhengHei UI", 10))

        self.automation = None
        self.pending_query = ""
        self.place_pdf_path: Path | None = None
        self.inspection_pdf_path: Path | None = None
        self.place_data = None
        self.inspection_detail = None
        self._calibration_state = None

        self._build_ui()
        self.refresh_calibration_status()

    def _build_ui(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text=APP_TITLE, font=("Microsoft JhengHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(root, text=BUILD_NOTE, foreground="#7a4f00").pack(anchor="w", pady=(2, 0))
        ttk.Label(
            root,
            text=(
                "此工具只操作已登入的安管桌面程式，不處理帳密、不呼叫內部 API／資料庫。"
                "查詢結果與檢查紀錄仍以人工確認為主。"
            ),
            wraplength=940,
            foreground="#555555",
        ).pack(anchor="w", pady=(6, 12))

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)

        self.auto_tab = ttk.Frame(self.tabs, padding=14)
        self.pdf_tab = ttk.Frame(self.tabs, padding=14)
        self.settings_tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(self.auto_tab, text="安管自動流程")
        self.tabs.add(self.pdf_tab, text="已有 PDF → Word")
        self.tabs.add(self.settings_tab, text="校正／設定")

        self._build_auto_tab()
        self._build_pdf_tab()
        self._build_settings_tab()

        self.status_var = tk.StringVar(value="請先登入並開啟安管系統。")
        ttk.Separator(root, orient="horizontal").pack(fill="x", pady=(12, 8))
        ttk.Label(root, textvariable=self.status_var).pack(anchor="w")

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
                "工具會將關鍵字送進安管。查詢結果清單與安全查察紀錄仍由你人工選擇，"
                "程式只負責後續 PDF／Word 產製。"
            ),
            wraplength=930,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 10))

        guide = ttk.LabelFrame(self.auto_tab, text="流程", padding=16)
        guide.pack(fill="both", expand=True)
        self.guide_var = tk.StringVar(
            value=(
                "1. 輸入場所名稱關鍵字並送出查詢。\n\n"
                "2. 在安管『查詢結果』人工選好正確場所後，按下方『我已選好場所』。\n\n"
                "3. 工具產生場所紀錄表 PDF①，顯示場所編號／名稱／地址，請你再次確認。\n\n"
                "4. 確認後進入安全查察，人工選擇要用的檢查紀錄。\n\n"
                "5. 工具產生消防安全檢查紀錄表 PDF②；兩份 PDF 場所識別相符後才產生 Word。"
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

    def _build_pdf_tab(self):
        box = ttk.LabelFrame(self.pdf_tab, text="選擇兩份安管 PDF", padding=16)
        box.pack(fill="x")
        ttk.Button(box, text="選擇場所紀錄表 PDF", command=self.pick_place_pdf).pack(side="left")
        ttk.Button(box, text="選擇消防安全檢查紀錄表 PDF", command=self.pick_inspection_pdf).pack(side="left", padx=(10, 0))
        ttk.Button(box, text="產生 Word", command=self.generate_word_from_pdfs).pack(side="right")

        self.place_pdf_var = tk.StringVar(value="尚未選擇場所紀錄表 PDF")
        self.inspection_pdf_var = tk.StringVar(value="尚未選擇消防安全檢查紀錄表 PDF")
        ttk.Label(self.pdf_tab, textvariable=self.place_pdf_var, wraplength=900).pack(anchor="w", pady=(16, 6))
        ttk.Label(self.pdf_tab, textvariable=self.inspection_pdf_var, wraplength=900).pack(anchor="w", pady=6)

    def _build_settings_tab(self):
        top = ttk.Frame(self.settings_tab)
        top.pack(fill="x")

        ttk.Button(top, text="重新整理", command=self.refresh_calibration_status).pack(side="left")
        ttk.Button(top, text="重設本機設定", command=self.reset_local_config).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="將目前校正儲存為標準設定", command=self.save_as_base_profile).pack(side="left", padx=(8, 0))

        count_box = ttk.LabelFrame(self.settings_tab, text="場所紀錄表帶入次數", padding=8)
        count_box.pack(fill="x", pady=(10, 8))
        ttk.Label(count_box, text="最近檢查次數").pack(side="left")
        self.inspection_count_var = tk.StringVar(value="5")
        ttk.Spinbox(count_box, from_=1, to=99, width=5, textvariable=self.inspection_count_var).pack(side="left", padx=(5, 14))
        ttk.Label(count_box, text="最近申報次數").pack(side="left")
        self.submission_count_var = tk.StringVar(value="2")
        ttk.Spinbox(count_box, from_=1, to=99, width=5, textvariable=self.submission_count_var).pack(side="left", padx=(5, 14))
        ttk.Button(count_box, text="儲存次數設定", command=self.save_report_counts).pack(side="left")

        ttk.Label(
            self.settings_tab,
            text=(
                "校正點會以相對座標儲存在 ankuan_config.json。標準設定可另存成 ankuan_base_profile.json；"
                "另一台電腦若解析度／縮放／安管版本一致，可直接沿用，否則只需微調必要點。"
            ),
            wraplength=900,
            foreground="#555555",
        ).pack(anchor="w", pady=(0, 8))

        self.cal_tree = ttk.Treeview(self.settings_tab, columns=("item", "status"), show="headings", height=14)
        self.cal_tree.heading("item", text="項目")
        self.cal_tree.heading("status", text="狀態")
        self.cal_tree.column("item", width=300, anchor="w")
        self.cal_tree.column("status", width=560, anchor="w")
        self.cal_tree.pack(fill="both", expand=True)

        buttons = ttk.Frame(self.settings_tab)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="校正選取項目", command=self.calibrate_selected).pack(side="left")
        ttk.Button(buttons, text="匯出安管控制項診斷", command=self.export_diagnostics).pack(side="right")

    def refresh_calibration_status(self):
        cfg = load_config()
        report = cfg.get("report", {})
        self.inspection_count_var.set(str(report.get("inspection_history_count", 5)))
        self.submission_count_var.set(str(report.get("submission_history_count", 2)))
        for item in self.cal_tree.get_children():
            self.cal_tree.delete(item)
        for key, label in CALIBRATION_ITEMS:
            p = cfg.get("points", {}).get(key)
            if isinstance(p, dict) and "x" in p and "y" in p:
                status = f"已校正 / {p.get('scope', 'ankuan')} / x={p['x']:.4f}, y={p['y']:.4f}"
            else:
                status = "未校正"
            self.cal_tree.insert("", "end", iid=key, values=(label, status))
        base_text = "已有標準設定" if base_profile_exists() else "尚未建立標準設定"
        summary = get_base_profile_summary()
        self.cal_tree.insert("", "end", values=("標準設定", f"{base_text} / {summary}"))
        self.cal_tree.insert("", "end", values=("結果表處理方式", "查詢結果與檢查紀錄由人工選擇，不讀取 legacy 自繪表格內容"))

    def reset_local_config(self):
        if not messagebox.askyesno("確認重設", "要將本機設定重設為標準設定／預設值嗎？"):
            return
        path = reset_config()
        self.refresh_calibration_status()
        self.status_var.set(f"已重設本機設定：{path}")

    def save_as_base_profile(self):
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
            env = auto.environment_info()
            path = save_current_as_base_profile(env)
        except Exception as exc:
            messagebox.showerror("儲存標準設定失敗", str(exc))
            return
        self.refresh_calibration_status()
        messagebox.showinfo("完成", f"已儲存標準設定：\n{path}")

    def save_report_counts(self):
        try:
            path = set_report_counts(int(self.inspection_count_var.get()), int(self.submission_count_var.get()))
        except Exception as exc:
            messagebox.showerror("設定失敗", str(exc))
            return
        self.status_var.set(f"已儲存場所紀錄表次數設定：{path}")

    def calibrate_selected(self):
        selected = self.cal_tree.selection()
        if not selected:
            messagebox.showwarning("尚未選擇", "請先在清單選擇要校正的項目。")
            return
        key = selected[0]
        mapping = dict(CALIBRATION_ITEMS)
        if key not in mapping:
            return
        scope = POINT_SCOPES.get(key, "ankuan")
        label = mapping[key]
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
            if scope == "preview" and auto.get_scope_window("preview") is None:
                raise RuntimeError("找不到報表預覽視窗，請先在安管開啟報表預覽後再校正。")
        except Exception as exc:
            messagebox.showerror("無法開始校正", str(exc))
            return

        ok = messagebox.askokcancel(
            "開始校正",
            f"即將校正：{label}\n\n按『確定』後本工具會縮小。4 秒內把滑鼠移到目標位置並停住，不用點擊。",
        )
        if not ok:
            return
        self._calibration_state = {"auto": auto, "key": key, "label": label, "scope": scope}
        self.status_var.set(f"校正中：4 秒內把滑鼠移到「{label}」……")
        self.iconify()
        self.after(4000, self._finish_calibration)

    def _finish_calibration(self):
        state = self._calibration_state
        self._calibration_state = None
        self.deiconify()
        self.lift()
        if not state:
            return
        try:
            auto = state["auto"]
            rect = auto.get_scope_rect(state["scope"])
            if not rect:
                raise RuntimeError("找不到目標視窗。")
            x, y = get_cursor_position()
            if not (rect.left <= x <= rect.right and rect.top <= y <= rect.bottom):
                raise RuntimeError("滑鼠不在目標視窗內，未儲存此次校正。")
            width = max(1, rect.right - rect.left)
            height = max(1, rect.bottom - rect.top)
            x_ratio = (x - rect.left) / width
            y_ratio = (y - rect.top) / height
            path = save_point(state["key"], x_ratio, y_ratio, state["scope"])
        except Exception as exc:
            self.status_var.set("校正失敗。")
            messagebox.showerror("校正失敗", str(exc))
            return
        self.refresh_calibration_status()
        self.status_var.set(f"已校正：{state['label']} → {path}")

    def export_diagnostics(self):
        path = filedialog.asksaveasfilename(
            title="匯出安管控制項診斷",
            defaultextension=".txt",
            filetypes=[("文字檔", "*.txt")],
        )
        if not path:
            return
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
            auto.export_diagnostics(Path(path))
        except Exception as exc:
            messagebox.showerror("匯出失敗", str(exc))
            return
        messagebox.showinfo("完成", f"診斷已儲存：\n{path}")

    def _warn_environment(self, auto: ManualSelectionAnKuanAutomation) -> bool:
        cfg = load_config()
        validation = cfg.get("validation", {})
        if not validation.get("warn_nonstandard_environment", True):
            return True
        env = auto.environment_info()
        issues = []
        if env.get("display_scale_percent") != 100:
            issues.append(f"顯示縮放目前為 {env.get('display_scale_percent')}%，標準為 100%")
        if not env.get("maximized"):
            issues.append("安管主視窗目前不是最大化")
        if not issues:
            return True
        return messagebox.askyesno(
            "環境與標準校正不同",
            "目前環境可能讓相對座標偏移：\n\n- " + "\n- ".join(issues) + "\n\n要繼續嗎？",
        )

    def search_ankuan(self):
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning("請輸入關鍵字", "請輸入場所名稱關鍵字。")
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
        except Exception as exc:
            self.status_var.set("安管查詢未完成。")
            messagebox.showerror("安管查詢未完成", str(exc))
            return
        self.pending_query = query
        self.guide_var.set(
            f"已將「{query}」送至安管查詢。\n\n"
            "請切到安管『查詢結果』，人工選擇正確場所。\n\n"
            "選好並進入場所資料後，回本工具按『我已選好場所，產生場所紀錄表』。"
        )
        self.place_continue_btn.configure(state="normal")
        self.status_var.set(f"查詢「{query}」已送出；等待你在安管人工選場所。")

    def continue_selected_place(self):
        if not self.automation:
            try:
                self.automation = ManualSelectionAnKuanAutomation().connect()
            except Exception as exc:
                messagebox.showerror("找不到安管", str(exc))
                return
        self.status_var.set("正在產生場所紀錄表 PDF①……")
        self.update_idletasks()
        try:
            self.automation.prepare_selected_place_for_export()
            pdf_path = self.automation.export_place_record(timeout=30)
            data = parse_place_record_pdf(pdf_path)
        except Exception as exc:
            self.status_var.set("場所紀錄表產製失敗。")
            messagebox.showerror("場所紀錄表產製失敗", str(exc))
            return

        ok = messagebox.askyesno(
            "請確認場所",
            f"場所編號：{data.place_no or '未辨識'}\n"
            f"場所名稱：{data.name or '未辨識'}\n"
            f"場所地址：{data.address or '未辨識'}\n"
            f"用途／樓層：{data.usage or '未辨識'}\n\n"
            "這就是你要製作的場所嗎？",
        )
        if not ok:
            self.status_var.set("你取消了場所確認；請回安管重新選擇。")
            return

        self.place_pdf_path = pdf_path
        self.place_data = data
        try:
            self.automation.open_safety_inspection()
        except Exception as exc:
            messagebox.showwarning("已完成第一份 PDF", f"場所紀錄表已確認，但無法自動切到安全查察：\n{exc}\n\n請人工切到安全查察後繼續。")

        self.guide_var.set(
            "場所紀錄表 PDF① 已完成並確認。\n\n"
            "現在請在安管的『安全查察』列表人工選擇要使用的檢查紀錄。\n\n"
            "選好後回本工具按『我已選好檢查紀錄，產生第二份 PDF』。"
        )
        self.inspection_continue_btn.configure(state="normal")
        self.status_var.set("等待你在安全查察列表人工選擇檢查紀錄。")

    def continue_selected_inspection(self):
        if not self.automation:
            messagebox.showwarning("尚未開始", "請先完成場所查詢與場所紀錄表。")
            return
        if not self.place_data or not self.place_pdf_path:
            messagebox.showwarning("尚未完成第一份 PDF", "請先完成並確認場所紀錄表 PDF①。")
            return
        self.status_var.set("正在產生消防安全檢查紀錄表 PDF②……")
        self.update_idletasks()
        try:
            pdf_path = self.automation.export_inspection_record(timeout=30)
            detail = parse_inspection_record_pdf(pdf_path)
        except Exception as exc:
            self.status_var.set("消防安全檢查紀錄表產製失敗。")
            messagebox.showerror("消防安全檢查紀錄表產製失敗", str(exc))
            return

        if detail.place_no and self.place_data.place_no and detail.place_no != self.place_data.place_no:
            messagebox.showerror(
                "場所不一致",
                f"場所紀錄表：{self.place_data.place_no} {self.place_data.name}\n"
                f"檢查紀錄表：{detail.place_no} {detail.name}\n\n"
                "兩份 PDF 場所編號不同，程式已停止，不會產生 Word。",
            )
            self.status_var.set("兩份 PDF 場所不一致，已停止。")
            return

        if detail.name and self.place_data.name and detail.name not in self.place_data.name and self.place_data.name not in detail.name:
            if not messagebox.askyesno(
                "場所名稱需人工確認",
                f"場所紀錄表：{self.place_data.name}\n"
                f"檢查紀錄表：{detail.name}\n\n"
                "場所名稱文字不完全相同。若你已確認為同一場所，按『是』繼續；否則按『否』停止。",
            ):
                self.status_var.set("你取消了場所名稱人工確認。")
                return

        self.inspection_pdf_path = pdf_path
        self.inspection_detail = detail
        output = default_output_path(detail)
        try:
            fill_word_template(self.place_pdf_path, self.inspection_pdf_path, output)
        except Exception as exc:
            messagebox.showerror("Word 產製失敗", str(exc))
            self.status_var.set("Word 產製失敗。")
            return
        self.status_var.set(f"完成：{output}")
        messagebox.showinfo("完成", f"Word 已產生：\n{output}")

    def pick_place_pdf(self):
        path = filedialog.askopenfilename(title="選擇場所紀錄表 PDF", filetypes=[("PDF", "*.pdf")])
        if path:
            self.place_pdf_path = Path(path)
            self.place_pdf_var.set(path)

    def pick_inspection_pdf(self):
        path = filedialog.askopenfilename(title="選擇消防安全檢查紀錄表 PDF", filetypes=[("PDF", "*.pdf")])
        if path:
            self.inspection_pdf_path = Path(path)
            self.inspection_pdf_var.set(path)

    def generate_word_from_pdfs(self):
        if not self.place_pdf_path or not self.inspection_pdf_path:
            messagebox.showwarning("缺少 PDF", "請先選擇兩份 PDF。")
            return
        try:
            place = parse_place_record_pdf(self.place_pdf_path)
            detail = parse_inspection_record_pdf(self.inspection_pdf_path)
        except Exception as exc:
            messagebox.showerror("PDF 解析失敗", str(exc))
            return
        if place.place_no and detail.place_no and place.place_no != detail.place_no:
            messagebox.showerror("場所不一致", "兩份 PDF 的場所編號不同，已停止。")
            return
        output = default_output_path(detail)
        try:
            fill_word_template(self.place_pdf_path, self.inspection_pdf_path, output)
        except Exception as exc:
            messagebox.showerror("Word 產製失敗", str(exc))
            return
        messagebox.showinfo("完成", f"Word 已產生：\n{output}")

    def _load_command_line_pdf(self, path: Path):
        if path.suffix.lower() != ".pdf":
            return
        if self.place_pdf_path is None:
            self.place_pdf_path = path
            self.place_pdf_var.set(str(path))
        elif self.inspection_pdf_path is None:
            self.inspection_pdf_path = path
            self.inspection_pdf_var.set(str(path))


def main():
    app = App()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists() and path.suffix.lower() == ".pdf":
            app.after(150, lambda: app._load_command_line_pdf(path))
    app.mainloop()


if __name__ == "__main__":
    main()
