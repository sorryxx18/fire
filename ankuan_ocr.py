from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import statistics
import subprocess
import unicodedata
from dataclasses import dataclass
from typing import Iterable


class OcrUnavailable(RuntimeError):
    """Windows OCR is not available on the current computer."""


class OcrReadError(RuntimeError):
    """Windows OCR was available but the current visible region could not be read."""


@dataclass
class OcrWord:
    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float | None = None

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0


@dataclass
class VisibleOcrCandidate:
    row_index: int
    row_center_y: float
    row_text: str
    place_no: str = ""
    name: str = ""
    address: str = ""
    match_score: float = 0.0
    ocr_confidence: float | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.row_text


_POWERSHELL_OCR = r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class DpiAwareness {
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
}
"@
[DpiAwareness]::SetProcessDPIAware() | Out-Null

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime

[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null

$available = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages)
$preferred = @("zh-Hant-TW", "zh-TW", "zh-Hant")
$lang = $null
foreach ($tag in $preferred) {
    $lang = $available | Where-Object { $_.LanguageTag -eq $tag } | Select-Object -First 1
    if ($lang) { break }
}
if (-not $lang) {
    $langs = @($available | ForEach-Object { $_.LanguageTag })
    [pscustomobject]@{
        ok = $false
        unavailable = $true
        reason = "Windows 未安裝可用的繁體中文 OCR 語言。"
        languages = $langs
    } | ConvertTo-Json -Depth 5 -Compress
    exit 0
}

$mode = $env:ANKUAN_OCR_MODE
if ($mode -eq "status") {
    [pscustomobject]@{
        ok = $true
        language = $lang.LanguageTag
        words = @()
    } | ConvertTo-Json -Depth 5 -Compress
    exit 0
}

$left = [int]$env:ANKUAN_OCR_LEFT
$top = [int]$env:ANKUAN_OCR_TOP
$width = [int]$env:ANKUAN_OCR_WIDTH
$height = [int]$env:ANKUAN_OCR_HEIGHT
if ($width -lt 8 -or $height -lt 8) {
    throw "OCR 截圖範圍太小。"
}

