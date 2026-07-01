import type { FinalReport, MatchResult } from "../api";

// Candidate shortlist rendered inline in the conversation (not a side panel),
// so the comparison lives right next to the agent's answer.

export function Candidates({
  candidates,
  report,
}: {
  candidates: MatchResult[];
  report?: FinalReport | null;
}) {
  if (!candidates.length) return null;
  return (
    <div className="cands">
      <div className="cands-title">
        <span>후보 {candidates.length}개</span>
        {report?.report_type && (
          <span style={{ color: "var(--text-faint)", fontWeight: 500, textTransform: "none" }}>
            · {reportTypeLabel(report.report_type)}
            {report.grounded ? " · 근거 검증됨" : ""}
          </span>
        )}
      </div>
      {candidates.map((c, i) => {
        const url = candidateUrl(c);
        return (
          <div className="cand" key={c.candidate_key ?? i}>
            <div className="cand-top">
              <span className="rank">{i + 1}</span>
              <span className="cand-name">{prettyKey(c.candidate_key)}</span>
              {c.recommendation && (
                <span className={`rec ${c.recommendation}`}>{recLabel(c.recommendation)}</span>
              )}
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
              <div className="missing">부족한 근거: {c.missing_fields.join(", ")}</div>
            )}
            {url && (
              <a className="cand-link" href={url} target="_blank" rel="noreferrer">
                상품 보기 ↗
              </a>
            )}
          </div>
        );
      })}
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

function candidateUrl(c: MatchResult): string | null {
  const direct = (c as { source_url?: string }).source_url;
  if (typeof direct === "string" && /^https?:\/\//.test(direct)) return direct;
  const bc = (c as { baseline_context?: { source_url?: string } }).baseline_context;
  if (bc?.source_url && /^https?:\/\//.test(bc.source_url)) return bc.source_url;
  return null;
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

function reportTypeLabel(t: string): string {
  const map: Record<string, string> = {
    shortlist: "shortlist",
    no_result: "결과 없음",
    baseline_category_unknown_no_shortlist: "기준 옷 미확정",
    cancelled: "취소됨",
  };
  return map[t] ?? t;
}

function prettyKey(key?: string): string {
  if (!key) return "(이름 없음)";
  return key.replace(/^live_/, "").replace(/_/g, " ").trim();
}
