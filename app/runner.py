from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from .comfy import ComfyClient, ComfyError
from .config import Settings
from .database import Database, iso, parse_iso, utc_now
from .image_audit import ImageAuditLog
from .nodes import EndpointClientPool, NodeHealthMonitor
from .private_gallery import cleanup_private_galleries
from .task_metadata import update_task_metadata
from .task_paths import relative_result_path
from .workflow import WorkflowError, WorkflowTemplate

logger = logging.getLogger("comfy_gateway.runner")
_AUTO_CLEANUP_INTERVAL_SECONDS = 60.0
_HISTORY_RECOVERY_RETRY_SECONDS = 60.0
_DISPATCH_LEASE_SECONDS = 60.0
_MAX_DISPATCH_SWITCHES = 3
_UNRECOVERABLE_ERROR_HINTS = (
    "prompt_outputs_failed_validation",
    "prompt outputs failed validation",
    "invalid prompt",
    "value not in list",
    "required input is missing",
    "invalid input",
    "rejected workflow nodes",
    "node_errors",
    "missing node",
    "model not found",
)
_LORA_ERROR_HINTS = (
    "lora",
    "loraloader",
    "lora key not loaded",
    "failed to load lora",
)


class JobRunner:
    def __init__(
        self,
        db: Database,
        comfy: ComfyClient,
        workflow: WorkflowTemplate,
        settings: Settings,
        audit: ImageAuditLog,
        *,
        clients: EndpointClientPool | None = None,
    ):
        self.db = db
        self.comfy = comfy
        self.workflow = workflow
        self.settings = settings
        self.audit = audit
        self.clients = clients or EndpointClientPool(
            timeout=settings.comfy_timeout_seconds
        )
        self.health = NodeHealthMonitor(self.db, self.clients)
        self.jobs_dir = settings.jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last_auto_cleanup = 0.0
        self._history_recovery_done = False
        self._last_history_recovery = 0.0
        self._recovered_endpoints: set[str] = set()
        self._thread = threading.Thread(target=self._run, name="comfy-job-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = utc_now()
                endpoints = self.health.probe_all(time.monotonic(), now)
                self._migrate_offline_tasks(endpoints, now)
                self._fail_invalid_requested_tasks(endpoints)
                self._reconcile_auto_pause(endpoints)
                self.db.expire_due_tasks(
                    now=now, free_limit=self._free_daily_quota()
                )
                self.db.refund_failed_account_tasks(
                    now=now, free_limit=self._free_daily_quota()
                )
                self.db.settle_pending_rewards(now=now)
                cleanup_private_galleries(self.db, self.settings, now)
                self._auto_cleanup(now)
                self._recover_lost_prompt_ids()
                self._submit_queued()
                self._poll_active()
            except Exception:
                logger.exception("job runner loop failed")
            self._wake.wait(self.settings.comfy_poll_seconds)
            self._wake.clear()

    def _reconcile_auto_pause(self, endpoints: list[dict[str, Any]]) -> None:
        status = self.db.queue_status()
        if status["paused"] and not status["pause_reason"]:
            self.db.set_queue_state(True, "manual", utc_now(), "runner")
            return
        if status["pause_reason"] == "manual":
            return
        available = [
            endpoint
            for endpoint in endpoints
            if endpoint.get("enabled") and endpoint.get("health_state") == "online"
        ]
        if not available:
            if not status["paused"] or status["pause_reason"] != "no_nodes":
                self.db.set_queue_state(True, "no_nodes", utc_now(), "runner")
                logger.warning("queue auto-paused: no available ComfyUI nodes")
            return
        if status["paused"] and status["pause_reason"] == "no_nodes":
            self.db.set_queue_state(False, "", utc_now(), "runner")
            logger.warning("queue auto-resumed: ComfyUI node available")

    def _auto_cleanup(self, now: Any) -> None:
        monotonic = time.monotonic()
        if monotonic - self._last_auto_cleanup < _AUTO_CLEANUP_INTERVAL_SECONDS:
            return
        self._last_auto_cleanup = monotonic
        try:
            purged = self.db.purge_expired_account_query_tokens(now=now)
            if purged:
                logger.info("purged %s expired account query tokens", purged)
        except Exception:
            logger.exception("account query token cleanup failed")
        try:
            flags = self.db.get_cleanup_settings()
            if flags["auto_delete_expired"]:
                self._delete_job_images(self.db.expired_job_ids(), "expired")
            if flags["auto_delete_destroyed"]:
                self._delete_job_images(self.db.destroyed_job_ids(), "destroyed")
        except Exception:
            logger.exception("auto cleanup failed")

    def _delete_job_images(self, job_ids: list[str], label: str) -> None:
        if not job_ids:
            return
        jobs_root = self.jobs_dir.resolve()
        removable: list[str] = []
        for job_id in job_ids:
            job_dir = (self.jobs_dir / job_id).resolve()
            if jobs_root not in job_dir.parents:
                continue
            if job_dir.exists():
                try:
                    shutil.rmtree(job_dir)
                except OSError:
                    logger.exception("could not remove job directory %s", job_dir)
                    continue
            removable.append(job_id)
        deleted = self.db.delete_tasks(removable)
        if deleted:
            logger.info("auto cleanup deleted %s %s images", len(deleted), label)

    def _update_metadata(self, job_id: str, **fields: Any) -> None:
        try:
            update_task_metadata(self.jobs_dir / job_id, **fields)
        except Exception:
            logger.exception("could not update task metadata for job %s", job_id)

    def _free_daily_quota(self) -> int:
        return max(0, int(self.settings.account_free_daily_quota))

    def _mark_failed(self, job_id: str, message: str, *, clear_queue: bool = False) -> None:
        now = utc_now()
        updates: dict[str, Any] = {
            "status": "failed",
            "error_message": message,
            "last_checked_at": iso(now),
        }
        if clear_queue:
            updates["queue_position"] = None
        self.db.update_task(job_id, **updates)
        try:
            self.db.refund_account_charge(
                job_id=job_id,
                now=now,
                free_limit=self._free_daily_quota(),
                note="generation_failed",
            )
        except Exception:
            logger.exception("could not refund account charge for job %s", job_id)
        self._update_metadata(job_id, status="failed", error=message, failed_at=iso(now))

    def _submit_queued(self) -> None:
        if self.db.get_queue_paused():
            return
        endpoints = self.db.list_endpoints()
        if endpoints:
            self._dispatch_queued(endpoints)
            return
        tasks = self.db.active_tasks()
        if any(task["status"] in {"submitted", "running"} for task in tasks):
            return
        queued = next(
            (task for task in tasks if task["status"] == "queued"), None
        )
        if queued is None:
            return
        self._submit_task(queued)

    def _dispatch_queued(self, endpoints: list[dict[str, Any]]) -> None:
        now = utc_now()
        queued = [
            task
            for task in self.db.active_tasks()
            if task["status"] in {"queued", "waiting_node"}
        ]
        if not queued:
            return
        endpoints_by_id = {endpoint["id"]: endpoint for endpoint in endpoints}
        loads = {
            endpoint["id"]: self.db.count_endpoint_in_flight(endpoint["id"], now)
            for endpoint in endpoints
        }
        for task in queued:
            lease = task.get("dispatch_lease_expires_at")
            if lease:
                try:
                    if parse_iso(lease) > now:
                        continue
                except (ValueError, TypeError):
                    pass
            requested_id = task.get("requested_endpoint_id")
            if requested_id:
                endpoint = endpoints_by_id.get(requested_id)
                if not endpoint:
                    self._fail_requested_node(
                        task,
                        "指定节点不存在",
                        "requested_node_unavailable",
                    )
                    continue
                if not endpoint.get("enabled"):
                    self._fail_requested_node(
                        task,
                        "指定节点已禁用",
                        "requested_node_unavailable",
                    )
                    continue
                if endpoint.get("health_state") != "online":
                    if task["status"] != "waiting_node":
                        self.db.update_task(
                            task["id"],
                            status="waiting_node",
                            queue_position=None,
                        )
                    continue
                if loads[endpoint["id"]] >= int(endpoint["max_concurrency"]):
                    if task["status"] != "waiting_node":
                        self.db.update_task(
                            task["id"],
                            status="waiting_node",
                            queue_position=None,
                        )
                    continue
                if not self._dispatch_to_endpoint(task, endpoint, now):
                    continue
                loads[endpoint["id"]] += 1
                continue
            if task["status"] != "queued":
                continue
            candidates = [
                endpoint
                for endpoint in endpoints
                if endpoint.get("enabled")
                and endpoint.get("health_state") == "online"
                and loads[endpoint["id"]] < int(endpoint["max_concurrency"])
            ]
            if not candidates:
                continue
            endpoint = self._select_endpoint(candidates)
            if not self._dispatch_to_endpoint(task, endpoint, now):
                continue
            loads[endpoint["id"]] += 1

    @staticmethod
    def _select_endpoint(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return min(
            candidates,
            key=lambda endpoint: (
                endpoint.get("last_task_completed_at") is not None,
                endpoint.get("last_task_completed_at") or "",
                endpoint.get("created_at") or "",
                endpoint["id"],
            ),
        )

    @staticmethod
    def _lora_build_args(task: dict[str, Any]) -> dict[str, Any]:
        if task.get("lora_disabled"):
            return {"disable_lora": True}
        return {
            "lora_filename": (
                task.get("lora_filename_snapshot")
                if task.get("lora_id")
                else None
            ),
            "lora_strength": task.get("lora_strength"),
        }

    def _dispatch_to_endpoint(
        self, task: dict[str, Any], endpoint: dict[str, Any], now: Any
    ) -> bool:
        job_id = task["id"]
        client = self.clients.get(endpoint)
        lease_end = now + timedelta(seconds=_DISPATCH_LEASE_SECONDS)
        self.db.update_task(
            job_id,
            endpoint_id=endpoint["id"],
            endpoint_name_snapshot=endpoint["name"],
            endpoint_public_name_snapshot=endpoint.get("public_name") or "",
            dispatch_claimed_at=iso(now),
            dispatch_lease_expires_at=iso(lease_end),
            queue_position=None,
        )
        try:
            built = self.workflow.build(
                task["prompt"],
                filename_prefix=f"public-job-{job_id}",
                negative_prompt=task.get("negative_prompt"),
                steps=task.get("steps"),
                seed=task.get("seed"),
                width=task.get("width"),
                height=task.get("height"),
                layout_json=task.get("layout_json"),
                **self._lora_build_args(task),
            )
            prompt_id = client.submit(built.payload)
        except ComfyError as exc:
            logger.warning(
                "job %s dispatch to node %s failed: %s",
                job_id,
                endpoint["id"],
                exc,
            )
            self.db.update_task(
                job_id,
                endpoint_id=None,
                endpoint_name_snapshot=None,
                endpoint_public_name_snapshot=None,
                dispatch_claimed_at=None,
                dispatch_lease_expires_at=None,
                queue_position=None,
            )
            if not exc.retryable:
                lora_error = self._is_lora_comfy_error(exc)
                if lora_error:
                    self._handle_generation_failure(
                        task,
                        "LoRA 加载失败",
                        now,
                        endpoint=endpoint,
                        lora_error=True,
                    )
                elif self._is_unrecoverable_comfy_error(exc):
                    self._fail_unrecoverable(
                        task,
                        "工作流模板不可用或参数被 ComfyUI 拒绝",
                        str(exc),
                    )
                else:
                    self._handle_generation_failure(
                        task,
                        "节点拒绝提交任务",
                        now,
                        endpoint=endpoint,
                    )
            return False
        except WorkflowError as exc:
            logger.error(
                "job %s workflow failed on node %s: %s",
                job_id,
                endpoint["id"],
                exc,
            )
            self.db.update_task(
                job_id,
                endpoint_id=None,
                endpoint_name_snapshot=None,
                endpoint_public_name_snapshot=None,
                dispatch_claimed_at=None,
                dispatch_lease_expires_at=None,
                queue_position=None,
            )
            self._fail_unrecoverable(
                task,
                "工作流模板不可用或无效",
                str(exc),
            )
            return False

        submitted_at = utc_now()
        self.db.add_task_binding(
            job_id=job_id,
            endpoint_id=endpoint["id"],
            endpoint_name=endpoint["name"],
            endpoint_public_name=endpoint.get("public_name") or "",
            comfy_prompt_id=prompt_id,
            state="active",
            created_at=submitted_at,
            last_progress_at=submitted_at,
            reward_account_id=endpoint.get("owner_account_id"),
        )
        lora_filename = None if task.get("lora_disabled") else built.lora_filename
        lora_strength = None if task.get("lora_disabled") else built.lora_strength
        self.db.update_task(
            job_id,
            status="submitted",
            comfy_prompt_id=prompt_id,
            submitted_at=iso(submitted_at),
            last_progress_at=iso(submitted_at),
            queue_position=None,
            dispatch_claimed_at=None,
            dispatch_lease_expires_at=None,
            error_message=None,
            lora_filename_snapshot=lora_filename,
            lora_strength=lora_strength,
        )
        self._update_metadata(
            job_id,
            status="submitted",
            comfy_prompt_id=prompt_id,
            submitted_at=iso(submitted_at),
            negative_prompt=built.negative_prompt,
            steps=built.steps,
            seed=built.seed,
            width=built.width,
            height=built.height,
            lora_filename=lora_filename,
            lora_strength=lora_strength,
            error=None,
        )
        logger.info(
            "job %s submitted to node %s as %s",
            job_id,
            endpoint["id"],
            prompt_id,
        )
        return True

    def _migrate_offline_tasks(
        self, endpoints: list[dict[str, Any]], now: Any
    ) -> None:
        for endpoint in endpoints:
            if endpoint.get("health_state") != "offline":
                continue
            for task in self.db.list_tasks_for_endpoint(endpoint["id"]):
                if task["status"] == "queued":
                    self.db.update_task(
                        task["id"],
                        endpoint_id=None,
                        endpoint_name_snapshot=None,
                        endpoint_public_name_snapshot=None,
                        dispatch_claimed_at=None,
                        dispatch_lease_expires_at=None,
                        queue_position=None,
                    )
                    continue
                self._migrate_task(task, "节点离线", now)

    def _fail_invalid_requested_tasks(
        self, endpoints: list[dict[str, Any]]
    ) -> None:
        endpoints_by_id = {endpoint["id"]: endpoint for endpoint in endpoints}
        for task in self.db.active_tasks():
            if task["status"] not in {"queued", "waiting_node"}:
                continue
            requested_id = task.get("requested_endpoint_id")
            if not requested_id:
                continue
            endpoint = endpoints_by_id.get(requested_id)
            if not endpoint:
                self._fail_requested_node(
                    task, "指定节点不存在", "requested_node_unavailable"
                )
            elif not endpoint.get("enabled"):
                self._fail_requested_node(
                    task, "指定节点已禁用", "requested_node_unavailable"
                )

    def _fail_requested_node(
        self,
        task: dict[str, Any],
        message: str,
        reason_code: str,
    ) -> None:
        job_id = task["id"]
        binding = self.db.get_active_binding(job_id)
        if binding:
            state = binding["state"]
            self.db.set_binding_state(
                binding["id"], "discarded", updated_at=utc_now()
            )
            if state == "active":
                self._cancel_binding(binding)
        self.db.update_task(job_id, failure_reason=reason_code)
        self._mark_failed(job_id, message, clear_queue=True)
        logger.error(
            "job %s failed for requested node %s: %s",
            job_id,
            task.get("requested_endpoint_id"),
            message,
        )

    @staticmethod
    def _is_lora_text(text: str | None) -> bool:
        lowered = (text or "").lower()
        return any(hint in lowered for hint in _LORA_ERROR_HINTS)

    @staticmethod
    def _is_lora_comfy_error(exc: ComfyError) -> bool:
        return JobRunner._is_lora_text(str(exc))

    @staticmethod
    def _is_unrecoverable_comfy_error(exc: ComfyError) -> bool:
        text = str(exc).lower()
        return any(hint in text for hint in _UNRECOVERABLE_ERROR_HINTS)

    def _fail_unrecoverable(
        self, task: dict[str, Any], message: str, detail: str
    ) -> None:
        self.db.update_task(
            task["id"], failure_reason=f"unrecoverable_error: {detail}"
        )
        self._mark_failed(task["id"], message, clear_queue=True)
        logger.error(
            "job %s failed with an unrecoverable error: %s",
            task["id"],
            detail,
        )

    def _handle_generation_failure(
        self,
        task: dict[str, Any],
        reason: str,
        now: Any,
        *,
        endpoint: dict[str, Any] | None = None,
        fallback_message: str | None = None,
        lora_error: bool = False,
    ) -> bool:
        job_id = task["id"]
        if task.get("requested_endpoint_id"):
            self._fail_requested_node(
                task,
                f"指定节点生成失败：{reason}",
                "requested_lora_failed" if lora_error else "requested_generation_failed",
            )
            return False
        if endpoint is not None:
            task = {
                **task,
                "endpoint_id": task.get("endpoint_id") or endpoint["id"],
                "endpoint_name_snapshot": task.get("endpoint_name_snapshot")
                or endpoint["name"],
            }
        if not task.get("endpoint_id"):
            self.db.update_task(job_id, failure_reason="generation_failed")
            self._mark_failed(
                job_id,
                fallback_message or f"生成失败：{reason}",
                clear_queue=True,
            )
            return False
        return self._migrate_task(task, f"生成失败：{reason}", now)

    def _cancel_binding(self, binding: dict[str, Any]) -> None:
        endpoint = self.db.get_endpoint(binding["endpoint_id"])
        prompt_id = binding.get("comfy_prompt_id")
        if not endpoint or not prompt_id:
            return
        client = self.clients.get(endpoint)
        try:
            client.delete_queue_item(prompt_id)
        except ComfyError as exc:
            logger.warning(
                "could not cancel prompt %s on node %s: %s",
                prompt_id,
                endpoint["id"],
                exc,
            )

    def _migrate_task(
        self, task: dict[str, Any], reason: str, now: Any
    ) -> bool:
        job_id = task["id"]
        if task.get("requested_endpoint_id"):
            unavailable = "离线" in reason
            reason_code = (
                "requested_node_unavailable"
                if unavailable
                else "requested_node_timeout"
            )
            message = (
                "指定节点不可用：节点离线"
                if unavailable
                else f"指定节点超时：{reason}"
            )
            self._fail_requested_node(task, message, reason_code)
            return False
        switches = int(task.get("dispatch_switch_count") or 0)
        if switches >= _MAX_DISPATCH_SWITCHES:
            binding = self.db.get_active_binding(job_id)
            if binding:
                self.db.set_binding_state(
                    binding["id"], "discarded", updated_at=now
                )
            endpoint_name = (
                task.get("endpoint_name_snapshot")
                or task.get("endpoint_id")
                or "未知节点"
            )
            endpoint_id = task.get("endpoint_id") or "未知 ID"
            self.db.update_task(
                job_id,
                failure_reason=(
                    f"switch_limit_reached: 最后节点={endpoint_name}"
                    f"({endpoint_id}); 原因={reason}"
                ),
            )
            self._mark_failed(
                job_id,
                f"多次换节点后失败（最后节点：{endpoint_name}，原因：{reason}）",
                clear_queue=True,
            )
            logger.error(
                "job %s exceeded dispatch switch limit (%s): %s",
                job_id,
                switches,
                reason,
            )
            return False
        binding = self.db.get_active_binding(job_id)
        if binding:
            self.db.set_binding_state(
                binding["id"],
                "abandoned",
                updated_at=now,
                last_progress_at=now,
            )
            self._cancel_binding(binding)
        self.db.update_task(
            job_id,
            status="queued",
            comfy_prompt_id=None,
            submitted_at=None,
            queue_position=None,
            endpoint_id=None,
            endpoint_name_snapshot=None,
            endpoint_public_name_snapshot=None,
            last_progress_at=None,
            auto_requeue_count=0,
            download_retry_count=0,
            dispatch_switch_count=switches + 1,
            dispatch_claimed_at=None,
            dispatch_lease_expires_at=None,
            error_message=None,
        )
        self._update_metadata(
            job_id,
            status="queued",
            comfy_prompt_id=None,
            submitted_at=None,
            queue_position=None,
            auto_requeue_count=0,
            download_retry_count=0,
            dispatch_switch_count=switches + 1,
            error=None,
        )
        logger.warning(
            "job %s migrated from node %s after switch %s: %s",
            job_id,
            task.get("endpoint_id"),
            switches + 1,
            reason,
        )
        return True

    def _recover_lost_prompt_ids(self) -> None:
        endpoints = self.db.list_endpoints()
        if not endpoints:
            self._recover_legacy_prompt_ids()
            return
        monotonic = time.monotonic()
        if monotonic - self._last_history_recovery < _HISTORY_RECOVERY_RETRY_SECONDS:
            return
        self._last_history_recovery = monotonic
        for endpoint in endpoints:
            if endpoint["id"] in self._recovered_endpoints:
                continue
            if self._recover_endpoint_bindings(endpoint):
                self._recovered_endpoints.add(endpoint["id"])
        self._recover_unbound_legacy_tasks()

    def _recover_endpoint_bindings(self, endpoint: dict[str, Any]) -> bool:
        client = self.clients.get(endpoint)
        try:
            history = client.history_all()
            queue_data = client.queue()
        except ComfyError as exc:
            logger.warning(
                "could not scan node %s for lost prompt ids: %s",
                endpoint["id"],
                exc,
            )
            return False
        tasks = self.db.list_tasks_for_endpoint(endpoint["id"])
        now = utc_now()
        for task in tasks:
            if task.get("comfy_prompt_id"):
                continue
            job_id = task["id"]
            prompt_id = self._find_history_prompt_id(history, job_id)
            if not prompt_id:
                prompt_id = self._find_queue_prompt_id(queue_data, job_id)
            if prompt_id:
                self._bind_recovered(task, endpoint, prompt_id, now)
                continue
            if task["status"] == "queued":
                lease = task.get("dispatch_lease_expires_at")
                if lease:
                    try:
                        if parse_iso(lease) <= now:
                            self.db.update_task(
                                job_id,
                                endpoint_id=None,
                                endpoint_name_snapshot=None,
                                endpoint_public_name_snapshot=None,
                                dispatch_claimed_at=None,
                                dispatch_lease_expires_at=None,
                                queue_position=None,
                            )
                    except (ValueError, TypeError):
                        pass
        return True

    def _bind_recovered(
        self,
        task: dict[str, Any],
        endpoint: dict[str, Any],
        prompt_id: str,
        now: Any,
    ) -> None:
        job_id = task["id"]
        try:
            self.db.add_task_binding(
                job_id=job_id,
                endpoint_id=endpoint["id"],
                endpoint_name=endpoint["name"],
                endpoint_public_name=endpoint.get("public_name") or "",
                comfy_prompt_id=prompt_id,
                state="active",
                created_at=now,
                last_progress_at=now,
                reward_account_id=endpoint.get("owner_account_id"),
            )
            self.db.update_task(
                job_id,
                status="submitted",
                endpoint_id=endpoint["id"],
                endpoint_name_snapshot=endpoint["name"],
                endpoint_public_name_snapshot=endpoint.get("public_name") or "",
                comfy_prompt_id=prompt_id,
                submitted_at=iso(now),
                last_progress_at=iso(now),
                dispatch_claimed_at=None,
                dispatch_lease_expires_at=None,
                queue_position=None,
                error_message=None,
            )
        except Exception:
            logger.exception("could not recover prompt id for job %s", job_id)
            return
        self._update_metadata(
            job_id,
            status="submitted",
            comfy_prompt_id=prompt_id,
            submitted_at=iso(now),
            error=None,
        )
        logger.warning(
            "job %s was already on node %s; recovered prompt id %s",
            job_id,
            endpoint["id"],
            prompt_id,
        )

    def _recover_unbound_legacy_tasks(self) -> None:
        if self._history_recovery_done:
            return
        tasks = [
            task
            for task in self.db.active_tasks()
            if not task.get("endpoint_id")
            and not task.get("comfy_prompt_id")
            and task["status"] in {"queued", "submitted", "running"}
        ]
        if not tasks:
            self._history_recovery_done = True
            return
        try:
            history = self.comfy.history_all()
        except ComfyError as exc:
            logger.warning(
                "could not scan legacy ComfyUI history for lost prompt ids: %s",
                exc,
            )
            return
        now = utc_now()
        recovered = 0
        for task in tasks:
            prompt_id = self._find_history_prompt_id(history, task["id"])
            if not prompt_id:
                continue
            try:
                self.db.update_task(
                    task["id"],
                    status="submitted",
                    comfy_prompt_id=prompt_id,
                    submitted_at=iso(now),
                    last_progress_at=iso(now),
                    last_checked_at=iso(now),
                    error_message=None,
                )
            except Exception:
                logger.exception(
                    "could not recover legacy prompt id for job %s", task["id"]
                )
                continue
            self._update_metadata(
                task["id"],
                status="submitted",
                comfy_prompt_id=prompt_id,
                submitted_at=iso(now),
                error=None,
            )
            recovered += 1
            logger.warning(
                "legacy job %s recovered prompt id %s from default endpoint",
                task["id"],
                prompt_id,
            )
        self._history_recovery_done = True
        if recovered:
            logger.warning(
                "recovered %s legacy ComfyUI prompt id(s)", recovered
            )

    def _recover_legacy_prompt_ids(self) -> None:
        if self._history_recovery_done:
            return
        monotonic = time.monotonic()
        if monotonic - self._last_history_recovery < _HISTORY_RECOVERY_RETRY_SECONDS:
            return
        self._last_history_recovery = monotonic
        queued = [
            task for task in self.db.active_tasks() if task["status"] == "queued"
        ]
        if not queued:
            self._history_recovery_done = True
            return
        try:
            history = self.comfy.history_all()
        except ComfyError as exc:
            logger.warning(
                "could not scan ComfyUI history for lost prompt ids: %s", exc
            )
            return

        recovered = 0
        for task in queued:
            job_id = task["id"]
            prompt_id = self._find_history_prompt_id(history, job_id)
            if not prompt_id:
                continue
            now = utc_now()
            try:
                self.db.update_task(
                    job_id,
                    status="submitted",
                    comfy_prompt_id=prompt_id,
                    submitted_at=iso(now),
                    last_checked_at=iso(now),
                    error_message=None,
                )
            except Exception:
                logger.exception("could not recover prompt id for job %s", job_id)
                continue
            self._update_metadata(
                job_id,
                status="submitted",
                comfy_prompt_id=prompt_id,
                submitted_at=iso(now),
                error=None,
            )
            recovered += 1
            logger.warning(
                "job %s was already in ComfyUI history; recovered prompt id %s",
                job_id,
                prompt_id,
            )
        self._history_recovery_done = True
        if recovered:
            logger.warning("recovered %s lost ComfyUI prompt id(s)", recovered)

    @staticmethod
    def _graph_has_job_prefix(graph: Any, job_id: str) -> bool:
        prefix = f"public-job-{job_id}"
        if not isinstance(graph, dict):
            return False
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") != "SaveImage":
                continue
            inputs = node.get("inputs")
            if isinstance(inputs, dict) and inputs.get("filename_prefix") == prefix:
                return True
        return False

    @classmethod
    def _find_queue_prompt_id(
        cls, queue_data: dict[str, Any], job_id: str
    ) -> str | None:
        entries = list(queue_data.get("queue_running") or []) + list(
            queue_data.get("queue_pending") or []
        )
        for entry in entries:
            if not isinstance(entry, list) or len(entry) <= 2:
                continue
            if cls._graph_has_job_prefix(entry[2], job_id):
                return str(entry[1])
        return None

    @staticmethod
    def _find_history_prompt_id(
        history: dict[str, Any], job_id: str
    ) -> str | None:
        for prompt_id, entry in history.items():
            if not isinstance(entry, dict):
                continue
            prompt = entry.get("prompt")
            graph = (
                prompt[2]
                if isinstance(prompt, list) and len(prompt) > 2
                else prompt
            )
            if not JobRunner._graph_has_job_prefix(graph, job_id):
                continue
            return str(prompt_id)
        return None

    def _submit_task(self, task: dict[str, Any]) -> None:
        job_id = task["id"]
        try:
            built = self.workflow.build(
                task["prompt"],
                filename_prefix=f"public-job-{job_id}",
                negative_prompt=task.get("negative_prompt"),
                steps=task.get("steps"),
                seed=task.get("seed"),
                width=task.get("width"),
                height=task.get("height"),
                layout_json=task.get("layout_json"),
                **self._lora_build_args(task),
            )
            prompt_id = self.comfy.submit(built.payload)
        except ComfyError as exc:
            logger.warning("job %s submission failed: %s", job_id, exc)
            if not exc.retryable:
                if self._is_lora_comfy_error(exc):
                    self.db.update_task(job_id, failure_reason="lora_load_failed")
                    self._mark_failed(
                        job_id,
                        "LoRA 加载失败：节点缺少该 LoRA 或与模型不兼容",
                    )
                else:
                    self._mark_failed(job_id, "ComfyUI rejected the fixed workflow")
            return
        except WorkflowError as exc:
            logger.error("job %s workflow failed: %s", job_id, exc)
            self._mark_failed(job_id, "The fixed workflow is unavailable or invalid")
            return
        now = utc_now()
        lora_filename = None if task.get("lora_disabled") else built.lora_filename
        lora_strength = None if task.get("lora_disabled") else built.lora_strength
        self.db.update_task(
            job_id,
            status="submitted",
            comfy_prompt_id=prompt_id,
            submitted_at=iso(now),
            last_checked_at=iso(now),
            negative_prompt=built.negative_prompt,
            steps=built.steps,
            seed=built.seed,
            width=built.width,
            height=built.height,
            lora_filename_snapshot=lora_filename,
            lora_strength=lora_strength,
        )
        self._update_metadata(
            job_id,
            status="submitted",
            comfy_prompt_id=prompt_id,
            submitted_at=iso(now),
            negative_prompt=built.negative_prompt,
            steps=built.steps,
            seed=built.seed,
            width=built.width,
            height=built.height,
            lora_filename=lora_filename,
            lora_strength=lora_strength,
            error=None,
        )
        logger.info("job %s submitted to ComfyUI as %s", job_id, prompt_id)

    def _poll_active(self) -> None:
        tasks = [task for task in self.db.active_tasks() if task["comfy_prompt_id"]]
        if not tasks:
            return
        endpoints = {
            endpoint["id"]: endpoint for endpoint in self.db.list_endpoints()
        }
        items: list[tuple[str, int, dict[str, Any], dict[str, Any]]] = []
        legacy_tasks: list[dict[str, Any]] = []
        for task in tasks:
            bindings = [
                binding
                for binding in self.db.list_task_bindings(task["id"])
                if binding["state"] in {"active", "abandoned"}
                and binding.get("comfy_prompt_id")
            ]
            if not bindings:
                legacy_tasks.append(task)
                continue
            for binding in bindings:
                items.append(
                    (
                        binding.get("created_at") or "",
                        int(binding["id"]),
                        task,
                        binding,
                    )
                )
        items.sort(key=lambda item: (item[0], item[1]))
        queue_cache: dict[str, dict[str, Any] | None] = {}
        for _created, _binding_id, task, binding in items:
            current = self.db.get_task(task["id"])
            if not current:
                continue
            if current["status"] not in {"queued", "submitted", "running"}:
                self.db.set_binding_state(
                    binding["id"], "discarded", updated_at=utc_now()
                )
                continue
            endpoint_id = binding["endpoint_id"]
            endpoint = endpoints.get(endpoint_id)
            if endpoint:
                client = self.clients.get(endpoint)
            else:
                client = self.comfy
            cache_key = endpoint_id if endpoint else "legacy"
            if cache_key not in queue_cache:
                try:
                    queue_cache[cache_key] = client.queue()
                except ComfyError as exc:
                    logger.warning(
                        "could not read node %s queue: %s", endpoint_id, exc
                    )
                    queue_cache[cache_key] = None
            queue_data = queue_cache[cache_key]
            if queue_data is None:
                continue
            if binding["state"] == "active":
                self._poll_task(current, queue_data, client, binding)
            else:
                self._poll_abandoned_binding(current, binding, client)
        if legacy_tasks:
            try:
                queue_data = self.comfy.queue()
            except ComfyError as exc:
                logger.warning("could not read ComfyUI queue: %s", exc)
                return
            for task in legacy_tasks:
                self._poll_task(task, queue_data, self.comfy)

    def _poll_abandoned_binding(
        self,
        task: dict[str, Any],
        binding: dict[str, Any],
        client: ComfyClient,
    ) -> None:
        prompt_id = binding["comfy_prompt_id"]
        try:
            history = client.history(prompt_id)
        except ComfyError as exc:
            logger.warning(
                "could not poll abandoned binding %s: %s", prompt_id, exc
            )
            return
        if history is None:
            return
        result = client.history_result(history)
        if result.status == "completed":
            self._complete_task(task, result.images[0], client, binding)
        elif result.status == "failed":
            self.db.set_binding_state(
                binding["id"], "discarded", updated_at=utc_now()
            )
            logger.warning(
                "abandoned binding %s on node %s failed; discarded",
                prompt_id,
                binding["endpoint_id"],
            )

    def _poll_task(
        self,
        task: dict,
        queue_data: dict,
        client: ComfyClient | None = None,
        binding: dict[str, Any] | None = None,
    ) -> None:
        client = client or self.comfy
        job_id = task["id"]
        prompt_id = task["comfy_prompt_id"]
        now = utc_now()
        try:
            status, position = client.prompt_position(queue_data, prompt_id)
            history = client.history(prompt_id)
        except ComfyError as exc:
            logger.warning("could not poll job %s: %s", job_id, exc)
            return

        if history is not None:
            result = client.history_result(history)
            if result.status == "failed":
                lora_error = self._is_lora_text(result.error_message)
                logger.warning(
                    "job %s generation failed on node %s: %s",
                    job_id,
                    task.get("endpoint_id"),
                    result.error_message,
                )
                self._handle_generation_failure(
                    task,
                    "LoRA 加载失败" if lora_error else "ComfyUI 执行失败",
                    now,
                    fallback_message=result.error_message
                    or "ComfyUI generation failed",
                    lora_error=lora_error,
                )
                return
            if result.status == "completed":
                self._complete_task(
                    task, result.images[0], client, binding
                )
                return

        submitted_at = task.get("submitted_at")
        missing_too_long = False
        if submitted_at:
            try:
                missing_too_long = (
                    now - parse_iso(submitted_at)
                ).total_seconds() > self.settings.missing_prompt_grace_seconds
            except (ValueError, TypeError):
                missing_too_long = False
        if position is None and history is None and missing_too_long:
            if task.get("endpoint_id"):
                self._migrate_task(
                    task,
                    f"任务丢失（超过 {self.settings.missing_prompt_grace_seconds} 秒）",
                    now,
                )
                return
            requeues = int(task.get("auto_requeue_count") or 0)
            if requeues < max(0, self.settings.comfy_auto_requeue_max):
                self.db.update_task(
                    job_id,
                    status="queued",
                    comfy_prompt_id=None,
                    submitted_at=None,
                    queue_position=None,
                    last_checked_at=iso(now),
                    auto_requeue_count=requeues + 1,
                    error_message=None,
                )
                self._update_metadata(
                    job_id,
                    status="queued",
                    comfy_prompt_id=None,
                    submitted_at=None,
                    queue_position=None,
                    auto_requeue_count=requeues + 1,
                    error=None,
                )
                logger.warning(
                    "job %s disappeared from ComfyUI; re-queued (%s/%s)",
                    job_id,
                    requeues + 1,
                    self.settings.comfy_auto_requeue_max,
                )
            else:
                self._mark_failed(
                    job_id,
                    "ComfyUI no longer reports this generation task after retries",
                    clear_queue=True,
                )
            return
        last_progress = task.get("last_progress_at") or submitted_at
        if (
            task.get("endpoint_id")
            and position is not None
            and last_progress
        ):
            try:
                no_progress_seconds = (
                    now - parse_iso(last_progress)
                ).total_seconds()
            except (ValueError, TypeError):
                no_progress_seconds = 0
            if (
                no_progress_seconds
                > self.settings.node_no_progress_timeout_seconds
            ):
                self._migrate_task(
                    task,
                    f"无进展超时（已 {int(no_progress_seconds)} 秒）",
                    now,
                )
                return
        progressed = status != task.get("status") or (
            position is not None and position != task.get("queue_position")
        )
        if progressed:
            self.db.update_task(
                job_id,
                status=status,
                queue_position=position,
                last_checked_at=iso(now),
                last_progress_at=iso(now),
            )
            active_binding = self.db.get_active_binding(job_id)
            if active_binding:
                self.db.update_binding_progress(
                    active_binding["id"],
                    updated_at=now,
                    last_progress_at=now,
                    last_queue_position=position,
                )
        else:
            self.db.update_task(
                job_id,
                status=status,
                queue_position=position,
                last_checked_at=iso(now),
            )

    def _complete_task(
        self,
        task: dict,
        image: dict[str, str],
        client: ComfyClient | None = None,
        binding: dict[str, Any] | None = None,
    ) -> None:
        job_id = task["id"]
        client = client or self.comfy
        current = self.db.get_task(job_id)
        if not current or current["status"] in {
            "completed",
            "failed",
            "expired",
        }:
            if binding:
                self.db.set_binding_state(
                    binding["id"], "discarded", updated_at=utc_now()
                )
            logger.warning(
                "duplicate result discarded for job %s from node %s prompt %s",
                job_id,
                binding.get("endpoint_id") if binding else task.get("endpoint_id"),
                binding.get("comfy_prompt_id")
                if binding
                else task.get("comfy_prompt_id"),
            )
            return
        try:
            content = client.download_image(image)
        except ComfyError as exc:
            retries = int(task.get("download_retry_count") or 0)
            limit = max(0, self.settings.comfy_download_retry_max)
            if retries < limit:
                now = utc_now()
                self.db.update_task(
                    job_id,
                    download_retry_count=retries + 1,
                    last_checked_at=iso(now),
                    error_message=str(exc),
                )
                self._update_metadata(
                    job_id, download_retry_count=retries + 1, error=str(exc)
                )
                logger.warning(
                    "could not download image for job %s (%s/%s): %s",
                    job_id,
                    retries + 1,
                    limit,
                    exc,
                )
                return
            logger.error("could not download image for job %s: %s", job_id, exc)
            self._handle_generation_failure(
                current,
                "结果图片下载失败",
                utc_now(),
                fallback_message="The generated image could not be retrieved",
            )
            return
        suffix = Path(image.get("filename", "result.png")).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        result_path = job_dir / f"result{suffix}"
        result_path.write_bytes(content)
        media_type = mimetypes.guess_type(result_path.name)[0] or "application/octet-stream"
        result_sha256 = hashlib.sha256(content).hexdigest()
        now = utc_now()
        expires_at = now + timedelta(hours=self.settings.result_ttl_hours)
        source_endpoint_id = (
            binding["endpoint_id"] if binding else task.get("endpoint_id")
        )
        source_prompt_id = (
            binding["comfy_prompt_id"]
            if binding
            else task.get("comfy_prompt_id")
        )
        try:
            self.audit.image_generated(
                generation_id=job_id,
                comfy_prompt_id=source_prompt_id,
                prompt=task["prompt"],
                negative_prompt=task.get("negative_prompt"),
                steps=task.get("steps"),
                image_path=str(result_path.resolve()),
                media_type=media_type,
                requested_at=task["created_at"],
                completed_at=iso(now),
                visitor_id=task.get("visitor_id"),
            )
        except Exception:
            logger.exception("could not append image audit record for job %s", job_id)
        updates: dict[str, Any] = {
            "status": "completed",
            "queue_position": None,
            "result_path": relative_result_path(job_id, result_path.name),
            "result_media_type": media_type,
            "completed_at": iso(now),
            "expires_at": iso(expires_at),
            "last_checked_at": iso(now),
            "error_message": None,
        }
        if binding:
            updates.update(
                endpoint_id=binding["endpoint_id"],
                endpoint_name_snapshot=binding.get("endpoint_name"),
                endpoint_public_name_snapshot=binding.get(
                    "endpoint_public_name"
                ),
                comfy_prompt_id=binding.get("comfy_prompt_id"),
            )
        self.db.update_task(job_id, **updates)
        winning_binding_id = binding["id"] if binding else None
        if not binding:
            active_binding = self.db.get_active_binding(job_id)
            if active_binding:
                winning_binding_id = active_binding["id"]
                self.db.set_binding_state(
                    active_binding["id"], "completed", updated_at=now
                )
        elif binding:
            self.db.set_binding_state(
                binding["id"], "completed", updated_at=now
            )
        for other in self.db.list_task_bindings(job_id):
            if other["id"] == winning_binding_id:
                continue
            if other["state"] in {"active", "abandoned"}:
                self.db.set_binding_state(
                    other["id"], "discarded", updated_at=now
                )
                if other["state"] == "active":
                    self._cancel_binding(other)
        self._update_metadata(
            job_id,
            status="completed",
            completed_at=iso(now),
            expires_at=iso(expires_at),
            result_file=result_path.name,
            result_media_type=media_type,
            result_size_bytes=len(content),
            result_sha256=result_sha256,
            error=None,
        )
        if source_endpoint_id:
            self.db.mark_endpoint_task_completed(source_endpoint_id, now)
        try:
            self.db.settle_endpoint_reward(job_id=job_id, now=now)
        except Exception:
            logger.exception("could not settle endpoint reward for job %s", job_id)
        logger.info("job %s completed with %s", job_id, result_path.name)

