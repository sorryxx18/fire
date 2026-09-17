from __future__ import annotations

import time
from pathlib import Path

from pywinauto import mouse

from ankuan_automation import AnKuanError
from ankuan_ocr import OcrReadError, OcrUnavailable, VisibleOcrCandidate, read_visible_candidates
from ankuan_runtime import CalibratedAnKuanAutomation


class ManualSelectionAnKuanAutomation(CalibratedAnKuanAutomation):
    """Safe AnKuan flow with human confirmation for legacy grids.

    The production query/inspection grids are custom-drawn and are not reliably
    readable through UIA/Win32.  v0.4.1 can use Windows OCR to propose candidates
    from the currently visible place-result region, but the user still decides
    which row to select.  OCR failure always falls back to the v0.4.0 manual flow.
    """

    def submit_place_name_query(self, query: str) -> None:
        query = query.strip()
        if not query:
            raise AnKuanError("請輸入場所名稱關鍵字。")

        self.open_place_search()
        self.reload_config()
        self._open_condition_tab()
        self._clear_query_fields()

        if not self._point("place_name"):
            raise AnKuanError(
                "尚未校正「場所名稱欄位」。為避免空白條件誤查，"
                "請先到「校正／設定」完成場所名稱欄位校正。"
            )
        self._set_calibrated_text("place_name", query)
        self._click_query()

    # ---------- OCR helper for the visible query result grid ----------
    def _result_grid_rect(self) -> tuple[int, int, int, int]:
        self.reload_config()
        area = self.config.get("ocr", {}).get("result_grid_area")
        if not isinstance(area, dict):
            raise OcrUnavailable("尚未校正 OCR 查詢結果區域；已改用人工選取。")
        r = self.get_scope_rect("ankuan")
        if not r:
            raise OcrReadError("找不到安管主視窗；已改用人工選取。")
        width = max(1, r.right - r.left)
        height = max(1, r.bottom - r.top)
        try:
            left = r.left + int(width * float(area["left"]))
            top = r.top + int(height * float(area["top"]))
            right = r.left + int(width * float(area["right"]))
            bottom = r.top + int(height * float(area["bottom"]))
        except Exception as exc:
            raise OcrReadError("OCR 結果區域校正資料無效；已改用人工選取。") from exc
        if right - left < 8 or bottom - top < 8:
            raise OcrReadError("OCR 結果區域太小；已改用人工選取。")
        return left, top, right, bottom

    def read_visible_ocr_candidates(self, query: str) -> tuple[list[VisibleOcrCandidate], str]:
        """Read only the currently visible result rows.  Never scroll automatically."""
        self.reload_config()
        if not bool(self.config.get("ocr", {}).get("enabled", True)):
            raise OcrUnavailable("OCR 已停用；已改用人工選取。")
        left, top, right, bottom = self._result_grid_rect()
        main = self.get_scope_rect("ankuan")
        row_ratio = self.config.get("ocr", {}).get("result_row_height_ratio")
        expected_row_height = None
        if main and row_ratio is not None:
            try:
                expected_row_height = max(3.0, (main.bottom - main.top) * float(row_ratio))
            except Exception:
                expected_row_height = None
        self.activate()
        time.sleep(0.12)
        return read_visible_candidates(
            query,
            left=left,
            top=top,
            width=right - left,
            height=bottom - top,
            expected_row_height=expected_row_height,
        )

    def click_ocr_candidate(self, candidate: VisibleOcrCandidate) -> tuple[int, int]:
        """Click the confirmed row at a stable X inside the grid, not on an OCR word box."""
        left, top, right, bottom = self._result_grid_rect()
        self.reload_config()
        try:
            x_ratio = float(self.config.get("ocr", {}).get("click_x_ratio", 0.35))
        except Exception:
            x_ratio = 0.35
        x_ratio = min(0.85, max(0.15, x_ratio))
        x = left + int((right - left) * x_ratio)
        y = top + int(round(candidate.row_center_y))
        if not (left <= x <= right and top <= y <= bottom):
            raise AnKuanError("OCR 候選列位置超出目前結果區域，未執行點擊。請改用人工選取。")
        self.activate()
        mouse.click(coords=(x, y))
        time.sleep(self._timing("after_select", 0.4))
        return x, y

    def prepare_selected_place_for_export(self) -> None:
        self.reload_config()
        has_safety = bool(self._point("safety_tab")) or bool(
            self._find_any_text(["安全查察"], ("TabItem", "Button", "Text"))
        )
        if not has_safety:
            raise AnKuanError(
                "目前無法辨識「安全查察」頁籤。請先確認安管已停在正確的場所資料畫面，"
                "或到「校正／設定」校正安全查察頁籤。"
            )

    def _click_required_point_or_text(self, point_key: str, names: list[str], error: str):
        if self._click_point(point_key):
            return
        ctrl = self._find_any_text(names, ("Button", "Text", "Hyperlink"))
        if not ctrl:
            raise AnKuanError(error)
        self._click(ctrl)

    def _set_place_record_counts(self):
        self.reload_config()
        report = self.config.get("report", {})
        inspection_count = str(int(report.get("inspection_history_count", 5)))
        submission_count = str(int(report.get("submission_history_count", 2)))

        missing = []
        if not self._point("report_inspection_count"):
            missing.append("最近檢查次數欄")
        if not self._point("report_submission_count"):
            missing.append("最近申報次數欄")
        if missing:
            raise AnKuanError(
                "場所紀錄表選項視窗已開啟，但尚未校正：" + "、".join(missing) + "。\n"
                "這兩個欄位會決定場所紀錄表帶入最近幾次檢查／申報資料，為避免抓錯資料已停止。"
            )

        self._set_calibrated_text("report_inspection_count", inspection_count)
        self._set_calibrated_text("report_submission_count", submission_count)

    def export_place_record(self, timeout: int = 30) -> Path:
        """Generate PDF 1: 場所紀錄表, including configured history counts."""
        self.reload_config()
        self.open_safety_inspection()
        self._click_required_point_or_text(
            "place_record_button",
            ["場所紀錄表"],
            "找不到「場所紀錄表」按鈕。請先校正該按鈕。",
        )
        time.sleep(self._timing("after_report_open", 0.8))

        self._set_place_record_counts()
        self._ensure_report_options(["管理權人", "消防設備", "防火管理", "防焰物品", "建物"])
        self._click_required_point_or_text(
            "report_confirm_button",
            ["確認"],
            "場所紀錄表選項視窗已開啟，但找不到「確認」按鈕。請先校正。",
        )
        time.sleep(self._timing("after_preview_open", 1.0))
        self.wait_for_preview()
        return self.save_current_preview_pdf("place_record", timeout=timeout)

    def export_inspection_record(self, timeout: int = 30) -> Path:
        """Generate PDF 2 from the inspection row selected by the user."""
        self.reload_config()
        self.open_safety_inspection()
        self._click_required_point_or_text(
            "inspection_record_button",
            ["檢查紀錄表"],
            "找不到「檢查紀錄表」按鈕。請先在安全查察列表選好檢查紀錄，並校正「檢查紀錄表」按鈕。",
        )
        time.sleep(self._timing("after_preview_open", 1.0))
        self.wait_for_preview()
        return self.save_current_preview_pdf("inspection_record", timeout=timeout)
