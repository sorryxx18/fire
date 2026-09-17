from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

CONFIG_FILENAME = "ankuan_config.json"

DEFAULT_CONFIG = {
    "version": 1,
    "window_title_contains": "臺北市政府消防局安全管理系統",
    "timing": {
        "open_page": 0.8,
        "after_tab": 0.25,
        "after_clear": 0.2,
        "after_input": 0.5,
        "after_query": 1.2,
        "after_select": 0.4,
        "after_report_open": 0.8,
    },
    "validation": {
        "require_input_echo": True,
        "require_query_match": True,
        "max_fuzzy_results": 200,
    },
    "points": {
        "place_name": None,
        "place_no": None,
        "query_button": None,
        "condition_tab": None,
        "result_tab": None,
        "csv_button": None,
        "safety_tab": None,
        "place_record_button": None,
        "report_confirm_button": None,
    },
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    return app_dir() / CONFIG_FILENAME


def _merge(default: dict, override: dict) -> dict:
    result = deepcopy(default)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        save_config(DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Keep the broken file untouched and run with safe defaults.
        return deepcopy(DEFAULT_CONFIG)
    return _merge(DEFAULT_CONFIG, raw)


def save_config(config: dict) -> Path:
    path = config_path()
    path.write_text(
        json.dumps(_merge(DEFAULT_CONFIG, config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def reset_config() -> Path:
    return save_config(deepcopy(DEFAULT_CONFIG))


def save_point(name: str, x_ratio: float, y_ratio: float) -> Path:
    if name not in DEFAULT_CONFIG["points"]:
        raise KeyError(name)
    if not (0 <= x_ratio <= 1 and 0 <= y_ratio <= 1):
        raise ValueError("校正點必須位於安管視窗內。")
    cfg = load_config()
    cfg["points"][name] = {
        "x": round(float(x_ratio), 6),
        "y": round(float(y_ratio), 6),
    }
    return save_config(cfg)


def clear_point(name: str) -> Path:
    if name not in DEFAULT_CONFIG["points"]:
        raise KeyError(name)
    cfg = load_config()
    cfg["points"][name] = None
    return save_config(cfg)
