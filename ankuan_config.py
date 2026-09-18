from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

CONFIG_FILENAME = "ankuan_config.json"
BASE_PROFILE_FILENAME = "ankuan_base_profile.json"

POINT_KEYS = (
    "place_name",
    "query_button",
    "condition_tab",
    "result_tab",
    "safety_tab",
    "place_record_button",
    "report_inspection_count",
    "report_submission_count",
    "report_confirm_button",
    "inspection_record_button",
    "preview_pdf_button",
    "preview_close_button",
    # Optional OCR result-grid corners. They are stored as ordinary points, so
    # their X/Y values are ratios relative to the AnKuan main window rather
    # than absolute screen pixels.
    "result_grid_top_left",
    "result_grid_bottom_right",
)

# Built into the EXE from the standard-machine calibration captured on
# 2026-09-18.  A local ankuan_config.json can still override any of these
# values on a specific computer.  This lets a fresh EXE start with a useful
# calibration even when no companion JSON file is present.
BUILTIN_STANDARD_POINTS = {
    "place_name": {"x": 0.175781, "y": 0.230469, "scope": "ankuan"},
    "query_button": {"x": 0.070312, "y": 0.115885, "scope": "ankuan"},
    "condition_tab": None,
    "result_tab": None,
    "safety_tab": {"x": 0.266602, "y": 0.21224, "scope": "ankuan"},
    "place_record_button": {"x": 0.071289, "y": 0.25651, "scope": "ankuan"},
    "report_inspection_count": {"x": 0.825195, "y": 0.369792, "scope": "ankuan"},
    "report_submission_count": {"x": 0.831055, "y": 0.407552, "scope": "ankuan"},
    "report_confirm_button": {"x": 0.90625, "y": 0.721354, "scope": "ankuan"},
    "inspection_record_button": {"x": 0.164062, "y": 0.253906, "scope": "ankuan"},
    "preview_pdf_button": {"x": 0.034607, "y": 0.046756, "scope": "preview"},
    "preview_close_button": {"x": 0.502583, "y": 0.657443, "scope": "preview"},
    # OCR will first try visible-header auto-location.  These remain optional
    # and may later be locally calibrated if the table cannot be found reliably.
    "result_grid_top_left": None,
    "result_grid_bottom_right": None,
}

DEFAULT_CONFIG = {
    "version": 4,
    "window_title_contains": "臺北市政府消防局安全管理系統",
    "timing": {
        "open_page": 0.8,
        "after_tab": 0.25,
        "after_clear": 0.2,
        "after_input": 0.5,
        "after_query": 1.2,
        "after_select": 0.4,
        "after_report_open": 0.8,
        "after_preview_open": 1.0,
        "after_preview_export": 0.5,
    },
    "validation": {
        "require_input_echo": True,
        "warn_nonstandard_environment": True,
    },
    "report": {
        "inspection_history_count": 5,
        "submission_history_count": 2,
    },
    "ocr": {
        "enabled": True,
        # Optional helper only. OCR can still group words into rows without it.
        "result_row_height_ratio": None,
        # Stable X position inside a result row. Y always comes from the OCR row
        # center; we deliberately never click an OCR word bounding-box center.
        "click_x_ratio": 0.35,
    },
    "points": deepcopy(BUILTIN_STANDARD_POINTS),
}

