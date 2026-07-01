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
  type RunControls,
} from "./api";
import { useRunStream } from "./useRunStream";
import { SidePanel, type RunControlsForm } from "./components/SidePanel";

interface ChatMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  streaming?: boolean;
}

const EXAMPLES = [
  "검은색 와이드 슬랙스랑 비슷한거 찾아줘",
  "흰색 반팔 티셔츠 무지 코튼 기본핏으로",
  "내가 좋아하는 후드티랑 핏이 비슷한 옷",
];

export function App() {
  const [status, setStatus] = useState<ApiStatus | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [banner, setBanner] = useState<{ kind: "error" | "info" | "warn"; text: string } | null>(null);
  const [form, setForm] = useState<RunControlsForm>({
    maxTurns: "",
    toolBudget: "",
    searchProvider: "",
    refreshWeb: false,
  });
  const chatRef = useRef<HTMLDivElement>(null);
  const agentMsgId = useRef<string | null>(null);

  const { run, streamedText, status: runStatus } = useRunStream(runId);
  const running = !!runId && !isTerminal(runStatus);

  // Load LLM/provider status once.
  useEffect(() => {
    getStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  // Reflect streamed text into the live agent message.
  useEffect(() => {
    if (!streamedText || !agentMsgId.current) return;
    const id = agentMsgId.current;
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, text: streamedText, streaming: running } : m)),
    );
  }, [streamedText, running]);

  // When the run reaches a terminal state, finalize the agent message from the report.
  useEffect(() => {
    if (!run || !isTerminal(run.status) || !agentMsgId.current) return;
    const id = agentMsgId.current;
    const finalText = finalAgentText(run);
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, text: finalText || m.text, streaming: false } : m)),
    );
    agentMsgId.current = null;
    if (run.status === "failed" && run.error?.code) {
      setBanner({ kind: "warn", text: `실행이 종료되었습니다: ${run.error.code}` });
    } else if (run.status === "configuration_required") {
      setBanner({ kind: "error", text: "LLM 설정이 필요합니다. 서버의 OPENAI_API_KEY/FITCHECK_LLM_* 구성을 확인하세요." });
    }
  }, [run]);

  // Auto-scroll chat.
  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

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

    const userMsg: ChatMessage = { id: rid(), role: "user", text };
    const agentId = rid();
    agentMsgId.current = agentId;
    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: agentId, role: "agent", text: "", streaming: true },
    ]);

    try {
      if (running && runId) {
        // Steering: enqueue into the running run (docs/24 #2/#3).
        const res = await replyToRun(runId, text);
        if (res.queued) {
          setBanner({
            kind: "info",
            text: `진행 중인 작업에 메시지를 큐로 전달했습니다 (대기 ${res.queue_depth ?? 1}건). 다음 안전 지점에서 반영됩니다.`,
          });
        }
      } else if (runId && isTerminal(runStatus)) {
        // Follow-up turn on a finished run (multi-turn context — docs/24 #7/#8/#16).
        const res = await replyToRun(runId, text);
        if (res.run_id) setRunId(res.run_id);
      } else {
        const body: CreateRunBody = { free_text: text, refresh_web: form.refreshWeb };
        if (controls) body.controls = controls;
        const res = await createRun(body);
        if (res.status === "configuration_required") {
          setBanner({ kind: "error", text: "LLM 설정이 필요합니다. 서버 구성을 확인하세요." });
          agentMsgId.current = null;
          return;
        }
        if (res.run_id) setRunId(res.run_id);
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "요청에 실패했습니다.";
      setBanner({ kind: "error", text: msg });
      agentMsgId.current = null;
      setMessages((prev) => prev.filter((m) => m.id !== agentId));
    }
  }

  async function onCancel() {
    if (!runId) return;
    try {
      await cancelRun(runId);
      setBanner({ kind: "info", text: "취소를 요청했습니다. 에이전트가 다음 안전 지점에서 멈춥니다." });
    } catch (err) {
      setBanner({ kind: "error", text: err instanceof ApiError ? err.message : "취소에 실패했습니다." });
    }
  }

  function newSession() {
    setRunId(null);
    setMessages([]);
    setBanner(null);
    agentMsgId.current = null;
  }

  return (
    <div className="app">
      <div className="main">
        <header className="header">
          <div>
            <h1>FitCheck</h1>
            <span className="tag">AI 의류 탐색 에이전트</span>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span className={`llm-pill ${llmConnected ? "connected" : status ? "error" : ""}`}>
              <span className="dot" />
              {llmConnected
                ? `LLM 연결됨 · ${String(status?.llm?.model ?? "")}`
                : status
                  ? "LLM 미설정"
                  : "상태 확인 중"}
            </span>
            <button className="btn ghost" onClick={newSession}>
              새 세션
            </button>
          </div>
        </header>

        {banner && <div className={`banner ${banner.kind}`}>{banner.text}</div>}

        <div className="chat" ref={chatRef}>
          {messages.length === 0 ? (
            <div className="empty-state">
              <h2>어떤 옷과 비슷한 걸 찾아드릴까요?</h2>
              <p>
                평소 만족스럽게 입는 옷의 핏·소재·사이즈·실루엣·디자인을 알려주세요. 실시간으로 검색·수집·정규화·점수화 과정을
                보여드리고, 근거가 검증된 후보를 순위로 제시합니다.
              </p>
              <div className="examples">
                {EXAMPLES.map((ex) => (
                  <button key={ex} className="example-chip" onClick={() => setInput(ex)}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={`msg ${m.role} ${m.streaming ? "streaming" : ""}`}>
                <div className="role">{m.role === "user" ? "나" : "FitCheck"}</div>
                {m.text || (m.streaming ? "생각 중…" : "")}
                {m.streaming && <span className="caret" />}
              </div>
            ))
          )}
        </div>

        <div className="composer">
          {running && (
            <div className="status-row">
              <span className="spinner" />
              에이전트 실행 중 · 메시지를 보내면 진행 중 작업에 반영됩니다
            </div>
          )}
          <div className="composer-row">
            <textarea
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
              <button className="btn danger" onClick={onCancel}>
                취소
              </button>
            )}
            <button className="btn primary" onClick={send} disabled={!input.trim()}>
              {running ? "전달" : "보내기"}
            </button>
          </div>
          <div className="composer-hint">
            <span>Enter 전송 · Shift+Enter 줄바꿈</span>
            {runId && <span>run: {runId.slice(0, 12)}</span>}
          </div>
        </div>
      </div>

      <SidePanel run={run} form={form} onFormChange={setForm} disabled={running} />
    </div>
  );
}

function finalAgentText(run: AgentRun): string {
  const fr = run.final_report;
  if (fr?.llm_final_text) return fr.llm_final_text;
  if (fr?.summary) return fr.summary;
  if (run.status === "cancelled") return "요청에 따라 실행을 취소했습니다.";
  if (run.status === "failed") return "죄송합니다. 이번 요청을 완료하지 못했습니다.";
  return "";
}

function rid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
