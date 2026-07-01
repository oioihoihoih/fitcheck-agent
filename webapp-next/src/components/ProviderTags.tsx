import { useState } from "react";
import type { ApiStatus, ProviderDetail } from "../api";

// docs/25 P1-6: honest provider/mode tags. Project hard rule — cached/fixture
// providers must NOT be shown as live. Each provider shows its real mode so the
// user knows whether a result came from a live source or a demo fixture/cache.

const LABELS: Record<string, string> = {
  search: "검색",
  ocr: "OCR",
  vision: "비전",
  page_fetch: "페이지",
};

function isLive(p?: ProviderDetail): boolean {
  return p?.mode === "live_provider" && p?.external_provider_configured === true;
}

function modeLabel(p?: ProviderDetail): string {
  if (!p) return "미설정";
  if (isLive(p)) return "라이브";
  const m = p.mode || "";
  if (m.includes("cached")) return "캐시";
  if (m.includes("fixture")) return "픽스처";
  if (m.includes("demo")) return "데모";
  return "데모";
}

export function ProviderTags({ status }: { status: ApiStatus | null }) {
  const [open, setOpen] = useState(false);
  if (!status?.providers) return null;
  const entries = (["search", "ocr", "vision", "page_fetch"] as const)
    .map((k) => [k, status.providers[k]] as const)
    .filter(([, p]) => !!p);
  if (!entries.length) return null;

  const anyDemo = entries.some(([, p]) => !isLive(p));

  return (
    <div className="provtags">
      <button
        className={`provtags-toggle ${anyDemo ? "has-demo" : "all-live"}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label="데이터 출처 provider 상태"
        title="검색·OCR·비전 등 데이터 출처가 라이브인지 데모인지"
      >
        {anyDemo ? "◐ 일부 데모 데이터" : "◉ 라이브 데이터"}
      </button>
      {open && (
        <div className="provtags-pop">
          {entries.map(([k, p]) => (
            <div className="provtag-row" key={k}>
              <span className="provtag-name">{LABELS[k] ?? k}</span>
              <span className="provtag-prov">{p?.provider || "-"}</span>
              <span className={`provtag-mode ${isLive(p) ? "live" : "demo"}`}>{modeLabel(p)}</span>
            </div>
          ))}
          {anyDemo && (
            <div className="provtag-note">
              데모(캐시/픽스처) 출처는 라이브 결과가 아닙니다. 실제 검색·OCR 결과는 provider 설정 시 제공됩니다.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
