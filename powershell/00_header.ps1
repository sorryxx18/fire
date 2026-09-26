# ============================================================
# 安管自動化 一鍵版
#
# 流程：場所建檔 → 查詢 → 人工選擇場所（唯一人工步驟）
#       → 場所紀錄表 PDF → 檢查紀錄表 PDF → 回主畫面
#       → 解析兩份 PDF 素材 → 固定格式 Word → 完成
#
# 使用方式（在 PowerShell 視窗）：
#   .\安管自動化_一鍵版.ps1
#   .\安管自動化_一鍵版.ps1 -Keyword 大巨蛋
#   .\安管自動化_一鍵版.ps1 -Keyword 大巨蛋 -RecentCheck 5 -RecentReport 2
#
# 沒給 -Keyword 會在開頭詢問。
# 任何一步失敗，整個流程立刻停止，不會繼續往下按。
# 各 STEP 內容沿用原本逐步測試成功的版本；
# 原 STEP 17（結構探測，只讀不操作）不在一鍵流程內。
# ============================================================

param(
    [string]$Keyword,
    [int]$RecentCheck = 5,
    [int]$RecentReport = 2
)

if ([string]::IsNullOrWhiteSpace($Keyword)) {
    $Keyword = Read-Host "請輸入場所名稱關鍵字"
}

if ([string]::IsNullOrWhiteSpace($Keyword)) {
    Write-Host "沒有輸入場所關鍵字，結束。" -ForegroundColor Red
    exit 1
}

if (-not (Get-Process TFDSafeCln -ErrorAction SilentlyContinue)) {
    Write-Host "找不到 TFDSafeCln（安管系統沒有開啟），結束。" -ForegroundColor Red
    exit 1
}

# 各 STEP 之間共用的資料
$TFD = @{
    Keyword      = $Keyword.Trim()
    RecentCheck  = [string]$RecentCheck
    RecentReport = [string]$RecentReport
    Choice       = $null
    PlaceNo      = $null
    PlaceName    = $null
    PlacePdf     = $null
    CheckPdf       = $null
    PlacePdfText   = $null
    CheckPdfText   = $null
    PlacePdfMethod = $null
    CheckPdfMethod = $null
    Parsed         = $null
    FinalDocx      = $null
    RetryChoice    = $false
}

$Steps = [ordered]@{}

# ============================================================
