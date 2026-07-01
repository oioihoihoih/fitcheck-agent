from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


# A run-loop lease is considered stale (reclaimable) after this many seconds
# without a heartbeat, so a crashed/abandoned run does not stay locked forever.
# docs/24 gaps #1 (cancel/reaping) and #15 (concurrency guard).
RUN_LEASE_STALE_SECONDS = 180.0


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _lease_is_fresh(lease: dict[str, Any] | None) -> bool:
    """True if a run-loop lease is held and still within the heartbeat window."""
    if not isinstance(lease, dict) or not lease.get("token"):
        return False
    heartbeat = _parse_iso(lease.get("heartbeat_at") or lease.get("acquired_at"))
    if heartbeat is None:
        return False
    age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    return age < RUN_LEASE_STALE_SECONDS


class JsonAgentRunStore:
    """JSON-backed run store for the local production-agent prototype."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def create_run(self, payload: dict[str, Any], llm_status: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        run_id = f"run_{uuid4().hex[:12]}"
        now = _now()
        run = {
            "run_id": run_id,
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "goal": payload.get("goal") or "내 옷의 마음에 드는 요소 기반 유사 의류 탐색",
            "input": deepcopy(payload),
            "llm": {
                "provider": llm_status.get("provider"),
                "model": llm_status.get("model"),
                "api_style": llm_status.get("api_style"),
                "tool_execution": llm_status.get("tool_execution"),
                "hosted_tools_supported": llm_status.get("hosted_tools_supported"),
                "local_function_tools_supported": llm_status.get("local_function_tools_supported"),
                "status": llm_status.get("status"),
                "external_api_called": False,
            },
            "state": {
                "previous_response_id": None,
                "pending_tool_outputs": [],
                "observations": [],
                "missing_info": [],
                "match_results": [],
            },
            "trace": [],
            "final_report": None,
            "error": None,
        }
        data["runs"][run_id] = run
        self._save(data)
        return deepcopy(run)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return deepcopy(self._load()["runs"].get(run_id))

    def update_run(self, run: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        run = deepcopy(run)
        _normalize_execution_plan_for_storage(run)
        existing = data["runs"].get(run["run_id"])
        if existing:
            _preserve_concurrent_control_signals(run, existing)
        run["updated_at"] = _now()
        data["runs"][run["run_id"]] = run
        self._save(data)
        return deepcopy(run)

    def list_runs(self) -> list[dict[str, Any]]:
        runs = list(self._load()["runs"].values())
        return sorted(runs, key=lambda item: item.get("created_at") or "", reverse=True)

    def try_acquire_run_lease(self, run_id: str) -> tuple[bool, dict[str, Any] | None]:
        data = self._load()
        run = data["runs"].get(run_id)
        if not run:
            return False, None
        state = run.setdefault("state", {})
        if _lease_is_fresh(state.get("active_lease")):
            return False, deepcopy(run)
        now = _now()
        state["active_lease"] = {"token": uuid4().hex, "acquired_at": now, "heartbeat_at": now}
        run["updated_at"] = now
        self._save(data)
        return True, deepcopy(run)

    def heartbeat_run_lease(self, run: dict[str, Any]) -> None:
        lease = (run.get("state") or {}).get("active_lease")
        if isinstance(lease, dict) and lease.get("token"):
            lease["heartbeat_at"] = _now()

    def request_cancel(self, run_id: str) -> dict[str, Any] | None:
        data = self._load()
        run = data["runs"].get(run_id)
        if not run:
            return None
        run.setdefault("state", {})["cancel_requested"] = {"requested_at": _now(), "source": "user"}
        run["updated_at"] = _now()
        self._save(data)
        return deepcopy(run)

    def clear_steering_inbox(self, run_id: str | None, consumed_keys: list[str]) -> None:
        if not run_id:
            return
        consumed = set(consumed_keys or [])
        data = self._load()
        run = data["runs"].get(run_id)
        if not run:
            return
        state = run.setdefault("state", {})
        inbox = state.get("pending_user_messages") or []
        state["pending_user_messages"] = [
            m for m in inbox if json.dumps(m, ensure_ascii=False, sort_keys=True) not in consumed
        ]
        run["updated_at"] = _now()
        self._save(data)

    def append_model_text_deltas(self, run_id: str | None, deltas: list[str]) -> None:
        if not run_id or not deltas:
            return
        data = self._load()
        run = data["runs"].get(run_id)
        if not run:
            return
        state = run.setdefault("state", {})
        existing = state.get("model_text_deltas") or []
        existing.extend(deltas)
        if len(existing) > 4000:
            existing = existing[-4000:]
        state["model_text_deltas"] = existing
        run["updated_at"] = _now()
        self._save(data)

    def run_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        run = self.get_run(run_id)
        if not run:
            return []
        events = []
        for step in run.get("trace") or []:
            seq = int(step.get("step_index") or 0)
            if seq <= after:
                continue
            events.append(_trace_step_to_event(run, step))
        return events

    def upsert_closet_item(self, session_id: str, item: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        items = data.setdefault("closet_items", [])
        item_id = item.get("id") or f"closet_{uuid4().hex[:12]}"
        now = _now()
        record = {
            "id": item_id,
            "session_id": session_id,
            "created_at": item.get("created_at") or now,
            "updated_at": now,
            **item,
        }
        for index, existing in enumerate(items):
            if existing.get("id") == item_id and existing.get("session_id") == session_id:
                record["created_at"] = existing.get("created_at") or record["created_at"]
                items[index] = record
                self._save(data)
                return deepcopy(record)
        items.append(record)
        self._save(data)
        return deepcopy(record)

    def list_closet_items(self, session_id: str) -> list[dict[str, Any]]:
        items = self._load().get("closet_items", [])
        return [deepcopy(item) for item in items if item.get("session_id") == session_id]

    def get_closet_item(self, session_id: str, item_id: str) -> dict[str, Any] | None:
        for item in self._load().get("closet_items", []):
            if item.get("id") == item_id and item.get("session_id") == session_id:
                return deepcopy(item)
        return None

    def delete_closet_item(self, session_id: str, item_id: str) -> bool:
        data = self._load()
        items = data.get("closet_items", [])
        kept = [
            it for it in items
            if not (it.get("id") == item_id and it.get("session_id") == session_id)
        ]
        if len(kept) == len(items):
            return False
        data["closet_items"] = kept
        self._save(data)
        return True

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"runs": {}}
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("runs", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_path.replace(self.path)


class SqliteAgentRunStore:
    """SQLite-backed run store used by the web/API runtime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def create_run(self, payload: dict[str, Any], llm_status: dict[str, Any]) -> dict[str, Any]:
        run_id = f"run_{uuid4().hex[:12]}"
        now = _now()
        run = {
            "run_id": run_id,
            "session_id": payload.get("session_id"),
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "goal": payload.get("goal") or "내 옷의 마음에 드는 요소 기반 유사 의류 탐색",
            "input": deepcopy(payload),
            "llm": {
                "provider": llm_status.get("provider"),
                "model": llm_status.get("model"),
                "api_style": llm_status.get("api_style"),
                "tool_execution": llm_status.get("tool_execution"),
                "hosted_tools_supported": llm_status.get("hosted_tools_supported"),
                "local_function_tools_supported": llm_status.get("local_function_tools_supported"),
                "status": llm_status.get("status"),
                "external_api_called": False,
            },
            "state": {
                "previous_response_id": None,
                "pending_tool_outputs": [],
                "observations": [],
                "missing_info": [],
                "match_results": [],
            },
            "trace": [],
            "final_report": None,
            "error": None,
        }
        self.update_run(run)
        return deepcopy(run)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            with conn:
                row = conn.execute("select payload from agent_runs where run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def update_run(self, run: dict[str, Any]) -> dict[str, Any]:
        run = deepcopy(run)
        _normalize_execution_plan_for_storage(run)
        run["updated_at"] = _now()
        session_id = run.get("session_id") or run.get("input", {}).get("session_id")
        run["session_id"] = session_id
        with closing(self._connect()) as conn:
            with conn:
                existing_row = conn.execute(
                    "select payload from agent_runs where run_id = ?", (run["run_id"],)
                ).fetchone()
                if existing_row:
                    _preserve_concurrent_control_signals(
                        run, json.loads(existing_row[0])
                    )
                payload = json.dumps(run, ensure_ascii=False)
                conn.execute(
                    """
                    insert into agent_runs
                      (run_id, session_id, status, created_at, updated_at, payload)
                    values (?, ?, ?, ?, ?, ?)
                    on conflict(run_id) do update set
                      session_id = excluded.session_id,
                      status = excluded.status,
                      updated_at = excluded.updated_at,
                      payload = excluded.payload
                    """,
                    (
                        run["run_id"],
                        session_id,
                        run.get("status"),
                        run.get("created_at"),
                        run.get("updated_at"),
                        payload,
                    ),
                )
                self._sync_trace_events(conn, run)
        return deepcopy(run)

    def try_acquire_run_lease(self, run_id: str) -> tuple[bool, dict[str, Any] | None]:
        """Atomically acquire the run-loop lease (docs/24 #15).

        Returns (acquired, run). acquired is False when another loop already
        holds a fresh lease. Reclaims a stale lease from a crashed/abandoned run.
        """
        token = uuid4().hex
        now = _now()
        with closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    "select payload from agent_runs where run_id = ?", (run_id,)
                ).fetchone()
                if not row:
                    return False, None
                run = json.loads(row[0])
                state = run.setdefault("state", {})
                if _lease_is_fresh(state.get("active_lease")):
                    return False, run
                state["active_lease"] = {
                    "token": token,
                    "acquired_at": now,
                    "heartbeat_at": now,
                }
                run["updated_at"] = now
                conn.execute(
                    "update agent_runs set updated_at = ?, payload = ? where run_id = ?",
                    (now, json.dumps(run, ensure_ascii=False), run_id),
                )
        return True, run

    def heartbeat_run_lease(self, run: dict[str, Any]) -> None:
        lease = (run.get("state") or {}).get("active_lease")
        if isinstance(lease, dict) and lease.get("token"):
            lease["heartbeat_at"] = _now()

    def request_cancel(self, run_id: str) -> dict[str, Any] | None:
        """Set the cancel flag so the run loop stops at its next safe point."""
        now = _now()
        with closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    "select payload from agent_runs where run_id = ?", (run_id,)
                ).fetchone()
                if not row:
                    return None
                run = json.loads(row[0])
                run.setdefault("state", {})["cancel_requested"] = {
                    "requested_at": now,
                    "source": "user",
                }
                run["updated_at"] = now
                conn.execute(
                    "update agent_runs set updated_at = ?, payload = ? where run_id = ?",
                    (now, json.dumps(run, ensure_ascii=False), run_id),
                )
        return run

    def clear_steering_inbox(self, run_id: str | None, consumed_keys: list[str]) -> None:
        """Remove consumed steering messages from the stored inbox (docs/24 #2/#3)."""
        if not run_id:
            return
        consumed = set(consumed_keys or [])
        now = _now()
        with closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    "select payload from agent_runs where run_id = ?", (run_id,)
                ).fetchone()
                if not row:
                    return
                run = json.loads(row[0])
                state = run.get("state") or {}
                inbox = state.get("pending_user_messages") or []
                remaining = [
                    m
                    for m in inbox
                    if json.dumps(m, ensure_ascii=False, sort_keys=True) not in consumed
                ]
                state["pending_user_messages"] = remaining
                run["state"] = state
                run["updated_at"] = now
                conn.execute(
                    "update agent_runs set updated_at = ?, payload = ? where run_id = ?",
                    (now, json.dumps(run, ensure_ascii=False), run_id),
                )

    def append_model_text_deltas(self, run_id: str | None, deltas: list[str]) -> None:
        """Append streamed model text deltas to run state (docs/24 #5)."""
        if not run_id or not deltas:
            return
        now = _now()
        with closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    "select payload from agent_runs where run_id = ?", (run_id,)
                ).fetchone()
                if not row:
                    return
                run = json.loads(row[0])
                state = run.setdefault("state", {})
                existing = state.get("model_text_deltas") or []
                existing.extend(deltas)
                # Bound memory: keep only a rolling window of recent deltas.
                if len(existing) > 4000:
                    existing = existing[-4000:]
                state["model_text_deltas"] = existing
                run["updated_at"] = now
                conn.execute(
                    "update agent_runs set updated_at = ?, payload = ? where run_id = ?",
                    (now, json.dumps(run, ensure_ascii=False), run_id),
                )

    def list_runs(self, session_id: str | None = None) -> list[dict[str, Any]]:
        query = "select payload from agent_runs"
        args: tuple[Any, ...] = ()
        if session_id:
            query += " where session_id = ?"
            args = (session_id,)
        query += " order by created_at desc"
        with closing(self._connect()) as conn:
            with conn:
                rows = conn.execute(query, args).fetchall()
        return [json.loads(row[0]) for row in rows]

    def run_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            with conn:
                rows = conn.execute(
                    """
                    select event_id, run_id, session_id, seq, created_at, event_type, name, status, payload
                    from agent_run_events
                    where run_id = ? and seq > ?
                    order by seq asc
                    """,
                    (run_id, after),
                ).fetchall()
        return [
            {
                "event_id": row[0],
                "run_id": row[1],
                "session_id": row[2],
                "seq": row[3],
                "created_at": row[4],
                "event_type": row[5],
                "name": row[6],
                "status": row[7],
                "payload": json.loads(row[8]),
            }
            for row in rows
        ]

    def upsert_closet_item(self, session_id: str, item: dict[str, Any]) -> dict[str, Any]:
        item = deepcopy(item)
        item_id = item.get("id") or f"closet_{uuid4().hex[:12]}"
        now = _now()
        record = {
            "id": item_id,
            "session_id": session_id,
            "created_at": item.get("created_at") or now,
            "updated_at": now,
            **item,
        }
        payload = json.dumps(record, ensure_ascii=False)
        with closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    "select created_at from closet_items where item_id = ? and session_id = ?",
                    (item_id, session_id),
                ).fetchone()
                if row:
                    record["created_at"] = row[0]
                    record["updated_at"] = now
                    payload = json.dumps(record, ensure_ascii=False)
                conn.execute(
                    """
                    insert into closet_items
                      (item_id, session_id, status, created_at, updated_at, payload)
                    values (?, ?, ?, ?, ?, ?)
                    on conflict(item_id, session_id) do update set
                      status = excluded.status,
                      updated_at = excluded.updated_at,
                      payload = excluded.payload
                    """,
                    (
                        item_id,
                        session_id,
                        record.get("status") or "confirmed",
                        record.get("created_at"),
                        record.get("updated_at"),
                        payload,
                    ),
                )
        return deepcopy(record)

    def list_closet_items(self, session_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            with conn:
                rows = conn.execute(
                    "select payload from closet_items where session_id = ? order by updated_at desc",
                    (session_id,),
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_closet_item(self, session_id: str, item_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    "select payload from closet_items where item_id = ? and session_id = ?",
                    (item_id, session_id),
                ).fetchone()
        return json.loads(row[0]) if row else None

    def delete_closet_item(self, session_id: str, item_id: str) -> bool:
        with closing(self._connect()) as conn:
            with conn:
                cur = conn.execute(
                    "delete from closet_items where item_id = ? and session_id = ?",
                    (item_id, session_id),
                )
                return cur.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("pragma journal_mode = wal")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    create table if not exists agent_runs (
                      run_id text primary key,
                      session_id text,
                      status text not null,
                      created_at text not null,
                      updated_at text not null,
                      payload text not null
                    )
                    """
                )
                conn.execute("create index if not exists idx_agent_runs_session on agent_runs(session_id)")
                conn.execute("create index if not exists idx_agent_runs_created on agent_runs(created_at)")
                conn.execute(
                    """
                    create table if not exists closet_items (
                      item_id text not null,
                      session_id text not null,
                      status text not null,
                      created_at text not null,
                      updated_at text not null,
                      payload text not null,
                      primary key (item_id, session_id)
                    )
                    """
                )
                conn.execute("create index if not exists idx_closet_items_session on closet_items(session_id)")
                conn.execute("create index if not exists idx_closet_items_updated on closet_items(updated_at)")
                conn.execute(
                    """
                    create table if not exists agent_run_events (
                      event_id integer primary key autoincrement,
                      run_id text not null,
                      session_id text,
                      seq integer not null,
                      created_at text not null,
                      event_type text not null,
                      name text,
                      status text,
                      payload text not null,
                      unique(run_id, seq)
                    )
                    """
                )
                conn.execute("create index if not exists idx_agent_run_events_run_seq on agent_run_events(run_id, seq)")
                conn.execute("create index if not exists idx_agent_run_events_session on agent_run_events(session_id)")

    def _sync_trace_events(self, conn: sqlite3.Connection, run: dict[str, Any]) -> None:
        for step in run.get("trace") or []:
            event = _trace_step_to_event(run, step)
            conn.execute(
                """
                insert or ignore into agent_run_events
                  (run_id, session_id, seq, created_at, event_type, name, status, payload)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["run_id"],
                    event.get("session_id"),
                    event["seq"],
                    event["created_at"],
                    event["event_type"],
                    event.get("name"),
                    event.get("status"),
                    json.dumps(event["payload"], ensure_ascii=False),
                ),
            )


