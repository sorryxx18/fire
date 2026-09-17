from __future__ import annotations

import csv
import io
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
    _result_index: int | None = None
    _result_count: int | None = None

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


def _class_name(ctrl) -> str:
    try:
        return (ctrl.class_name() or "").strip()
    except Exception:
        try:
            return (getattr(ctrl.element_info, "class_name", "") or "").strip()
        except Exception:
            return ""


def _friendly_class(ctrl) -> str:
    try:
        return (ctrl.friendly_class_name() or "").strip()
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


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


class AnKuanAutomation:
    """Drive only the already logged-in AnKuan desktop UI.

    It never handles credentials, calls internal APIs/databases, or performs
    create/edit/delete actions.  Search-result reading prefers the system's own
    official CSV export when the legacy grid is not exposed through Windows UIA.
    """

    def __init__(self):
        self.window = None
        self.handle: int | None = None
        self._last_candidates: list[PlaceCandidate] = []

    # ---------- connection / generic controls ----------
    def connect(self):
        windows = []
        for w in Desktop(backend="uia").windows():
            title = _safe_text(w)
            if ANKUAN_TITLE_KEY in title:
                windows.append(w)
        if not windows:
            raise AnKuanError("找不到已開啟的安管系統。請先正常登入並保持安管視窗開啟。")

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

    def _win32_top(self):
        if not self.handle:
            return None
        try:
            app = Application(backend="win32").connect(handle=self.handle)
            return app.window(handle=self.handle)
        except Exception:
            return None

    def _win32_descendants(self):
        top = self._win32_top()
        if top is None:
            return []
        try:
            return top.descendants()
        except Exception:
            return []

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

    def _find_win32_by_text(self, names: Iterable[str]):
        wanted = [x.strip() for x in names]
        exact = []
        contains = []
        for c in self._win32_descendants():
            text = _safe_text(c)
            if not text:
                continue
            if text in wanted:
                exact.append(c)
            elif any(name in text for name in wanted):
                contains.append(c)
        return exact[0] if exact else (contains[0] if contains else None)

    def _find_any_text(self, names: Iterable[str], control_types: tuple[str, ...] = ()):
        return self._find_by_text(names, control_types) or self._find_win32_by_text(names)

    @staticmethod
    def _click(ctrl):
        if ctrl is None:
            raise AnKuanError("找不到預期的安管按鈕／頁籤，已停止操作。")
        for method in ("invoke", "select", "click_input", "click"):
            try:
                getattr(ctrl, method)()
                return
            except Exception:
                continue
        raise AnKuanError(f"無法操作控制項：{_safe_text(ctrl) or _control_type(ctrl) or _class_name(ctrl)}")

    # ---------- query page ----------
    def open_place_search(self):
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

    def _open_condition_tab(self):
        tab = self._find_any_text(["條件設定"], ("TabItem", "Button", "Text"))
        if tab:
            try:
                self._click(tab)
                time.sleep(0.25)
            except Exception:
                pass

    def _clear_query_fields(self):
        clear = self._find_any_text(["清除欄位"], ("Button", "Text"))
        if clear:
            try:
                self._click(clear)
                time.sleep(0.2)
            except Exception:
                pass

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
            dy = abs(_center_y(r) - _center_y(lr))
            dx = r.left - lr.right
            if dy <= max(18, int((lr.bottom - lr.top) * 1.2)) and dx >= -5:
                candidates.append((dy, max(0, dx), c))
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][2] if candidates else None

    def _nearest_win32_edit_to_label(self, label_text: str):
        label = self._find_win32_by_text([label_text])
        lr = _rect(label) if label is not None else None
        if not lr:
            return None
        candidates = []
        for c in self._win32_descendants():
            r = _rect(c)
            if not r:
                continue
            cls = (_class_name(c) + " " + _friendly_class(c)).lower()
            if "edit" not in cls:
                continue
            try:
                if not c.is_visible() or not c.is_enabled():
                    continue
            except Exception:
                pass
            dy = abs(_center_y(r) - _center_y(lr))
            dx = r.left - lr.right
            if dy <= 18 and dx >= -8:
                candidates.append((dy, max(0, dx), c))
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][2] if candidates else None

    def _legacy_place_name_edit(self):
        top = self._win32_top()
        tr = _rect(top) if top is not None else None
        if not tr:
            return None
        tw = max(1, tr.right - tr.left)
        th = max(1, tr.bottom - tr.top)
        edits = []
        for c in self._win32_descendants():
            r = _rect(c)
            if not r:
                continue
            cls = (_class_name(c) + " " + _friendly_class(c)).lower()
            if "edit" not in cls:
                continue
            try:
                if not c.is_visible() or not c.is_enabled():
                    continue
            except Exception:
                pass
            w = r.right - r.left
            h = r.bottom - r.top
            cy = _center_y(r)
            if w < max(180, int(tw * 0.16)) or h < 14 or h > 70:
                continue
            if cy < tr.top + int(th * 0.18) or cy > tr.top + int(th * 0.58):
                continue
            edits.append((r, c))
        if len(edits) < 2:
            return None

        groups: list[list[tuple[object, object]]] = []
        for entry in sorted(edits, key=lambda x: (_center_y(x[0]), x[0].left)):
            cy = _center_y(entry[0])
            target = None
            for g in groups:
                gy = sum(_center_y(x[0]) for x in g) / len(g)
                if abs(cy - gy) <= 16:
                    target = g
                    break
            if target is None:
                groups.append([entry])
            else:
                target.append(entry)
        two_wide_rows = [g for g in groups if len(g) >= 2]
        if not two_wide_rows:
            return None
        row = sorted(two_wide_rows, key=lambda g: min(_center_y(x[0]) for x in g))[0]
        row = sorted(row, key=lambda x: x[0].left)
        return row[0][1] if _center_x(row[0][0]) < _center_x(row[1][0]) else None

    def _legacy_place_no_edit(self):
        by_label = self._nearest_win32_edit_to_label("場所編號")
        if by_label is not None:
            return by_label
        top = self._win32_top()
        tr = _rect(top) if top is not None else None
        if not tr:
            return None
        th = max(1, tr.bottom - tr.top)
        candidates = []
        for c in self._win32_descendants():
            r = _rect(c)
            if not r:
                continue
            cls = (_class_name(c) + " " + _friendly_class(c)).lower()
            if "edit" not in cls:
                continue
            text = _safe_text(c)
            if text and not re.fullmatch(r"\d{1,7}", text):
                continue
            w = r.right - r.left
            cy = _center_y(r)
            if 35 <= w <= 180 and tr.top + int(th * 0.28) <= cy <= tr.top + int(th * 0.62):
                candidates.append((cy, r.left, c))
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

    def _click_query(self):
        button = self._find_any_text(["Q.查詢資料", "Q. 查詢資料", "查詢資料"], ("Button", "Text", "Hyperlink"))
        if not button:
            raise AnKuanError("找不到「查詢資料」按鈕，已停止操作。")
        self._click(button)
        time.sleep(1.2)
        result_tab = self._find_any_text(["查詢結果"], ("TabItem", "Button", "Text"))
        if result_tab:
            try:
                self._click(result_tab)
                time.sleep(0.25)
            except Exception:
                pass

    def search_places(self, query: str) -> list[PlaceCandidate]:
        query = query.strip()
        if not query:
            raise AnKuanError("請輸入場所名稱關鍵字。")

        self.open_place_search()
        self._open_condition_tab()
        self._clear_query_fields()

        edit = self._nearest_edit_to_label("場所名稱") or self._legacy_place_name_edit()
        if not edit:
            raise AnKuanError(
                "已進入場所建檔，但仍無法安全辨識「場所名稱」輸入框。"
                "程式已嘗試 UI Automation 與 Win32 原生控制項；畫面結構不符即停止。"
            )
        self._set_edit(edit, query)
        self._click_query()

        candidates = self._filter_candidates(self._read_result_candidates(), query)
        if not candidates:
            # The legacy grid on this client is painted/custom and exposes no row text.
            # Use the system's own read-only/export action instead of OCR or fixed pixels.
            candidates = self._export_result_csv_candidates(query)

        self._last_candidates = candidates
        if not candidates:
            raise AnKuanError("查詢完成，但找不到符合關鍵字的場所。")
        return candidates

    # ---------- legacy result grid ----------
    @staticmethod
    def _looks_like_date_or_time(token: str) -> bool:
        t = token.strip()
        return bool(
            re.fullmatch(r"20\d{2}/\d{1,2}/\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", t)
            or re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", t)
        )

    def _read_result_candidates(self) -> list[PlaceCandidate]:
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

        for source in (self._descendants(), self._win32_descendants()):
            texts = []
            for c in source:
                t = _safe_text(c)
                r = _rect(c)
                if not t or not r:
                    continue
                try:
                    if not c.is_visible():
                        continue
                except Exception:
                    pass
                texts.append((r, t, c))

            groups: list[list[tuple[object, str, object]]] = []
            for entry in sorted(texts, key=lambda x: (_center_y(x[0]), x[0].left)):
                y = _center_y(entry[0])
                target = None
                for g in groups:
                    gy = sum(_center_y(x[0]) for x in g) / len(g)
                    if abs(y - gy) <= 10:
                        target = g
                        break
                if target is None:
                    groups.append([entry])
                else:
                    target.append(entry)

            for g in groups:
                ordered = sorted(g, key=lambda x: x[0].left)
                cand = self._candidate_from_tokens([x[1] for x in ordered])
                if cand:
                    cand._wrapper = ordered[0][2]
                    cand._click_point = (_center_x(ordered[0][0]), _center_y(ordered[0][0]))
                    rows.append(cand)
            if rows:
                break
        return self._dedupe(rows)

    def _candidate_from_tokens(self, tokens: list[str], wrapper=None) -> PlaceCandidate | None:
        cleaned = [re.sub(r"\s+", " ", x).strip() for x in tokens if x and x.strip()]
        headers = {"場所編號", "場所名稱", "最新更新日期", "列管日期", "安檢日期", "場所地址", "列管狀況", "使照號碼"}
        place_idx = -1
        for i, token in enumerate(cleaned):
            if re.fullmatch(r"\d{1,7}", token):
                place_idx = i
                break
        if place_idx < 0:
            return None
        place_no = cleaned[place_idx]
        tail = cleaned[place_idx + 1 :]
        if not tail:
            return None

        status = ""
        permit_no = ""
        address = ""
        name = ""
        for token in tail:
            if token in headers or self._looks_like_date_or_time(token):
                continue
            if token in ("合法", "免列管", "停業", "歇業"):
                status = token
                continue
            if "使字" in token or "使照" in token:
                permit_no = token
                continue
            if any(k in token for k in ("市", "區", "路", "街", "巷", "弄", "號")) and len(token) >= 6:
                address = token
                continue
            if not name and len(token) >= 2 and not re.fullmatch(r"[\d./:-]+", token):
                name = token
        if not name:
            return None
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

    @staticmethod
    def _filter_candidates(rows: list[PlaceCandidate], query: str) -> list[PlaceCandidate]:
        q = _norm(query)
        if not q:
            return rows
        matched = []
        for r in rows:
            hay = _norm(" ".join([r.place_no, r.name, r.address, r.permit_no, r.status]))
            if q in hay:
                matched.append(r)
        return matched

    # ---------- official CSV result export ----------
    @staticmethod
    def _watch_dirs() -> list[Path]:
        home = Path.home()
        dirs = [home / "Downloads", home / "Desktop", Path(tempfile.gettempdir())]
        return [p for p in dirs if p.exists()]

    def _snapshot_files(self, suffix: str) -> dict[Path, float]:
        result: dict[Path, float] = {}
        suffix = suffix.lower()
        for d in self._watch_dirs():
            try:
                for p in d.iterdir():
                    if p.is_file() and p.suffix.lower() == suffix:
                        try:
                            result[p.resolve()] = p.stat().st_mtime
                        except OSError:
                            pass
            except OSError:
                pass
        return result

    def _wait_new_file(self, suffix: str, before: dict[Path, float], timeout: float = 8) -> Path | None:
        deadline = time.time() + timeout
        suffix = suffix.lower()
        while time.time() < deadline:
            candidates = []
            for d in self._watch_dirs():
                try:
                    for p in d.iterdir():
                        if not p.is_file() or p.suffix.lower() != suffix:
                            continue
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
                time.sleep(0.35)
                return candidates[0][1]
            time.sleep(0.25)
        return None

    def _try_save_dialog(self, target: Path, timeout: float = 2.5) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for dlg in Desktop(backend="win32").windows():
                title = _safe_text(dlg)
                cls = _class_name(dlg)
                if cls != "#32770":
                    continue
                if not any(k in title for k in ("另存", "儲存", "存檔", "Save")):
                    continue
                edits = []
                try:
                    for c in dlg.descendants():
                        r = _rect(c)
                        if not r:
                            continue
                        if "edit" not in (_class_name(c) + " " + _friendly_class(c)).lower():
                            continue
                        try:
                            if not c.is_visible() or not c.is_enabled():
                                continue
                        except Exception:
                            pass
                        edits.append((r.bottom, r.right - r.left, c))
                except Exception:
                    edits = []
                if not edits:
                    continue
                # Standard Save As: file-name edit is normally the lowest usable edit.
                edits.sort(key=lambda x: (x[0], x[1]), reverse=True)
                file_edit = edits[0][2]
                self._set_edit(file_edit, str(target))

                save = None
                try:
                    for c in dlg.descendants():
                        text = _safe_text(c)
                        if any(k in text for k in ("儲存", "存檔", "Save")) and "Button" in (_friendly_class(c) or "Button"):
                            save = c
                            break
                except Exception:
                    pass
                if save is None:
                    save = self._find_win32_by_text(["儲存", "存檔", "Save"])
                if save is not None:
                    self._click(save)
                    return True
            time.sleep(0.15)
        return False

    def _export_result_csv_candidates(self, query: str) -> list[PlaceCandidate]:
        btn = self._find_any_text(["存檔(CSV)", "存檔（CSV）"], ("Button", "Text"))
        if not btn:
            raise AnKuanError(
                "安管結果表格無法直接讀取，且找不到畫面上的「存檔(CSV)」按鈕。"
                "程式已停止，不會用 OCR 或固定螢幕座標猜資料。"
            )

        before = self._snapshot_files(".csv")
        target = Path(tempfile.gettempdir()) / f"ankuan_query_{int(time.time() * 1000)}.csv"
        self._click(btn)
        time.sleep(0.45)
        self._try_save_dialog(target)

        csv_path = target if target.exists() else self._wait_new_file(".csv", before, timeout=7)
        if not csv_path:
            raise AnKuanError(
                "已按下安管的「存檔(CSV)」，但沒有取得 CSV。"
                "若畫面出現另存新檔視窗，請截圖回報。"
            )
        rows = self._parse_result_csv(csv_path)
        return self._filter_candidates(rows, query)

    def _parse_result_csv(self, path: Path) -> list[PlaceCandidate]:
        raw = path.read_bytes()
        text = ""
        for enc in ("utf-8-sig", "cp950", "big5", "utf-16", "utf-8"):
            try:
                candidate = raw.decode(enc)
            except Exception:
                continue
            if "場所" in candidate[:10000] or enc in ("cp950", "big5"):
                text = candidate
                if "場所" in candidate:
                    break
        if not text:
            raise AnKuanError("安管 CSV 已產生，但無法辨識文字編碼。")

        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
            parsed = list(csv.reader(io.StringIO(text), dialect))
        except Exception:
            parsed = list(csv.reader(io.StringIO(text)))

        header_i = None
        header = []
        for i, row in enumerate(parsed[:30]):
            n = [_norm(x) for x in row]
            if any("場所編號" in x for x in n) and any("場所名稱" in x for x in n):
                header_i = i
                header = row
                break
        if header_i is None:
            raise AnKuanError("安管 CSV 已產生，但找不到「場所編號／場所名稱」欄位。")

        def col(*names: str) -> int | None:
            normalized = [_norm(x) for x in header]
            for name in names:
                key = _norm(name)
                for idx, h in enumerate(normalized):
                    if key == h or key in h:
                        return idx
            return None

        i_no = col("場所編號")
        i_name = col("場所名稱")
        i_addr = col("場所地址", "地址")
        i_status = col("列管狀況")
        i_permit = col("使照號碼", "使用執照")
        if i_no is None or i_name is None:
            raise AnKuanError("安管 CSV 欄位不足，無法建立場所清單。")

        def value(row: list[str], idx: int | None) -> str:
            return row[idx].strip() if idx is not None and idx < len(row) else ""

        all_rows: list[PlaceCandidate] = []
        for row in parsed[header_i + 1 :]:
            place_no = value(row, i_no)
            name = value(row, i_name)
            if not re.fullmatch(r"\d{1,7}", place_no) or not name:
                continue
            all_rows.append(
                PlaceCandidate(
                    place_no=place_no,
                    name=name,
                    status=value(row, i_status),
                    permit_no=value(row, i_permit),
                    address=value(row, i_addr),
                )
            )
        total = len(all_rows)
        for idx, item in enumerate(all_rows):
            item._result_index = idx
            item._result_count = total
        return self._dedupe(all_rows)

    # ---------- select chosen place ----------
    def _select_exact_place_no(self, candidate: PlaceCandidate) -> bool:
        try:
            self.open_place_search()
            self._open_condition_tab()
            self._clear_query_fields()
            edit = self._nearest_edit_to_label("場所編號") or self._legacy_place_no_edit()
            if edit is None:
                return False
            self._set_edit(edit, candidate.place_no)
            self._click_query()
            first = self._find_any_text(["首筆"], ("Button", "Text"))
            if first:
                self._click(first)
                time.sleep(0.35)
            return True
        except Exception:
            return False

    def _select_by_navigation(self, candidate: PlaceCandidate) -> bool:
        if candidate._result_index is None or not candidate._result_count:
            return False
        idx = candidate._result_index
        count = candidate._result_count
        from_start = idx
        from_end = count - 1 - idx
        if min(from_start, from_end) > 250:
            return False

        if from_start <= from_end:
            anchor = self._find_any_text(["首筆"], ("Button", "Text"))
            step_btn = self._find_any_text(["下筆"], ("Button", "Text"))
            steps = from_start
        else:
            anchor = self._find_any_text(["末筆"], ("Button", "Text"))
            step_btn = self._find_any_text(["上筆"], ("Button", "Text"))
            steps = from_end
        if anchor is None or (steps and step_btn is None):
            return False
        self._click(anchor)
        time.sleep(0.2)
        for _ in range(steps):
            self._click(step_btn)
            time.sleep(0.025)
        time.sleep(0.25)
        return True

    def select_place(self, candidate: PlaceCandidate):
        if not candidate.place_no:
            raise AnKuanError("無效的場所選擇。")
        self.activate()

        if candidate._wrapper is not None:
            try:
                candidate._wrapper.click_input()
                time.sleep(0.4)
                return
            except Exception:
                pass
        if candidate._click_point:
            try:
                from pywinauto import mouse
                mouse.click(coords=candidate._click_point)
                time.sleep(0.4)
                return
            except Exception:
                pass

        # CSV candidates have no clickable row object. Re-query by exact place number;
        # this is safer than guessing a painted grid coordinate.
        if self._select_exact_place_no(candidate):
            return
        if self._select_by_navigation(candidate):
            return
        raise AnKuanError(
            "場所清單已讀取，但無法安全切換到所選場所。"
            "程式沒有使用固定螢幕座標，請回報目前查詢結果畫面。"
        )

    # ---------- report export ----------
    def open_safety_inspection(self):
        tab = self._find_any_text(["安全查察"], ("TabItem", "Button", "Text"))
        if not tab:
            raise AnKuanError("找不到「安全查察」頁籤。")
        self._click(tab)
        time.sleep(0.8)

    def export_place_record(self, timeout: int = 30) -> Path:
        self.open_safety_inspection()
        btn = self._find_any_text(["場所紀錄表"], ("Button", "Text"))
        if not btn:
            raise AnKuanError("找不到「場所紀錄表」按鈕。")

        before = self._snapshot_files(".pdf")
        self._click(btn)
        time.sleep(0.8)
        self._ensure_report_options(["管理權人", "消防設備", "防火管理", "防焰物品", "建物"])
        confirm = self._find_any_text(["確認"], ("Button", "Text"))
        if not confirm:
            raise AnKuanError("場所紀錄表選項視窗已開啟，但找不到「確認」按鈕。")
        self._click(confirm)

        pdf = self._wait_new_file(".pdf", before, timeout=timeout)
        if not pdf:
            raise AnKuanError(
                "已送出「場所紀錄表」產出，但未在下載／桌面／暫存資料夾偵測到新 PDF。"
                "安管可能先開啟預覽視窗；請將預覽畫面截圖回報。"
            )
        return pdf

    def _ensure_report_options(self, labels: list[str]):
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