BASE_PROFILE_DEFAULT = {
    "version": 3,
    "name": "安管內建標準校正",
    "standard_environment": {
        "display_scale_percent": 100,
        "require_maximized": True,
        "window_width": None,
        "window_height": None,
    },
    "report": {
        "inspection_history_count": 5,
        "submission_history_count": 2,
    },
    "ocr": {
        "enabled": True,
        "result_row_height_ratio": None,
        "click_x_ratio": 0.35,
    },
    "points": deepcopy(BUILTIN_STANDARD_POINTS),
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    return app_dir() / CONFIG_FILENAME


def base_profile_path() -> Path:
    return app_dir() / BASE_PROFILE_FILENAME


def _merge(default: dict, override: dict) -> dict:
    result = deepcopy(default)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_base_profile() -> dict:
    path = base_profile_path()
    if not path.exists():
        return deepcopy(BASE_PROFILE_DEFAULT)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(BASE_PROFILE_DEFAULT)
    return _merge(BASE_PROFILE_DEFAULT, raw)


def base_profile_exists() -> bool:
    path = base_profile_path()
    if not path.exists():
        return False
    base = load_base_profile()
    return any(isinstance(p, dict) and "x" in p and "y" in p for p in base.get("points", {}).values())


def _seed_from_base() -> dict:
    # DEFAULT_CONFIG already contains the embedded standard calibration.
    # An optional external base profile can override it, followed by the local
    # per-machine config in load_config().
    cfg = deepcopy(DEFAULT_CONFIG)
    base = load_base_profile()
    cfg["report"] = _merge(cfg["report"], base.get("report", {}))
    cfg["ocr"] = _merge(cfg["ocr"], base.get("ocr", {}))
    for key in POINT_KEYS:
        p = base.get("points", {}).get(key)
        if isinstance(p, dict) and "x" in p and "y" in p:
            cfg["points"][key] = deepcopy(p)
    return cfg


def load_config() -> dict:
    seed = _seed_from_base()
    path = config_path()
    if not path.exists():
        # The EXE is usable without a companion JSON.  Persist the built-in
        # standard values when possible so future local calibration has a file
        # to edit, but do not make a read-only folder fatal.
        try:
            save_config(seed)
        except Exception:
            pass
        return seed
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return seed

    cfg = _merge(seed, raw)
    # Older configs contain explicit null points. Treat null as "no local
    # override" so the embedded/base profile can still supply them.
    raw_points = raw.get("points", {}) if isinstance(raw, dict) else {}
    base_points = seed.get("points", {})
    for key in POINT_KEYS:
        if raw_points.get(key) is None and isinstance(base_points.get(key), dict):
            cfg["points"][key] = deepcopy(base_points[key])

    raw_ocr = raw.get("ocr", {}) if isinstance(raw, dict) else {}
    base_ocr = seed.get("ocr", {})
    if raw_ocr.get("result_row_height_ratio") is None and base_ocr.get("result_row_height_ratio") is not None:
        cfg["ocr"]["result_row_height_ratio"] = base_ocr["result_row_height_ratio"]
    return cfg


def save_config(config: dict) -> Path:
    path = config_path()
    normalized = _merge(DEFAULT_CONFIG, config)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def reset_config() -> Path:
    """Restore local settings to the embedded/base standard profile."""
    return save_config(_seed_from_base())


def save_point(name: str, x_ratio: float, y_ratio: float, scope: str = "ankuan") -> Path:
    if name not in POINT_KEYS:
        raise KeyError(name)
    if not (0 <= x_ratio <= 1 and 0 <= y_ratio <= 1):
        raise ValueError("校正點必須位於目標視窗內。")
    if scope not in ("ankuan", "preview"):
        raise ValueError("未知校正範圍。")
    cfg = load_config()
    cfg["points"][name] = {
        "x": round(float(x_ratio), 6),
        "y": round(float(y_ratio), 6),
        "scope": scope,
    }
    return save_config(cfg)


def save_points(points: dict[str, dict]) -> Path:
    cfg = load_config()
    for name, p in points.items():
        if name not in POINT_KEYS or not isinstance(p, dict):
            continue
        x, y = p.get("x"), p.get("y")
        scope = p.get("scope", "ankuan")
        if x is None or y is None or scope not in ("ankuan", "preview"):
            continue
        if 0 <= float(x) <= 1 and 0 <= float(y) <= 1:
            cfg["points"][name] = {
                "x": round(float(x), 6),
                "y": round(float(y), 6),
                "scope": scope,
            }
    return save_config(cfg)


def clear_point(name: str) -> Path:
    if name not in POINT_KEYS:
        raise KeyError(name)
    cfg = load_config()
    cfg["points"][name] = None
    return save_config(cfg)


def set_report_counts(inspection_count: int, submission_count: int) -> Path:
    inspection_count = int(inspection_count)
    submission_count = int(submission_count)
    if not (1 <= inspection_count <= 99 and 1 <= submission_count <= 99):
        raise ValueError("檢查／申報次數請設定為 1 到 99。")
    cfg = load_config()
    cfg["report"]["inspection_history_count"] = inspection_count
    cfg["report"]["submission_history_count"] = submission_count
    return save_config(cfg)


def save_result_row_height_ratio(value: float | None) -> Path:
    cfg = load_config()
    if value is None:
        cfg["ocr"]["result_row_height_ratio"] = None
        return save_config(cfg)
    ratio = abs(float(value))
    if not (0.002 <= ratio <= 0.2):
        raise ValueError("列高校正值異常，請指定相鄰兩列的中心點。")
    cfg["ocr"]["result_row_height_ratio"] = round(ratio, 6)
    return save_config(cfg)


def save_current_as_base_profile(reference_environment: dict | None = None) -> Path:
    """Save this machine's current calibration as an optional external profile.

    The EXE already contains the standard calibration.  This external profile
    is only needed when administrators intentionally want another portable
    profile to override the embedded one.
    """
    cfg = load_config()
    base = deepcopy(BASE_PROFILE_DEFAULT)
    base["points"] = deepcopy(cfg.get("points", {}))
    base["report"] = deepcopy(cfg.get("report", base["report"]))
    base["ocr"] = deepcopy(cfg.get("ocr", base["ocr"]))
    if reference_environment:
        base["standard_environment"] = _merge(base["standard_environment"], reference_environment)
    path = base_profile_path()
    path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
