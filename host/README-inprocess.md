# 安管自動化：In-Process PowerShell Host（STEP 1～21）

本分支只做第一階段驗證：把已實機逐步驗證的 PowerShell STEP 1～21 放進真正的 C#/.NET Windows EXE，並在同一個 STA PowerShell Runspace 中執行。

設計原則：
- 不使用任何持久化 X/Y 校正點。
- 不使用 `ankuan_base_profile.json` 或預先校正流程。
- 不把腳本解到 `TFDxxxx.ps1` 後再啟動外部 `powershell.exe`。
- 保留原 PowerShell 內既有的 UIA、Win32、FastReport 動態辨識邏輯；例如執行當下的 `TB_GETITEMRECT` 不屬於校正點。
- 本階段只驗證 EXE 是否可重新走完 STEP 1～21；PDF 解析與 Word 產製等下一階段再接入。

GitHub Actions 會建立 Windows x64、self-contained、single-file 的 `安管自動化.exe`，並先執行 `--selftest` 檢查內嵌腳本是否包含 STEP 1、STEP 21、`TcxCustomInnerTextEdit`、`TB_GETITEMRECT`，且不含校正設定標記。
