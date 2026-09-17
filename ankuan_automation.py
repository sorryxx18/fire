from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pywinauto import Application, Desktop


ANKUAN_TITLE_KEY = "臺北市政府消防局安全管理系統"


class AnKuanError(RuntimeError):
    pass


@dataclass
class PlaceCandidate:
    place_no: str
    name: str
    status: str = ""
    permit_no: str = ""
    address: str = ""
    _wrapper: object | None = None
    _click_point: tuple[int, int] | None = None

    @property
    def display(self) -> str:
        bits = [self.place_no, self.name]
        if self.address:
            bits.append(self.address)
        return "｜".join(bits)


def _safe_text(ctrl) -> str:
    try:
        return (ctrl.window_text() or "").strip()
    except Exception:
        return ""


def _control_type(ctrl) -> str:
    try:
        return getattr(ctrl.element_info, "control_type", "") or ""
    except Exception:
        return ""


def _rect(ctrl):
    try:
        return ctrl.rectangle()
    except Exception:
        return None


def _center_y(rect) -> int:
    return int((rect.top + rect.bottom) / 2)


def _center_x(rect) -> int:
    return int((rect.left + rect.right) / 2)


class AnKuanAutomation:
    """Only drives the already logged-in AnKuan desktop UI.

    Design principles:
    - never handles credentials;
    - never calls internal APIs or databases;
    - only performs query / select / report-export actions;
    - if expected controls cannot be identified, stop instead of guessing.
    """

    def __init__(self):
        self.window = None
        self.handle: int | None = None
        self._last_candidates: list[PlaceCandidate] = []

    def connect(self):
        windows = []
        for w in Desktop(backend="uia").windows():
            title = _safe_text(w)
            if ANKUAN_TITLE_KEY in title:
                windows.append(w)
        if not windows:
            raise AnKuanError("找不到已開啟的安管系統。請先正常登入並保持安管視窗開啟。")

        # Prefer a visible, non-minimized top-level window.
        windows.sort(key=lambda w: (not self._is_visible(w), len(_safe_text(w))))
        self.window = windows[0]
        try:
            self.handle = int(self.window.element_info.handle)
        except Exception:
            self.handle = None
        self.activate()
        return self

    @staticmethod
    def _is_visible(ctrl) -> bool:
        try:
            return bool(ctrl.is_visible())
        except Exception:
            return True

    def activate(self):
        if not self.window:
            return
        try:
            self.window.restore()
        except Exception:
            pass
        try:
            self.window.set_focus()
        except Exception:
            pass

    def _descendants(self):
        if not self.window:
            raise AnKuanError("尚未連線安管系統。")
        try:
            return self.window.descendants()
        except Exception as e:
            raise AnKuanError(f"無法讀取安管控制項：{e}") from e

    def _find_by_text(self, names: Iterable[str], control_types: tuple[str, ...] = ()):
        wanted = [x.strip() for x in names]
        exact = []
        contains = []
        for c in self._descendants():
            text = _safe_text(c)
            if not text:
                continue
            ctype = _control_type(c)
            if control_types and ctype not in control_types:
                continue
            if text in wanted:
                exact.append(c)
            elif any(name in text for name in wanted):
                contains.append(c)
        return exact[0] if exact else (contains[0] if contains else None)

    @staticmethod
    def _click(ctrl):
        if ctrl is None:
            raise AnKuanError("找不到預期的安管按鈕／頁籤，已停止操作。")
        for method in ("invoke", "select", "click_input"):
            try:
                getattr(ctrl, method)()
                return
            except Exception:
                continue
        raise AnKuanError(f"無法操作控制項：{_safe_text(ctrl) or _control_type(ctrl)}")

    def open_place_search(self):
        """Open 建檔 > 場所建檔, preferring the native Win32 menu."""
        if not self.window:
            self.connect()
        self.activate()

        if self.handle:
            try:
                app = Application(backend="win32").connect(handle=self.handle)
                top = app.window(handle=self.handle)
                top.menu_select("建檔->場所建檔")
                time.sleep(0.8)
                return
            except Exception:
                pass

        # UIA fallback: click 建檔 then 場所建檔 by visible text.
        menu = self._find_by_text(["建檔"], ("MenuItem", "Text", "Button"))
        if not menu:
            raise AnKuanError("找不到「建檔」選單。請保持安管主視窗在最前方後重試。")
        self._click(menu)
        time.sleep(0.3)
        item = self._find_by_text(["場所建檔"], ("MenuItem", "Text", "Button"))
        if not item:
            raise AnKuanError("找不到「場所建檔」。已停止操作。")
        self._click(item)
        time.sleep(0.8)

    def _nearest_edit_to_label(self, label_text: str):
        label = self._find_by_text([label_text])
        if not label:
            return None
        lr = _rect(label)
        if not lr:
            return None

        candidates = []
        for c in self._descendants():
            if _control_type(c) not in ("Edit", "ComboBox", "Spinner"):
                continue
            r = _rect(c)
            if not r:
                continue
            # Candidate should be on the same row and to the right of the label.
            dy = abs(_center_y(r) - _center_y(lr))
            dx = r.left - lr.right
            if dy <= max(18, int((lr.bottom - lr.top) * 1.2)) and dx >= -5:
                candidates.append((dy, max(0, dx), c))
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][2] if candidates else None

    @staticmethod
    def _set_edit(ctrl, value: str):
        if ctrl is None:
            raise AnKuanError("找不到查詢輸入欄位，已停止操作。")
        for method in ("set_edit_text", "set_text"):
            try:
                getattr(ctrl, method)(value)
                return
            except Exception:
                continue
        try:
            ctrl.click_input()
            ctrl.type_keys("^a{BACKSPACE}", set_foreground=False)
            ctrl.type_keys(value, with_spaces=True, set_foreground=False)
            return
        except Exception as e:
            raise AnKuanError(f"無法輸入場所查詢條件：{e}") from e

    def search_places(self, query: str) -> list[PlaceCandidate]:
        query = query.strip()
        if not query:
            raise AnKuanError("請輸入場所名稱關鍵字。")

        self.open_place_search()

        # Ensure query condition page is selected if it is exposed as a control.
        condition_tab = self._find_by_text(["條件設定"], ("TabItem", "Button", "Text"))
        if condition_tab:
            try:
                self._click(condition_tab)
                time.sleep(0.3)
            except Exception:
                pass

        edit = self._nearest_edit_to_label("場所名稱")
        if not edit:
            raise AnKuanError(
                "已進入場所建檔，但無法辨識「場所名稱」輸入框。"
                "程式不會用座標亂點；請將這個畫面回報給開發端。"
            )
        self._set_edit(edit, query)

        button = self._find_by_text(["Q.查詢資料", "查詢資料"], ("Button", "Text", "Hyperlink"))
        if not button:
            raise AnKuanError("找不到「查詢資料」按鈕，已停止操作。")
        self._click(button)
        time.sleep(1.0)

        result_tab = self._find_by_text(["查詢結果"], ("TabItem", "Button", "Text"))
        if result_tab:
            try:
                self._click(result_tab)
                time.sleep(0.3)
            except Exception:
                pass

        candidates = self._read_result_candidates()
        self._last_candidates = candidates
        if not candidates:
            raise AnKuanError(
                "查詢已送出，但目前無法從安管結果表格讀出場所清單。"
                "這通常代表舊式表格沒有提供 Windows 無障礙文字；程式已停止，不會自行亂點。"
            )
        return candidates

    def _read_result_candidates(self) -> list[PlaceCandidate]:
        # Strategy A: DataItem rows exposed by UI Automation.
        rows: list[PlaceCandidate] = []
        for item in self._descendants():
            if _control_type(item) not in ("DataItem", "ListItem"):
                continue
            texts = []
            try:
                for c in item.descendants():
                    t = _safe_text(c)
                    if t and t not in texts:
                        texts.append(t)
            except Exception:
                pass
            own = _safe_text(item)
            if own and own not in texts:
                texts.insert(0, own)
            cand = self._candidate_from_tokens(texts, wrapper=item)
            if cand:
                rows.append(cand)

        if rows:
            return self._dedupe(rows)

        # Strategy B: group visible text controls by horizontal row.
        texts = []
        for c in self._descendants():
            t = _safe_text(c)
            r = _rect(c)
            if not t or not r:
                continue
            if _control_type(c) not in ("Text", "Edit", "Custom", "Pane"):
                continue
            if r.width() <= 0 or r.height() <= 0:
                continue
            texts.append((r, t, c))

        groups: list[list[tuple[object, str, object]]] = []
        for entry in sorted(texts, key=lambda x: (_center_y(x[0]), x[0].left)):
            y = _center_y(entry[0])
            target = None
            for g in groups:
                gy = sum(_center_y(x[0]) for x in g) / len(g)
                if abs(y - gy) <= 8:
                    target = g
                    break
            if target is None:
                groups.append([entry])
            else:
                target.append(entry)

        for g in groups:
            ordered = sorted(g, key=lambda x: x[0].left)
            tokens = [x[1] for x in ordered]
            cand = self._candidate_from_tokens(tokens)
            if cand:
                r0 = ordered[0][0]
                r1 = ordered[-1][0]
                cand._click_point = (int((r0.left + r1.right) / 2), int((r0.top + r0.bottom) / 2))
                rows.append(cand)
        return self._dedupe(rows)

    @staticmethod
    def _candidate_from_tokens(tokens: list[str], wrapper=None) -> PlaceCandidate | None:
        cleaned = [re.sub(r"\s+", " ", x).strip() for x in tokens if x and x.strip()]
        place_idx = -1
        for i, token in enumerate(cleaned):
            if re.fullmatch(r"\d{5,7}", token):
                place_idx = i
                break
        if place_idx < 0 or place_idx + 1 >= len(cleaned):
            return None

        place_no = cleaned[place_idx]
        tail = cleaned[place_idx + 1 :]
        if not tail:
            return None
        # Ignore obvious headers / unrelated numbers.
        if tail[0] in ("場所名稱", "最新更新日期", "列管日期"):
            return None

        name = tail[0]
        status = tail[1] if len(tail) > 1 and tail[1] in ("合法", "免列管", "停業", "歇業") else ""
        permit_no = ""
        address = ""
        for token in tail[1:]:
            if "使字" in token or "使照" in token:
                permit_no = token
            if any(k in token for k in ("市", "區", "路", "街", "號")) and token != name:
                address = token
        return PlaceCandidate(place_no, name, status, permit_no, address, wrapper)

    @staticmethod
    def _dedupe(rows: list[PlaceCandidate]) -> list[PlaceCandidate]:
        seen = set()
        out = []
        for r in rows:
            key = (r.place_no, r.name)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    def select_place(self, candidate: PlaceCandidate):
        if candidate not in self._last_candidates and not candidate.place_no:
            raise AnKuanError("無效的場所選擇。")
        self.activate()
        if candidate._wrapper is not None:
            try:
                candidate._wrapper.click_input()
                time.sleep(0.5)
                return
            except Exception:
                pass
        if candidate._click_point:
            try:
                from pywinauto import mouse

                mouse.click(coords=candidate._click_point)
                time.sleep(0.5)
                return
            except Exception:
                pass
        raise AnKuanError("已找到場所資料，但無法安全選取該列。")

    def open_safety_inspection(self):
        tab = self._find_by_text(["安全查察"], ("TabItem", "Button", "Text"))
        if not tab:
            raise AnKuanError("找不到「安全查察」頁籤。")
        self._click(tab)
        time.sleep(0.8)

    def export_place_record(self, timeout: int = 30) -> Path:
        self.open_safety_inspection()
        btn = self._find_by_text(["場所紀錄表"], ("Button", "Text"))
        if not btn:
            raise AnKuanError("找不到「場所紀錄表」按鈕。")

        before = self._snapshot_pdfs()
        self._click(btn)
        time.sleep(0.8)

        # The report option dialog uses these labels in the current system.
        self._ensure_report_options(
            ["管理權人", "消防設備", "防火管理", "防焰物品", "建物"]
        )
        confirm = self._find_by_text(["確認"], ("Button", "Text"))
        if not confirm:
            raise AnKuanError("場所紀錄表選項視窗已開啟，但找不到「確認」按鈕。")
        self._click(confirm)

        pdf = self._wait_new_pdf(before, timeout=timeout)
        if not pdf:
            raise AnKuanError(
                "已送出「場所紀錄表」產出，但未在下載／桌面／暫存資料夾偵測到新 PDF。"
                "安管可能是先開啟預覽視窗；請先人工另存一次，之後再補上預覽處理。"
            )
        return pdf

    def _ensure_report_options(self, labels: list[str]):
        # If checkboxes are exposed, turn on the required ones. If a label is not
        # exposed as a checkbox, leave existing defaults untouched rather than guess.
        controls = self._descendants()
        for label in labels:
            matches = [c for c in controls if _safe_text(c) == label and _control_type(c) == "CheckBox"]
            if not matches:
                continue
            cb = matches[0]
            try:
                state = cb.get_toggle_state()
            except Exception:
                state = None
            if state in (0, False):
                self._click(cb)

    @staticmethod
    def _watch_dirs() -> list[Path]:
        home = Path.home()
        dirs = [home / "Downloads", home / "Desktop", Path(tempfile.gettempdir())]
        return [p for p in dirs if p.exists()]

    def _snapshot_pdfs(self) -> dict[Path, float]:
        result = {}
        for d in self._watch_dirs():
            try:
                for p in d.glob("*.pdf"):
                    try:
                        result[p.resolve()] = p.stat().st_mtime
                    except OSError:
                        pass
            except OSError:
                pass
        return result

    def _wait_new_pdf(self, before: dict[Path, float], timeout: int = 30) -> Path | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidates = []
            for d in self._watch_dirs():
                try:
                    for p in d.glob("*.pdf"):
                        try:
                            rp = p.resolve()
                            mt = p.stat().st_mtime
                        except OSError:
                            continue
                        if rp not in before or mt > before.get(rp, 0) + 0.1:
                            candidates.append((mt, p))
                except OSError:
                    continue
            if candidates:
                candidates.sort(reverse=True)
                newest = candidates[0][1]
                # Wait briefly for the writer to finish.
                time.sleep(0.5)
                return newest
            time.sleep(0.5)
        return None
