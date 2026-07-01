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
import { getRun } from "./api";
import { AgentActivity } from "./components/AgentActivity";
import { Candidates } from "./components/Candidates";
import { ControlsDrawer, type RunControlsForm } from "./components/ControlsDrawer";
import { Markdown } from "./components/Markdown";
import { HistoryDrawer, useHistory } from "./components/HistoryDrawer";
import { ClosetPage } from "./components/ClosetPage";
import { FavoritesPage } from "./components/FavoritesPage";
import { CitationReceipts } from "./components/CitationReceipts";
import { ProviderTags } from "./components/ProviderTags";
import { saveFavorite, listFavorites, uploadImage, type ClosetItem, type CitationBadge, type Attachment } from "./api";

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
  degraded?: boolean; // partial/provisional answer from a failure path (docs/25 P0-2)
  waiting?: boolean; // waiting_for_user — this turn asks a question (docs/25 P0-1)
  badges?: CitationBadge[]; // grounding citation receipts (docs/25 P1-3)
  groundingReason?: string;
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
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [view, setView] = useState<"chat" | "closet" | "favorites">("chat");
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set());
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const liveTurnId = useRef<string | null>(null);
  const loadedRunId = useRef<string | null>(null);

  const { run, streamedText, status: runStatus } = useRunStream(runId);
  const running = !!runId && !isTerminal(runStatus);
  const history = useHistory(historyRefresh);

  useEffect(() => {
    getStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  // docs/25 P0-4: restore a run from the URL on first load and on back/forward.
  useEffect(() => {
    const applyFromUrl = () => {
      const id = new URLSearchParams(window.location.search).get("run");
      if (id && id !== loadedRunId.current) loadRun(id, false);
      if (!id) {
        loadedRunId.current = null;
        setRunId(null);
        setTurns([]);
      }
    };
    applyFromUrl();
    window.addEventListener("popstate", applyFromUrl);
    return () => window.removeEventListener("popstate", applyFromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the URL in sync with the active run so refresh/share restores it.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const current = params.get("run");
    if (runId && runId !== current) {
      params.set("run", runId);
      window.history.replaceState({}, "", `?${params.toString()}`);
    } else if (!runId && current) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [runId]);

  // Refresh the history list when a run starts (so a running run shows up right
  // away) and again when it reaches a terminal state.
  useEffect(() => {
    if (runId) setHistoryRefresh((n) => n + 1);
  }, [runId]);
  useEffect(() => {
    if (run && isTerminal(run.status)) setHistoryRefresh((n) => n + 1);
  }, [run?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load a past run into the conversation view (rebuild turns from its record).
  async function loadRun(id: string, pushUrl = true) {
    try {
      loadedRunId.current = id;
      const full = await getRun(id);
      if (full.status === "not_found") return;
      setTurns(rebuildTurns(full));
      setRunId(id);
      setHistoryOpen(false);
      if (pushUrl) {
        const params = new URLSearchParams(window.location.search);
        params.set("run", id);
        window.history.pushState({}, "", `?${params.toString()}`);
      }
    } catch {
      /* ignore */
    }
  }

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
    const isWaiting = run.status === "waiting_for_user";
    setTurns((prev) =>
      prev.map((t) =>
        t.id === id
          ? {
              ...t,
              text: finalText || t.text,
              streaming: false,
              live: false,
              waiting: isWaiting,
              degraded: run.final_report?.degraded === true,
              trace: run.trace ?? t.trace,
              candidates: run.state?.match_results ?? t.candidates,
              reportType: run.final_report?.report_type,
              grounded: run.final_report?.grounded,
              badges: run.final_report?.grounding_validation?.citation_badges ?? [],
              groundingReason: run.final_report?.grounding_validation?.reason,
            }
          : t,
      ),
    );
    liveTurnId.current = null;
    // A waiting_for_user run is answerable, not dead — keep the same runId so the
    // composer's next message continues it (docs/25 P0-1).
    if (isWaiting) setBanner({ kind: "info", text: "답변을 입력하면 이 탐색을 이어갑니다." });
    else if (run.status === "cancelled") setBanner({ kind: "info", text: "실행을 취소했습니다." });
    else if (run.status === "failed")
      setBanner({
        kind: run.final_report?.degraded ? "warn" : "warn",
        text: run.error?.user_message ?? "요청을 완료하지 못했습니다. 다시 시도해 주세요.",
      });
    else if (run.status === "configuration_required")
      setBanner({ kind: "error", text: run.error?.user_message ?? "LLM 설정이 필요합니다. 서버 구성을 확인하세요." });
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
    const pending = attachments;
    // Allow sending with just photos (no text) — a visual-only search.
    if (!text && pending.length === 0) return;
    setInput("");
    setBanner(null);

    const userText = text || (pending.length ? `📷 사진 ${pending.length}장으로 비슷한 옷 찾기` : "");
    const userTurn: Turn = { id: rid(), role: "user", text: userText };
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
        const body: CreateRunBody = { free_text: text || undefined, refresh_web: form.refreshWeb };
        if (pending.length) body.attachments = pending;
        setAttachments([]);
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

  // docs/25 P1-2: which candidate_keys are already saved (for the ♥ state).
  useEffect(() => {
    listFavorites()
      .then((favs) => setSavedKeys(new Set(favs.map((f) => String(f.candidate_key || "")).filter(Boolean))))
      .catch(() => undefined);
  }, []);

  async function saveCandidate(c: MatchResult) {
    const key = String(c.candidate_key || "");
    const url = (c as { source_url?: string }).source_url;
    try {
      await saveFavorite({
        candidate_key: key || undefined,
        name: key ? key.replace(/^live_/, "").replace(/_/g, " ").trim() : undefined,
        source_url: typeof url === "string" ? url : undefined,
        recommendation: c.recommendation,
        score: (c.score as Record<string, number>) || undefined,
        from_run_id: runId || undefined,
      });
      if (key) setSavedKeys((prev) => new Set(prev).add(key));
    } catch {
      setBanner({ kind: "error", text: "후보 저장에 실패했습니다." });
    }
  }

  // docs/25 P1-4: upload clothing photos and attach them to the next run, so the
  // visual-comparison story ("find clothes like THIS photo") is reachable.
  async function addFiles(files: FileList | File[]) {
    const imgs = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (!imgs.length) return;
    setUploading(true);
    try {
      for (const file of imgs) {
        try {
          const att = await uploadImage(file);
          setAttachments((prev) => [...prev, att]);
        } catch {
          setBanner({ kind: "error", text: `이미지 업로드 실패: ${file.name}` });
        }
      }
    } finally {
      setUploading(false);
    }
  }
  function removeAttachment(id: string) {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }

  // docs/25 P1-1: start a search seeded from a closet item — the product's core
  // flow ("find candidates similar to MY clothes").
  function searchFromCloset(item: ClosetItem) {
    const parts = [item.name, item.category, item.brand, item.material, ...(item.colors ?? [])]
      .filter(Boolean)
      .join(" ");
    const query = `${parts}랑 비슷한 옷 찾아줘`.trim();
    setView("chat");
    newSession();
    setInput(query);
    setTimeout(() => taRef.current?.focus(), 50);
  }

  function newSession() {
    setRunId(null);
    setTurns([]);
    setBanner(null);
    liveTurnId.current = null;
    loadedRunId.current = null;
    setHistoryOpen(false);
    // "새 대화" archives the current run (it stays in history) rather than losing it.
    window.history.pushState({}, "", window.location.pathname);
    setHistoryRefresh((n) => n + 1);
  }

  return (
    <div className="app">
      <HistoryDrawer
        open={historyOpen}
        entries={history}
        activeRunId={runId}
        onSelect={(id) => loadRun(id)}
        onClose={() => setHistoryOpen(false)}
        onNew={newSession}
      />
      <div className="topbar">
        <div className="brand">
          <button
            className="icon-btn hist-btn"
            onClick={() => {
              setHistoryRefresh((n) => n + 1);
              setHistoryOpen(true);
            }}
            aria-label="탐색 기록"
          >
            ☰
          </button>
          <div className="mark">F</div>
          <div className="name">
            FitCheck
            <span className="sub">AI 의류 탐색 에이전트</span>
          </div>
        </div>
        <div className="topbar-right">
          <div className="view-switch" role="tablist" aria-label="화면 전환">
            <button
              className={`view-tab ${view === "chat" ? "active" : ""}`}
              onClick={() => setView("chat")}
              role="tab"
              aria-selected={view === "chat"}
            >
              탐색
            </button>
            <button
              className={`view-tab ${view === "closet" ? "active" : ""}`}
              onClick={() => setView("closet")}
              role="tab"
              aria-selected={view === "closet"}
            >
              내 옷장
            </button>
            <button
              className={`view-tab ${view === "favorites" ? "active" : ""}`}
              onClick={() => setView("favorites")}
              role="tab"
              aria-selected={view === "favorites"}
            >
              저장함
            </button>
          </div>
          <ProviderTags status={status} />
          <span className={`llm-pill ${llmConnected ? "connected" : status ? "error" : ""}`}>
            <span className="dot" />
            {llmConnected
              ? `${String(status?.llm?.model ?? "LLM")}`
              : status
                ? "LLM 미설정"
                : "연결 확인 중"}
          </span>
          {view === "chat" && turns.length > 0 && (
            <button className="icon-btn" onClick={newSession}>
              새 대화
            </button>
          )}
        </div>
      </div>

      {view === "closet" ? (
        <div className="stream">
          <ClosetPage onUseAsBaseline={searchFromCloset} />
        </div>
      ) : view === "favorites" ? (
        <div className="stream">
          <FavoritesPage />
        </div>
      ) : (
      <>
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
                  <div className="who">
                    FitCheck
                    {t.waiting && <span className="turn-badge ask">질문</span>}
                    {t.degraded && <span className="turn-badge partial">잠정 답변</span>}
                  </div>
                  {(t.trace?.length || t.streaming) && (
                    <AgentActivity trace={t.trace ?? []} running={!!t.live} />
                  )}
                  {(t.text || t.streaming) && (
                    <div className="bubble" aria-live={t.streaming ? "polite" : undefined}>
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
                      onSave={saveCandidate}
                      savedKeys={savedKeys}
                    />
                  )}
                  {!t.live && (t.badges?.length || t.grounded !== undefined) && (
                    <CitationReceipts
                      badges={t.badges ?? []}
                      grounded={t.grounded}
                      reason={t.groundingReason}
                    />
                  )}
                </div>
              ),
            )
          )}
          {banner && (
            <div
              className={`banner ${banner.kind}`}
              role={banner.kind === "error" ? "alert" : "status"}
              aria-live={banner.kind === "error" ? "assertive" : "polite"}
            >
              {banner.text}
            </div>
          )}
        </div>
      </div>

      <div className="composer-wrap">
        {showControls && <ControlsDrawer form={form} onChange={setForm} disabled={running} />}
        <div className="composer">
          {running && (
            <div className="running-strip" role="status" aria-live="polite">
              <span className="spinner" />
              에이전트 실행 중 · 메시지를 보내면 진행 중 작업에 반영됩니다
            </div>
          )}
          {attachments.length > 0 && (
            <div className="attach-row">
              {attachments.map((a) => (
                <div className="attach-thumb" key={a.id}>
                  {a.url ? <img src={a.url} alt={a.filename || "첨부"} /> : <span className="attach-ph">📷</span>}
                  <button className="attach-remove" onClick={() => removeAttachment(a.id)} aria-label="삭제">
                    ✕
                  </button>
                </div>
              ))}
              {uploading && <div className="attach-thumb loading"><span className="spinner" /></div>}
            </div>
          )}
          <div
            className="input-shell"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
            }}
          >
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: "none" }}
              onChange={(e) => {
                if (e.target.files?.length) addFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <button className="attach-btn" onClick={() => fileRef.current?.click()} aria-label="사진 첨부" title="옷 사진 첨부">
              ＋
            </button>
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              placeholder={
                attachments.length
                  ? "사진과 함께 설명을 덧붙이거나 바로 보내세요…"
                  : running
                    ? "진행 방향을 바꾸거나 추가 요청을 입력하세요…"
                    : "예: 검은색 와이드 슬랙스랑 비슷한거 찾아줘 (또는 옷 사진 첨부)"
              }
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
            <button
              className="send-btn"
              onClick={send}
              disabled={!input.trim() && attachments.length === 0}
              aria-label="보내기"
            >
              {running ? "↺" : "↑"}
            </button>
          </div>
          <div className="composer-foot">
            <button
              className="settings-toggle"
              onClick={() => setShowControls((s) => !s)}
              aria-expanded={showControls}
            >
              ⚙ 실행 설정 {showControls ? "닫기" : ""}
            </button>
            <span>Enter 전송 · Shift+Enter 줄바꿈{runId ? ` · ${runId.slice(0, 12)}` : ""}</span>
          </div>
        </div>
      </div>
      </>
      )}
    </div>
  );
}

