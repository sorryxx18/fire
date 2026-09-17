"""Optional OCR-assisted candidate detection for the AnKuan query result grid.

The grid is a legacy custom-drawn control (see ankuan_manual_flow.py) that
UIA/Win32 cannot read reliably, so the human always makes the final selection.
This module only proposes candidates for the human to confirm; it never
clicks anything by itself.  Every function here is defensive: any failure
(missing OCR language pack, uncalibrated points, unexpected data shape) is
swallowed and reported as "no candidates found" rather than raised, so a
problem in this optional feature can never block the existing manual flow.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResultCandidate:
    text: str
    x: int
    y: int


def ocr_available() -> bool:
    try:
        import winocr  # noqa: F401
        from PIL import ImageGrab  # noqa: F401
    except Exception:
        return False
    return True


def _grid_rect(automation) -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) absolute screen pixels, or None."""
    try:
        tl = automation._point("result_grid_top_left")
        br = automation._point("result_grid_bottom_right")
    except Exception:
        return None
    if not tl or not br:
        return None
    left, top = tl
    right, bottom = br
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _capture_grid(automation):
    rect = _grid_rect(automation)
    if not rect:
        return None, None
    try:
        from PIL import ImageGrab
    except Exception:
        return None, None
    try:
        image = ImageGrab.grab(bbox=rect)
    except Exception:
        return None, None
    return image, rect


def _iter_lines(ocr_result: dict):
    """Yield (text, y_center_in_image_pixels) for each recognised line.

    winocr's recognize_pil_sync() reflects the WinRT OcrResult object into a
    plain dict (see https://github.com/GitHub30/winocr).  Key casing has
    varied between winrt-python versions, so this accepts both snake_case
    and PascalCase spellings defensively instead of assuming one.
    """
    lines = ocr_result.get("lines") if isinstance(ocr_result, dict) else None
    if lines is None and isinstance(ocr_result, dict):
        lines = ocr_result.get("Lines")
    if not lines:
        return
    for line in lines:
        if not isinstance(line, dict):
            continue
        text = (line.get("text") or line.get("Text") or "").strip()
        if not text:
            continue
        words = line.get("words") or line.get("Words") or []
        y_values = []
        for word in words:
            if not isinstance(word, dict):
                continue
            rect = word.get("bounding_rect") or word.get("BoundingRect") or {}
            y = rect.get("y", rect.get("Y"))
            h = rect.get("height", rect.get("Height"))
            if y is not None and h is not None:
                y_values.append(float(y) + float(h) / 2)
        if y_values:
            yield text, sum(y_values) / len(y_values)


def find_result_candidates(automation, query: str, lang: str = "zh-Hant") -> list[ResultCandidate] | None:
    """Return matching rows in the (still on-screen) query result grid.

    Returns ``None`` when OCR could not run at all (not calibrated, OCR
    engine unavailable, capture/recognition failed) so the caller can fall
    back to the existing pure-manual flow.  Returns ``[]`` when OCR ran
    successfully but found zero rows matching ``query`` — the caller should
    still fall back to manual selection rather than guess.
    """
    if not ocr_available():
        return None
    image, rect = _capture_grid(automation)
    if image is None or rect is None:
        return None

    try:
        from winocr import recognize_pil_sync
        result = recognize_pil_sync(image, lang)
    except Exception:
        return None

    query = (query or "").strip()
    if not query:
        return None

    left, top, _right, _bottom = rect
    candidates: list[ResultCandidate] = []
    try:
        for text, y_center in _iter_lines(result):
            if query in text:
                candidates.append(
                    ResultCandidate(
                        text=text,
                        x=left + (image.width // 2),
                        y=top + int(y_center),
                    )
                )
    except Exception:
        return None

    return candidates
