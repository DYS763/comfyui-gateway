from __future__ import annotations

import json
from typing import Any

from .database import Database
from .workflow import SIZE_DEFAULT, SIZE_MAX, STEPS_DEFAULT, STEPS_MAX, STEPS_MIN

DEFAULT_PROFILE = "default"


def default_capabilities(db: Database) -> dict[str, Any]:
    loras = [
        {"id": int(entry["id"]), "display_name": entry["display_name"]}
        for entry in db.list_lora_entries(enabled_only=True)
    ]
    return {
        "profiles": [DEFAULT_PROFILE],
        "layout": True,
        "max_width": SIZE_MAX,
        "max_height": SIZE_MAX,
        "min_steps": STEPS_MIN,
        "max_steps": STEPS_MAX,
        "loras": loras,
        "negative_prompt": True,
        "features": [
            "profile",
            "layout",
            "size",
            "steps",
            "lora",
            "negative_prompt",
        ],
    }


def parse_capabilities(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def merge_capabilities(
    base: dict[str, Any], override: dict[str, Any] | None
) -> dict[str, Any]:
    merged = dict(base)
    if not override:
        return merged
    for key in ("profiles", "layout", "max_width", "max_height", "min_steps", "max_steps", "loras", "negative_prompt", "features"):
        if key not in override:
            continue
        value = override[key]
        if key in {"profiles", "features"}:
            if isinstance(value, list):
                merged[key] = [str(item) for item in value if str(item).strip()]
        elif key == "loras":
            if isinstance(value, list):
                merged[key] = [
                    {
                        "id": item.get("id"),
                        "display_name": str(item.get("display_name") or "").strip(),
                    }
                    for item in value
                    if isinstance(item, dict) and str(item.get("display_name") or "").strip()
                ]
        elif key in {"max_width", "max_height", "min_steps", "max_steps"}:
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                merged[key] = value
        elif isinstance(value, bool):
            merged[key] = value
    return merged


def endpoint_capabilities(
    endpoint: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    if endpoint.get("kind") == "gateway":
        remote = parse_capabilities(endpoint.get("remote_capabilities_json"))
        merged = merge_capabilities(defaults, remote)
        merged["remote"] = True
        return merged
    local = parse_capabilities(endpoint.get("capabilities_json"))
    merged = merge_capabilities(defaults, local)
    merged["remote"] = False
    return merged


def task_requirement(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": str(task.get("profile") or DEFAULT_PROFILE),
        "layout": bool(task.get("layout_json")),
        "width": int(task.get("width") or SIZE_DEFAULT),
        "height": int(task.get("height") or SIZE_DEFAULT),
        "steps": int(task.get("steps") or STEPS_DEFAULT),
        "lora_name": (
            None
            if task.get("lora_disabled")
            else (str(task.get("lora_name_snapshot") or "").strip() or None)
        ),
        "negative_prompt": bool(str(task.get("negative_prompt") or "").strip()),
    }


def requirement_from_values(
    *,
    profile: str | None,
    layout: bool,
    width: int | None,
    height: int | None,
    steps: int | None,
    lora_name: str | None,
    negative_prompt: str | None,
) -> dict[str, Any]:
    return {
        "profile": str(profile or DEFAULT_PROFILE),
        "layout": bool(layout),
        "width": int(width or SIZE_DEFAULT),
        "height": int(height or SIZE_DEFAULT),
        "steps": int(steps or STEPS_DEFAULT),
        "lora_name": (str(lora_name or "").strip() or None),
        "negative_prompt": bool(str(negative_prompt or "").strip()),
    }


def match_capabilities(
    caps: dict[str, Any], requirement: dict[str, Any]
) -> str | None:
    profiles = caps.get("profiles") or [DEFAULT_PROFILE]
    if requirement["profile"] not in profiles:
        return f"节点不支持参数配置 {requirement['profile']}"
    if requirement["layout"] and not caps.get("layout", True):
        return "节点不支持 layout 专家模式"
    if requirement["width"] > int(caps.get("max_width") or SIZE_DEFAULT):
        return f"节点最大宽度为 {caps.get('max_width')}"
    if requirement["height"] > int(caps.get("max_height") or SIZE_DEFAULT):
        return f"节点最大高度为 {caps.get('max_height')}"
    steps = requirement["steps"]
    if steps < int(caps.get("min_steps") or STEPS_MIN):
        return f"节点最低步数为 {caps.get('min_steps')}"
    if steps > int(caps.get("max_steps") or STEPS_MAX):
        return f"节点最高步数为 {caps.get('max_steps')}"
    lora_name = requirement.get("lora_name")
    if lora_name:
        names = {
            str(item.get("display_name") or "")
            for item in caps.get("loras") or []
            if isinstance(item, dict)
        }
        if lora_name not in names:
            return f"节点缺少 LoRA {lora_name}"
    if requirement.get("negative_prompt") and not caps.get("negative_prompt", True):
        return "节点不支持负面提示词"
    return None
