from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

LAYOUT_MAX_TOKENS = 4096
LAYOUT_TEMPLATE_TOKEN_OVERHEAD = 10
LAYOUT_TOKEN_SAFETY = 1.02
LAYOUT_MAX_JSON_CHARS = 32768
LAYOUT_MAX_ITEMS = 64
SUMMARY_CAPTION_MAX = 3900
ITEM_CAPTION_MAX = 3900
TEXT_ITEM_MAX = 3900
META_MAX_ITEMS = 8
META_ITEM_MAX = 40
CANVAS_VALUES = {"single", "multi_panel", "collage"}
LABELS = {"object", "character", "text"}
_ASCII_LETTER_RUNS = re.compile(r"[A-Za-z]+")
_ASCII_DIGIT_RUNS = re.compile(r"\d+")
_TOP_KEYS = {"summary", "items", "bbox_coord_space", "bbox_format"}
_SUMMARY_KEYS = {"caption", "meta", "canvas", "palette"}
_ITEM_KEYS = {"label", "caption", "bbox", "text", "tags", "palette"}
_TEXT_LIMIT_REASON = f"受文本编码器 {LAYOUT_MAX_TOKENS} token 上限约束"


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class LayoutResult:
    data: dict[str, Any]
    json_text: str
    caption: str

    @property
    def char_count(self) -> int:
        return len(self.json_text)


def _clean_text(
    value: Any,
    *,
    field: str,
    max_length: int,
    required: bool = True,
    limit_reason: str | None = None,
) -> str | None:
    if value is None:
        if required:
            raise LayoutError(f"{field} 不能为空")
        return None
    if not isinstance(value, str):
        raise LayoutError(f"{field} 必须是字符串")
    text = value.strip()
    if not text:
        if required:
            raise LayoutError(f"{field} 不能为空")
        return None
    if len(text) > max_length:
        reason = f"（{limit_reason}）" if limit_reason else ""
        raise LayoutError(f"{field} 不能超过 {max_length} 个字符{reason}")
    for char in text:
        category = unicodedata.category(char)
        if category == "Cc" and char not in "\r\n\t":
            raise LayoutError(f"{field} 包含不支持的控制字符")
    return text


def _string_list(
    value: Any,
    *,
    field: str,
    max_items: int,
    max_length: int,
) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise LayoutError(f"{field} 必须是字符串数组")
    if len(value) > max_items:
        raise LayoutError(f"{field} 最多 {max_items} 项")
    items: list[str] = []
    for index, item in enumerate(value):
        text = _clean_text(
            item,
            field=f"{field}[{index}]",
            max_length=max_length,
            required=True,
        )
        assert text is not None
        items.append(text)
    return items


def _dense_run_extra(
    runs: list[str],
    *,
    base_divisor: float,
    dense_divisor: float,
    threshold: int,
) -> int:
    extra = 0
    for run in runs:
        length = len(run)
        if length > threshold:
            extra += math.ceil(length / dense_divisor) - math.ceil(
                length / base_divisor
            )
    return extra


def _is_cjk(code: int) -> bool:
    return (
        0x3000 <= code <= 0x303F
        or 0x3040 <= code <= 0x30FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF
        or 0x20000 <= code <= 0x2FA1F
    )


def _is_emoji_like(code: int) -> bool:
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0xFE00 <= code <= 0xFE0F
        or code == 0x200D
    )


def _is_math_symbol(code: int) -> bool:
    return (
        0x2190 <= code <= 0x21FF
        or 0x2200 <= code <= 0x22FF
        or 0x2A00 <= code <= 0x2AFF
        or 0x1D400 <= code <= 0x1D7FF
    )


def estimate_layout_tokens(text: str) -> int:
    ascii_letters = 0
    ascii_digits = 0
    ascii_punctuation = 0
    whitespace = 0
    cjk = 0
    emoji = 0
    math_symbols = 0
    other_non_ascii = 0
    for char in text:
        code = ord(char)
        if code < 128:
            if char.isalpha():
                ascii_letters += 1
            elif char.isdigit():
                ascii_digits += 1
            elif char.isspace():
                whitespace += 1
            else:
                ascii_punctuation += 1
        elif _is_cjk(code):
            cjk += 1
        elif _is_emoji_like(code):
            emoji += 1
        elif _is_math_symbol(code):
            math_symbols += 1
        else:
            other_non_ascii += 1
    letter_runs = _ASCII_LETTER_RUNS.findall(text)
    digit_runs = _ASCII_DIGIT_RUNS.findall(text)
    tokens = LAYOUT_TEMPLATE_TOKEN_OVERHEAD
    tokens += math.ceil(ascii_letters / 4.5)
    tokens += math.ceil(ascii_digits / 2)
    tokens += ascii_punctuation
    tokens += math.ceil(whitespace / 8)
    tokens += cjk
    tokens += emoji * 3
    tokens += math_symbols * 2
    tokens += math.ceil(other_non_ascii * 1.2)
    tokens += _dense_run_extra(
        letter_runs,
        base_divisor=4.5,
        dense_divisor=1.8,
        threshold=32,
    )
    tokens += _dense_run_extra(
        digit_runs,
        base_divisor=2,
        dense_divisor=1,
        threshold=16,
    )
    return math.ceil(tokens * LAYOUT_TOKEN_SAFETY)


def validate_layout_text_size(text: str) -> int:
    if len(text) > LAYOUT_MAX_JSON_CHARS:
        raise LayoutError(
            f"layout JSON 不能超过 {LAYOUT_MAX_JSON_CHARS} 个字符"
            f"（输入长度防御上限，文本编码器单次最多 {LAYOUT_MAX_TOKENS} token），"
            f"当前 {len(text)}"
        )
    estimated = estimate_layout_tokens(text)
    if estimated > LAYOUT_MAX_TOKENS:
        raise LayoutError(
            "layout 内容超出文本编码器上限，请精简构图"
            f"（估算 {estimated} tokens，官方 MOSS 节点上限 {LAYOUT_MAX_TOKENS} token；"
            "可缩短 summary.caption、items[].caption/text、"
            "tags/palette 或减少 items）"
        )
    return estimated


