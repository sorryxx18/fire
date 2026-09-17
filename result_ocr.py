"""OCR-assisted candidate detection for the AnKuan query result grid.

The legacy result grid is custom-drawn and cannot be read reliably through
UIA/Win32.  OCR therefore has one narrow job: propose candidates from the
CURRENTLY VISIBLE result area.  It never decides a place and never clicks.
The UI must obtain explicit human confirmation before using a candidate.

Failure is deliberately non-fatal: missing calibration, missing Windows OCR,
screenshot/recognition errors, or zero matches all return to the existing
manual-selection flow.
"""

from __future__ import annotations

import difflib
import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Iterable


@dataclass
class OcrWord:
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0


@dataclass
class ResultCandidate:
    row_index: int
    row_text: str
    row_center_y: float
    x: int
    y: int
    place_no: str = ""
    name: str = ""
    address: str = ""
    match_score: float = 0.0
    # Windows.Media.Ocr does not expose a reliable per-word confidence value.
    ocr_confidence: float | None = None

    @property
    def text(self) -> str:
        """Backward-compatible display text used by older callers."""
        return self.row_text


def ocr_available() -> bool:
    try:
        import winocr  # noqa: F401
        from PIL import ImageGrab  # noqa: F401
    except Exception:
        return False
    return True


