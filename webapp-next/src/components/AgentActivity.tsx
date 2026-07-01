import { useState } from "react";
import type { TraceEntry } from "../api";

// Inline, collapsible tool-execution steps rendered in the conversation stream,
// modeled on Claude Code's tool-use blocks: a one-line summary you can expand to
// see each backend tool call as it happens.

const HIDDEN_STEPS = new Set([
  "create_agent_run",
  "match_agent_skills",
  "queue_steering_message",
]);

export function AgentActivity({
  trace,
  running,
}: {
  trace: TraceEntry[];
  running: boolean;
}) {
  const [open, setOpen] = useState(false);
  const steps = trace.filter(
    (t) =>
      (t.step_type === "tool_call" || t.step_type === "tool_output" || t.step_type === "context") &&
      !HIDDEN_STEPS.has(t.name),
  );
  const toolCalls = trace.filter((t) => t.step_type === "tool_call" && !HIDDEN_STEPS.has(t.name));
  if (!steps.length && !running) return null;

  const latest = toolCalls[toolCalls.length - 1];
  const label = running
    ? latest
      ? `${prettyTool(latest.name)} 실행 중…`
      : "작업 계획 중…"
    : `${toolCalls.length}개 단계로 후보를 찾았습니다`;

  return (
    <div className="activity">
      <button className={`activity-head ${open ? "open" : ""}`} onClick={() => setOpen((o) => !o)}>
        <span className="chev">▶</span>
        <SparkIcon spinning={running} />
        <span className="label">{label}</span>
        <span className="count">{toolCalls.length}</span>
      </button>
      {open && (
        <div className="activity-body">
          {steps.map((s) => (
            <div className={`step ${s.status}`} key={`${s.step_index}-${s.step_type}`}>
              <span className="st-ico">{stepIcon(s.status)}</span>
              <span className="st-name">{prettyTool(s.name)}</span>
              <span className="st-time">{shortTime(s.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SparkIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg
      className="spark"
      viewBox="0 0 24 24"
      fill="none"
      style={spinning ? { animation: "spin 1.4s linear infinite" } : undefined}
    >
      <path
        d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z"
        fill="var(--accent)"
        opacity={spinning ? 1 : 0.7}
      />
    </svg>
  );
}

function stepIcon(status: string): string {
  if (status === "completed") return "✓";
  if (status === "failed") return "✕";
  if (status === "started" || status === "running") return "◆";
  return "·";
}

function shortTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(
    d.getSeconds(),
  ).padStart(2, "0")}`;
}

const TOOL_LABELS: Record<string, string> = {
  load_agent_skill: "스킬 로드",
  plan_context_and_sources: "맥락·소스 계획",
  parse_user_goal: "요청 해석",
  load_closet_memory: "옷장 메모리 조회",
  enrich_closet_item_from_web: "기준 옷 정보 보강",
  search_web_candidates: "웹 후보 검색",
  fetch_product_page: "상품 페이지 수집",
  classify_product_assets: "상품 이미지 분류",
  extract_size_material_info: "사이즈·소재 추출",
  normalize_product_spec: "스펙 정규화",
  score_candidate_match: "매칭 점수 계산",
  write_purchase_report: "리포트 작성",
  ask_missing_info: "부족 정보 질문",
  confirm_baseline_identity: "기준 옷 확인",
};

function prettyTool(name: string): string {
  return TOOL_LABELS[name] ?? name;
}
