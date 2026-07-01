from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class HarnessStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                create table if not exists harness_tasks (
                  task_id text primary key,
                  status text not null,
                  state_version integer not null default 1,
                  client_id text,
                  idempotency_key text,
                  parent_task_id text,
                  lease_owner text,
                  lease_epoch integer not null default 0,
                  lease_expires_at text,
                  created_at text not null,
                  updated_at text not null,
                  payload text not null
                );
                create table if not exists harness_events (
                  event_id integer primary key autoincrement,
                  task_id text not null,
                  seq integer not null,
                  created_at text not null,
                  event_type text not null,
                  actor text,
                  parent_event_id integer,
                  status text,
                  payload text not null
                );
                create table if not exists harness_loops (
                  loop_id text primary key,
                  status text not null,
                  next_run_at text,
                  payload text not null
                );
                create table if not exists harness_hook_executions (
                  hook_id integer primary key autoincrement,
                  task_id text not null,
                  event_name text not null,
                  status text not null,
                  created_at text not null,
                  payload text not null
                );
                create table if not exists harness_verification_results (
                  verification_id integer primary key autoincrement,
                  task_id text not null,
                  command text not null,
                  status text not null,
                  returncode integer,
                  duration_ms integer,
                  created_at text not null,
                  payload text not null
                );
                create table if not exists harness_provider_diagnostics (
                  diagnostic_id integer primary key autoincrement,
                  client_id text not null,
                  provider text,
                  api_style text,
                  base_url text,
                  model text,
                  status text not null,
                  created_at text not null,
                  payload text not null
                );
                create table if not exists harness_tool_calls (
                  call_pk integer primary key autoincrement,
                  call_id text not null,
                  task_id text not null,
                  tool_name text not null,
                  status text not null,
                  started_at text,
                  completed_at text,
                  created_at text not null,
                  payload text not null
                );
                create table if not exists harness_observations (
                  observation_id integer primary key autoincrement,
                  task_id text not null,
                  call_id text,
                  tool_name text not null,
                  status text not null,
                  evidence_refs text not null,
                  created_at text not null,
                  payload text not null
                );
                create table if not exists harness_permission_decisions (
                  decision_id integer primary key autoincrement,
                  task_id text not null,
                  call_id text,
                  tool_name text not null,
                  behavior text not null,
                  status text not null,
                  created_at text not null,
                  payload text not null
                );
                create table if not exists harness_task_progress (
                  progress_id integer primary key autoincrement,
                  task_id text not null,
                  status text not null,
                  created_at text not null,
                  payload text not null
                );
                create table if not exists harness_artifacts (
                  artifact_id integer primary key autoincrement,
                  task_id text not null,
                  kind text not null,
                  path text,
                  url text,
                  digest text,
                  created_at text not null,
                  payload text not null
                );
                create table if not exists harness_eval_cases (
                  eval_case_id integer primary key autoincrement,
                  client_id text not null,
                  name text not null,
                  source_task_id text,
                  failure_label text,
                  status text not null,
                  verifier_command text,
                  created_at text not null,
                  payload text not null
                );
                """
            )
            self._ensure_column(conn, "harness_tasks", "state_version", "integer not null default 1")
            self._ensure_column(conn, "harness_tasks", "client_id", "text")
            self._ensure_column(conn, "harness_tasks", "idempotency_key", "text")
            self._ensure_column(conn, "harness_tasks", "parent_task_id", "text")
            self._ensure_column(conn, "harness_tasks", "lease_owner", "text")
            self._ensure_column(conn, "harness_tasks", "lease_epoch", "integer not null default 0")
            self._ensure_column(conn, "harness_tasks", "lease_expires_at", "text")
            self._ensure_column(conn, "harness_events", "seq", "integer not null default 0")
            self._ensure_column(conn, "harness_events", "actor", "text")
            self._ensure_column(conn, "harness_events", "parent_event_id", "integer")
            self._ensure_column(conn, "harness_events", "status", "text")
            conn.execute(
                "create unique index if not exists idx_harness_events_task_seq on harness_events(task_id, seq)"
            )
            conn.execute(
                """
                create unique index if not exists idx_harness_tasks_idempotency
                on harness_tasks(client_id, idempotency_key)
                where idempotency_key is not null
                """
            )
            conn.execute(
                """
                create index if not exists idx_harness_provider_diagnostics_client_created
                on harness_provider_diagnostics(client_id, created_at desc)
                """
            )
            conn.execute(
                "create index if not exists idx_harness_tool_calls_task on harness_tool_calls(task_id, call_pk)"
            )
            conn.execute(
                "create index if not exists idx_harness_observations_task on harness_observations(task_id, observation_id)"
            )
            conn.execute(
                "create index if not exists idx_harness_permission_decisions_task on harness_permission_decisions(task_id, decision_id)"
            )
            conn.execute(
                "create index if not exists idx_harness_task_progress_task on harness_task_progress(task_id, progress_id)"
            )
            conn.execute(
                "create index if not exists idx_harness_artifacts_task on harness_artifacts(task_id, artifact_id)"
            )
            conn.execute(
                """
                create index if not exists idx_harness_eval_cases_client_created
                on harness_eval_cases(client_id, eval_case_id desc)
                """
            )

    def create_task(self, request: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_task_by_idempotency(
            str(request.get("client_id") or "anonymous"),
            request.get("idempotency_key"),
        )
        if existing:
            return existing
        timestamp = now()
        task = {
            "task_id": f"task_{uuid4().hex[:12]}",
            "status": "queued",
            "state_version": 1,
            "client_id": request.get("client_id") or "anonymous",
            "idempotency_key": request.get("idempotency_key"),
            "parent_task_id": request.get("parent_task_id"),
            "lease": None,
            "created_at": timestamp,
            "updated_at": timestamp,
            "request": request,
            "thread_id": None,
            "result": None,
            "error": None,
        }
        self.save_task(task)
        self.append_event(task["task_id"], "task.queued", {"request": request}, actor="api", status="queued")
        return task

    def save_task(self, task: dict[str, Any], *, expected_state_version: int | None = None) -> dict[str, Any]:
        task = dict(task)
        current = self.get_task(task["task_id"])
        if expected_state_version is not None and current and current.get("state_version") != expected_state_version:
            raise StateConflictError(task["task_id"], expected_state_version, current.get("state_version"))
        task["state_version"] = int((current or task).get("state_version") or 0) + 1 if current else int(task.get("state_version") or 1)
        timestamp = now()
        created_at = str(task.get("created_at") or timestamp)
        created_dt = _parse_iso_datetime(created_at)
        updated_dt = _parse_iso_datetime(timestamp)
        task["created_at"] = created_at
        task["updated_at"] = created_at if created_dt and updated_dt and updated_dt < created_dt else timestamp
        lease = task.get("lease") or {}
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                insert into harness_tasks(task_id,status,state_version,client_id,idempotency_key,parent_task_id,lease_owner,lease_epoch,lease_expires_at,created_at,updated_at,payload)
                values(?,?,?,?,?,?,?,?,?,?,?,?)
                on conflict(task_id) do update set
                  status=excluded.status, updated_at=excluded.updated_at, payload=excluded.payload
                  , state_version=excluded.state_version
                  , client_id=excluded.client_id
                  , idempotency_key=excluded.idempotency_key
                  , parent_task_id=excluded.parent_task_id
                  , lease_owner=excluded.lease_owner
                  , lease_epoch=excluded.lease_epoch
                  , lease_expires_at=excluded.lease_expires_at
                """,
                (
                    task["task_id"],
                    task["status"],
                    task["state_version"],
                    task.get("client_id"),
                    task.get("idempotency_key"),
                    task.get("parent_task_id"),
                    lease.get("owner"),
                    int(lease.get("epoch") or 0),
                    lease.get("expires_at"),
                    task["created_at"],
                    task["updated_at"],
                    json.dumps(task, ensure_ascii=False),
                ),
            )
        return task

    def get_task_by_idempotency(self, client_id: str, key: Any) -> dict[str, Any] | None:
        if not isinstance(key, str) or not key.strip():
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                select payload from harness_tasks
                where client_id=? and idempotency_key=?
                """,
                (client_id, key.strip()),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "select payload from harness_tasks where task_id=?", (task_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_tasks(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "select payload from harness_tasks order by created_at desc"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def child_tasks(self, parent_task_id: str, *, client_id: str | None = None) -> list[dict[str, Any]]:
        query = "select payload from harness_tasks where parent_task_id=?"
        params: list[Any] = [parent_task_id]
        if client_id is not None:
            query += " and client_id=?"
            params.append(client_id)
        query += " order by created_at"
        with closing(self._connect()) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append_event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor: str | None = None,
        status: str | None = None,
        parent_event_id: int | None = None,
    ) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "select coalesce(max(seq), 0) from harness_events where task_id=?",
                    (task_id,),
                ).fetchone()
                seq = int(row[0] or 0) + 1
                cursor = conn.execute(
                    """
                    insert into harness_events(task_id,seq,created_at,event_type,actor,parent_event_id,status,payload)
                    values(?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        seq,
                        now(),
                        event_type,
                        actor,
                        parent_event_id,
                        status,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                event_id = int(cursor.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "event_id": event_id,
            "seq": seq,
            "task_id": task_id,
            "event_type": event_type,
            "actor": actor,
            "status": status,
            "payload": payload,
        }

    def events(self, task_id: str, after: int = 0) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select event_id,seq,created_at,event_type,actor,parent_event_id,status,payload from harness_events
                where task_id=? and event_id>? order by event_id
                """,
                (task_id, after),
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "seq": row[1],
                "created_at": row[2],
                "event_type": row[3],
                "actor": row[4],
                "parent_event_id": row[5],
                "status": row[6],
                "payload": json.loads(row[7]),
            }
            for row in rows
        ]

    def claim_next_task(
        self,
        owner: str,
        lease_seconds: int = 300,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any] | None:
        expires_at = (datetime.now(timezone.utc) + _timedelta(seconds=lease_seconds)).isoformat()
        with closing(self._connect()) as conn, conn:
            client_filter = ""
            params: list[Any] = [now()]
            if client_id is not None:
                client_filter = " and client_id=?"
                params.append(client_id)
            row = conn.execute(
                f"""
                select task_id,payload from harness_tasks
                where (
                    status='queued'
                    or (status='running' and (lease_expires_at is null or lease_expires_at < ?))
                )
                {client_filter}
                order by created_at limit 1
                """,
                tuple(params),
            ).fetchone()
            if not row:
                return None
            task = json.loads(row[1])
            epoch = int((task.get("lease") or {}).get("epoch") or 0) + 1
            task["status"] = "running"
            task["lease"] = {"owner": owner, "epoch": epoch, "expires_at": expires_at}
            task = self.save_task(task)
            self.append_event(task["task_id"], "task.claimed", task["lease"], actor=owner, status="running")
            return task

    def heartbeat_task(
        self,
        task_id: str,
        owner: str,
        lease_epoch: int,
        lease_seconds: int = 300,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if client_id is not None and task.get("client_id") != client_id:
            raise KeyError(task_id)
        lease = task.get("lease") or {}
        if task.get("status") != "running":
            raise LeaseConflictError(task_id, "task is not running")
        if lease.get("owner") != owner:
            raise LeaseConflictError(task_id, "lease owner mismatch")
        if int(lease.get("epoch") or 0) != int(lease_epoch):
            raise LeaseConflictError(task_id, "lease epoch mismatch")
        lease["expires_at"] = (datetime.now(timezone.utc) + _timedelta(seconds=lease_seconds)).isoformat()
        task["lease"] = lease
        task = self.save_task(task)
        self.append_event(task_id, "task.heartbeat", lease, actor=owner, status="running")
        return task

    def record_hook(self, task_id: str, event_name: str, status: str, payload: dict[str, Any]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "insert into harness_hook_executions(task_id,event_name,status,created_at,payload) values(?,?,?,?,?)",
                (task_id, event_name, status, now(), json.dumps(payload, ensure_ascii=False)),
            )

    def record_verification(self, task_id: str, result: dict[str, Any]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                insert into harness_verification_results(task_id,command,status,returncode,duration_ms,created_at,payload)
                values(?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    result.get("command", ""),
                    result.get("status", "unknown"),
                    result.get("returncode"),
                    result.get("duration_ms"),
                    now(),
                    json.dumps(result, ensure_ascii=False),
                ),
            )
        self.append_event(task_id, "verification.completed", result, actor="verification", status=result.get("status"))

    def record_provider_diagnostic(self, client_id: str, result: dict[str, Any]) -> dict[str, Any]:
        provider = result.get("provider") if isinstance(result.get("provider"), dict) else {}
        diagnostic = {
            "client_id": client_id,
            "created_at": now(),
            "status": result.get("status", "unknown"),
            "provider": provider.get("provider"),
            "api_style": provider.get("api_style"),
            "base_url": provider.get("base_url"),
            "model": provider.get("model"),
            "result": result,
        }
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                insert into harness_provider_diagnostics(client_id,provider,api_style,base_url,model,status,created_at,payload)
                values(?,?,?,?,?,?,?,?)
                """,
                (
                    client_id,
                    diagnostic.get("provider"),
                    diagnostic.get("api_style"),
                    diagnostic.get("base_url"),
                    diagnostic.get("model"),
                    diagnostic["status"],
                    diagnostic["created_at"],
                    json.dumps(diagnostic, ensure_ascii=False),
                ),
            )
            diagnostic["diagnostic_id"] = int(cursor.lastrowid)
        return diagnostic

    def list_provider_diagnostics(self, client_id: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select diagnostic_id, payload from harness_provider_diagnostics
                where client_id=?
                order by diagnostic_id desc
                limit ?
                """,
                (client_id, limit),
            ).fetchall()
        diagnostics: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row[1])
            if isinstance(payload, dict):
                payload.setdefault("diagnostic_id", int(row[0]))
                diagnostics.append(payload)
        return diagnostics

    def record_tool_call(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        timestamp = now()
        call_id = str(record.get("call_id") or f"call_{uuid4().hex[:12]}")
        status = str(record.get("status") or "queued")
        stored = {
            "task_id": task_id,
            "call_id": call_id,
            "tool_name": record["tool_name"],
            "input": record.get("input") or {},
            "status": status,
            "metadata": record.get("metadata") or {},
            "started_at": timestamp if status in {"running", "completed", "failed", "cancelled"} else None,
            "completed_at": timestamp if status in {"completed", "failed", "cancelled"} else None,
            "created_at": timestamp,
        }
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                insert into harness_tool_calls(call_id,task_id,tool_name,status,started_at,completed_at,created_at,payload)
                values(?,?,?,?,?,?,?,?)
                """,
                (
                    stored["call_id"],
                    task_id,
                    stored["tool_name"],
                    stored["status"],
                    stored["started_at"],
                    stored["completed_at"],
                    stored["created_at"],
                    json.dumps(stored, ensure_ascii=False),
                ),
            )
            stored["call_pk"] = int(cursor.lastrowid)
            conn.execute(
                "update harness_tool_calls set payload=? where call_pk=?",
                (json.dumps(stored, ensure_ascii=False), stored["call_pk"]),
            )
        self.append_event(task_id, "tool_call.recorded", stored, actor="tool", status=stored["status"])
        return stored

    def record_observation(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        timestamp = now()
        stored = {
            "task_id": task_id,
            "call_id": record.get("call_id"),
            "tool_name": record["tool_name"],
            "result": record.get("result") or {},
            "evidence_refs": [str(item) for item in record.get("evidence_refs") or []],
            "status": record.get("status") or "completed",
            "metadata": record.get("metadata") or {},
            "created_at": timestamp,
        }
        updated_call: dict[str, Any] | None = None
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                insert into harness_observations(task_id,call_id,tool_name,status,evidence_refs,created_at,payload)
                values(?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    stored["call_id"],
                    stored["tool_name"],
                    stored["status"],
                    json.dumps(stored["evidence_refs"], ensure_ascii=False),
                    stored["created_at"],
                    json.dumps(stored, ensure_ascii=False),
                ),
            )
            stored["observation_id"] = int(cursor.lastrowid)
            conn.execute(
                "update harness_observations set payload=? where observation_id=?",
                (json.dumps(stored, ensure_ascii=False), stored["observation_id"]),
            )
            if stored["call_id"] and stored["status"] in {"completed", "failed", "cancelled"}:
                call_row = conn.execute(
                    """
                    select call_pk,payload from harness_tool_calls
                    where task_id=? and call_id=? order by call_pk desc limit 1
                    """,
                    (task_id, stored["call_id"]),
                ).fetchone()
                if call_row:
                    call_payload = json.loads(call_row[1])
                    if call_payload.get("status") not in {"completed", "failed", "cancelled"}:
                        call_payload["status"] = stored["status"]
                        call_payload["completed_at"] = timestamp
                        if not call_payload.get("started_at"):
                            call_payload["started_at"] = timestamp
                        updated_call = call_payload
                        conn.execute(
                            """
                            update harness_tool_calls
                            set status=?, started_at=?, completed_at=?, payload=?
                            where call_pk=?
                            """,
                            (
                                call_payload["status"],
                                call_payload.get("started_at"),
                                call_payload["completed_at"],
                                json.dumps(call_payload, ensure_ascii=False),
                                int(call_row[0]),
                            ),
                        )
        if updated_call:
            self.append_event(
                task_id,
                "tool_call.status_updated",
                updated_call,
                actor="tool",
                status=updated_call["status"],
            )
        self.append_event(task_id, "observation.recorded", stored, actor="tool", status=stored["status"])
        return stored

    def record_permission_decision(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        stored = {
            "task_id": task_id,
            "call_id": record.get("call_id"),
            "tool_name": record["tool_name"],
            "behavior": record["behavior"],
            "input": record.get("input") or {},
            "updated_input": record.get("updated_input"),
            "decision_reason": record.get("decision_reason") or {},
            "message": record.get("message"),
            "blocked_path": record.get("blocked_path"),
            "suggestions": record.get("suggestions") or [],
            "status": record.get("status") or "recorded",
            "metadata": record.get("metadata") or {},
            "created_at": now(),
        }
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                insert into harness_permission_decisions(task_id,call_id,tool_name,behavior,status,created_at,payload)
                values(?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    stored["call_id"],
                    stored["tool_name"],
                    stored["behavior"],
                    stored["status"],
                    stored["created_at"],
                    json.dumps(stored, ensure_ascii=False),
                ),
            )
            stored["decision_id"] = int(cursor.lastrowid)
            conn.execute(
                "update harness_permission_decisions set payload=? where decision_id=?",
                (json.dumps(stored, ensure_ascii=False), stored["decision_id"]),
            )
        self.append_event(
            task_id,
            "permission_decision.recorded",
            stored,
            actor="permission",
            status=stored["behavior"],
        )
        return stored

    def record_task_progress(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        items = []
        for index, item in enumerate(record.get("items") or []):
            items.append(
                {
                    "item_id": item.get("item_id") or f"progress_{index + 1}",
                    "content": item["content"],
                    "active_form": item["active_form"],
                    "status": item["status"],
                    "metadata": item.get("metadata") or {},
                }
            )
        counts = {"pending": 0, "in_progress": 0, "completed": 0}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        verification_nudge_needed = (
            bool(items)
            and len(items) >= 3
            and counts.get("pending", 0) == 0
            and counts.get("in_progress", 0) == 0
            and counts.get("completed", 0) == len(items)
            and not any("verif" in str(item.get("content", "")).lower() for item in items)
        )
        stored = {
            "task_id": task_id,
            "items": items,
            "counts": counts,
            "verification_nudge_needed": verification_nudge_needed,
            "metadata": record.get("metadata") or {},
            "created_at": now(),
        }
        status = "completed" if counts.get("completed", 0) == len(items) and items else "updated"
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                insert into harness_task_progress(task_id,status,created_at,payload)
                values(?,?,?,?)
                """,
                (
                    task_id,
                    status,
                    stored["created_at"],
                    json.dumps(stored, ensure_ascii=False),
                ),
            )
            stored["progress_id"] = int(cursor.lastrowid)
            conn.execute(
                "update harness_task_progress set payload=? where progress_id=?",
                (json.dumps(stored, ensure_ascii=False), stored["progress_id"]),
            )
        self.append_event(task_id, "task.progress_updated", stored, actor="progress", status=status)
        return stored

    def record_artifact(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        stored = {
            "task_id": task_id,
            "kind": record["kind"],
            "path": record.get("path"),
            "url": record.get("url"),
            "digest": record.get("digest"),
            "metadata": record.get("metadata") or {},
            "created_at": now(),
        }
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                insert into harness_artifacts(task_id,kind,path,url,digest,created_at,payload)
                values(?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    stored["kind"],
                    stored["path"],
                    stored["url"],
                    stored["digest"],
                    stored["created_at"],
                    json.dumps(stored, ensure_ascii=False),
                ),
            )
            stored["artifact_id"] = int(cursor.lastrowid)
            conn.execute(
                "update harness_artifacts set payload=? where artifact_id=?",
                (json.dumps(stored, ensure_ascii=False), stored["artifact_id"]),
            )
        self.append_event(task_id, "artifact.recorded", stored, actor="artifact", status="completed")
        return stored

    def task_evidence(self, task_id: str) -> dict[str, list[dict[str, Any]]]:
        with closing(self._connect()) as conn:
            tool_rows = conn.execute(
                "select payload from harness_tool_calls where task_id=? order by call_pk",
                (task_id,),
            ).fetchall()
            observation_rows = conn.execute(
                "select payload from harness_observations where task_id=? order by observation_id",
                (task_id,),
            ).fetchall()
            permission_rows = conn.execute(
                "select payload from harness_permission_decisions where task_id=? order by decision_id",
                (task_id,),
            ).fetchall()
            progress_rows = conn.execute(
                "select payload from harness_task_progress where task_id=? order by progress_id",
                (task_id,),
            ).fetchall()
            artifact_rows = conn.execute(
                "select payload from harness_artifacts where task_id=? order by artifact_id",
                (task_id,),
            ).fetchall()
            verification_rows = conn.execute(
                "select payload from harness_verification_results where task_id=? order by verification_id",
                (task_id,),
            ).fetchall()
            hook_rows = conn.execute(
                """
                select hook_id,event_name,status,created_at,payload from harness_hook_executions
                where task_id=? order by hook_id
                """,
                (task_id,),
            ).fetchall()
        return {
            "tool_calls": [json.loads(row[0]) for row in tool_rows],
            "observations": [json.loads(row[0]) for row in observation_rows],
            "permission_decisions": [json.loads(row[0]) for row in permission_rows],
            "task_progress": [json.loads(row[0]) for row in progress_rows],
            "artifacts": [json.loads(row[0]) for row in artifact_rows],
            "verification_results": [json.loads(row[0]) for row in verification_rows],
            "hook_executions": [
                {
                    "hook_id": row[0],
                    "event_name": row[1],
                    "status": row[2],
                    "created_at": row[3],
                    "payload": json.loads(row[4]),
                }
                for row in hook_rows
            ],
            "subagents": [
                _task_child_summary(task) for task in self.child_tasks(task_id)
            ],
        }

    def record_eval_case(self, client_id: str, record: dict[str, Any]) -> dict[str, Any]:
        stored = {
            "client_id": client_id,
            "name": record["name"],
            "source_task_id": record.get("source_task_id"),
            "failure_label": record.get("failure_label"),
            "input": record.get("input") or {},
            "expected": record.get("expected") or {},
            "verifier_command": record.get("verifier_command"),
            "evidence_refs": [str(item) for item in record.get("evidence_refs") or []],
            "status": record.get("status") or "active",
            "metadata": record.get("metadata") or {},
            "created_at": now(),
        }
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                insert into harness_eval_cases(client_id,name,source_task_id,failure_label,status,verifier_command,created_at,payload)
                values(?,?,?,?,?,?,?,?)
                """,
                (
                    client_id,
                    stored["name"],
                    stored["source_task_id"],
                    stored["failure_label"],
                    stored["status"],
                    stored["verifier_command"],
                    stored["created_at"],
                    json.dumps(stored, ensure_ascii=False),
                ),
            )
            stored["eval_case_id"] = int(cursor.lastrowid)
            conn.execute(
                "update harness_eval_cases set payload=? where eval_case_id=?",
                (json.dumps(stored, ensure_ascii=False), stored["eval_case_id"]),
            )
        if stored["source_task_id"]:
            self.append_event(
                stored["source_task_id"],
                "eval_case.recorded",
                stored,
                actor="eval",
                status=stored["status"],
            )
        return stored

    def list_eval_cases(self, client_id: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select payload from harness_eval_cases
                where client_id=?
                order by eval_case_id desc
                limit ?
                """,
                (client_id, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def create_loop(self, payload: dict[str, Any], next_run_at: str) -> dict[str, Any]:
        loop = {
            "loop_id": f"loop_{uuid4().hex[:12]}",
            "status": "active",
            "created_at": now(),
            "iteration": 0,
            "last_task_id": None,
            "next_run_at": next_run_at,
            **payload,
        }
        self.save_loop(loop)
        return loop

    def save_loop(self, loop: dict[str, Any]) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                insert into harness_loops(loop_id,status,next_run_at,payload) values(?,?,?,?)
                on conflict(loop_id) do update set
                  status=excluded.status,next_run_at=excluded.next_run_at,payload=excluded.payload
                """,
                (
                    loop["loop_id"],
                    loop["status"],
                    loop.get("next_run_at"),
                    json.dumps(loop, ensure_ascii=False),
                ),
            )
        return loop

    def get_loop(self, loop_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "select payload from harness_loops where loop_id=?", (loop_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_loops(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("select payload from harness_loops order by rowid desc").fetchall()
        return [json.loads(row[0]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("pragma journal_mode=wal")
        return conn

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {definition}")


class StateConflictError(RuntimeError):
    def __init__(self, task_id: str, expected: int, observed: int | None) -> None:
        super().__init__(f"state conflict for {task_id}: expected {expected}, observed {observed}")
        self.task_id = task_id
        self.expected = expected
        self.observed = observed


class LeaseConflictError(RuntimeError):
    def __init__(self, task_id: str, message: str) -> None:
        super().__init__(f"lease conflict for {task_id}: {message}")
        self.task_id = task_id
        self.message = message


def _timedelta(*, seconds: int) -> Any:
    from datetime import timedelta

    return timedelta(seconds=seconds)


def _task_child_summary(task: dict[str, Any]) -> dict[str, Any]:
    request = task.get("request") if isinstance(task.get("request"), dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    error = task.get("error") if isinstance(task.get("error"), dict) else None
    return {
        "task_id": task.get("task_id"),
        "parent_task_id": task.get("parent_task_id"),
        "client_id": task.get("client_id"),
        "status": task.get("status"),
        "state_version": task.get("state_version"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "thread_id": task.get("thread_id"),
        "join_policy": request.get("join_policy"),
        "agent": request.get("agent"),
        "model": request.get("model"),
        "workspace_mode": request.get("workspace_mode"),
        "cwd": request.get("cwd"),
        "prompt_preview": _truncate_text(str(request.get("prompt") or ""), 240),
        "last_message": _truncate_text(str(result.get("last_message") or ""), 500),
        "returncode": result.get("returncode"),
        "error": error,
    }


def _truncate_text(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
