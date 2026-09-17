from __future__ import annotations

import ctypes
from ctypes import wintypes
import tempfile
import time
from pathlib import Path

from pywinauto import Desktop, keyboard, mouse

from ankuan_automation import (
    AnKuanAutomation,
    AnKuanError,
    PlaceCandidate,
    _class_name,
    _control_type,
    _friendly_class,
    _rect,
    _safe_text,
)
from ankuan_config import config_path, load_config


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    # INPUT's union must include the largest native member (MOUSEINPUT).
    # If only KEYBDINPUT is declared, ctypes.sizeof(INPUT) becomes 32 bytes on
    # x64 instead of the Windows ABI-required 40 bytes and SendInput returns 0.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


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
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    units = text.encode("utf-16-le")
    for i in range(0, len(units), 2):
        code_unit = int.from_bytes(units[i : i + 2], "little")
        down = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE, 0, 0))
        up = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))
        arr = (INPUT * 2)(down, up)
        ctypes.set_last_error(0)
        sent = user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        if sent != 2:
            err = ctypes.get_last_error()
            raise OSError(
                f"Windows 無法送出文字輸入（SendInput={sent}/2，INPUT={ctypes.sizeof(INPUT)} bytes，WinError={err}）。"
            )


def _window_text_from_point(x: int, y: int) -> str | None:
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = ctypes.c_void_p
    user32.SendMessageW.restype = ctypes.c_ssize_t
    hwnd = user32.WindowFromPoint(POINT(x, y))
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
    kernel32.GlobalLock.restype = ctypes.c_void_p
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
    keyboard.send_keys("^a^c", pause=0.03)
    time.sleep(0.12)
    value = _clipboard_text()
    keyboard.send_keys("{RIGHT}", pause=0.02)
    return value


