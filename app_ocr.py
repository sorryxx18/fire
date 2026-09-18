from __future__ import annotations

import sys
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from app import App as BaseApp, BUILD_NOTE
from ankuan_config import load_config, save_result_row_height_ratio
from ankuan_manual_flow import ManualSelectionAnKuanAutomation
from ankuan_runtime import get_cursor_position
from result_ocr import ResultCandidate, scan_result_candidates


class OcrCandidateApp(BaseApp):
    """v0.4.2 TEST: OCR proposes visible candidates; the human always decides."""

    def __init__(self):
        self._row_calibration_state = None
        super().__init__()
        self.status_var.set(f"{BUILD_NOTE}｜請先登入並開啟安管系統。")

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
                "安管查詢結果仍由人決定。工具只用 Windows 本機 OCR 分析『目前畫面可見範圍』，"
                "整理可能候選；你確認後才會點擊。OCR 0 筆、不可用或失敗時，直接回到人工選取。"
            ),
            wraplength=930,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 5))
        ttk.Label(
            self.auto_tab,
            text="⚠ 不自動捲動，也不會把目前可見範圍當成全部查詢結果。目標不在畫面時，可手動捲動後重新 OCR。",
            wraplength=930,
            foreground="#7a4f00",
        ).pack(anchor="w", pady=(0, 9))

        guide = ttk.LabelFrame(self.auto_tab, text="流程", padding=16)
        guide.pack(fill="both", expand=True)
        self.guide_var = tk.StringVar(
            value=(
                "1. 輸入場所名稱關鍵字並送出查詢。\n\n"
                "2. 工具 OCR 目前可見的結果區並顯示候選；單筆／多筆都必須由你確認後才點。\n\n"
                "3. OCR 0 筆或失敗就人工選取；也可先在安管手動捲動，再按『重新 OCR 目前畫面』。\n\n"
                "4. 選定場所後產生場所紀錄表 PDF①，仍以 PDF 的場所編號／名稱／地址做二次核對。\n\n"
                "5. 安全查察紀錄仍由人工選擇，再產 PDF②；兩份 PDF 場所識別相符後才產生 Word。"
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
        self.ocr_retry_btn = ttk.Button(
            actions,
            text="重新 OCR 目前畫面",
            command=self.retry_visible_ocr,
            state="disabled",
        )
        self.ocr_retry_btn.pack(side="left", padx=(8, 0))
        self.inspection_continue_btn = ttk.Button(
            actions,
            text="我已選好檢查紀錄，產生第二份 PDF",
            command=self.continue_selected_inspection,
            state="disabled",
        )
        self.inspection_continue_btn.pack(side="right")

    def search_ankuan(self):
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning("請輸入關鍵字", "請輸入場所名稱關鍵字，例如「洲際」或「大巨蛋」。")
            return

        self.place_continue_btn.configure(state="disabled")
        self.inspection_continue_btn.configure(state="disabled")
        self.ocr_retry_btn.configure(state="disabled")
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
        self.place_continue_btn.configure(state="normal")
        self.ocr_retry_btn.configure(state="normal")
        self._offer_ocr_candidates(query, show_zero_message=False)

    def retry_visible_ocr(self):
        if not self.automation or not self.pending_query:
            messagebox.showwarning("尚未查詢", "請先送出場所名稱查詢。")
            return
        self._offer_ocr_candidates(self.pending_query, show_zero_message=True)

    def _manual_fallback_guide(self, query: str, reason: str = ""):
        detail = f"\nOCR：{reason}\n" if reason else "\n"
        self.guide_var.set(
            f"已將「{query}」送至安管查詢。{detail}\n"
            "目前請在安管人工選擇正確場所。若目標不在目前畫面，可先手動捲動後，"
            "再回本工具按『重新 OCR 目前畫面』。工具不會自動捲動。\n\n"
            "人工選好並進入場所資料後，按『我已選好場所，產生場所紀錄表』。"
        )
        self.status_var.set(f"查詢「{query}」已送出；可人工選取或重新 OCR 目前畫面。")

    def _offer_ocr_candidates(self, query: str, *, show_zero_message: bool) -> bool:
        """Offer visible OCR candidates. Never clicks without explicit confirmation."""
        if not self.automation:
            return False

        self.status_var.set("OCR：正在辨識目前畫面可見的查詢結果……")
        self.update_idletasks()

        try:
            report = scan_result_candidates(self.automation, query)
        except Exception as exc:
            self._manual_fallback_guide(query, f"辨識失敗（{exc}），已回到人工模式。")
            return False

        # Make OCR state visible every time; no more silent fallback.
        self.status_var.set(report.message)

        if not report.candidates:
            self._manual_fallback_guide(query, report.message.replace("OCR：", "", 1))
            if show_zero_message or report.status in {"engine_unavailable", "grid_not_found", "capture_failed", "recognition_failed"}:
                title = "OCR 狀態"
                messagebox.showinfo(
                    title,
                    report.message
                    + "\n\n原本人工選取流程仍可正常使用。"
                    + ("\n若目標不在目前畫面，可在安管手動捲動後按『重新 OCR 目前畫面』。" if report.status == "no_candidates" else ""),
                )
            return False

        self.guide_var.set(
            f"{report.message}\n\n"
            "⚠ 只分析目前畫面可見結果，不代表全部查詢結果。"
            "單筆／多筆都必須由你確認後才會點擊。"
        )
        choice = self._choose_ocr_candidate(report.candidates)
        if choice is None:
            self._manual_fallback_guide(query, "你沒有確認 OCR 候選，程式沒有點擊任何列。")
            return False

        try:
            self.automation.click_absolute(choice.x, choice.y)
        except Exception as exc:
            self._manual_fallback_guide(query, f"候選已確認，但點擊失敗（{exc}）。")
            messagebox.showwarning("未完成列選取", f"無法安全點擊候選列：\n{exc}\n\n請改用人工選取。")
            return False

        label = choice.name or choice.row_text
        source_text = "已校正結果區" if report.source == "calibrated" else "自動定位結果表"
        self.guide_var.set(
            f"OCR（{source_text}）已依你的確認點選候選：{label}\n\n"
            "請先看安管畫面確認目前場所是否正確，再按『我已選好場所，產生場所紀錄表』。\n\n"
            "後續場所紀錄表 PDF①仍會顯示場所編號、名稱、地址，作為第二道人工核對。"
        )
        self.status_var.set(f"OCR：候選已由你確認並點選（{source_text}）；請核對安管畫面後繼續。")
        return True

    def _choose_ocr_candidate(self, candidates: list[ResultCandidate]) -> ResultCandidate | None:
        warning = "⚠ 僅分析目前畫面可見的查詢結果；不是全部查詢結果。"
        if len(candidates) == 1:
            c = candidates[0]
            ok = messagebox.askyesno(
                "確認 OCR 場所候選",
                f"場所編號：{c.place_no or '未辨識'}\n"
                f"場所名稱：{c.name or '未完整辨識'}\n"
                f"地址：{c.address or '未完整辨識'}\n"
                f"OCR 原始列文字：{c.row_text}\n"
                f"匹配程度：{c.match_score:.0%}\n\n"
                f"{warning}\n\n"
                "按『是』才會點擊這一列；按『否』不會執行任何點擊，可改用人工選取。",
            )
            return c if ok else None

        dialog = tk.Toplevel(self)
        dialog.title("選擇 OCR 場所候選")
        dialog.geometry("980x430")
        dialog.minsize(820, 360)
        dialog.transient(self)

        ttk.Label(
            dialog,
            text=f"目前可見範圍找到 {len(candidates)} 筆候選。請自行選擇；程式不會依分數自動決定。",
            wraplength=930,
        ).pack(anchor="w", padx=14, pady=(14, 5))
        ttk.Label(dialog, text=warning, foreground="#7a4f00").pack(anchor="w", padx=14, pady=(0, 10))

        tree = ttk.Treeview(
            dialog,
            columns=("no", "name", "address", "score", "raw"),
            show="headings",
            selectmode="browse",
            height=11,
        )
        tree.heading("no", text="場所編號")
        tree.heading("name", text="場所名稱")
        tree.heading("address", text="地址")
        tree.heading("score", text="匹配")
        tree.heading("raw", text="OCR 原始列文字")
        tree.column("no", width=90, anchor="w")
        tree.column("name", width=220, anchor="w")
        tree.column("address", width=260, anchor="w")
        tree.column("score", width=65, anchor="center")
        tree.column("raw", width=300, anchor="w")
        tree.pack(fill="both", expand=True, padx=14)

        item_map: dict[str, ResultCandidate] = {}
        for index, candidate in enumerate(candidates):
            item = tree.insert(
                "",
                "end",
                values=(
                    candidate.place_no or "",
                    candidate.name or "",
                    candidate.address or "",
                    f"{candidate.match_score:.0%}",
                    candidate.row_text,
                ),
            )
            item_map[item] = candidate
            if index == 0:
                tree.selection_set(item)
                tree.focus(item)

        result: dict[str, ResultCandidate | None] = {"candidate": None}

        def confirm():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("尚未選擇", "請先選一筆候選。", parent=dialog)
                return
            result["candidate"] = item_map[selected[0]]
            dialog.destroy()

        def cancel():
            result["candidate"] = None
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=14, pady=14)
        ttk.Button(buttons, text="改用人工選取", command=cancel).pack(side="left")
        ttk.Button(buttons, text="確認並選取", command=confirm).pack(side="right")
        tree.bind("<Double-1>", lambda _event: confirm())
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.grab_set()
        self.wait_window(dialog)
        return result["candidate"]

    # ---------- settings / optional row-height calibration ----------
    def _build_settings_tab(self):
        super()._build_settings_tab()
        box = ttk.LabelFrame(self.settings_tab, text="OCR 查詢結果輔助", padding=8)
        box.pack(fill="x", pady=(8, 0))
        ttk.Label(
            box,
            text=(
                "結果區域請用上方『查詢結果清單－左上角／右下角』兩個校正點。"
                "列高不是必要條件；若實機聚列不穩，可用相鄰兩列中心點補做列高校正。"
            ),
            wraplength=690,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(box, text="校正相鄰列高", command=self.calibrate_result_row_height).pack(side="right", padx=(8, 0))

    def refresh_calibration_status(self):
        super().refresh_calibration_status()
        if not hasattr(self, "cal_tree"):
            return
        cfg = load_config()
        ratio = cfg.get("ocr", {}).get("result_row_height_ratio")
        row_status = f"已校正 / 視窗高度比例={float(ratio):.5f}" if ratio is not None else "未校正（非必要；OCR 會自動聚列）"
        self.cal_tree.insert("", "end", values=("OCR 相鄰列高", row_status))
        for item in self.cal_tree.get_children():
            values = self.cal_tree.item(item, "values")
            if values and values[0] == "結果表處理方式":
                self.cal_tree.item(item, values=("結果表處理方式", "OCR 找候選 → 人工確認後才點；0 筆／失敗退回人工"))

    def calibrate_result_row_height(self):
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
        except Exception as exc:
            messagebox.showerror("找不到安管", str(exc))
            return
        ok = messagebox.askokcancel(
            "校正 OCR 相鄰列高",
            "請先讓安管停在『查詢結果』，並讓至少兩筆相鄰資料列清楚可見。\n\n"
            "接下來會依序記錄兩個相鄰資料列的中心 Y 位置；不需要知道 pixel。",
        )
        if not ok:
            return
        self._row_calibration_state = {"auto": auto, "step": 0, "points": []}
        self._start_row_calibration_step()

    def _start_row_calibration_step(self):
        state = self._row_calibration_state
        if not state:
            return
        step = int(state["step"])
        if step >= 2:
            self._save_row_calibration()
            return
        label = "第一列資料的中心" if step == 0 else "緊鄰下一列資料的中心"
        ok = messagebox.askokcancel(
            f"列高校正 {step + 1}/2",
            f"按『確定』後本工具會縮小。4 秒內把滑鼠移到「{label}」並停住，不用點擊。",
        )
        if not ok:
            self._row_calibration_state = None
            return
        self.status_var.set(f"列高校正：4 秒內把滑鼠移到 {label}……")
        self.iconify()
        self.after(4000, self._capture_row_calibration_step)

    def _capture_row_calibration_step(self):
        try:
            state = self._row_calibration_state
            if not state:
                return
            auto = state["auto"]
            rect = auto.get_scope_rect("ankuan")
            if not rect:
                raise RuntimeError("找不到安管主視窗。")
            x, y = get_cursor_position()
            if not (rect.left <= x <= rect.right and rect.top <= y <= rect.bottom):
                raise RuntimeError("滑鼠不在安管主視窗內，未儲存此次校正。")
            height = max(1, rect.bottom - rect.top)
            state["points"].append((y - rect.top) / height)
            state["step"] += 1
        except Exception as exc:
            self.deiconify()
            self.lift()
            self._row_calibration_state = None
            self.status_var.set("OCR 列高校正失敗。")
            messagebox.showerror("校正失敗", str(exc))
            return
        self.deiconify()
        self.lift()
        self.after(150, self._start_row_calibration_step)

    def _save_row_calibration(self):
        state = self._row_calibration_state
        self._row_calibration_state = None
        if not state or len(state.get("points", [])) != 2:
            return
        try:
            ratio = abs(float(state["points"][1]) - float(state["points"][0]))
            save_result_row_height_ratio(ratio)
        except Exception as exc:
            messagebox.showerror("校正失敗", str(exc))
            return
        self.refresh_calibration_status()
        self.status_var.set("已儲存 OCR 相鄰列高比例。")
        messagebox.showinfo("校正完成", "已由相鄰兩列中心點計算列高比例；OCR 聚列仍保留自動判斷作為主要機制。")


def main():
    app = OcrCandidateApp()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists() and path.suffix.lower() == ".pdf":
            app.after(150, lambda: app._load_command_line_pdf(path))
    app.mainloop()


if __name__ == "__main__":
    main()
