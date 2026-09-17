from __future__ import annotations

import sys
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from app import App as BaseApp
from ankuan_config import (
    load_config,
    save_ocr_calibration,
    save_points,
    save_result_grid_area,
    save_result_row_height_ratio,
)
from ankuan_manual_flow import ManualSelectionAnKuanAutomation
from ankuan_ocr import OcrReadError, OcrUnavailable, VisibleOcrCandidate, windows_ocr_status
from ankuan_runtime import get_cursor_position


class OcrCandidateApp(BaseApp):
    """v0.4.1 UI: OCR proposes visible result rows; the user still chooses."""

    def __init__(self):
        self._ocr_cal_state = None
        super().__init__()

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
                "送出查詢後，工具只對『目前畫面可見的結果區』做 Windows 本機 OCR，整理場所候選；"
                "OCR 不會自行決定或點擊，必須由你確認。OCR 無法使用、辨識失敗或 0 筆時，"
                "一律退回原本人工選取流程。"
            ),
            wraplength=930,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 8))

        ttk.Label(
            self.auto_tab,
            text="⚠ 目前不自動捲動，也不把可視範圍視為全部查詢結果。目標不在畫面時，可手動捲動後重新辨識。",
            wraplength=930,
            foreground="#7a4f00",
        ).pack(anchor="w", pady=(0, 8))

        guide = ttk.LabelFrame(self.auto_tab, text="流程", padding=16)
        guide.pack(fill="both", expand=True)
        self.guide_var = tk.StringVar(
            value=(
                "1. 輸入場所名稱關鍵字並送出查詢。\n\n"
                "2. 工具 OCR 目前可見結果並顯示候選；你按『確認並選取』後才會點該列。\n\n"
                "3. OCR 失敗／0 筆時，直接在安管人工選取；也可手動捲動後按『重新 OCR 目前畫面』。\n\n"
                "4. 選定場所後產生『場所紀錄表』PDF①，仍由 PDF 名稱／地址做第二次人工核對。\n\n"
                "5. 安全查察紀錄仍由人工選擇，再產生 PDF②；兩份 PDF 場所識別相符後才產 Word。"
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
        except Exception as e:
            self.status_var.set("安管查詢未完成。")
            messagebox.showerror("安管查詢未完成", str(e))
            return

        self.pending_query = query
        self.place_continue_btn.configure(state="normal")
        self.ocr_retry_btn.configure(state="normal")
        self._attempt_visible_ocr(query, show_fallback_dialog=False)

    def retry_visible_ocr(self):
        if not self.automation or not self.pending_query:
            messagebox.showwarning("尚未查詢", "請先送出場所名稱查詢。")
            return
        self._attempt_visible_ocr(self.pending_query, show_fallback_dialog=True)

    def _set_manual_fallback_guide(self, query: str, reason: str = ""):
        reason_line = f"\nOCR：{reason}\n" if reason else "\n"
        self.guide_var.set(
            f"已將「{query}」送至安管查詢。{reason_line}\n"
            "目前請在安管結果中人工選擇正確場所。若目標不在目前畫面，可先手動捲動，"
            "再回本工具按『重新 OCR 目前畫面』；工具不會自動捲動。\n\n"
            "人工選好並進入場所資料後，按『我已選好場所，產生場所紀錄表』。"
        )
        self.status_var.set(f"查詢「{query}」已送出；目前使用人工選取／可重新 OCR。")

    def _attempt_visible_ocr(self, query: str, *, show_fallback_dialog: bool) -> bool:
        if not self.automation:
            return False
        self.status_var.set("正在辨識目前畫面可見的查詢結果……")
        self.update_idletasks()
        try:
            candidates, language = self.automation.read_visible_ocr_candidates(query)
        except (OcrUnavailable, OcrReadError) as e:
            reason = str(e)
            self._set_manual_fallback_guide(query, reason)
            if show_fallback_dialog:
                messagebox.showinfo("改用人工選取", reason)
            return False
        except Exception as e:
            reason = f"OCR 發生未預期錯誤：{e}；已改用人工選取。"
            self._set_manual_fallback_guide(query, reason)
            if show_fallback_dialog:
                messagebox.showwarning("OCR 未完成", reason)
            return False

        if not candidates:
            reason = "目前可見範圍沒有找到符合的 OCR 候選。"
            self._set_manual_fallback_guide(query, reason)
            if show_fallback_dialog:
                messagebox.showinfo(
                    "目前畫面 0 筆候選",
                    "目前只辨識畫面可見範圍，沒有找到符合候選。\n\n"
                    "若目標不在目前畫面，請在安管手動捲動後再按『重新 OCR 目前畫面』，"
                    "或直接人工選取。",
                )
            return False

        chosen = self._choose_ocr_candidate(candidates, language)
        if chosen is None:
            self._set_manual_fallback_guide(query, "你沒有確認 OCR 候選，未執行任何列點擊。")
            return False

        try:
            self.automation.click_ocr_candidate(chosen)
        except Exception as e:
            reason = f"候選已確認，但無法安全點擊該列：{e}"
            self._set_manual_fallback_guide(query, reason)
            messagebox.showwarning("未執行選取", reason)
            return False

        label = chosen.display_name or chosen.row_text
        self.guide_var.set(
            f"已依你的確認點選 OCR 候選：{label}\n\n"
            "請先看安管畫面確認目前場所是否正確；確認後按『我已選好場所，產生場所紀錄表』。\n\n"
            "後續仍會從場所紀錄表 PDF 再顯示場所編號、名稱、地址供你二次確認。"
        )
        self.status_var.set("OCR 候選已由你確認並點選；請核對安管畫面後繼續。")
        return True

    def _choose_ocr_candidate(self, candidates: list[VisibleOcrCandidate], language: str) -> VisibleOcrCandidate | None:
        warning = "⚠ 僅分析目前畫面可見的查詢結果；不是全部查詢結果。"
        if len(candidates) == 1:
            c = candidates[0]
            ok = messagebox.askyesno(
                "確認 OCR 場所候選",
                f"場所編號：{c.place_no or '未辨識'}\n"
                f"場所名稱：{c.name or '未完整辨識'}\n"
                f"地址：{c.address or '未完整辨識'}\n"
                f"OCR 列文字：{c.row_text}\n"
                f"匹配程度：{c.match_score:.0%}\n"
                f"OCR 語言：{language}\n\n"
                f"{warning}\n\n"
                "按『是』才會點擊這一列；按『否』不會點擊，可改由人工選取。",
            )
            return c if ok else None

        dialog = tk.Toplevel(self)
        dialog.title("選擇 OCR 場所候選")
        dialog.geometry("980x430")
        dialog.minsize(820, 360)
        dialog.transient(self)

        ttk.Label(
            dialog,
            text=(
                f"Windows OCR（{language}）找到 {len(candidates)} 筆目前可見候選。"
                "請自行選擇；工具不會依分數自動決定。"
            ),
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

        item_map: dict[str, VisibleOcrCandidate] = {}
        for idx, c in enumerate(candidates):
            item = tree.insert(
                "",
                "end",
                values=(c.place_no or "", c.name or "", c.address or "", f"{c.match_score:.0%}", c.row_text),
            )
            item_map[item] = c
            if idx == 0:
                tree.selection_set(item)
                tree.focus(item)

        result: dict[str, VisibleOcrCandidate | None] = {"candidate": None}

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
        tree.bind("<Double-1>", lambda _e: confirm())
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.grab_set()
        self.wait_window(dialog)
        return result["candidate"]

    # ---------- OCR settings / calibration ----------
    def _build_settings_tab(self):
        super()._build_settings_tab()
        ocr_bar = ttk.LabelFrame(self.settings_tab, text="OCR 候選輔助", padding=8)
        ocr_bar.pack(fill="x", pady=(8, 0))
        ttk.Label(
            ocr_bar,
            text="Windows 本機繁中 OCR；失敗永遠退回人工流程。結果區域與列高皆以安管視窗比例保存。",
            wraplength=650,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(ocr_bar, text="OCR 設定／校正", command=self.open_ocr_settings).pack(side="right", padx=(8, 0))

    def refresh_calibration_status(self):
        super().refresh_calibration_status()
        if not hasattr(self, "cal_tree"):
            return
        cfg = load_config()
        ocr = cfg.get("ocr", {})
        area = ocr.get("result_grid_area")
        if isinstance(area, dict):
            area_status = (
                f"已校正 / L={float(area.get('left', 0)):.4f}, T={float(area.get('top', 0)):.4f}, "
                f"R={float(area.get('right', 0)):.4f}, B={float(area.get('bottom', 0)):.4f}"
            )
        else:
            area_status = "未校正（OCR 會直接退回人工流程）"
        row_ratio = ocr.get("result_row_height_ratio")
        row_status = f"已校正 / 高度比例={float(row_ratio):.5f}" if row_ratio is not None else "未校正（OCR 仍可自動聚列）"
        self.cal_tree.insert("", "end", values=("OCR 結果區域", area_status))
        self.cal_tree.insert("", "end", values=("OCR 列高", row_status))
        self.cal_tree.insert("", "end", values=("結果表處理方式", "OCR 找候選 → 人工確認後才點擊；0 筆退回人工"))

    def open_ocr_settings(self):
        win = tk.Toplevel(self)
        win.title("OCR 設定／校正")
        win.geometry("650x300")
        win.transient(self)
        cfg = load_config()
        ocr = cfg.get("ocr", {})
        area = ocr.get("result_grid_area")
        row_ratio = ocr.get("result_row_height_ratio")
        area_text = "已校正" if isinstance(area, dict) else "未校正"
        row_text = f"已校正（{float(row_ratio):.5f}）" if row_ratio is not None else "未校正（非必要）"
        ttk.Label(win, text=f"OCR 結果區域：{area_text}\nOCR 列高：{row_text}", justify="left").pack(anchor="w", padx=16, pady=(16, 10))
        ttk.Label(
            win,
            text=(
                "結果區域：指定查詢結果表格的左上角與右下角。\n"
                "列高：依序指定相鄰兩列的中心點，程式只取兩點 Y 距離；不需要輸入 pixel。\n"
                "本版不自動捲動；Windows OCR 不可用時不阻斷原人工流程。"
            ),
            wraplength=610,
            foreground="#555555",
        ).pack(anchor="w", padx=16, pady=(0, 12))
        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=16)
        ttk.Button(buttons, text="檢查 Windows OCR", command=self.show_windows_ocr_status).pack(side="left")
        ttk.Button(buttons, text="校正結果區域", command=lambda: [win.destroy(), self.calibrate_ocr_area()]).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="校正列高", command=lambda: [win.destroy(), self.calibrate_ocr_row_height()]).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="關閉", command=win.destroy).pack(side="right")

    def show_windows_ocr_status(self):
        ok, text = windows_ocr_status()
        if ok:
            messagebox.showinfo("Windows OCR", f"繁體中文 OCR {text}\n\n不會下載模型或連外。")
        else:
            messagebox.showwarning("Windows OCR 不可用", f"{text}\n\n主流程仍可使用人工選取。")

    def calibrate_ocr_area(self):
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
        except Exception as e:
            messagebox.showerror("找不到安管", str(e))
            return
        self._ocr_cal_state = {"kind": "area", "auto": auto, "points": [], "step": 0}
        self._start_ocr_cal_step()

    def calibrate_ocr_row_height(self):
        try:
            auto = ManualSelectionAnKuanAutomation().connect()
        except Exception as e:
            messagebox.showerror("找不到安管", str(e))
            return
        self._ocr_cal_state = {"kind": "row", "auto": auto, "points": [], "step": 0}
        self._start_ocr_cal_step()

    def _start_ocr_cal_step(self):
        state = self._ocr_cal_state
        if not state:
            return
        step = int(state["step"])
        kind = state["kind"]
        if kind == "area":
            labels = ("查詢結果表格的左上角", "查詢結果表格的右下角")
        else:
            labels = ("任一資料列的中心", "它下一列（相鄰列）的中心")
        if step >= 2:
            self._save_ocr_calibration_state()
            return
        target = labels[step]
        ok = messagebox.askokcancel(
            f"OCR 校正 {step + 1}/2",
            f"請先讓安管停在有查詢結果的畫面。\n\n"
            f"按『確定』後本工具會縮小；4 秒內把滑鼠移到「{target}」並停住，不用點擊。",
        )
        if not ok:
            self._ocr_cal_state = None
            return
        self.status_var.set(f"OCR 校正：4 秒內把滑鼠移到 {target}……")
        self.iconify()
        self.after(4000, self._finish_ocr_cal_step)

    def _finish_ocr_cal_step(self):
        try:
            state = self._ocr_cal_state
            if not state:
                return
            auto = state["auto"]
            r = auto.get_scope_rect("ankuan")
            if not r:
                raise RuntimeError("找不到安管主視窗。")
            x, y = get_cursor_position()
            if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
                raise RuntimeError("滑鼠不在安管主視窗內，未儲存此次 OCR 校正。")
            width = max(1, r.right - r.left)
            height = max(1, r.bottom - r.top)
            state["points"].append(((x - r.left) / width, (y - r.top) / height))
            state["step"] += 1
        except Exception as e:
            self.deiconify()
            self.lift()
            self._ocr_cal_state = None
            self.status_var.set("OCR 校正失敗。")
            messagebox.showerror("OCR 校正失敗", str(e))
            return
        self.deiconify()
        self.lift()
        self.after(150, self._start_ocr_cal_step)

    def _save_ocr_calibration_state(self):
        state = self._ocr_cal_state
        self._ocr_cal_state = None
        if not state or len(state.get("points", [])) != 2:
            return
        p1, p2 = state["points"]
        try:
            if state["kind"] == "area":
                left, right = sorted((float(p1[0]), float(p2[0])))
                top, bottom = sorted((float(p1[1]), float(p2[1])))
                save_result_grid_area(left, top, right, bottom)
                message = "已記錄 OCR 查詢結果區域（相對於安管視窗）。"
            else:
                ratio = abs(float(p2[1]) - float(p1[1]))
                save_result_row_height_ratio(ratio)
                message = "已由相鄰兩列中心點計算並記錄列高比例。"
        except Exception as e:
            messagebox.showerror("OCR 校正失敗", str(e))
            return
        self.refresh_calibration_status()
        self.status_var.set(message)
        messagebox.showinfo("OCR 校正完成", message)

    # Keep OCR rectangle aligned when the existing 3-point quick calibration
    # transforms the portable base profile onto another computer.
    def _finish_quick_calibration(self):
        state = self._quick_state
        self._quick_state = None
        if not state:
            return
        base_points = state["base"].get("points", {})
        captured = state["captured"]
        bx, nx, by, ny = [], [], [], []
        for key, _label in (("place_name", "場所名稱欄位"), ("query_button", "查詢資料按鈕"), ("safety_tab", "安全查察頁籤")):
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
            base_ocr = state["base"].get("ocr", {})
            area = base_ocr.get("result_grid_area")
            transformed_area = None
            if isinstance(area, dict):
                left = min(1.0, max(0.0, ax * float(area["left"]) + cx))
                right = min(1.0, max(0.0, ax * float(area["right"]) + cx))
                top = min(1.0, max(0.0, ay * float(area["top"]) + cy))
                bottom = min(1.0, max(0.0, ay * float(area["bottom"]) + cy))
                transformed_area = {
                    "left": min(left, right),
                    "top": min(top, bottom),
                    "right": max(left, right),
                    "bottom": max(top, bottom),
                }
            base_row = base_ocr.get("result_row_height_ratio")
            transformed_row = None
            if base_row is not None:
                transformed_row = abs(ay) * float(base_row)
            if transformed_area is not None or transformed_row is not None:
                save_ocr_calibration(area=transformed_area, row_height_ratio=transformed_row)
        except Exception as e:
            messagebox.showerror("快速校正失敗", str(e))
            return

        self.refresh_calibration_status()
        messagebox.showinfo(
            "快速校正完成",
            "已用場所名稱、查詢按鈕、安全查察 3 個錨點修正主視窗基本校正；"
            "若基本校正含 OCR 結果區域／列高，也已同步轉換。\n\n"
            "若仍有單一按鈕或 OCR 區域偏移，可再做完整校正。",
        )


def main():
    app = OcrCandidateApp()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists() and path.suffix.lower() == ".pdf":
            app.after(150, lambda: app._load_command_line_pdf(path))
    app.mainloop()


if __name__ == "__main__":
    main()
