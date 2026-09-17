from __future__ import annotations

import sys
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from parser import parse_place_record_pdf
from word_writer import default_output_path, fill_word_template, roc_date

APP_TITLE = "施工中場所火災表自動產製工具"


def _short_status(data) -> list[tuple[str, str]]:
    rows = [("場所名稱", data.display_name or "未讀取"), ("場所地址", data.address or "未讀取")]
    if data.latest_equipment_inspection:
        r = data.latest_equipment_inspection
        rows.append(("消防設備檢查", f"{roc_date(r.date)} / {r.result}"))
    else:
        rows.append(("消防設備檢查", "未找到（Word 保留預留文字）"))
    if data.latest_equipment_report:
        r = data.latest_equipment_report
        rows.append(("檢修申報", f"{roc_date(r.received_date)} / {r.period} / {r.result}"))
    else:
        rows.append(("檢修申報", "未找到（Word 保留預留文字）"))
    rows.append(("消防防護計畫", roc_date(data.fire_plan_date) if data.fire_plan_date else "未找到（Word 保留預留文字）"))
    rows.append(("防焰物品", data.flame_retardant_text or "未找到（Word 保留預留文字）"))
    return rows


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x500")
        self.minsize(680, 440)
        self.pdf_path: Path | None = None
        self.data = None
        self._build_ui()

    def _build_ui(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text=APP_TITLE, font=("Microsoft JhengHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(root, text="選擇「臺北市政府消防局場所紀錄表」PDF，程式依固定欄位規則填入內建 Word 制式表。全程不使用 AI。", wraplength=710).pack(anchor="w", pady=(5, 14))
        file_frame = ttk.Frame(root)
        file_frame.pack(fill="x")
        self.path_var = tk.StringVar(value="尚未選擇 PDF")
        ttk.Entry(file_frame, textvariable=self.path_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(file_frame, text="選擇 PDF", command=self.choose_pdf).pack(side="left", padx=(8, 0))
        ttk.Separator(root).pack(fill="x", pady=14)
        ttk.Label(root, text="讀取結果", font=("Microsoft JhengHei UI", 11, "bold")).pack(anchor="w")
        self.tree = ttk.Treeview(root, columns=("field", "value"), show="headings", height=9)
        self.tree.heading("field", text="欄位")
        self.tree.heading("value", text="擷取結果")
        self.tree.column("field", width=160, anchor="w")
        self.tree.column("value", width=520, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(6, 10))
        bottom = ttk.Frame(root)
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="請先選擇 PDF。")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.generate_btn = ttk.Button(bottom, text="產生 Word", command=self.generate_word, state="disabled")
        self.generate_btn.pack(side="right")

    def choose_pdf(self):
        path = filedialog.askopenfilename(title="選擇場所紀錄表 PDF", filetypes=[("PDF 檔案", "*.pdf")])
        if path:
            self.load_pdf(Path(path))

    def load_pdf(self, path: Path):
        try:
            self.status_var.set("讀取 PDF 中……")
            self.update_idletasks()
            data = parse_place_record_pdf(path)
        except Exception as e:
            self.data = None
            self.generate_btn.configure(state="disabled")
            self.status_var.set("讀取失敗。")
            messagebox.showerror("讀取失敗", str(e))
            return
        self.pdf_path = path
        self.data = data
        self.path_var.set(str(path))
        for item in self.tree.get_children():
            self.tree.delete(item)
        for field, value in _short_status(data):
            self.tree.insert("", "end", values=(field, value))
        self.generate_btn.configure(state="normal")
        self.status_var.set("PDF 讀取完成，可產生 Word。")

    def generate_word(self):
        if not self.pdf_path or not self.data:
            return
        default_path = default_output_path(self.pdf_path, self.data)
        path = filedialog.asksaveasfilename(title="儲存 Word", defaultextension=".docx", initialdir=str(default_path.parent), initialfile=default_path.name, filetypes=[("Word 文件", "*.docx")])
        if not path:
            return
        try:
            out, updated = fill_word_template(self.data, Path(path))
        except Exception as e:
            self.status_var.set("Word 產生失敗。")
            messagebox.showerror("產生失敗", f"{e}\n\n{traceback.format_exc()}")
            return
        self.status_var.set(f"完成：{out.name}")
        messagebox.showinfo("完成", "Word 已產生。\n\n已自動填入：\n- " + "\n- ".join(updated) + "\n\n火災發生後資料及 PDF 未提供資料均維持制式預留文字。")


def main():
    app = App()
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.exists() and p.suffix.lower() == ".pdf":
            app.after(150, lambda: app.load_pdf(p))
    app.mainloop()


if __name__ == "__main__":
    main()
