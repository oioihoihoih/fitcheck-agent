import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  cancelRun,
  createRun,
  getStatus,
  isTerminal,
  replyToRun,
  type AgentRun,
  type ApiStatus,
  type CreateRunBody,
  type MatchResult,
  type RunControls,
  type TraceEntry,
} from "./api";
import { useRunStream } from "./useRunStream";
import { AgentActivity } from "./components/AgentActivity";
import { Candidates } from "./components/Candidates";
import { ControlsDrawer, type RunControlsForm } from "./components/ControlsDrawer";
import { Markdown } from "./components/Markdown";

interface Turn {
  id: string;
  role: "user" | "agent";
  text: string;
  streaming?: boolean;
  // agent turns own a snapshot of the trace + candidates that produced them
  trace?: TraceEntry[];
  candidates?: MatchResult[];
  reportType?: string;
  grounded?: boolean;
  live?: boolean; // the currently-running agent turn
}

const EXAMPLES = [
  { icon: "◆", text: "검은색 와이드 슬랙스랑 비슷한거 찾아줘" },
  { icon: "◆", text: "흰색 반팔 티셔츠 무지 코튼 기본핏으로" },
  { icon: "◆", text: "내가 좋아하는 후드티랑 핏이 비슷한 옷" },
];

export function App() {
  const [status, setStatus] = useState<ApiStatus | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [banner, setBanner] = useState<{ kind: "error" | "info" | "warn"; text: string } | null>(null);
  const [showControls, setShowControls] = useState(false);
  const [form, setForm] = useState<RunControlsForm>({
    maxTurns: "",
    toolBudget: "",
    searchProvider: "",
    refreshWeb: false,
  });
  const streamRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const liveTurnId = useRef<string | null>(null);

  const { run, streamedText, status: runStatus } = useRunStream(runId);
  const running = !!runId && !isTerminal(runStatus);

  useEffect(() => {
    getStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  // Feed streamed text + live trace/candidates into the live agent turn.
  useEffect(() => {
    if (!liveTurnId.current) return;
    const id = liveTurnId.current;
    setTurns((prev) =>
      prev.map((t) =>
        t.id === id
          ? {
              ...t,
              text: streamedText || t.text,
              streaming: running,
              live: running,
              trace: run?.trace ?? t.trace,
              candidates: run?.state?.match_results ?? t.candidates,
            }
          : t,
      ),
    );
  }, [streamedText, running, run]);

  // Finalize the live agent turn when the run reaches a terminal state.
  useEffect(() => {
    if (!run || !isTerminal(run.status) || !liveTurnId.current) return;
    const id = liveTurnId.current;
    const finalText = finalAgentText(run);
    setTurns((prev) =>
      prev.map((t) =>
        t.id === id
          ? {
              ...t,
              text: finalText || t.text,
              streaming: false,
              live: false,
              trace: run.trace ?? t.trace,
              candidates: run.state?.match_results ?? t.candidates,
              reportType: run.final_report?.report_type,
              grounded: run.final_report?.grounded,
            }
          : t,
      ),
    );
    liveTurnId.current = null;
    if (run.status === "cancelled") setBanner({ kind: "info", text: "실행을 취소했습니다." });
    else if (run.status === "failed" && run.error?.code)
      setBanner({ kind: "warn", text: `종료 코드: ${run.error.code}` });
    else if (run.status === "configuration_required")
      setBanner({ kind: "error", text: "LLM 설정이 필요합니다. 서버 구성을 확인하세요." });
  }, [run]);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  // Auto-grow textarea.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  }, [input]);

  const llmConnected = status?.llm?.status === "connected";

  const controls = useMemo<RunControls | undefined>(() => {
    const c: RunControls = {};
    if (form.maxTurns) c.max_turns = Number(form.maxTurns);
    if (form.toolBudget) c.tool_budget = Number(form.toolBudget);
    if (form.searchProvider) c.search_provider = form.searchProvider;
    return Object.keys(c).length ? c : undefined;
  }, [form]);

  async function send() {
    const text = input.trim();
    if (!text) return;
    setInput("");
    setBanner(null);

    const userTurn: Turn = { id: rid(), role: "user", text };
    const agentId = rid();
    liveTurnId.current = agentId;
    setTurns((prev) => [
      ...prev,
      userTurn,
      { id: agentId, role: "agent", text: "", streaming: true, live: true, trace: [], candidates: [] },
    ]);

    try {
      if (running && runId) {
        const res = await replyToRun(runId, text);
        if (res.queued)
          setBanner({
            kind: "info",
            text: `진행 중 작업에 메시지를 전달했습니다 (대기 ${res.queue_depth ?? 1}건). 다음 안전 지점에서 반영됩니다.`,
          });
      } else if (runId && isTerminal(runStatus)) {
        const res = await replyToRun(runId, text);
        if (res.run_id) setRunId(res.run_id);
      } else {
        const body: CreateRunBody = { free_text: text, refresh_web: form.refreshWeb };
        if (controls) body.controls = controls;
        const res = await createRun(body);
        if (res.status === "configuration_required") {
          setBanner({ kind: "error", text: "LLM 설정이 필요합니다." });
          liveTurnId.current = null;
          return;
        }
        if (res.run_id) setRunId(res.run_id);
      }
    } catch (err) {
      setBanner({ kind: "error", text: err instanceof ApiError ? err.message : "요청에 실패했습니다." });
      const dropId = agentId;
      liveTurnId.current = null;
      setTurns((prev) => prev.filter((t) => t.id !== dropId));
    }
  }

  async function onCancel() {
    if (!runId) return;
    try {
      await cancelRun(runId);
      setBanner({ kind: "info", text: "취소를 요청했습니다. 다음 안전 지점에서 멈춥니다." });
    } catch (err) {
      setBanner({ kind: "error", text: err instanceof ApiError ? err.message : "취소 실패." });
    }
  }

  function newSession() {
    setRunId(null);
    setTurns([]);
    setBanner(null);
    liveTurnId.current = null;
  }

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand">
          <div className="mark">F</div>
          <div className="name">
            FitCheck
            <span className="sub">AI 의류 탐색 에이전트</span>
          </div>
        </div>
        <div className="topbar-right">
          <span className={`llm-pill ${llmConnected ? "connected" : status ? "error" : ""}`}>
            <span className="dot" />
            {llmConnected
              ? `${String(status?.llm?.model ?? "LLM")}`
              : status
                ? "LLM 미설정"
                : "연결 확인 중"}
          </span>
          {turns.length > 0 && (
            <button className="icon-btn" onClick={newSession}>
              새 대화
            </button>
          )}
        </div>
      </div>

      <div className="stream" ref={streamRef}>
        <div className="stream-inner">
          {turns.length === 0 ? (
            <div className="hero">
              <div className="glyph">✦</div>
              <h1>어떤 옷과 비슷한 걸 찾아드릴까요?</h1>
              <p>
                평소 만족스럽게 입는 옷의 핏·소재·사이즈·실루엣·디자인을 알려주세요. 검색부터 수집·정규화·점수화까지
                실시간으로 보여드리고, 근거가 검증된 후보를 순위로 제시합니다.
              </p>
              <div className="examples">
                {EXAMPLES.map((ex) => (
                  <button key={ex.text} className="example-card" onClick={() => { setInput(ex.text); taRef.current?.focus(); }}>
                    <span className="ico">{ex.icon}</span>
                    {ex.text}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            turns.map((t) =>
              t.role === "user" ? (
                <div className="turn user" key={t.id}>
                  <div className="bubble">{t.text}</div>
                </div>
              ) : (
                <div className="turn agent" key={t.id}>
                  <div className="who">FitCheck</div>
                  {(t.trace?.length || t.streaming) && (
                    <AgentActivity trace={t.trace ?? []} running={!!t.live} />
                  )}
                  {(t.text || t.streaming) && (
                    <div className="bubble">
                      {t.streaming ? (
                        <>
                          {t.text}
                          <span className="caret" />
                        </>
                      ) : (
                        <Markdown text={t.text} />
                      )}
                    </div>
                  )}
                  {t.candidates && t.candidates.length > 0 && (
                    <Candidates
                      candidates={t.candidates}
                      report={{ report_type: t.reportType, grounded: t.grounded }}
                    />
                  )}
                </div>
              ),
            )
          )}
          {banner && <div className={`banner ${banner.kind}`}>{banner.text}</div>}
        </div>
      </div>

      <div className="composer-wrap">
        {showControls && <ControlsDrawer form={form} onChange={setForm} disabled={running} />}
        <div className="composer">
          {running && (
            <div className="running-strip">
              <span className="spinner" />
              에이전트 실행 중 · 메시지를 보내면 진행 중 작업에 반영됩니다
            </div>
          )}
          <div className="input-shell">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              placeholder={running ? "진행 방향을 바꾸거나 추가 요청을 입력하세요…" : "예: 검은색 와이드 슬랙스랑 비슷한거 찾아줘"}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            {running && (
              <button className="stop-btn" onClick={onCancel}>
                ■ 취소
              </button>
            )}
            <button className="send-btn" onClick={send} disabled={!input.trim()} aria-label="보내기">
              {running ? "↺" : "↑"}
            </button>
          </div>
          <div className="composer-foot">
            <button className="settings-toggle" onClick={() => setShowControls((s) => !s)}>
              ⚙ 실행 설정 {showControls ? "닫기" : ""}
            </button>
            <span>Enter 전송 · Shift+Enter 줄바꿈{runId ? ` · ${runId.slice(0, 12)}` : ""}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function finalAgentText(run: AgentRun): string {
  const fr = run.final_report;
  if (fr?.llm_final_text) return fr.llm_final_text;
  if (fr?.summary) return fr.summary;
  if (run.status === "cancelled") return "요청에 따라 실행을 취소했습니다.";
  if (run.status === "failed") return "죄송합니다. 이번 요청을 완료하지 못했습니다. 다시 시도하거나 다른 표현으로 요청해 주세요.";
  return "";
}

function rid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
