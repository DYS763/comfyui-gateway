
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required: pip install Pillow")

TEXT_KEYS = ("prompt", "workflow", "parameters", "Comment", "Description")


def _as_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return None


def read_chunks(path: Path) -> tuple[dict[str, str], tuple[int, int]]:
    with Image.open(path) as image:
        size = image.size
        chunks: dict[str, str] = {}
        for key in TEXT_KEYS:
            text = _as_text(image.info.get(key))
            if text:
                chunks[key] = text
        for key, value in getattr(image, "text", {}).items():
            text = _as_text(value)
            if text and key not in chunks and key in TEXT_KEYS:
                chunks[key] = text
    return chunks, size


def _link_target(value: Any) -> str | None:
    if isinstance(value, list) and value and isinstance(value[0], (str, int)):
        return str(value[0])
    return None


def _text_from_api_node(node: dict[str, Any]) -> str | None:
    inputs = node.get("inputs") or {}
    pieces = []
    for key in ("text", "text_g", "text_l", "positive", "negative", "string", "value"):
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            pieces.append(value)
    return "\n".join(pieces) if pieces else None


def _resolve_text(
    graph: dict[str, Any], node_id: str | None, seen: set[str] | None = None
) -> str | None:
    if not node_id:
        return None
    seen = seen or set()
    if node_id in seen:
        return None
    seen.add(node_id)

    node = graph.get(node_id)
    if not isinstance(node, dict):
        return None

    text = _text_from_api_node(node)
    if text:
        return text

    inputs = node.get("inputs") or {}
    for value in inputs.values():
        target = _link_target(value)
        if target:
            found = _resolve_text(graph, target, seen)
            if found:
                return found
    return None


def _collect_models(graph: dict[str, Any]) -> dict[str, Any]:
    models: dict[str, Any] = {}
    loras: list[dict[str, Any]] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "")
        inputs = node.get("inputs") or {}
        if "CheckpointLoader" in cls and inputs.get("ckpt_name"):
            models["checkpoint"] = inputs["ckpt_name"]
        elif "UNETLoader" in cls and inputs.get("unet_name"):
            models.setdefault("unet", inputs["unet_name"])
        elif "CLIPLoader" in cls and (inputs.get("clip_name") or inputs.get("clip_name1")):
            models.setdefault("clip", inputs.get("clip_name") or inputs.get("clip_name1"))
        elif "VAELoader" in cls and inputs.get("vae_name"):
            models.setdefault("vae", inputs["vae_name"])
        elif "LoraLoader" in cls and inputs.get("lora_name"):
            loras.append(
                {
                    "name": inputs["lora_name"],
                    "strength_model": inputs.get("strength_model"),
                    "strength_clip": inputs.get("strength_clip"),
                }
            )
    if loras:
        models["loras"] = loras
    return models


def _collect_samplers(graph: dict[str, Any]) -> list[dict[str, Any]]:
    samplers: list[dict[str, Any]] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "")
        if "KSampler" not in cls and "SamplerCustom" not in cls:
            continue
        inputs = node.get("inputs") or {}
        entry = {
            "node_id": node_id,
            "class_type": cls,
            "seed": inputs.get("seed", inputs.get("noise_seed")),
            "steps": inputs.get("steps"),
            "cfg": inputs.get("cfg"),
            "sampler_name": inputs.get("sampler_name"),
            "scheduler": inputs.get("scheduler"),
            "denoise": inputs.get("denoise"),
        }
        entry["positive"] = _resolve_text(graph, _link_target(inputs.get("positive")))
        entry["negative"] = _resolve_text(graph, _link_target(inputs.get("negative")))
        samplers.append({k: v for k, v in entry.items() if v is not None})
    return samplers


def _collect_size(graph: dict[str, Any]) -> tuple[int | None, int | None]:
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        width, height = inputs.get("width"), inputs.get("height")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
    return None, None


def _normalize_graph(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and any(
                isinstance(value, dict) and "class_type" in value for value in item.values()
            ):
                return item
    return {}


def parse_comfy_prompt(raw: str) -> dict[str, Any]:
    graph = _normalize_graph(json.loads(raw))
    if not graph:
        return {"error": "prompt chunk is not a node graph"}

    samplers = _collect_samplers(graph)
    all_texts = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "")
        if "CLIPTextEncode" in cls or "TextEncode" in cls:
            text = _text_from_api_node(node)
            if text:
                all_texts.append({"node_id": node_id, "class_type": cls, "text": text})

    positive = next((s.get("positive") for s in samplers if s.get("positive")), None)
    negative = next((s.get("negative") for s in samplers if s.get("negative")), None)
    width, height = _collect_size(graph)

    return {
        "positive": positive,
        "negative": negative,
        "samplers": samplers,
        "models": _collect_models(graph),
        "size": {"width": width, "height": height} if width and height else None,
        "text_nodes": all_texts,
        "node_count": len(graph),
    }


