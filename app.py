from __future__ import annotations

import sys
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ankuan_automation import AnKuanAutomation, AnKuanError, PlaceCandidate
from parser import parse_place_record_pdf
from word_writer import default_output_path, fill_word_template, roc_date

APP_TITLE = "消防安全管理及應變表自動產製工具"


def _short_status(data) -> list[tuple[str, str]]:
    rows = [
        ("場所名稱", data.display_name or "未讀取"),
        ("場所地址", data.address or "未讀取"),
        ("用途／樓層", f"{data.purpose or '未讀取'} / {data.business_floors or '未讀取'}"),
    ]
    if data.latest_equipment_inspection:
        r = data.latest_equipment_inspection
        rows.append(("消防設備檢查", f"{roc_date(r.date)} / {r.result}"))
    else:
        rows.append(("消防設備檢查", "未找到（Word 會標註待補）"))
    if data.latest_equipment_report:
        r = data.latest_equipment_report
        rows.append(("檢修申報", f"{roc_date(r.received_date)} / {r.period} / {r.result}"))
    else:
        rows.append(("檢修申報", "未找到（Word 會標註待補）"))
    if data.latest_fire_management_inspection:
        r = data.latest_fire_management_inspection
        rows.append(("防火管理檢查", f"{roc_date(r.date)} / {r.result}"))
    else:
        rows.append(("防火管理檢查", "未找到（Word 會標註待補）"))
    return rows


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("920x650")
        self.minsize(800, 560)
        self.automation: AnKuanAutomation | None = None
        self.candidates: list[PlaceCandidate] = []
        self.pdf_path: Path | None = None
        self.data = None
        self._build_ui()

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text=APP_TITLE, font=("Microsoft JhengHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text=(
                "先正常登入安管系統，再於本工具輸入場所名稱關鍵字。"
                "工具只操作既有安管畫面的查詢與報表匯出，不處理帳密、不連內部 API。"
            ),
            wraplength=880,
        ).pack(anchor="w", pady=(5, 10))

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)
        self.auto_tab = ttk.Frame(notebook, padding=12)
        self.manual_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.auto_tab, text="安管自動查詢")
        notebook.add(self.manual_tab, text="手動 PDF 備援")
        self._build_auto_tab()
        self._build_manual_tab()

        self.status_var = tk.StringVar(value="請先登入並開啟安管系統。")
        ttk.Label(root, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

    def _build_auto_tab(self):
        search = ttk.Frame(self.auto_tab)
        search.pack(fill="x")
        ttk.Label(search, text="場所名稱關鍵字：").pack(side="left")
        self.query_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.query_var)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 8))
        entry.bind("<Return>", lambda _e: self.search_ankuan())
        ttk.Button(search, text="查詢", command=self.search_ankuan).pack(side="left")

        ttk.Label(
            self.auto_tab,
            text="模糊查詢若有多筆結果，請人工選擇；程式不會自行猜測場所。",
            foreground="#555555",
        ).pack(anchor="w", pady=(8, 6))

        self.result_tree = ttk.Treeview(
            self.auto_tab,
            columns=("no", "name", "address", "permit", "status"),
            show="headings",
            height=12,
            selectmode="browse",
        )
        headings = {
            "no": "場所編號",
            "name": "場所名稱",
            "address": "場所地址",
            "permit": "使照號碼",
            "status": "列管狀況",
        }
        widths = {"no": 85, "name": 300, "address": 300, "permit": 130, "status": 80}
        for key in headings:
            self.result_tree.heading(key, text=headings[key])
            self.result_tree.column(key, width=widths[key], anchor="w")
        self.result_tree.pack(fill="both", expand=True)

        actions = ttk.Frame(self.auto_tab)
        actions.pack(fill="x", pady=(10, 0))
        self.confirm_btn = ttk.Button(
            actions,
            text="確認場所並產生 Word",
            command=self.generate_from_ankuan,
            state="disabled",
        )
        self.confirm_btn.pack(side="right")

    def _build_manual_tab(self):
        ttk.Label(
            self.manual_tab,
            text="如果安管介面自動化暫時無法辨識，可照原流程手動匯出「場所紀錄表」PDF，再由本工具產生 Word。",
            wraplength=840,
        ).pack(anchor="w", pady=(0, 10))

        row = ttk.Frame(self.manual_tab)
        row.pack(fill="x")
        self.path_var = tk.StringVar(value="尚未選擇 PDF")
        ttk.Entry(row, textvariable=self.path_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="選擇 PDF", command=self.choose_pdf).pack(side="left", padx=(8, 0))

        self.manual_tree = ttk.Treeview(
            self.manual_tab,
            columns=("field", "value"),
            show="headings",
            height=10,
        )
        self.manual_tree.heading("field", text="欄位")
        self.manual_tree.heading("value", text="擷取結果")
        self.manual_tree.column("field", width=170, anchor="w")
        self.manual_tree.column("value", width=600, anchor="w")
        self.manual_tree.pack(fill="both", expand=True, pady=(10, 10))

        self.manual_generate_btn = ttk.Button(
            self.manual_tab,
            text="產生 Word",
            command=self.generate_manual_word,
            state="disabled",
        )
        self.manual_generate_btn.pack(anchor="e")

    def search_ankuan(self):
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning("請輸入關鍵字", "請輸入場所名稱關鍵字，例如「洲際」或「大巨蛋」。")
            return
        self.status_var.set("正在連線安管並查詢場所……")
        self.update_idletasks()
        try:
            self.automation = AnKuanAutomation().connect()
            self.candidates = self.automation.search_places(query)
        except Exception as e:
            self.confirm_btn.configure(state="disabled")
            self.status_var.set("安管查詢未完成。")
            messagebox.showerror("安管查詢未完成", str(e))
            return

        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        for idx, candidate in enumerate(self.candidates):
            self.result_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    candidate.place_no,
                    candidate.name,
                    candidate.address,
                    candidate.permit_no,
                    candidate.status,
                ),
            )
        if len(self.candidates) == 1:
            self.result_tree.selection_set("0")
            self.result_tree.focus("0")
        self.confirm_btn.configure(state="normal")
        self.status_var.set(f"查到 {len(self.candidates)} 筆場所，請確認後產生 Word。")

    def _selected_candidate(self) -> PlaceCandidate | None:
        selected = self.result_tree.selection()
        if not selected:
            return None
        try:
            return self.candidates[int(selected[0])]
        except Exception:
            return None

    def generate_from_ankuan(self):
        candidate = self._selected_candidate()
        if not candidate or not self.automation:
            messagebox.showwarning("尚未選擇場所", "請先在查詢結果中選擇正確場所。")
            return

        confirm = messagebox.askyesno(
            "確認場所",
            f"場所編號：{candidate.place_no}\n"
            f"場所名稱：{candidate.name}\n"
            f"場所地址：{candidate.address or '未讀取'}\n"
            f"使照號碼：{candidate.permit_no or '未讀取'}\n\n"
            "確認後將操作安管的「安全查察 → 場所紀錄表」匯出報表。\n"
            "全程不會新增、修改或刪除安管資料。",
        )
        if not confirm:
            return

        try:
            self.status_var.set("正在選定場所並匯出場所紀錄表……")
            self.update_idletasks()
            self.automation.select_place(candidate)
            pdf_path = self.automation.export_place_record()
            self.status_var.set("場所紀錄表已匯出，正在解析……")
            self.update_idletasks()
            data = parse_place_record_pdf(pdf_path)
        except Exception as e:
            self.status_var.set("自動匯出未完成。")
            messagebox.showerror("自動匯出未完成", str(e))
            return

        default_path = default_output_path(pdf_path, data)
        out_path = filedialog.asksaveasfilename(
            title="儲存 Word",
            defaultextension=".docx",
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            filetypes=[("Word 文件", "*.docx")],
        )
        if not out_path:
            self.status_var.set("已匯出 PDF；未儲存 Word。")
            return
        try:
            out, updated = fill_word_template(data, Path(out_path))
        except Exception as e:
            self.status_var.set("Word 產生失敗。")
            messagebox.showerror("產生失敗", f"{e}\n\n{traceback.format_exc()}")
            return

        self.status_var.set(f"完成：{out.name}")
        messagebox.showinfo(
            "完成",
            "Word 已產生。\n\n"
            "安管可取得的資料已自動填入；事故後才知道的事實以紅色【待補：○○】保留完整句型。",
        )

    def choose_pdf(self):
        path = filedialog.askopenfilename(
            title="選擇場所紀錄表 PDF",
            filetypes=[("PDF 檔案", "*.pdf")],
        )
        if path:
            self.load_pdf(Path(path))

    def load_pdf(self, path: Path):
        try:
            self.status_var.set("讀取 PDF 中……")
            self.update_idletasks()
            data = parse_place_record_pdf(path)
        except Exception as e:
            self.data = None
            self.manual_generate_btn.configure(state="disabled")
            self.status_var.set("讀取失敗。")
            messagebox.showerror("讀取失敗", str(e))
            return

        self.pdf_path = path
        self.data = data
        self.path_var.set(str(path))
        for item in self.manual_tree.get_children():
            self.manual_tree.delete(item)
        for field, value in _short_status(data):
            self.manual_tree.insert("", "end", values=(field, value))
        self.manual_generate_btn.configure(state="normal")
        self.status_var.set("PDF 讀取完成，可產生 Word。")

    def generate_manual_word(self):
        if not self.pdf_path or not self.data:
            return
        default_path = default_output_path(self.pdf_path, self.data)
        path = filedialog.asksaveasfilename(
            title="儲存 Word",
            defaultextension=".docx",
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            filetypes=[("Word 文件", "*.docx")],
        )
        if not path:
            return
        try:
            out, _updated = fill_word_template(self.data, Path(path))
        except Exception as e:
            self.status_var.set("Word 產生失敗。")
            messagebox.showerror("產生失敗", f"{e}\n\n{traceback.format_exc()}")
            return
        self.status_var.set(f"完成：{out.name}")
        messagebox.showinfo(
            "完成",
            "Word 已產生。\n\n安管可取得的資料已填入；其餘欄位以紅色【待補：○○】保留完整句型。",
        )


def main():
    app = App()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists() and path.suffix.lower() == ".pdf":
            app.after(150, lambda: app.load_pdf(path))
    app.mainloop()


if __name__ == "__main__":
    main()
