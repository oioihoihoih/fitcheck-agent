import { useState } from "react";
import type { CitationBadge } from "../api";

// docs/25 P1-3 (북극성 3): the project's hard guardrail — every number, material,
// price, and URL must come from a tool observation — is already computed as
// citation badges + a grounding verdict. This projects them into the UI so the
// user can see the receipt behind each claim (the AI-shopping trust differentiator).

const CLAIM_LABELS: Record<string, string> = {
  measurement: "실측",
  measurements: "실측",
  material: "소재",
  price: "가격",
  url: "링크",
  source_url: "링크",
  score: "점수",
  identity: "기준 옷",
  fit: "핏",
  review: "리뷰",
  category: "카테고리",
  evidence: "근거",
};

export function CitationReceipts({
  badges,
  grounded,
  reason,
}: {
  badges: CitationBadge[];
  grounded?: boolean;
  reason?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!badges.length && grounded === undefined) return null;

  return (
    <div className="receipts">
      <button className={`receipts-head ${open ? "open" : ""}`} onClick={() => setOpen((o) => !o)}>
        <span className={`ground-badge ${grounded ? "ok" : "warn"}`}>
          {grounded ? "✓ 근거 검증됨" : "⚠ 근거 확인 필요"}
        </span>
        <span className="receipts-count">
          {badges.length > 0 ? `출처 ${badges.length}건` : "출처 없음"}
        </span>
        {badges.length > 0 && <span className="chev">{open ? "▲" : "▼"}</span>}
      </button>
      {open && badges.length > 0 && (
        <div className="receipts-body">
          {badges.map((b) => (
            <div className="receipt" key={b.id}>
              <span className="receipt-claim">{claimLabel(b.claim_type)}</span>
              <div className="receipt-detail">
                <span className="receipt-label">{b.label}</span>
                {b.source_value_preview && (
                  <span className="receipt-preview">{b.source_value_preview}</span>
                )}
                <span className="receipt-source">
                  {b.source_kind ? `${b.source_kind} · ` : ""}
                  {shortenPath(b.source_path)}
                  {b.source_count && b.source_count > 1 ? ` (+${b.source_count - 1})` : ""}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
      {open && badges.length === 0 && reason && (
        <div className="receipts-body">
          <div className="receipt-note">{reason}</div>
        </div>
      )}
    </div>
  );
}

function claimLabel(t?: string): string {
  if (!t) return "근거";
  return CLAIM_LABELS[t] ?? t;
}

function shortenPath(p: string): string {
  if (!p) return "";
  // paths look like state.observations[3].result.measurements.waist — show the tail.
  const parts = p.split(".");
  return parts.slice(-2).join(".");
}
