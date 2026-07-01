import { useEffect, useRef, useState, useCallback } from "react";
import { getRun, isTerminal, type AgentRun } from "./api";

// Consumes the run's SSE stream: `agent_state` events (trace/observations/report)
// and `agent_delta` events (token-by-token streamed model text — docs/24 #5).

export interface RunStreamState {
  run: AgentRun | null;
  streamedText: string;
  connected: boolean;
  status: string | undefined;
}

export function useRunStream(runId: string | null): RunStreamState {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [streamedText, setStreamedText] = useState("");
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    setStreamedText("");
    setRun(null);
    if (!runId) return;

    let cancelled = false;
    // Seed with a plain fetch so we have state even before the first SSE tick.
    getRun(runId)
      .then((r) => {
        if (!cancelled) setRun(r);
      })
      .catch(() => undefined);

    const source = new EventSource(`/api/agent/runs/${encodeURIComponent(runId)}/events`);
    sourceRef.current = source;

    source.addEventListener("open", () => setConnected(true));

    source.addEventListener("agent_delta", (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as { text_delta?: string };
        if (data.text_delta) setStreamedText((prev) => prev + data.text_delta);
      } catch {
        /* ignore malformed delta */
      }
    });

    source.addEventListener("agent_state", (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as Partial<AgentRun> & {
          trace_delta?: unknown[];
        };
        setRun((prev) => mergeStateEvent(prev, data));
        if (isTerminal(data.status)) {
          // Pull the full run once terminal to get the complete report/trace.
          getRun(runId)
            .then((full) => !cancelled && setRun(full))
            .catch(() => undefined);
          source.close();
          setConnected(false);
        }
      } catch {
        /* ignore malformed state */
      }
    });

    source.addEventListener("error", () => {
      setConnected(false);
    });

    return () => {
      cancelled = true;
      source.close();
      sourceRef.current = null;
    };
  }, [runId]);

  return { run, streamedText, connected, status: run?.status };
}

// Merge an incremental agent_state event into the accumulated run. trace_delta
// carries only new entries; append them to what we already have.
function mergeStateEvent(
  prev: AgentRun | null,
  ev: Partial<AgentRun> & { trace_delta?: unknown[]; latest_trace?: unknown[] },
): AgentRun {
  const base: AgentRun = prev ?? { status: ev.status ?? "running", trace: [] };
  const existingTrace = base.trace ?? [];
  const delta = (ev.trace_delta ?? []) as AgentRun["trace"];
  const nextTrace =
    delta && delta.length
      ? dedupeTrace([...(existingTrace ?? []), ...delta])
      : existingTrace;
  return {
    ...base,
    ...ev,
    trace: nextTrace,
    final_report: ev.final_report ?? base.final_report,
    error: ev.error ?? base.error,
  } as AgentRun;
}

function dedupeTrace(entries: NonNullable<AgentRun["trace"]>): NonNullable<AgentRun["trace"]> {
  const seen = new Set<number>();
  const out: NonNullable<AgentRun["trace"]> = [];
  for (const e of entries) {
    if (!seen.has(e.step_index)) {
      seen.add(e.step_index);
      out.push(e);
    }
  }
  return out.sort((a, b) => a.step_index - b.step_index);
}

export function useControls() {
  const cancel = useCallback(() => undefined, []);
  return { cancel };
}
