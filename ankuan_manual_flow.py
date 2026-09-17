from __future__ import annotations

from ankuan_automation import AnKuanError
from ankuan_runtime import CalibratedAnKuanAutomation


class ManualSelectionAnKuanAutomation(CalibratedAnKuanAutomation):
    """AnKuan flow that stops after submitting the place-name query.

    The legacy query-result grid on the production client is custom-drawn and is
    not reliably readable through UI Automation / Win32 accessibility.  This
    class therefore does not inspect the result grid and never uses CSV as a
    fallback.  The user selects the correct row in AnKuan, then the app resumes
    from the already-selected place.
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

        # This method includes read-back verification.  Query is not clicked if
        # the text cannot be verified in the calibrated field.
        self._set_calibrated_text("place_name", query)
        self._click_query()

    def prepare_selected_place_for_export(self) -> None:
        """Refresh settings and verify that the export path is configured.

        Selection itself is intentionally human-driven.  We only check that the
        next read-only action can be performed; the exported PDF is parsed and
        shown back to the user for an explicit identity confirmation.
        """
        self.reload_config()
        has_safety = bool(self._point("safety_tab")) or bool(
            self._find_any_text(["安全查察"], ("TabItem", "Button", "Text"))
        )
        if not has_safety:
            raise AnKuanError(
                "目前無法辨識「安全查察」頁籤。請先確認安管已停在查詢結果／場所資料畫面，"
                "或到「校正／設定」校正安全查察頁籤。"
            )
