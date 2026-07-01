import { useState } from "react";
import type { FinalReport, MatchResult } from "../api";

// Candidate shortlist rendered inline in the conversation (not a side panel),
// so the comparison lives right next to the agent's answer.

export function Candidates({
  candidates,
  report,
  onSave,
  savedKeys,
}: {
  candidates: MatchResult[];
  report?: FinalReport | null;
  onSave?: (c: MatchResult) => void;
  savedKeys?: Set<string>;
}) {
  const [mode, setMode] = useState<"cards" | "table">("cards");
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
        {candidates.length > 1 && (
          <span className="cands-viewswitch">
            <button
              className={`cvs-btn ${mode === "cards" ? "active" : ""}`}
              onClick={() => setMode("cards")}
              aria-pressed={mode === "cards"}
            >
              카드
            </button>
            <button
              className={`cvs-btn ${mode === "table" ? "active" : ""}`}
              onClick={() => setMode("table")}
              aria-pressed={mode === "table"}
            >
              표 비교
            </button>
          </span>
        )}
      </div>
      {mode === "table" ? (
        <ComparisonTable candidates={candidates} />
      ) : (
      candidates.map((c, i) => {
        const url = candidateUrl(c);
        return (
          <div className="cand" key={c.candidate_key ?? i}>
            <div className="cand-top">
              <span className="rank">{i + 1}</span>
              <span className="cand-name">{prettyKey(c.candidate_key)}</span>
              {c.recommendation && (
                <span className={`rec ${guardedRec(c)}`}>{recLabel(guardedRec(c))}</span>
              )}
            </div>
            <CoverageMeter cand={c} />
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
            <div className="cand-foot">
              {url && (
                <a className="cand-link" href={url} target="_blank" rel="noreferrer">
                  상품 보기 ↗
                </a>
              )}
              {onSave && (
                <SaveButton
                  saved={!!(c.candidate_key && savedKeys?.has(c.candidate_key))}
                  onSave={() => onSave(c)}
                />
              )}
            </div>
          </div>
        );
      })
      )}
    </div>
  );
}

// docs/25 P1-9: attribute-grouped comparison table across the shortlist. Rows are
// the score axes that actually exist on every candidate; the best value per row
// is highlighted so the user scans differences at a glance (Baymard pattern).
function ComparisonTable({ candidates }: { candidates: MatchResult[] }) {
  const cands = candidates.slice(0, 5); // keep it scannable
  const axes: Array<{ key: keyof NonNullable<MatchResult["score"]>; label: string }> = [
    { key: "overall", label: "종합" },
    { key: "size", label: "사이즈" },
    { key: "material", label: "소재" },
    { key: "price", label: "가격" },
    { key: "confidence", label: "신뢰도" },
  ];
  const bestFor = (key: string): number => {
    let best = -Infinity;
    for (const c of cands) {
      const v = (c.score as Record<string, number> | undefined)?.[key];
      if (typeof v === "number" && v > best) best = v;
    }
    return best;
  };
  return (
    <div className="cmp-scroll">
      <table className="cmp-table">
        <thead>
          <tr>
            <th className="cmp-axis" />
            {cands.map((c, i) => (
              <th key={c.candidate_key ?? i} title={prettyKey(c.candidate_key)}>
                <span className="cmp-rank">{i + 1}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {axes.map((a) => {
            const best = bestFor(a.key as string);
            return (
              <tr key={a.key as string}>
                <td className="cmp-axis">{a.label}</td>
                {cands.map((c, i) => {
                  const v = (c.score as Record<string, number> | undefined)?.[a.key as string];
                  const isBest = typeof v === "number" && v === best && cands.length > 1;
                  return (
                    <td key={i} className={isBest ? "cmp-best" : ""}>
                      {typeof v === "number" ? v : "—"}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          <tr>
            <td className="cmp-axis">추천</td>
            {cands.map((c, i) => (
              <td key={i} className="cmp-rec">
                {c.recommendation ? recLabel(guardedRec(c)) : "—"}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// docs/25 P1-8: evidence-coverage meter. Coverage = how many of the fields this
// category needs were actually observed (not the raw model %). Low coverage
// caps the recommendation so an ungrounded candidate can never read as a strong
// match — the project's grounding guardrail, made visible.
interface Coverage {
  ratio: number; // 0..1
  observed: number;
  required: number;
  missing: string[];
}

function coverageOf(c: MatchResult): Coverage {
  const ev = (c as { evidence_coverage?: { required_fields?: string[]; observed_fields?: string[] } })
    .evidence_coverage;
  const missing = c.missing_fields ?? [];
  // Prefer explicit required/observed counts; else derive from missing_fields
  // against a conventional required set for clothing (measurements/material/price).
  let required = ev?.required_fields?.length ?? 0;
  let observed = ev?.observed_fields?.length ?? 0;
  if (!required) {
    const CONVENTIONAL = 6; // e.g. 4 measurements + material + price
    required = Math.max(CONVENTIONAL, missing.length);
    observed = Math.max(0, required - missing.length);
  }
  const ratio = required > 0 ? Math.min(1, observed / required) : missing.length ? 0 : 1;
  return { ratio, observed, required, missing };
}

// Block a "strong match" claim when evidence coverage is weak — never present an
// ungrounded candidate as strongly matching.
function guardedRec(c: MatchResult): string {
  const rec = c.recommendation ?? "";
  const cov = coverageOf(c);
  const conf = c.score?.confidence ?? 0;
  if ((rec === "strong_match" || rec === "buy") && (cov.ratio < 0.6 || conf < 40)) {
    return "need_more_info";
  }
  return rec;
}

function CoverageMeter({ cand }: { cand: MatchResult }) {
  const cov = coverageOf(cand);
  const pct = Math.round(cov.ratio * 100);
  const level = cov.ratio >= 0.75 ? "high" : cov.ratio >= 0.4 ? "mid" : "low";
  return (
    <div className="coverage" title={`관찰된 근거 ${cov.observed}/${cov.required} 필드`}>
      <div className="coverage-head">
        <span className="coverage-label">근거 커버리지</span>
        <span className={`coverage-pct ${level}`}>{pct}%</span>
      </div>
      <div className="coverage-bar">
        <div className={`coverage-fill ${level}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function SaveButton({ saved, onSave }: { saved: boolean; onSave: () => void }) {
  const [justSaved, setJustSaved] = useState(false);
  const done = saved || justSaved;
  return (
    <button
      className={`cand-save ${done ? "saved" : ""}`}
      disabled={done}
      onClick={() => {
        onSave();
        setJustSaved(true);
      }}
    >
      {done ? "♥ 저장됨" : "♡ 저장"}
    </button>
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
