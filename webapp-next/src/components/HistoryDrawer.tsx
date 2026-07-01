import { useEffect, useState } from "react";
import { listRuns, type AgentRun } from "../api";

// docs/25 P0-4: a slide-in run-history drawer. GET /api/agent/runs + listRuns()
// already existed but the UI never called them, so runs vanished on refresh /
// "새 대화". This projects the session's runs and lets the user reopen any of
// them (the parent reloads the run by id).

export interface HistoryEntry {
  runId: string;
  title: string;
  status: string;
  createdAt?: string;
}

export function useHistory(refreshKey: number): HistoryEntry[] {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  useEffect(() => {
    let cancelled = false;
    listRuns()
      .then((res) => {
        if (cancelled) return;
        setEntries((res.runs || []).map(toEntry));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);
  return entries;
}

function toEntry(run: AgentRun & { goal?: string; created_at?: string }): HistoryEntry {
  const input = (run as { input?: { free_text?: string; candidate_url?: string } }).input;
  const title =
    input?.free_text?.trim() ||
    input?.candidate_url?.trim() ||
    run.goal?.trim() ||
    "이름 없는 탐색";
  return {
    runId: run.run_id ?? "",
    title,
    status: run.status,
    createdAt: run.created_at,
  };
}

export function HistoryDrawer({
  open,
  entries,
  activeRunId,
  onSelect,
  onClose,
  onNew,
}: {
  open: boolean;
  entries: HistoryEntry[];
  activeRunId: string | null;
  onSelect: (runId: string) => void;
  onClose: () => void;
  onNew: () => void;
}) {
  return (
    <>
      <div className={`drawer-scrim ${open ? "open" : ""}`} onClick={onClose} />
      <aside className={`history-drawer ${open ? "open" : ""}`} aria-hidden={!open}>
        <div className="drawer-head">
          <span>탐색 기록</span>
          <button className="icon-btn" onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </div>
        <button className="drawer-new" onClick={onNew}>
          ＋ 새 대화
        </button>
        <div className="drawer-list">
          {entries.length === 0 ? (
            <div className="drawer-empty">아직 탐색 기록이 없습니다.</div>
          ) : (
            entries.map((e) => (
              <button
                key={e.runId}
                className={`drawer-item ${e.runId === activeRunId ? "active" : ""}`}
                onClick={() => onSelect(e.runId)}
              >
                <span className={`dot ${e.status}`} />
                <span className="drawer-item-title">{e.title}</span>
                <span className="drawer-item-time">{relTime(e.createdAt)}</span>
              </button>
            ))
          )}
        </div>
      </aside>
    </>
  );
}

function relTime(iso?: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const diff = Date.now() - t;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "방금";
  if (min < 60) return `${min}분 전`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  const day = Math.floor(hr / 24);
  return `${day}일 전`;
}