def _grid_rect(automation) -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) absolute screen pixels, or None.

    The two stored calibration points are relative to the AnKuan window, so the
    resulting rectangle remains portable across resolutions/window sizes in the
    same way as the rest of the current calibration/profile system.
    """
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
    if right - left < 20 or bottom - top < 20:
        return None
    return left, top, right, bottom


def _capture_grid(automation):
    rect = _grid_rect(automation)
    if not rect:
        return None, None
    try:
        from PIL import ImageGrab
        image = ImageGrab.grab(bbox=rect)
    except Exception:
        return None, None
    return image, rect


def _dict_value(data: dict, *names: str, default=None):
    for name in names:
        if name in data:
            return data[name]
    return default


def _rect_values(rect: dict) -> tuple[float, float, float, float] | None:
    if not isinstance(rect, dict):
        return None
    try:
        x = float(_dict_value(rect, "x", "X"))
        y = float(_dict_value(rect, "y", "Y"))
        width = float(_dict_value(rect, "width", "Width"))
        height = float(_dict_value(rect, "height", "Height"))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _iter_words(ocr_result: dict) -> Iterable[OcrWord]:
    """Yield every OCR word with its bounding box, independent of line split.

    We intentionally rebuild table rows from Y coordinates instead of trusting
    OCR's line segmentation or hard-coding table columns.  This is more tolerant
    of old grid rendering, column-width drift, and words being split/merged.
    """
    if not isinstance(ocr_result, dict):
        return
    lines = _dict_value(ocr_result, "lines", "Lines", default=[]) or []
    for line in lines:
        if not isinstance(line, dict):
            continue
        words = _dict_value(line, "words", "Words", default=[]) or []
        for word in words:
            if not isinstance(word, dict):
                continue
            text = str(_dict_value(word, "text", "Text", default="") or "").strip()
            rect = _dict_value(word, "bounding_rect", "BoundingRect", default={}) or {}
            values = _rect_values(rect)
            if not text or values is None:
                continue
            x, y, width, height = values
            yield OcrWord(text=text, x=x, y=y, width=width, height=height)


def _expected_row_height_px(automation) -> float | None:
    """Optional helper from the configured row-height ratio; never required."""
    try:
        cfg = getattr(automation, "config", {}) or {}
        ratio = cfg.get("ocr", {}).get("result_row_height_ratio")
        if ratio is None:
            return None
        main = automation.get_scope_rect("ankuan")
        if not main:
            return None
        value = abs(float(ratio)) * max(1, main.bottom - main.top)
        return value if value >= 3 else None
    except Exception:
        return None


def _cluster_rows(words: Iterable[OcrWord], expected_row_height: float | None = None) -> list[list[OcrWord]]:
    items = [word for word in words if word.text.strip()]
    if not items:
        return []

    heights = [word.height for word in items if word.height > 0]
    typical_height = statistics.median(heights) if heights else 15.0
    if expected_row_height and expected_row_height > 2:
        tolerance = max(5.0, expected_row_height * 0.34)
    else:
        tolerance = max(5.0, typical_height * 0.72)

    groups: list[list[OcrWord]] = []
    centers: list[float] = []
    for word in sorted(items, key=lambda item: (item.center_y, item.x)):
        best_idx = None
        best_delta = None
        for idx, center in enumerate(centers):
            delta = abs(word.center_y - center)
            if delta <= tolerance and (best_delta is None or delta < best_delta):
                best_idx = idx
                best_delta = delta
        if best_idx is None:
            groups.append([word])
            centers.append(word.center_y)
        else:
            groups[best_idx].append(word)
            centers[best_idx] = sum(item.center_y for item in groups[best_idx]) / len(groups[best_idx])

    ordered = sorted(zip(centers, groups), key=lambda item: item[0])
    return [sorted(group, key=lambda word: word.x) for _center, group in ordered]


def _normalize(value: str, *, drop_parenthetical: bool = False) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    if drop_parenthetical:
        text = re.sub(r"[（(][^）)]*[）)]", "", text)
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[，,。．.、：:；;！!？?\-—_／/\\|【】\[\]{}<>《》「」『』'\"]+", "", text)
    return text


def _is_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    cursor = 0
    for char in haystack:
        if cursor < len(needle) and char == needle[cursor]:
            cursor += 1
    return cursor == len(needle)


def _partial_ratio(shorter: str, longer: str) -> float:
    if not shorter or not longer:
        return 0.0
    if len(shorter) > len(longer):
        shorter, longer = longer, shorter
    if shorter in longer:
        return 1.0
    best = 0.0
    low = max(1, len(shorter) - 1)
    high = min(len(longer), len(shorter) + 2)
    for window_len in range(low, high + 1):
        for start in range(0, len(longer) - window_len + 1):
            ratio = difflib.SequenceMatcher(None, shorter, longer[start : start + window_len]).ratio()
            best = max(best, ratio)
    return best


def match_score(query: str, text: str) -> float:
    q = _normalize(query, drop_parenthetical=True)
    t = _normalize(text, drop_parenthetical=True)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    partial = _partial_ratio(q, t)
    if _is_subsequence(q, t):
        return max(0.86, partial)
    whole = difflib.SequenceMatcher(None, q, t).ratio()
    return max(partial, whole * 0.92)


_ADDRESS_MARKERS = ("市", "縣", "區", "鄉", "鎮", "路", "街", "巷", "弄", "號", "樓")
_IGNORE_TOKENS = {
    "場所編號",
    "場所名稱",
    "場所地址",
    "地址",
    "列管狀況",
    "最新更新日期",
    "安檢日期",
    "列管日期",
    "合法",
    "免列管",
    "停業",
    "歇業",
}


def _looks_address(text: str) -> bool:
    compact = _normalize(text)
    return len(compact) >= 5 and sum(marker in compact for marker in _ADDRESS_MARKERS) >= 2


def _looks_date_or_number(text: str) -> bool:
    compact = unicodedata.normalize("NFKC", text or "").strip()
    return bool(
        re.fullmatch(r"\d{1,7}", compact)
        or re.fullmatch(r"\d{2,4}[/.-]\d{1,2}[/.-]\d{1,2}(?:\s+\d{1,2}:\d{2})?", compact)
        or re.fullmatch(r"[\d./: -]+", compact)
    )


def _parse_row(row: list[OcrWord]) -> tuple[str, str, str, str]:
    tokens = [re.sub(r"\s+", " ", word.text).strip() for word in row if word.text.strip()]
    row_text = " ".join(tokens)

    place_no = ""
    place_idx = -1
    for idx, token in enumerate(tokens):
        normalized = unicodedata.normalize("NFKC", token).strip()
        if re.fullmatch(r"\d{1,7}", normalized):
            place_no = normalized
            place_idx = idx
            break

    address = ""
    address_idx: int | None = None
    for idx, token in enumerate(tokens):
        if _looks_address(token):
            address = token
            address_idx = idx
            break
    if address_idx is None:
        for idx in range(len(tokens)):
            suffix = "".join(tokens[idx:])
            if _looks_address(suffix):
                address = suffix
                address_idx = idx
                break

    start = place_idx + 1 if place_idx >= 0 else 0
    end = address_idx if address_idx is not None else len(tokens)
    name_parts = []
    for token in tokens[start:end]:
        compact = _normalize(token)
        if not compact or token in _IGNORE_TOKENS or _looks_date_or_number(token):
            continue
        if any(header in token for header in _IGNORE_TOKENS):
            continue
        name_parts.append(token)
    name = "".join(name_parts)

    if not name:
        alternatives = [
            token
            for idx, token in enumerate(tokens)
            if idx != place_idx and (address_idx is None or idx < address_idx) and not _looks_date_or_number(token)
        ]
        name = max(alternatives, key=lambda value: len(_normalize(value)), default="")

    return place_no, name, address, row_text


def _safe_click_x(automation, left: int, right: int) -> int:
    """Use a stable position inside the row, never an OCR word-box center."""
    ratio = 0.35
    try:
        cfg = getattr(automation, "config", {}) or {}
        ratio = float(cfg.get("ocr", {}).get("click_x_ratio", ratio))
    except Exception:
        ratio = 0.35
    ratio = min(0.85, max(0.15, ratio))
    return left + int((right - left) * ratio)


def find_result_candidates(
    automation,
    query: str,
    lang: str = "zh-Hant",
    minimum_score: float = 0.58,
    max_candidates: int = 12,
) -> list[ResultCandidate] | None:
    """Return matching rows in the still-visible query result grid.

    ``None`` means OCR could not run at all. ``[]`` means OCR ran but no visible
    row matched.  Both states are intentional manual fallbacks; this function
    never scrolls, never clicks, and never treats the visible viewport as the
    complete query result set.
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

    words = list(_iter_words(result))
    rows = _cluster_rows(words, expected_row_height=_expected_row_height_px(automation))
    left, top, right, _bottom = rect
    click_x = _safe_click_x(automation, left, right)

    candidates: list[ResultCandidate] = []
    for row_index, row in enumerate(rows):
        if not row:
            continue
        place_no, name, address, row_text = _parse_row(row)
        score = max(match_score(query, row_text), match_score(query, name))
        if place_no:
            score = min(1.0, score + 0.02)
        if address:
            score = min(1.0, score + 0.02)
        if score < minimum_score:
            continue
        center_y = sum(word.center_y for word in row) / len(row)
        candidates.append(
            ResultCandidate(
                row_index=row_index,
                row_text=row_text,
                row_center_y=center_y,
                x=click_x,
                y=top + int(round(center_y)),
                place_no=place_no,
                name=name,
                address=address,
                match_score=score,
                ocr_confidence=None,
            )
        )

    candidates.sort(key=lambda candidate: (-candidate.match_score, candidate.row_center_y))
    return candidates[:max_candidates]
