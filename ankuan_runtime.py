from __future__ import annotations

import ctypes
import time
from pathlib import Path

from pywinauto import Desktop, keyboard, mouse

from ankuan_automation import AnKuanAutomation, AnKuanError, PlaceCandidate, _class_name, _control_type, _friendly_class, _rect, _safe_text
from ankuan_config import config_path, load_config


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
CF_UNICODETEXT = 13


def get_cursor_position() -> tuple[int, int]:
    pt = POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        raise OSError("無法讀取滑鼠位置。")
    return int(pt.x), int(pt.y)


def _send_unicode_text(text: str):
    user32 = ctypes.windll.user32
    units = text.encode("utf-16-le")
    for i in range(0, len(units), 2):
        code_unit = int.from_bytes(units[i : i + 2], "little")
        down = INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE, 0, None)))
        up = INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)))
        arr = (INPUT * 2)(down, up)
        sent = user32.SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT))
        if sent != 2:
            raise OSError("Windows 無法送出文字輸入。")


def _window_text_from_point(x: int, y: int) -> str | None:
    user32 = ctypes.windll.user32
    pt = POINT(x, y)
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    length = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if length <= 0:
        return None
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.byref(buf))
    return buf.value


def _clipboard_text() -> str | None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not user32.OpenClipboard(None):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _clipboard_echo() -> str | None:
    previous = _clipboard_text()
    try:
        keyboard.send_keys("^a^c", pause=0.03)
        time.sleep(0.12)
        value = _clipboard_text()
        keyboard.send_keys("{RIGHT}", pause=0.02)
        return value
    finally:
        # We intentionally do not overwrite a non-text clipboard.  If there was
        # text before, leaving the copied field value is less destructive than
        # blindly clearing other clipboard formats.
        _ = previous