function finalAgentText(run: AgentRun): string {
  // A waiting_for_user run's "answer" is its question(s) (docs/25 P0-1).
  if (run.status === "waiting_for_user") {
    const q = missingInfoQuestions(run);
    if (q) return q;
  }
  const fr = run.final_report;
  if (fr?.llm_final_text) return fr.llm_final_text;
  if (fr?.summary) return fr.summary;
  if (run.status === "cancelled") return "요청에 따라 실행을 취소했습니다.";
  if (run.status === "waiting_for_user") return "이어가려면 추가 정보가 필요합니다.";
  if (run.status === "failed")
    return run.error?.user_message ?? "죄송합니다. 이번 요청을 완료하지 못했습니다. 다시 시도하거나 다른 표현으로 요청해 주세요.";
  return "";
}

function missingInfoQuestions(run: AgentRun): string {
  const items = (run.state?.missing_info ?? []) as Array<Record<string, unknown>>;
  const qs = items
    .map((it) => (typeof it === "string" ? it : (it?.question as string) || (it?.prompt as string) || ""))
    .filter(Boolean);
  return qs.join("\n\n");
}

// docs/25 P0-4: rebuild the conversation turns from a persisted run so a past
// run can be reopened. The initial user message comes from input.free_text /
// candidate_url; each user_reply is a follow-up user turn; the final agent turn
// carries the report + trace + candidates.
function rebuildTurns(run: AgentRun): Turn[] {
  const input = (run as { input?: { free_text?: string; candidate_url?: string; user_replies?: Array<{ message?: string }> } }).input;
  const out: Turn[] = [];
  const firstText = input?.free_text?.trim() || input?.candidate_url?.trim() || (run as { goal?: string }).goal || "";
  if (firstText) out.push({ id: rid(), role: "user", text: firstText });
  for (const reply of input?.user_replies ?? []) {
    if (reply?.message?.trim()) out.push({ id: rid(), role: "user", text: reply.message.trim() });
  }
  out.push({
    id: rid(),
    role: "agent",
    text: finalAgentText(run),
    trace: run.trace ?? [],
    candidates: run.state?.match_results ?? [],
    reportType: run.final_report?.report_type,
    grounded: run.final_report?.grounded,
    degraded: run.final_report?.degraded === true,
    waiting: run.status === "waiting_for_user",
    badges: run.final_report?.grounding_validation?.citation_badges ?? [],
    groundingReason: run.final_report?.grounding_validation?.reason,
  });
  return out;
}

function rid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
