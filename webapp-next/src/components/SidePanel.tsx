import { useState } from "react";
import type { AgentRun, MatchResult, TraceEntry } from "../api";

type Tab = "trace" | "candidates" | "controls";

export interface RunControlsForm {
  maxTurns: string;
  toolBudget: string;
  searchProvider: string;
  refreshWeb: boolean;
}

export function SidePanel(props: {
  run: AgentRun | null;
  form: RunControlsForm;
  onFormChange: (next: RunControlsForm) => void;
  disabled: boolean;
}) {
  const { run, form, onFormChange, disabled } = props;
  const [tab, setTab] = useState<Tab>("trace");
  const trace = run?.trace ?? [];
  const candidates = run?.state?.match_results ?? [];

  return (
    <aside className="side">
      <div className="tabs">
        <button className={`tab ${tab === "trace" ? "active" : ""}`} onClick={() => setTab("trace")}>
          트레이스<span className="count">{trace.length}</span>
        </button>
        <button
          className={`tab ${tab === "candidates" ? "active" : ""}`}
          onClick={() => setTab("candidates")}
        >
          후보<span className="count">{candidates.length}</span>
        </button>
        <button
          className={`tab ${tab === "controls" ? "active" : ""}`}
          onClick={() => setTab("controls")}
        >
          실행 설정
        </button>
      </div>
      <div className="side-body">
        {tab === "trace" && <TraceList trace={trace} />}
        {tab === "candidates" && <CandidateList candidates={candidates} report={run?.final_report} />}
        {tab === "controls" && (
          <ControlsForm form={form} onChange={onFormChange} disabled={disabled} />
        )}
      </div>
    </aside>
  );
}

function TraceList({ trace }: { trace: TraceEntry[] }) {
  if (!trace.length) {
    return <div className="side-empty">아직 실행 단계가 없습니다. 요청을 보내면 도구 호출이 여기에 실시간으로 표시됩니다.</div>;
  }
  return (
    <div>
      {trace.map((t) => (
        <div className="trace-item" key={t.step_index}>
          <span className="idx">{t.step_index}</span>
          <span className="name">{t.name}</span>
          <span className={`st ${t.status}`}>{t.status}</span>
        </div>
      ))}
    </div>
  );
}

function CandidateList({
  candidates,
  report,
}: {
  candidates: MatchResult[];
  report: AgentRun["final_report"];
}) {
  if (!candidates.length) {
    return (
      <div className="side-empty">
        아직 비교할 후보가 없습니다. 에이전트가 검색·수집·정규화·점수화를 마치면 여기에 후보가 순위와 함께 표시됩니다.
      </div>
    );
  }
  return (
    <div>
      {candidates.map((c, i) => (
        <div className="cand" key={c.candidate_key ?? i}>
          <div>
            <span className="rank">{i + 1}</span>
            {c.recommendation && (
              <span className={`rec ${c.recommendation}`}>{recLabel(c.recommendation)}</span>
            )}
          </div>
          <div className="key" style={{ marginTop: 6 }}>
            {prettyKey(c.candidate_key)}
          </div>
          {c.score && (
            <div className="scores">
              <ScorePill label="종합" value={c.score.overall} />
              <ScorePill label="사이즈" value={c.score.size} />
              <ScorePill label="소재" value={c.score.material} />
              <ScorePill label="가격" value={c.score.price} />
              <ScorePill label="신뢰도" value={c.score.confidence} />
            </div>
          )}
          {c.missing_fields && c.missing_fields.length > 0 && (
            <div className="help" style={{ marginTop: 8 }}>
              부족한 근거: {c.missing_fields.join(", ")}
            </div>
          )}
        </div>
      ))}
      {report?.report_type && (
        <div className="help" style={{ marginTop: 4 }}>
          리포트 유형: <b>{report.report_type}</b>
          {report.grounded ? " · 근거 검증됨" : ""}
        </div>
      )}
    </div>
  );
}

function ScorePill({ label, value }: { label: string; value?: number }) {
  if (value === undefined || value === null) return null;
  return (
    <span className="score-pill">
      {label} <b>{value}</b>
    </span>
  );
}

function ControlsForm({
  form,
  onChange,
  disabled,
}: {
  form: RunControlsForm;
  onChange: (next: RunControlsForm) => void;
  disabled: boolean;
}) {
  return (
    <div>
      <div className="control-group">
        <label>최대 턴 (max_turns)</label>
        <input
          type="number"
          min={1}
          max={40}
          placeholder="기본값(18)"
          value={form.maxTurns}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, maxTurns: e.target.value })}
        />
        <div className="help">에이전트 루프 반복 상한. 비용·폭주 제어 (docs/24 #9).</div>
      </div>
      <div className="control-group">
        <label>도구 예산 (tool_budget)</label>
        <input
          type="number"
          min={1}
          max={200}
          placeholder="제한 없음"
          value={form.toolBudget}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, toolBudget: e.target.value })}
        />
        <div className="help">총 도구 호출 상한 (docs/24 #9).</div>
      </div>
      <div className="control-group">
        <label>검색 제공자 (search_provider)</label>
        <select
          value={form.searchProvider}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, searchProvider: e.target.value })}
        >
          <option value="">기본값 (서버 설정)</option>
          <option value="brave_search">Brave</option>
          <option value="tavily_search">Tavily</option>
          <option value="duckduckgo_html">DuckDuckGo (키 불필요)</option>
          <option value="searxng">SearXNG</option>
        </select>
        <div className="help">이 실행에만 적용되는 검색 백엔드 (docs/24 #11).</div>
      </div>
      <div className="control-group">
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={form.refreshWeb}
            disabled={disabled}
            onChange={(e) => onChange({ ...form, refreshWeb: e.target.checked })}
          />
          웹 캐시 무시하고 새로 검색
        </label>
      </div>
    </div>
  );
}

function recLabel(rec: string): string {
  const map: Record<string, string> = {
    buy: "추천",
    strong_match: "강력 추천",
    need_more_info: "근거 부족",
    avoid: "보류",
  };
  return map[rec] ?? rec;
}

function prettyKey(key?: string): string {
  if (!key) return "(이름 없음)";
  return key.replace(/^live_/, "").replace(/_/g, " ");
}