def parse_comfy_workflow(raw: str) -> dict[str, Any]:
    workflow = json.loads(raw)
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    if not isinstance(nodes, list):
        return {"error": "workflow chunk has no nodes array"}

    texts, samplers, models = [], [], []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        cls = str(node.get("type") or "")
        title = node.get("title")
        widgets = node.get("widgets_values")
        if isinstance(widgets, dict):
            widgets = list(widgets.values())

        if "CLIPTextEncode" in cls and widgets:
            texts.append({"node_id": node.get("id"), "title": title, "text": widgets[0]})
        if "KSampler" in cls and isinstance(widgets, list) and len(widgets) >= 7:
            samplers.append(
                {
                    "node_id": node.get("id"),
                    "seed": widgets[0],
                    "steps": widgets[2],
                    "cfg": widgets[3],
                    "sampler_name": widgets[4],
                    "scheduler": widgets[5],
                    "denoise": widgets[6],
                }
            )
        if "Loader" in cls and isinstance(widgets, list) and widgets:
            models.append({"node_id": node.get("id"), "class_type": cls, "name": widgets[0]})

    return {
        "text_nodes": texts,
        "samplers": samplers,
        "model_loaders": models,
        "node_count": len(nodes),
    }


def extract(path: Path) -> dict[str, Any]:
    chunks, size = read_chunks(path)
    result: dict[str, Any] = {
        "file": str(path),
        "size": {"width": size[0], "height": size[1]},
        "chunks": sorted(chunks),
        "format": None,
        "prompt": None,
        "workflow": None,
        "a1111_parameters": None,
        "notes": [],
    }

    raw_prompt = chunks.get("prompt")
    if raw_prompt:
        result["format"] = "comfyui"
        try:
            result["prompt"] = parse_comfy_prompt(raw_prompt)
        except json.JSONDecodeError as exc:
            result["notes"].append(f"prompt chunk is not valid JSON: {exc}")

    raw_workflow = chunks.get("workflow")
    if raw_workflow:
        result["format"] = result["format"] or "comfyui"
        try:
            result["workflow"] = parse_comfy_workflow(raw_workflow)
        except json.JSONDecodeError as exc:
            result["notes"].append(f"workflow chunk is not valid JSON: {exc}")

    if chunks.get("parameters"):
        result["format"] = result["format"] or "a1111"
        result["a1111_parameters"] = chunks["parameters"]

    if not result["format"]:
        result["notes"].append(
            "no prompt/workflow/parameters chunk found - metadata was stripped "
            "or never embedded (JPEG/WebP conversions, screenshots, chat uploads)"
        )
    return result


def format_human(data: dict[str, Any]) -> str:
    lines = [f"File : {data['file']}", f"Size : {data['size']['width']}x{data['size']['height']}"]
    lines.append(f"Kind : {data['format'] or 'unknown'}")
    lines.append(f"Chunks: {', '.join(data['chunks']) or '(none)'}")

    prompt = data.get("prompt") or {}
    if prompt:
        if prompt.get("positive"):
            lines.append("\n[Positive prompt]\n" + prompt["positive"])
        if prompt.get("negative"):
            lines.append("\n[Negative prompt]\n" + prompt["negative"])
        for index, sampler in enumerate(prompt.get("samplers") or [], 1):
            settings = {k: v for k, v in sampler.items() if k not in ("positive", "negative")}
            lines.append(f"\n[Sampler {index}] " + json.dumps(settings, ensure_ascii=False))
        models = prompt.get("models") or {}
        if models:
            lines.append("\n[Models] " + json.dumps(models, ensure_ascii=False))
        if prompt.get("size"):
            lines.append("[Latent] " + json.dumps(prompt["size"], ensure_ascii=False))

    workflow = data.get("workflow") or {}
    if workflow and workflow.get("text_nodes"):
        lines.append("\n[Workflow text nodes]")
        for item in workflow["text_nodes"]:
            lines.append(f"  #{item.get('node_id')} {item.get('title') or ''}: {item.get('text')}")

    if data.get("a1111_parameters"):
        lines.append("\n[AUTOMATIC1111 parameters]\n" + data["a1111_parameters"])

    for note in data.get("notes") or []:
        lines.append(f"\nNote: {note}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--raw",
        choices=["prompt", "workflow", "parameters"],
        help="print the raw embedded chunk instead of the summary",
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    images: list[Path] = []
    for pattern in args.images:
        text = str(pattern)
        if any(char in text for char in "*?["):
            images.extend(Path(match) for match in glob.glob(text, recursive=True))
        else:
            images.append(pattern)

    exit_code = 0
    for path in images:
        if not path.is_file():
            print(f"skip: {path} (not a file)", file=sys.stderr)
            exit_code = 2
            continue
        try:
            if args.raw:
                chunks, _ = read_chunks(path)
                print(chunks.get(args.raw, f"<no {args.raw} chunk>"))
                continue
            data = extract(path)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                if len(images) > 1:
                    print("=" * 70)
                print(format_human(data))
        except Exception as exc:  # noqa: BLE001 - CLI should keep going
            print(f"error: {path}: {exc}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