def build_agent_run_store(path: Path) -> JsonAgentRunStore | SqliteAgentRunStore:
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return SqliteAgentRunStore(path)
    return JsonAgentRunStore(path)


AgentRunStore = JsonAgentRunStore


def _preserve_concurrent_control_signals(
    incoming: dict[str, Any], stored: dict[str, Any]
) -> None:
    """Keep control signals a concurrent request wrote to the store from being
    clobbered by a mid-loop checkpoint write (docs/24 #1/#2/#3/#5).

    A running-status write is a checkpoint from the loop's in-memory run, which
    is unaware of a /cancel or /reply that landed on the store meanwhile. Merge
    those back. Terminal-status writes (cancelled/completed/failed) are
    intentional and always win for cancel/steering, so those are preserved only
    while running. Streamed text deltas, however, are written to the store by
    the streaming callback and are absent from the loop's in-memory run, so they
    must be preserved on ALL writes (including terminal) or the final blob wipes
    the progressive text the SSE client was reading.
    """
    in_state = incoming.setdefault("state", {})
    stored_state = stored.get("state") or {}
    # Streamed model text deltas: preserve on every write (docs/24 #5).
    stored_deltas = stored_state.get("model_text_deltas") or []
    in_deltas = in_state.get("model_text_deltas") or []
    if len(stored_deltas) > len(in_deltas):
        in_state["model_text_deltas"] = stored_deltas
    if incoming.get("status") != "running":
        return
    # Cancel flag: never lose a pending cancel during a running checkpoint.
    if stored_state.get("cancel_requested") and not in_state.get("cancel_requested"):
        in_state["cancel_requested"] = stored_state["cancel_requested"]
    # Steering inbox: merge stored messages the in-memory run hasn't consumed.
    stored_inbox = stored_state.get("pending_user_messages") or []
    if stored_inbox:
        current = in_state.get("pending_user_messages") or []
        seen = {json.dumps(m, ensure_ascii=False, sort_keys=True) for m in current}
        for msg in stored_inbox:
            key = json.dumps(msg, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                current.append(msg)
                seen.add(key)
        in_state["pending_user_messages"] = current


def _normalize_execution_plan_for_storage(run: dict[str, Any]) -> None:
    plan = run.get("state", {}).get("execution_plan")
    if not isinstance(plan, dict):
        return
    steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    recovered_failure_count = sum(1 for step in steps if step.get("status") == "failed")
    if recovered_failure_count:
        plan["recovered_failure_count"] = recovered_failure_count
    else:
        plan.pop("recovered_failure_count", None)
    if run.get("status") == "completed" and run.get("final_report"):
        plan["status"] = "completed"
        plan["terminal_status"] = "completed"
    elif run.get("status") == "waiting_for_user":
        plan["status"] = "waiting_for_user"
        plan["terminal_status"] = "waiting_for_user"
    elif run.get("status") in {"failed", "configuration_required"}:
        plan["status"] = run.get("status")
        plan["terminal_status"] = run.get("status")
    else:
        plan.pop("terminal_status", None)


def append_trace(
    run: dict[str, Any],
    *,
    step_type: str,
    status: str,
    name: str,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
) -> None:
    run.setdefault("trace", []).append(
        {
            "step_index": len(run.setdefault("trace", [])) + 1,
            "step_type": step_type,
            "name": name,
            "status": status,
            "input": input_payload or {},
            "output": output_payload or {},
            "created_at": _now(),
        }
    )


def _trace_step_to_event(run: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    seq = int(step.get("step_index") or 0)
    payload = deepcopy(step)
    return {
        "event_id": None,
        "run_id": run.get("run_id"),
        "session_id": run.get("session_id") or (run.get("input") or {}).get("session_id"),
        "seq": seq,
        "created_at": step.get("created_at") or _now(),
        "event_type": step.get("step_type") or "trace",
        "name": step.get("name"),
        "status": step.get("status"),
        "payload": payload,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
