from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from .auth import harness_identity, isolated_harness_identity
from .models import (
    ArtifactRecord,
    EvalCaseRecord,
    HarnessCommand,
    HarnessLoopCreate,
    HarnessLoopTick,
    HarnessQueuedMessage,
    HarnessQueuedMessageCancel,
    HarnessTaskAnnotation,
    HarnessTaskCreate,
    HarnessTaskFork,
    HarnessTaskReply,
    LlmProbeRequest,
    ObservationRecord,
    PermissionDecisionRecord,
    TaskProgressUpdate,
    ToolCallRecord,
    WorkerHeartbeat,
)
from .service import harness
from .store import LeaseConflictError


router = APIRouter(tags=["development-harness"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "api_surface": "/api/harness/v1"}


@router.get("/ready")
def ready(identity: dict[str, str] = Depends(harness_identity)) -> dict:
    runtime = harness()
    return {
        "status": "ready",
        "client": identity,
        "api_surface": "/api/harness/v1",
        "persistence": str(runtime.store.path),
        "capabilities_status": runtime.capabilities().get("status"),
    }


@router.get("/capabilities")
def capabilities(identity: dict[str, str] = Depends(harness_identity)) -> dict:
    return {**harness().capabilities(), "client": identity}


@router.get("/sdk/initialize")
def sdk_initialize(identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    return harness().sdk_initialize(identity["client_id"])


@router.get("/sdk/mcp-status")
def sdk_mcp_status(identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    return {**harness().sdk_mcp_status(), "client_id": identity["client_id"]}


@router.get("/sdk/context-usage")
def sdk_context_usage(identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    return harness().sdk_context_usage(identity["client_id"])


@router.get("/llm/providers")
def llm_provider_templates(identity: dict[str, str] = Depends(harness_identity)) -> dict:
    return {"status": "completed", "client": identity, "providers": harness().llm_provider_templates()}


@router.get("/llm/diagnostics")
def llm_provider_diagnostics(
    limit: int = Query(default=50, ge=1, le=200),
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    return {
        "status": "completed",
        "client": identity,
        "diagnostics": harness().store.list_provider_diagnostics(identity["client_id"], limit=limit),
    }


@router.post("/llm/probe")
def probe_llm_provider(payload: LlmProbeRequest, identity: dict[str, str] = Depends(harness_identity)) -> dict:
    result = harness().probe_llm_provider(
        payload.provider.model_dump(mode="json"),
        timeout_seconds=payload.timeout_seconds,
        client_id=identity["client_id"] if identity.get("client_id_source") == "x_harness_client_id" else None,
    )
    return {"client": identity, **result}


@router.get("/eval-cases")
def list_eval_cases(
    limit: int = Query(default=50, ge=1, le=200),
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    return {
        "status": "completed",
        "client": identity,
        "eval_cases": harness().list_eval_cases(identity["client_id"], limit=limit),
    }


@router.post("/eval-cases")
def record_eval_case(
    payload: EvalCaseRecord,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    try:
        return harness().record_eval_case(identity["client_id"], payload.model_dump(mode="json"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source task not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions")
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    return harness().list_task_sessions(identity["client_id"], limit=limit, offset=offset)


@router.get("/tasks")
def list_tasks(identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    tasks = [
        task
        for task in harness().store.list_tasks()
        if task.get("client_id") == identity["client_id"]
    ]
    return {"status": "completed", "client": identity, "tasks": tasks}


@router.post("/tasks")
def create_task(
    payload: HarnessTaskCreate,
    background_tasks: BackgroundTasks,
    background: bool = True,
    start: bool = True,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    runtime = harness()
    try:
        request_payload = payload.model_dump(mode="json")
        if idempotency_key and not request_payload.get("idempotency_key"):
            request_payload["idempotency_key"] = idempotency_key
        request_payload["client_id"] = identity["client_id"]
        existing = _idempotent_task_replay(runtime, request_payload)
        if existing:
            return existing
        task = runtime.prepare_task(request_payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not start:
        task["status"] = "paused"
        task["lease"] = None
        task = runtime.store.save_task(task)
        runtime.store.append_event(
            task["task_id"],
            "task.paused",
            {"command": "create", "reason": "start_false"},
            actor="api",
            status="paused",
        )
        return task
    if background:
        background_tasks.add_task(runtime.run_task, task["task_id"])
        return task
    return runtime.run_task(task["task_id"])


@router.get("/tasks/{task_id}")
def get_task(task_id: str, identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    task = harness().store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.get("/tasks/{task_id}/session-info")
def task_session_info(task_id: str, identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return harness().task_session_info(task_id)


@router.post("/tasks/{task_id}/annotations")
def annotate_task(
    task_id: str,
    payload: HarnessTaskAnnotation,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        return harness().annotate_task(
            task_id,
            payload.model_dump(mode="json"),
            expected_state_version=payload.expected_state_version,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/fork")
def fork_task(
    task_id: str,
    payload: HarnessTaskFork,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    try:
        return harness().fork_task(
            task_id,
            payload.model_dump(mode="json"),
            client_id=identity["client_id"],
            expected_state_version=payload.expected_state_version,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/evidence")
def task_evidence(task_id: str, identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "completed", "task_id": task_id, **harness().task_evidence(task_id)}


@router.get("/tasks/{task_id}/client-transcript")
def task_client_transcript(
    task_id: str,
    summary: bool = Query(default=False),
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return harness().task_client_transcript(task_id, summary=summary)


@router.get("/tasks/{task_id}/sdk-events")
def task_sdk_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    summary: bool = Query(default=False),
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return harness().task_sdk_events(task_id, after=after, summary=summary)


@router.post("/tasks/{task_id}/tool-calls")
def record_tool_call(
    task_id: str,
    payload: ToolCallRecord,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return harness().record_tool_call(task_id, payload.model_dump(mode="json"))


@router.post("/tasks/{task_id}/observations")
def record_observation(
    task_id: str,
    payload: ObservationRecord,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return harness().record_observation(task_id, payload.model_dump(mode="json"))


@router.post("/tasks/{task_id}/permission-decisions")
def record_permission_decision(
    task_id: str,
    payload: PermissionDecisionRecord,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return harness().record_permission_decision(task_id, payload.model_dump(mode="json"))


@router.post("/tasks/{task_id}/progress")
def record_task_progress(
    task_id: str,
    payload: TaskProgressUpdate,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        return harness().record_task_progress(
            task_id,
            payload.model_dump(mode="json"),
            expected_state_version=payload.expected_state_version,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/artifacts")
def record_artifact(
    task_id: str,
    payload: ArtifactRecord,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return harness().record_artifact(task_id, payload.model_dump(mode="json"))


@router.post("/tasks/{task_id}/commands")
def command_task(task_id: str, payload: HarnessCommand, identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    task = harness().store.get_task(task_id)
    if task and task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        return harness().command_task(
            task_id,
            payload.command,
            expected_state_version=payload.expected_state_version,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/subagents")
def create_subagent_task(
    task_id: str,
    payload: HarnessTaskCreate,
    background_tasks: BackgroundTasks,
    background: bool = True,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    runtime = harness()
    parent = runtime.store.get_task(task_id)
    if not parent or parent.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="parent task not found")
    request = payload.model_dump(mode="json")
    if idempotency_key and not request.get("idempotency_key"):
        request["idempotency_key"] = idempotency_key
    request["parent_task_id"] = task_id
    request["client_id"] = identity["client_id"]
    existing = _idempotent_task_replay(runtime, request)
    if existing:
        if existing.get("parent_task_id") != task_id:
            raise HTTPException(status_code=409, detail="idempotency key belongs to another parent task")
        return existing
    task = runtime.prepare_task(request)
    runtime.store.append_event(
        task_id,
        "subagent.created",
        {"child_task_id": task["task_id"], "join_policy": request.get("join_policy")},
        actor="api",
        status="queued",
    )
    if background:
        background_tasks.add_task(runtime.run_task, task["task_id"])
        return task
    return runtime.run_task(task["task_id"])


def _idempotent_task_replay(runtime: DevelopmentHarness, request: dict) -> dict | None:
    task = runtime.store.get_task_by_idempotency(
        str(request.get("client_id") or "anonymous"),
        request.get("idempotency_key"),
    )
    if not task:
        return None
    return task


@router.get("/tasks/{task_id}/subagents")
def list_subagent_tasks(
    task_id: str,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    runtime = harness()
    task = runtime.store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    return runtime.task_subagents(task_id)


@router.post("/tasks/{task_id}/reply")
def reply_task(
    task_id: str,
    payload: HarnessTaskReply,
    background_tasks: BackgroundTasks,
    background: bool = True,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    runtime = harness()
    task = runtime.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    if not task.get("thread_id"):
        raise HTTPException(status_code=409, detail="task has no resumable Codex thread")
    if payload.expected_state_version is not None and task.get("state_version") != payload.expected_state_version:
        raise HTTPException(status_code=409, detail="state version conflict")
    if background:
        background_tasks.add_task(runtime.run_task, task_id, payload.prompt)
        return {**task, "status": "queued"}
    return runtime.run_task(task_id, payload.prompt)


@router.post("/tasks/{task_id}/queued-messages")
def enqueue_task_message(
    task_id: str,
    payload: HarnessQueuedMessage,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    runtime = harness()
    task = runtime.store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        return runtime.enqueue_task_message(
            task_id,
            payload.model_dump(mode="json"),
            expected_state_version=payload.expected_state_version,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/queued-messages/cancel")
def cancel_queued_task_message(
    task_id: str,
    payload: HarnessQueuedMessageCancel,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    runtime = harness()
    task = runtime.store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        return runtime.cancel_queued_message(
            task_id,
            payload.message_uuid,
            expected_state_version=payload.expected_state_version,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> StreamingResponse:
    task = harness().store.get_task(task_id)
    if not task or task.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        after = max(after, int(last_event_id or 0))
    except ValueError:
        pass

    async def stream():
        cursor = after
        for _ in range(600):
            events = harness().store.events(task_id, cursor)
            for event in events:
                cursor = event["event_id"]
                yield f"id: {cursor}\nevent: harness_event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            task = harness().store.get_task(task_id) or {}
            if task.get("status") in {"completed", "failed", "cancelled", "paused"} and not events:
                break
            if not events:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/workers/tick")
def worker_tick(identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    return harness().worker_tick(owner=identity["client_id"], client_id=identity["client_id"])


@router.post("/workers/heartbeat")
def worker_heartbeat(payload: WorkerHeartbeat, identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    try:
        task = harness().worker_heartbeat(
            payload.task_id,
            owner=identity["client_id"],
            lease_epoch=payload.lease_epoch,
            lease_seconds=payload.lease_seconds,
            client_id=identity["client_id"],
        )
        return {"status": "completed", "worker": identity["client_id"], "task": task}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except LeaseConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/loops")
def list_loops(identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    loops = [
        loop
        for loop in harness().store.list_loops()
        if loop.get("client_id") == identity["client_id"]
    ]
    return {"status": "completed", "client": identity, "loops": loops}


@router.post("/loops")
def create_loop(payload: HarnessLoopCreate, identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    data = payload.model_dump(mode="json")
    data["client_id"] = identity["client_id"]
    data["task"]["client_id"] = identity["client_id"]
    return harness().create_loop(data)


@router.get("/loops/{loop_id}/evidence")
def loop_evidence(loop_id: str, identity: dict[str, str] = Depends(isolated_harness_identity)) -> dict:
    loop = harness().store.get_loop(loop_id)
    if not loop or loop.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="loop not found")
    try:
        return {"status": "completed", "client": identity, **harness().loop_evidence(loop_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="loop not found") from exc


@router.post("/loops/{loop_id}/tick")
def tick_loop(
    loop_id: str,
    payload: HarnessLoopTick,
    background_tasks: BackgroundTasks,
    identity: dict[str, str] = Depends(isolated_harness_identity),
) -> dict:
    loop = harness().store.get_loop(loop_id)
    if not loop or loop.get("client_id") != identity["client_id"]:
        raise HTTPException(status_code=404, detail="loop not found")
    try:
        result = harness().tick_loop(loop_id, payload.force)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="loop not found") from exc
    task = result.get("task") if isinstance(result, dict) else None
    if task and payload.start:
        if payload.background:
            background_tasks.add_task(harness().run_task, task["task_id"])
        else:
            result["task"] = harness().run_task(task["task_id"])
    return result