class CalibratedAnKuanAutomation(AnKuanAutomation):
    """AnKuan automation with user-calibrated relative points.

    Calibration points are stored outside the EXE in ankuan_config.json.  They
    are relative to the AnKuan window, so moving the window does not invalidate
    them.  Native/UIA controls are still preferred where reliable; calibrated
    points are used for legacy controls that Windows cannot expose correctly.
    """

    def __init__(self):
        super().__init__()
        self.config = load_config()

    def reload_config(self):
        self.config = load_config()
        return self.config

    def _timing(self, key: str, default: float) -> float:
        try:
            return float(self.config.get("timing", {}).get(key, default))
        except Exception:
            return default

    def _point(self, key: str) -> tuple[int, int] | None:
        p = self.config.get("points", {}).get(key)
        if not p or not self.window:
            return None
        r = self.window.rectangle()
        width = max(1, r.right - r.left)
        height = max(1, r.bottom - r.top)
        try:
            x = r.left + int(width * float(p["x"]))
            y = r.top + int(height * float(p["y"]))
        except Exception:
            return None
        if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
            return None
        return x, y

    def _click_point(self, key: str) -> bool:
        pt = self._point(key)
        if not pt:
            return False
        self.activate()
        mouse.click(coords=pt)
        return True

    def _set_calibrated_text(self, key: str, value: str) -> bool:
        pt = self._point(key)
        if not pt:
            return False
        self.activate()
        mouse.click(coords=pt)
        time.sleep(0.08)
        keyboard.send_keys("^a{BACKSPACE}", pause=0.03)
        _send_unicode_text(value)
        time.sleep(self._timing("after_input", 0.5))

        require_echo = bool(self.config.get("validation", {}).get("require_input_echo", True))
        direct = _window_text_from_point(*pt)
        if direct is not None:
            if direct.strip() == value:
                return True
            if require_echo:
                raise AnKuanError(f"已嘗試輸入「{value}」，但安管欄位讀回為「{direct}」，已停止查詢。")

        copied = _clipboard_echo()
        if copied is not None and copied.strip() == value:
            return True
        if require_echo:
            raise AnKuanError(
                f"已嘗試輸入「{value}」，但無法從安管欄位讀回相同文字。"
                "為避免空白條件誤查，程式已停止。可重新校正該欄位。"
            )
        return True

    def _open_condition_tab(self):
        if self._click_point("condition_tab"):
            time.sleep(self._timing("after_tab", 0.25))
            return
        super()._open_condition_tab()

    def _click_query(self):
        if self._click_point("query_button"):
            time.sleep(self._timing("after_query", 1.2))
            if self._click_point("result_tab"):
                time.sleep(self._timing("after_tab", 0.25))
            return
        super()._click_query()

    def search_places(self, query: str) -> list[PlaceCandidate]:
        query = query.strip()
        if not query:
            raise AnKuanError("請輸入場所名稱關鍵字。")
        self.open_place_search()
        self.reload_config()
        self._open_condition_tab()
        self._clear_query_fields()

        if not self._set_calibrated_text("place_name", query):
            edit = self._nearest_edit_to_label("場所名稱") or self._legacy_place_name_edit()
            if not edit:
                raise AnKuanError(
                    "無法安全辨識「場所名稱」輸入框。請先到「校正／設定」完成場所名稱欄位校正。"
                )
            self._set_edit(edit, query)
            time.sleep(self._timing("after_input", 0.5))

        self._click_query()
        candidates = self._filter_candidates(self._read_result_candidates(), query)
        if not candidates:
            candidates = self._export_result_csv_candidates(query)

        if bool(self.config.get("validation", {}).get("require_query_match", True)):
            candidates = self._filter_candidates(candidates, query)
        max_results = int(self.config.get("validation", {}).get("max_fuzzy_results", 200) or 200)
        if len(candidates) > max_results:
            raise AnKuanError(
                f"查詢結果有 {len(candidates)} 筆，超過安全上限 {max_results} 筆。"
                "可能查詢條件未正確套用，已停止。"
            )
        self._last_candidates = candidates
        if not candidates:
            raise AnKuanError("查詢完成，但找不到符合關鍵字的場所。")
        return candidates

    def _export_result_csv_candidates(self, query: str) -> list[PlaceCandidate]:
        if self._point("csv_button"):
            before = self._snapshot_files(".csv")
            target = Path(__import__("tempfile").gettempdir()) / f"ankuan_query_{int(time.time() * 1000)}.csv"
            self._click_point("csv_button")
            time.sleep(0.45)
            self._try_save_dialog(target)
            csv_path = target if target.exists() else self._wait_new_file(".csv", before, timeout=7)
            if not csv_path:
                raise AnKuanError("已按下校正後的「存檔(CSV)」，但沒有取得 CSV。")
            return self._filter_candidates(self._parse_result_csv(csv_path), query)
        return super()._export_result_csv_candidates(query)

    def _select_exact_place_no(self, candidate: PlaceCandidate) -> bool:
        if not self._point("place_no"):
            return super()._select_exact_place_no(candidate)
        try:
            self.open_place_search()
            self.reload_config()
            self._open_condition_tab()
            self._clear_query_fields()
            if not self._set_calibrated_text("place_no", candidate.place_no):
                return False
            self._click_query()
            first = self._find_any_text(["首筆"], ("Button", "Text"))
            if first:
                self._click(first)
                time.sleep(0.25)
            return True
        except Exception:
            return False

    def open_safety_inspection(self):
        if self._click_point("safety_tab"):
            time.sleep(self._timing("after_tab", 0.25))
            return
        super().open_safety_inspection()

    def export_place_record(self, timeout: int = 30) -> Path:
        self.open_safety_inspection()
        before = self._snapshot_files(".pdf")
        if not self._click_point("place_record_button"):
            btn = self._find_any_text(["場所紀錄表"], ("Button", "Text"))
            if not btn:
                raise AnKuanError("找不到「場所紀錄表」按鈕。請先校正該按鈕。")
            self._click(btn)
        time.sleep(self._timing("after_report_open", 0.8))
        self._ensure_report_options(["管理權人", "消防設備", "防火管理", "防焰物品", "建物"])
        if not self._click_point("report_confirm_button"):
            confirm = self._find_any_text(["確認"], ("Button", "Text"))
            if not confirm:
                raise AnKuanError("場所紀錄表選項視窗已開啟，但找不到「確認」按鈕。")
            self._click(confirm)
        pdf = self._wait_new_file(".pdf", before, timeout=timeout)
        if not pdf:
            raise AnKuanError(
                "已送出「場所紀錄表」產出，但未偵測到新 PDF。安管可能先開啟預覽視窗。"
            )
        return pdf

    def export_diagnostics(self, path: Path) -> Path:
        if not self.window:
            self.connect()
        safe_texts = {
            "建檔", "場所建檔", "條件設定", "查詢結果", "查詢資料", "Q.查詢資料", "Q. 查詢資料",
            "清除欄位", "場所名稱", "場所地址", "場所編號", "使照號碼", "安全查察", "場所紀錄表",
            "存檔(CSV)", "確認", "首筆", "上筆", "下筆", "末筆",
        }
        lines = [
            "=== 安管控制項診斷（結構版） ===",
            f"config={config_path()}",
            f"title={_safe_text(self.window)}",
            f"rect={self.window.rectangle()}",
            "",
        ]
        for backend, controls in (("UIA", self._descendants()), ("WIN32", self._win32_descendants())):
            lines.append(f"--- {backend} ---")
            for idx, c in enumerate(controls[:4000], 1):
                text = _safe_text(c)
                visible_text = text if text in safe_texts else ("<masked>" if text else "")
                lines.append(
                    f"{idx:04d} class={_class_name(c)!r} friendly={_friendly_class(c)!r} "
                    f"type={_control_type(c)!r} text={visible_text!r} rect={_rect(c)!r}"
                )
            lines.append("")
        path = Path(path)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