class CalibratedAnKuanAutomation(AnKuanAutomation):
    """AnKuan automation using external relative calibration points.

    ``scope=ankuan`` points are relative to the main AnKuan window.
    ``scope=preview`` points are relative to the report preview window.  This
    keeps preview-toolbar calibration portable even when the preview window has
    a different size from the main application.
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

    # ---------- environment / scopes ----------
    def environment_info(self) -> dict:
        if not self.window:
            self.connect()
        r = self.window.rectangle()
        hwnd = int(self.handle or 0)
        user32 = ctypes.windll.user32
        dpi = 96
        try:
            get_dpi = user32.GetDpiForWindow
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
            dpi = int(get_dpi(hwnd) or 96)
        except Exception:
            try:
                dpi = int(user32.GetDpiForSystem() or 96)
            except Exception:
                dpi = 96
        try:
            maximized = bool(user32.IsZoomed(hwnd))
        except Exception:
            maximized = False
        return {
            "display_scale_percent": int(round(dpi / 96 * 100)),
            "maximized": maximized,
            "window_width": int(r.right - r.left),
            "window_height": int(r.bottom - r.top),
        }

    def _find_preview_window(self):
        candidates = []
        for backend in ("win32", "uia"):
            try:
                for w in Desktop(backend=backend).windows():
                    text = _safe_text(w)
                    cls = _class_name(w)
                    if "預覽" not in text and "preview" not in text.lower():
                        continue
                    r = _rect(w)
                    if not r or r.right <= r.left or r.bottom <= r.top:
                        continue
                    candidates.append(((r.right - r.left) * (r.bottom - r.top), w))
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def wait_for_preview(self, timeout: float = 12.0):
        end = time.time() + timeout
        while time.time() < end:
            w = self._find_preview_window()
            if w is not None:
                return w
            time.sleep(0.2)
        raise AnKuanError("報表已送出，但沒有偵測到「預覽」視窗。")

    def get_scope_window(self, scope: str = "ankuan"):
        if scope == "preview":
            return self._find_preview_window()
        return self.window

    def get_scope_rect(self, scope: str = "ankuan"):
        w = self.get_scope_window(scope)
        return _rect(w) if w is not None else None

    def _activate_scope(self, scope: str):
        if scope == "ankuan":
            self.activate()
            return
        w = self.get_scope_window(scope)
        if w is None:
            raise AnKuanError("找不到報表預覽視窗。")
        for method in ("set_focus", "set_foreground"):
            try:
                getattr(w, method)()
                return
            except Exception:
                continue

    # ---------- calibrated operations ----------
    def _point_scope(self, key: str) -> str:
        p = self.config.get("points", {}).get(key)
        if isinstance(p, dict):
            return str(p.get("scope", "ankuan"))
        return "ankuan"

    def _point(self, key: str) -> tuple[int, int] | None:
        p = self.config.get("points", {}).get(key)
        if not isinstance(p, dict):
            return None
        scope = str(p.get("scope", "ankuan"))
        r = self.get_scope_rect(scope)
        if not r:
            return None
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
        scope = self._point_scope(key)
        self._activate_scope(scope)
        mouse.click(coords=pt)
        return True

    def _set_calibrated_text(self, key: str, value: str) -> bool:
        pt = self._point(key)
        if not pt:
            return False
        scope = self._point_scope(key)
        self._activate_scope(scope)
        mouse.click(coords=pt)
        time.sleep(0.08)
        keyboard.send_keys("^a{BACKSPACE}", pause=0.03)
        _send_unicode_text(value)
        time.sleep(self._timing("after_input", 0.5))

        require_echo = bool(self.config.get("validation", {}).get("require_input_echo", True))
        direct = _window_text_from_point(*pt)
        if direct is not None and direct.strip() == value:
            return True

        copied = _clipboard_echo()
        if copied is not None and copied.strip() == value:
            return True

        if require_echo:
            direct_text = "無法讀取" if direct is None else direct
            copied_text = "無法讀取" if copied is None else copied
            raise AnKuanError(
                f"已嘗試輸入「{value}」，但安管欄位未通過讀回驗證。"
                f"\n視窗文字：{direct_text}"
                f"\n複製讀回：{copied_text}"
                "\n為避免空白條件誤查，程式已停止。請重新校正該欄位。"
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

    # Kept for compatibility with older callers.  The current app uses the
    # human-selection flow and does not read the legacy result grid or CSV.
    def search_places(self, query: str) -> list[PlaceCandidate]:
        query = query.strip()
        if not query:
            raise AnKuanError("請輸入場所名稱關鍵字。")
        self.open_place_search()
        self.reload_config()
        self._open_condition_tab()
        self._clear_query_fields()
        if not self._point("place_name"):
            raise AnKuanError("尚未校正「場所名稱欄位」。")
        self._set_calibrated_text("place_name", query)
        self._click_query()
        candidates = self._filter_candidates(self._read_result_candidates(), query)
        self._last_candidates = candidates
        return candidates

    def open_safety_inspection(self):
        if self._click_point("safety_tab"):
            time.sleep(self._timing("after_tab", 0.25))
            return
        super().open_safety_inspection()

    # ---------- preview export ----------
    def save_current_preview_pdf(self, kind: str, timeout: int = 30) -> Path:
        self.reload_config()
        self.wait_for_preview(timeout=min(timeout, 12))
        if not self._point("preview_pdf_button"):
            raise AnKuanError(
                "尚未校正「預覽 PDF 匯出按鈕」。請先人工確認預覽器中真正會儲存 PDF 的按鈕，再進行校正。"
            )

        before = self._snapshot_files(".pdf")
        target = Path(tempfile.gettempdir()) / f"ankuan_{kind}_{int(time.time() * 1000)}.pdf"
        self._click_point("preview_pdf_button")
        time.sleep(self._timing("after_preview_export", 0.5))
        try:
            self._try_save_dialog(target)
        except Exception:
            pass

        pdf = target if target.exists() else self._wait_new_file(".pdf", before, timeout=timeout)
        if not pdf:
            raise AnKuanError(
                "已操作預覽器的 PDF 匯出按鈕，但沒有取得 PDF。請確認校正的是實際『儲存／匯出 PDF』按鈕。"
            )
        self.close_preview()
        return Path(pdf)

    def close_preview(self):
        w = self._find_preview_window()
        if w is None:
            return
        if self._click_point("preview_close_button"):
            time.sleep(0.4)
            return
        try:
            w.close()
            time.sleep(0.4)
            return
        except Exception:
            pass
        try:
            self._activate_scope("preview")
            keyboard.send_keys("%{F4}")
            time.sleep(0.4)
        except Exception:
            pass

    def export_diagnostics(self, path: Path) -> Path:
        if not self.window:
            self.connect()
        env = self.environment_info()
        safe_texts = {
            "建檔", "場所建檔", "條件設定", "查詢結果", "查詢資料", "Q.查詢資料", "Q. 查詢資料",
            "清除欄位", "場所名稱", "場所地址", "場所編號", "使照號碼", "安全查察", "場所紀錄表",
            "檢查紀錄表", "確認", "首筆", "上筆", "下筆", "末筆", "預覽", "結束",
        }
        lines = [
            "=== 安管控制項診斷（結構版） ===",
            f"config={config_path()}",
            f"title={_safe_text(self.window)}",
            f"rect={self.window.rectangle()}",
            f"environment={env}",
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
        preview = self._find_preview_window()
        if preview is not None:
            lines.append("--- PREVIEW ---")
            lines.append(f"title={_safe_text(preview)!r} class={_class_name(preview)!r} rect={_rect(preview)!r}")
        path = Path(path)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
