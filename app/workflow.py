from __future__ import annotations

import copy
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STEPS_MIN = 3
STEPS_MAX = 70
STEPS_DEFAULT = 50
SEED_MAX = 2**53 - 1
SIZE_MIN = 16
SIZE_MAX = 1920
SIZE_DEFAULT = 1024
SIZE_STEP = 16


class WorkflowError(Exception):
    pass


def normalize_dimension(value: int | None) -> int:
    if value is None:
        return SIZE_DEFAULT
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowError("width and height must be integers")
    clamped = max(SIZE_MIN, min(SIZE_MAX, value))
    snapped = ((clamped + SIZE_STEP // 2) // SIZE_STEP) * SIZE_STEP
    return max(SIZE_MIN, min(SIZE_MAX, snapped))


@dataclass(frozen=True)
class BuiltWorkflow:
    payload: dict[str, Any]
    negative_prompt: str
    steps: int
    seed: int
    width: int
    height: int
    lora_filename: str | None = None
    lora_strength: float | None = None


class WorkflowTemplate:
    def __init__(
        self,
        path: Path,
        prompt_node_id: str,
        negative_prompt_node_id: str = "3",
    ):
        self.path = path
        self.prompt_node_id = prompt_node_id
        self.negative_prompt_node_id = negative_prompt_node_id

    @staticmethod
    def _find_lora_node(
        data: dict[str, Any],
    ) -> tuple[str | None, dict[str, Any] | None]:
        matches = [
            (node_id, node)
            for node_id, node in data.items()
            if isinstance(node, dict)
            and node.get("class_type") in {"LoraLoader", "LoraLoaderModelOnly"}
        ]
        if len(matches) > 1:
            raise WorkflowError(
                "multiple LoRA nodes are not supported"
            )
        return matches[0] if matches else (None, None)

    def default_lora_filename(self) -> str | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"workflow could not be loaded: {exc}") from exc
        if not isinstance(data, dict):
            raise WorkflowError("workflow root must be an object")
        _, node = self._find_lora_node(data)
        if node is None:
            return None
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            return None
        name = inputs.get("lora_name")
        return str(name) if name else None

    @staticmethod
    def _remove_lora_node(
        data: dict[str, Any],
        lora_node_id: str,
        inputs: dict[str, Any],
    ) -> None:
        def source_for(output_index: int) -> Any:
            key = "model" if output_index == 0 else "clip"
            source = inputs.get(key)
            if not (
                isinstance(source, list)
                and len(source) == 2
                and isinstance(source[1], int)
            ):
                raise WorkflowError(
                    f"LoRA node has no {key} input; cannot bypass"
                )
            return copy.deepcopy(source)

        def rewire(value: Any) -> Any:
            if isinstance(value, list):
                if (
                    len(value) == 2
                    and str(value[0]) == str(lora_node_id)
                    and isinstance(value[1], int)
                ):
                    return source_for(value[1])
                return [rewire(item) for item in value]
            if isinstance(value, dict):
                return {key: rewire(item) for key, item in value.items()}
            return value

        for node_id, node in data.items():
            if node_id == lora_node_id or not isinstance(node, dict):
                continue
            node_inputs = node.get("inputs")
            if isinstance(node_inputs, dict):
                node["inputs"] = rewire(node_inputs)
        del data[lora_node_id]

    def build(
        self,
        prompt: str,
        filename_prefix: str,
        *,
        negative_prompt: str | None = None,
        steps: int | None = None,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        layout_json: str | None = None,
        lora_filename: str | None = None,
        lora_strength: float | None = None,
        disable_lora: bool = False,
    ) -> BuiltWorkflow:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"workflow could not be loaded: {exc}") from exc
        if not isinstance(data, dict):
            raise WorkflowError("workflow root must be an object")

        node = data.get(self.prompt_node_id)
        if not isinstance(node, dict) or node.get("class_type") != "CLIPTextEncode":
            raise WorkflowError(
                f"prompt node {self.prompt_node_id!r} must be a CLIPTextEncode node"
            )
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or "text" not in inputs:
            raise WorkflowError(f"prompt node {self.prompt_node_id!r} has no text input")

        negative_node = data.get(self.negative_prompt_node_id)
        if not isinstance(negative_node, dict) or negative_node.get("class_type") != "CLIPTextEncode":
            raise WorkflowError(
                f"negative prompt node {self.negative_prompt_node_id!r} "
                "must be a CLIPTextEncode node"
            )
        negative_inputs = negative_node.get("inputs")
        if not isinstance(negative_inputs, dict) or not isinstance(negative_inputs.get("text"), str):
            raise WorkflowError(
                f"negative prompt node {self.negative_prompt_node_id!r} has no text input"
            )

        if steps is not None and (
            isinstance(steps, bool) or not isinstance(steps, int) or not STEPS_MIN <= steps <= STEPS_MAX
        ):
            raise WorkflowError(f"steps must be an integer between {STEPS_MIN} and {STEPS_MAX}")

        if seed is not None and (
            isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX
        ):
            raise WorkflowError(f"seed must be an integer between 0 and {SEED_MAX}")

        effective_width = normalize_dimension(width)
        effective_height = normalize_dimension(height)

        if lora_strength is not None and (
            isinstance(lora_strength, bool)
            or not isinstance(lora_strength, (int, float))
            or not 0 <= float(lora_strength) <= 2
        ):
            raise WorkflowError("lora strength must be between 0 and 2")

        result = copy.deepcopy(data)
        lora_id, lora_node = self._find_lora_node(result)
        template_lora_name = None
        effective_strength = None
        if lora_id is not None:
            lora_inputs = lora_node.setdefault("inputs", {})
            if not isinstance(lora_inputs, dict):
                raise WorkflowError("LoRA node has invalid inputs")
            if disable_lora:
                self._remove_lora_node(result, lora_id, lora_inputs)
            else:
                if lora_filename is not None:
                    lora_inputs["lora_name"] = lora_filename
                if lora_strength is not None:
                    lora_inputs["strength_model"] = float(lora_strength)
                template_lora_name = lora_inputs.get("lora_name")
                effective_strength = (
                    float(lora_inputs["strength_model"])
                    if "strength_model" in lora_inputs
                    else None
                )
        elif lora_filename is not None:
            raise WorkflowError("workflow has no LoRA node")
        result[self.prompt_node_id]["inputs"]["text"] = layout_json or prompt

        custom_negative = negative_prompt.strip() if negative_prompt else ""
        effective_negative = custom_negative or negative_inputs["text"]
        if custom_negative:
            result[self.negative_prompt_node_id]["inputs"]["text"] = custom_negative

        latent_nodes = [
            value
            for value in result.values()
            if isinstance(value, dict)
            and isinstance(value.get("class_type"), str)
            and "LatentImage" in value["class_type"]
            and isinstance(value.get("inputs"), dict)
            and isinstance(value["inputs"].get("width"), int)
            and isinstance(value["inputs"].get("height"), int)
        ]
        if not latent_nodes:
            raise WorkflowError("workflow has no usable LatentImage node")
        for latent_node in latent_nodes:
            latent_node["inputs"]["width"] = effective_width
            latent_node["inputs"]["height"] = effective_height

        save_nodes = [
            value
            for value in result.values()
            if isinstance(value, dict) and value.get("class_type") == "SaveImage"
        ]
        if not save_nodes:
            raise WorkflowError("workflow has no SaveImage node")
        for save_node in save_nodes:
            save_node.setdefault("inputs", {})["filename_prefix"] = filename_prefix

        sampler_nodes = [
            value
            for value in result.values()
            if isinstance(value, dict) and value.get("class_type") == "KSampler"
        ]
        if not sampler_nodes:
            raise WorkflowError("workflow has no KSampler node")

        effective_steps: int | None = None
        effective_seed: int | None = None
        for sampler_node in sampler_nodes:
            sampler_inputs = sampler_node.setdefault("inputs", {})
            if "seed" not in sampler_inputs:
                raise WorkflowError("KSampler node has no seed input")
            sampler_seed = secrets.randbelow(SEED_MAX + 1) if seed is None else seed
            sampler_inputs["seed"] = sampler_seed
            if effective_seed is None:
                effective_seed = sampler_seed
            if steps is not None:
                sampler_inputs["steps"] = steps
            sampler_steps = sampler_inputs.get("steps")
            if isinstance(sampler_steps, bool) or not isinstance(sampler_steps, int):
                raise WorkflowError("KSampler node has no steps input")
            if effective_steps is None:
                effective_steps = sampler_steps

        if effective_steps is None or effective_seed is None:
            raise WorkflowError("workflow has no usable KSampler node")
        return BuiltWorkflow(
            payload=result,
            negative_prompt=effective_negative,
            steps=effective_steps,
            seed=effective_seed,
            width=effective_width,
            height=effective_height,
            lora_filename=template_lora_name,
            lora_strength=effective_strength,
        )
