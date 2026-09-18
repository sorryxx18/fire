from __future__ import annotations

import ctypes
import shutil
import time
from pathlib import Path

import app
import app_ocr
from ankuan_automation import AnKuanError
from ankuan_runtime import CalibratedAnKuanAutomation


BUILD_NOTE = "v0.4.2-test3｜PDF 一律先存桌面｜內建校正｜OCR 狀態可視化｜PDF 匯出修正"
EXE_TITLE = "消防安全管理及應變表自動產製工具 v0.4.2 TEST3"


# Keep the test identity visible inside the app, not only in the EXE filename.
app.APP_TITLE = EXE_TITLE
app.BUILD_NOTE = BUILD_NOTE
app_ocr.BUILD_NOTE = BUILD_NOTE


def _desktop_dir() -> Path:
    """Resolve the real Windows Desktop, including redirected desktops."""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        # CSIDL_DESKTOPDIRECTORY = 0x0010
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and buf.value:
            path = Path(buf.value)
            if path.exists():
                return path
    except Exception:
        pass

    candidates = [
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "OneDrive" / "桌面",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise AnKuanError("找不到 Windows 桌面資料夾，無法儲存 PDF。")


def _desktop_pdf_target(kind: str) -> Path:
    desktop = _desktop_dir()
    label = {
        "place_record": "場所紀錄表",
        "inspection_record": "消防安全檢查紀錄表",
    }.get(kind, kind or "報表")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    millis = int((time.time() % 1) * 1000)
    return desktop / f"安管_{label}_{stamp}_{millis:03d}.pdf"


def _ensure_on_desktop(pdf: Path, target: Path) -> Path:
    """If the legacy viewer ignored our requested path, copy the PDF to Desktop."""
    pdf = Path(pdf)
    target = Path(target)
    try:
        if pdf.resolve() == target.resolve():
            return target
    except Exception:
        pass

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(f"{target.stem}_{int(time.time() * 1000)}{target.suffix}")
    shutil.copy2(pdf, target)
    return target


def _save_current_preview_pdf_to_desktop(self, kind: str, timeout: int = 30) -> Path:
    """TEST3: every report PDF is saved/copied to Desktop before parsing."""
    self.reload_config()
    self.wait_for_preview(timeout=min(timeout, 12))
    if not self._point("preview_pdf_button"):
        raise AnKuanError(
            "尚未校正「預覽 PDF 匯出按鈕」。請先人工確認預覽器中真正會儲存 PDF 的按鈕，再進行校正。"
        )

    before = self._snapshot_files(".pdf")
    target = _desktop_pdf_target(kind)

    self._click_point("preview_pdf_button")
    time.sleep(self._timing("after_preview_export", 0.5))
    export_confirmed = self._confirm_pdf_export_dialog_win32(timeout=4.0)

    try:
        save_dialog_handled = self._try_save_dialog(target, timeout=6.0)
    except Exception:
        save_dialog_handled = False

    pdf = target if target.exists() else self._wait_new_file(".pdf", before, timeout=timeout)
    if not pdf:
        if export_confirmed and not save_dialog_handled:
            raise AnKuanError(
                f"已通過「匯出到 PDF」確認，但沒有取得 PDF。\n預定桌面位置：{target}\n請截圖目前畫面回報。"
            )
        if save_dialog_handled:
            raise AnKuanError(
                f"已操作 Windows「另存新檔」，但桌面及監看位置都沒有取得 PDF。\n預定位置：{target}"
            )
        raise AnKuanError(
            f"PDF 匯出流程未完成。程式已指定先存桌面，但沒有取得 PDF。\n預定位置：{target}"
        )

    pdf = _ensure_on_desktop(Path(pdf), target)
    self.close_preview()
    return pdf


# ManualSelectionAnKuanAutomation inherits this method, so patching the base
# keeps the existing app_ocr flow untouched while TEST3 validates Desktop-first
# saving on the real government PC.
CalibratedAnKuanAutomation.save_current_preview_pdf = _save_current_preview_pdf_to_desktop


if __name__ == "__main__":
    app_ocr.main()