$tempPath = Join-Path ([IO.Path]::GetTempPath()) ("ankuan_ocr_" + [guid]::NewGuid().ToString("N") + ".png")
$bmp = $null
$graphics = $null
$stream = $null
try {
    $bmp = New-Object System.Drawing.Bitmap($width, $height)
    $graphics = [System.Drawing.Graphics]::FromImage($bmp)
    $graphics.CopyFromScreen($left, $top, 0, 0, $bmp.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
    $bmp.Save($tempPath, [System.Drawing.Imaging.ImageFormat]::Png)

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
    } | Select-Object -First 1)
    if (-not $asTaskGeneric) { throw "找不到 WinRT AsTask 轉接方法。" }

    function Await-WinRt($operation, [Type]$resultType) {
        $method = $script:asTaskGeneric.MakeGenericMethod($resultType)
        $task = $method.Invoke($null, @($operation))
        $task.Wait()
        return $task.Result
    }

    $file = Await-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tempPath)) ([Windows.Storage.StorageFile])
    $stream = Await-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    $decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $softwareBitmap = Await-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
    if (-not $engine) { throw "Windows 無法建立繁體中文 OCR 引擎。" }
    $result = Await-WinRt ($engine.RecognizeAsync($softwareBitmap)) ([Windows.Media.Ocr.OcrResult])

    $words = @()
    foreach ($line in $result.Lines) {
        foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            $words += [pscustomobject]@{
                text = [string]$word.Text
                x = [double]$r.X
                y = [double]$r.Y
                width = [double]$r.Width
                height = [double]$r.Height
            }
        }
    }

    [pscustomobject]@{
        ok = $true
        language = $lang.LanguageTag
        text = [string]$result.Text
        words = $words
    } | ConvertTo-Json -Depth 7 -Compress
}
finally {
    if ($stream) { try { $stream.Dispose() } catch {} }
    if ($graphics) { $graphics.Dispose() }
    if ($bmp) { $bmp.Dispose() }
    if (Test-Path $tempPath) { Remove-Item $tempPath -Force -ErrorAction SilentlyContinue }
}
'''


def _powershell_path() -> str:
    path = shutil.which("powershell.exe") or shutil.which("powershell")
    if not path:
        raise OcrUnavailable("找不到 Windows PowerShell；已改用人工選取。")
    return path


def _run_windows_ocr(*, left: int = 0, top: int = 0, width: int = 0, height: int = 0, status_only: bool = False) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "ANKUAN_OCR_MODE": "status" if status_only else "recognize",
            "ANKUAN_OCR_LEFT": str(int(left)),
            "ANKUAN_OCR_TOP": str(int(top)),
            "ANKUAN_OCR_WIDTH": str(int(width)),
            "ANKUAN_OCR_HEIGHT": str(int(height)),
        }
    )
    try:
        proc = subprocess.run(
            [_powershell_path(), "-NoProfile", "-NonInteractive", "-Command", "-"],
            input=_POWERSHELL_OCR,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            env=env,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrReadError("Windows OCR 逾時；已改用人工選取。") from exc
    except OSError as exc:
        raise OcrUnavailable(f"Windows OCR 無法啟動：{exc}") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if not stdout:
        detail = stderr or f"PowerShell exit code {proc.returncode}"
        raise OcrReadError(f"Windows OCR 沒有回傳結果：{detail}")

    # PowerShell can emit harmless leading lines on some managed desktops.  The
    # final non-empty line is the JSON result produced by our script.
    payload_line = [line for line in stdout.splitlines() if line.strip()][-1]
    try:
        payload = json.loads(payload_line)
    except json.JSONDecodeError as exc:
        raise OcrReadError(f"Windows OCR 回傳格式無法解析：{payload_line[:200]}") from exc

    if not payload.get("ok"):
        reason = payload.get("reason") or "Windows OCR 不可用。"
        if payload.get("unavailable"):
            raise OcrUnavailable(reason)
        raise OcrReadError(reason)
    return payload


def windows_ocr_status() -> tuple[bool, str]:
    try:
        payload = _run_windows_ocr(status_only=True)
    except (OcrUnavailable, OcrReadError) as exc:
        return False, str(exc)
    return True, f"可用（{payload.get('language') or '繁體中文'}）"


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
    it = iter(haystack)
    return all(any(ch == h for h in it) for ch in needle)


def _partial_ratio(shorter: str, longer: str) -> float:
    if not shorter or not longer:
        return 0.0
    if len(shorter) > len(longer):
        shorter, longer = longer, shorter
    if shorter in longer:
        return 1.0
    best = 0.0
    min_len = max(1, len(shorter) - 1)
    max_len = min(len(longer), len(shorter) + 2)
    for window_len in range(min_len, max_len + 1):
        for start in range(0, len(longer) - window_len + 1):
            ratio = difflib.SequenceMatcher(None, shorter, longer[start : start + window_len]).ratio()
            if ratio > best:
                best = ratio
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


def _cluster_rows(words: Iterable[OcrWord], expected_row_height: float | None = None) -> list[list[OcrWord]]:
    items = [w for w in words if w.text.strip() and w.width > 0 and w.height > 0]
    if not items:
        return []
    heights = [w.height for w in items if w.height > 0]
    typical_height = statistics.median(heights) if heights else 16.0
    if expected_row_height and expected_row_height > 2:
        tolerance = max(5.0, expected_row_height * 0.34)
    else:
        tolerance = max(5.0, typical_height * 0.72)

    groups: list[list[OcrWord]] = []
    centers: list[float] = []
    for word in sorted(items, key=lambda w: (w.center_y, w.x)):
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
            centers[best_idx] = sum(w.center_y for w in groups[best_idx]) / len(groups[best_idx])

    ordered = sorted(zip(centers, groups), key=lambda pair: pair[0])
    return [sorted(group, key=lambda w: w.x) for _center, group in ordered]


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
    tokens = [re.sub(r"\s+", " ", w.text).strip() for w in row if w.text.strip()]
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
    address_idx = None
    for idx, token in enumerate(tokens):
        if _looks_address(token):
            address = token
            address_idx = idx
            break
    if address_idx is None:
        # Addresses are often split into several OCR words.  Search suffixes.
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
        name = max(alternatives, key=lambda x: len(_normalize(x)), default="")
    return place_no, name, address, row_text


def read_visible_candidates(
    query: str,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    expected_row_height: float | None = None,
    minimum_score: float = 0.48,
    max_candidates: int = 12,
) -> tuple[list[VisibleOcrCandidate], str]:
    payload = _run_windows_ocr(left=left, top=top, width=width, height=height)
    words = [
        OcrWord(
            text=str(item.get("text") or ""),
            x=float(item.get("x") or 0),
            y=float(item.get("y") or 0),
            width=float(item.get("width") or 0),
            height=float(item.get("height") or 0),
            confidence=None,  # Windows.Media.Ocr does not expose per-word confidence.
        )
        for item in payload.get("words", [])
        if str(item.get("text") or "").strip()
    ]
    rows = _cluster_rows(words, expected_row_height=expected_row_height)

    candidates: list[VisibleOcrCandidate] = []
    for idx, row in enumerate(rows):
        place_no, name, address, row_text = _parse_row(row)
        score = max(match_score(query, row_text), match_score(query, name))
        if place_no:
            score = min(1.0, score + 0.02)
        if address:
            score = min(1.0, score + 0.02)
        if score < minimum_score:
            continue
        center_y = sum(w.center_y for w in row) / len(row)
        candidates.append(
            VisibleOcrCandidate(
                row_index=idx,
                row_center_y=center_y,
                row_text=row_text,
                place_no=place_no,
                name=name,
                address=address,
                match_score=score,
                ocr_confidence=None,
            )
        )

    candidates.sort(key=lambda c: (-c.match_score, c.row_center_y))
    return candidates[:max_candidates], str(payload.get("language") or "zh-Hant")