def _check_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise LayoutError(f"{field} 包含不支持的字段: {', '.join(unknown)}")


def _normalize_bbox(value: Any, width: int, height: int, field: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise LayoutError(f"{field} 必须是 [y_min, x_min, y_max, x_max] 四个整数")
    coords: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise LayoutError(f"{field} 的坐标必须是整数")
        coords.append(item)
    y_min, x_min, y_max, x_max = coords
    if x_min >= x_max or y_min >= y_max:
        raise LayoutError(f"{field} 必须满足 y_min < y_max 且 x_min < x_max")
    half_width = width / 2
    half_height = height / 2
    if not (-half_width <= x_min and x_max <= half_width):
        raise LayoutError(
            f"{field} 的 x 坐标超出画布范围: {int(-half_width)} ~ {int(half_width)}"
        )
    if not (-half_height <= y_min and y_max <= half_height):
        raise LayoutError(
            f"{field} 的 y 坐标超出画布范围: {int(-half_height)} ~ {int(half_height)}"
        )
    return coords


def normalize_layout(
    raw: Any,
    *,
    width: int,
    height: int,
) -> LayoutResult:
    if not isinstance(raw, dict):
        raise LayoutError("layout 必须是 JSON 对象")
    _check_keys(raw, _TOP_KEYS, "layout")

    bbox_coord_space = raw.get("bbox_coord_space")
    if bbox_coord_space != "abs_centralized":
        raise LayoutError('bbox_coord_space 必须是 "abs_centralized"')
    bbox_format = raw.get("bbox_format")
    if bbox_format != "yxyx":
        raise LayoutError('bbox_format 必须是 "yxyx"')

    summary = raw.get("summary")
    if not isinstance(summary, dict):
        raise LayoutError("summary 必须是对象")
    _check_keys(summary, _SUMMARY_KEYS, "summary")
    caption = _clean_text(
        summary.get("caption"),
        field="summary.caption",
        max_length=SUMMARY_CAPTION_MAX,
        limit_reason=_TEXT_LIMIT_REASON,
    )
    assert caption is not None
    normalized_summary: dict[str, Any] = {"caption": caption}
    meta = _string_list(
        summary.get("meta"),
        field="summary.meta",
        max_items=META_MAX_ITEMS,
        max_length=META_ITEM_MAX,
    )
    if meta:
        normalized_summary["meta"] = meta
    canvas = summary.get("canvas")
    if canvas is not None:
        if not isinstance(canvas, str) or canvas not in CANVAS_VALUES:
            raise LayoutError(
                'summary.canvas 只能是 "single"、"multi_panel" 或 "collage"'
            )
        normalized_summary["canvas"] = canvas
    summary_palette = _string_list(
        summary.get("palette"),
        field="summary.palette",
        max_items=META_MAX_ITEMS,
        max_length=META_ITEM_MAX,
    )
    if summary_palette:
        normalized_summary["palette"] = summary_palette

    items = raw.get("items")
    if not isinstance(items, list) or not items:
        raise LayoutError("items 必须是非空数组")
    if len(items) > LAYOUT_MAX_ITEMS:
        raise LayoutError(
            f"items 最多 {LAYOUT_MAX_ITEMS} 项"
            "（网关防御上限，官方节点不限数量；实际受 4096 token 预算约束）"
        )

    normalized_items: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        field = f"items[{index}]"
        if not isinstance(item, dict):
            raise LayoutError(f"{field} 必须是对象")
        _check_keys(item, _ITEM_KEYS, field)
        label = item.get("label")
        if not isinstance(label, str) or label not in LABELS:
            raise LayoutError(
                f'{field}.label 只能是 "object"、"character" 或 "text"'
            )
        item_caption = _clean_text(
            item.get("caption"),
            field=f"{field}.caption",
            max_length=ITEM_CAPTION_MAX,
            limit_reason=_TEXT_LIMIT_REASON,
        )
        assert item_caption is not None
        bbox = _normalize_bbox(
            item.get("bbox"),
            width,
            height,
            f"{field}.bbox",
        )
        normalized_item: dict[str, Any] = {
            "label": label,
            "caption": item_caption,
            "bbox": bbox,
        }
        if label == "text":
            text = _clean_text(
                item.get("text"),
                field=f"{field}.text",
                max_length=TEXT_ITEM_MAX,
                limit_reason=_TEXT_LIMIT_REASON,
            )
            assert text is not None
            normalized_item["text"] = text
        elif "text" in item:
            raise LayoutError(f'{field}.text 只允许用于 label="text" 的元素')
        tags = _string_list(
            item.get("tags"),
            field=f"{field}.tags",
            max_items=META_MAX_ITEMS,
            max_length=META_ITEM_MAX,
        )
        if tags:
            normalized_item["tags"] = tags
        palette = _string_list(
            item.get("palette"),
            field=f"{field}.palette",
            max_items=META_MAX_ITEMS,
            max_length=META_ITEM_MAX,
        )
        if palette:
            normalized_item["palette"] = palette
        normalized_items.append(normalized_item)

    normalized: dict[str, Any] = {
        "summary": normalized_summary,
        "items": normalized_items,
        "bbox_coord_space": "abs_centralized",
        "bbox_format": "yxyx",
    }
    json_text = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    validate_layout_text_size(json_text)
    return LayoutResult(
        data=normalized,
        json_text=json_text,
        caption=caption,
    )
