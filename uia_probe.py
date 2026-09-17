from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pywinauto import Desktop

APP_TITLE = "安管介面檢測工具（唯讀）"


@dataclass
class WindowItem:
    title: str
    control_type: str
    class_name: str
    handle: int
    process_id: int | None
    wrapper: object

    @property
    def label(self) -> str:
        t = self.title.strip() or "(無標題)"
        return f"{t}  |  {self.class_name or '-'}  |  PID {self.process_id or '-'}"


def safe(callable_, default=""):
    try:
        value = callable_()
        return value if value is not None else default
    except Exception:
        return default


def list_windows() -> list[WindowItem]:
    desktop = Desktop(backend="uia")
    items: list[WindowItem] = []
    for w in desktop.windows():
        try:
            if not w.is_visible():
                continue
        except Exception:
            pass
        title = safe(lambda: w.window_text(), "")
        info = getattr(w, "element_info", None)
        items.append(
            WindowItem(
                title=title,
                control_type=getattr(info, "control_type", "") if info else "",
                class_name=getattr(info, "class_name", "") if info else "",
                handle=int(getattr(info, "handle", 0) or 0) if info else 0,
                process_id=getattr(info, "process_id", None) if info else None,
                wrapper=w,
            )
        )
    items.sort(key=lambda x: (x.title == "", x.title.lower()))
    return items


def dump_control_tree(window, max_nodes: int = 5000) -> str:
    lines: list[str] = []
    count = 0

    info = getattr(window, "element_info", None)
    lines.append("=== 安管介面檢測工具：唯讀 UI Automation 控制項清單 ===")
    lines.append("本工具不點擊、不輸入、不修改資料，只列出 Windows UI Automation 可見的控制項。")
    lines.append("")
    lines.append(f"Window title: {safe(lambda: window.window_text(), '')}")
    if info:
        lines.append(f"Window class: {getattr(info, 'class_name', '')}")
        lines.append(f"Window control_type: {getattr(info, 'control_type', '')}")
        lines.append(f"Window automation_id: {getattr(info, 'automation_id', '')}")
        lines.append(f"Window process_id: {getattr(info, 'process_id', '')}")
        lines.append(f"Window handle: {getattr(info, 'handle', '')}")
    lines.append("")
    lines.append("--- Controls ---")

    try:
        descendants = window.descendants()
    except Exception as e:
        lines.append(f"ERROR: 無法列舉控制項：{e}")
        return "\n".join(lines)

    for ctrl in descendants:
        count += 1
        if count > max_nodes:
            lines.append(f"... 已達 {max_nodes} 個控制項上限，後續省略 ...")
            break
        ei = getattr(ctrl, "element_info", None)
        if not ei:
            continue
        name = safe(lambda: ctrl.window_text(), "")
        control_type = getattr(ei, "control_type", "")
        automation_id = getattr(ei, "automation_id", "")
        class_name = getattr(ei, "class_name", "")
        handle = getattr(ei, "handle", "")
        rect = safe(lambda: ctrl.rectangle(), "")
        enabled = safe(lambda: ctrl.is_enabled(), "")
        visible = safe(lambda: ctrl.is_visible(), "")
        lines.append(
            f"[{count:04d}] name={name!r} | type={control_type!r} | automation_id={automation_id!r} | "
            f"class={class_name!r} | handle={handle!r} | enabled={enabled!r} | visible={visible!r} | rect={rect}"
        )

    lines.append("")
    lines.append(f"Total controls listed: {min(count, max_nodes)}")
    return "\n".join(lines)


class ProbeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("920x620")
        self.minsize(760, 500)
        self.windows: list[WindowItem] = []
        self._build_ui()
        self.refresh_windows()

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text=APP_TITLE, font=("Microsoft JhengHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text=(
                "請先登入並開啟安管 Windows 程式，再按「重新掃描視窗」。"
                "本工具只讀取 UI Automation 資訊，不會點擊、輸入或修改安管資料。"
            ),
            wraplength=880,
        ).pack(anchor="w", pady=(5, 12))

        row = ttk.Frame(root)
        row.pack(fill="x")
        ttk.Label(row, text="目標視窗：").pack(side="left")
        self.window_var = tk.StringVar()
        self.combo = ttk.Combobox(row, textvariable=self.window_var, state="readonly")
        self.combo.pack(side="left", fill="x", expand=True, padx=(6, 8))
        ttk.Button(row, text="重新掃描視窗", command=self.refresh_windows).pack(side="left")

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(10, 8))
        ttk.Button(actions, text="檢測控制項", command=self.inspect_selected).pack(side="left")
        ttk.Button(actions, text="另存檢測結果 TXT", command=self.save_result).pack(side="left", padx=(8, 0))
        ttk.Label(actions, text="建議先切到你實際要『匯出 PDF』的頁面再檢測。", foreground="#555555").pack(side="left", padx=(14, 0))

        self.text = tk.Text(root, wrap="none", font=("Consolas", 9))
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", "等待檢測。\n")
        self.last_result = ""

        self.status_var = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

    def refresh_windows(self):
        try:
            self.windows = list_windows()
        except Exception as e:
            messagebox.showerror("掃描失敗", str(e))
            return
        labels = [x.label for x in self.windows]
        self.combo["values"] = labels
        if labels:
            self.combo.current(0)
        self.status_var.set(f"找到 {len(labels)} 個可見視窗。請選擇安管程式。")

    def selected_item(self) -> WindowItem | None:
        idx = self.combo.current()
        if idx < 0 or idx >= len(self.windows):
            return None
        return self.windows[idx]

    def inspect_selected(self):
        item = self.selected_item()
        if not item:
            messagebox.showwarning("尚未選擇", "請先選擇安管程式視窗。")
            return
        self.status_var.set("正在列舉控制項……")
        self.update_idletasks()
        result = dump_control_tree(item.wrapper)
        self.last_result = result
        self.text.delete("1.0", "end")
        self.text.insert("1.0", result)
        self.status_var.set("檢測完成。若能看到按鈕名稱、AutomationId、ControlType，就可進一步做穩定的自動匯出。")

    def save_result(self):
        if not self.last_result:
            messagebox.showwarning("尚無結果", "請先按「檢測控制項」。")
            return
        path = filedialog.asksaveasfilename(
            title="儲存安管介面檢測結果",
            defaultextension=".txt",
            initialfile="ankuan_ui_probe.txt",
            filetypes=[("文字檔", "*.txt")],
        )
        if not path:
            return
        Path(path).write_text(self.last_result, encoding="utf-8")
        messagebox.showinfo("完成", f"已儲存：\n{path}")


if __name__ == "__main__":
    ProbeApp().mainloop()
