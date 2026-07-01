import { useEffect, useState } from "react";
import { deleteFavorite, listFavorites, type Favorite } from "../api";

// docs/25 P1-2 (북극성 2): saved-candidates view. The shopping-comparison payoff —
// candidates collected across turns/sessions, compared side by side, persisted.

export function FavoritesPage() {
  const [items, setItems] = useState<Favorite[]>([]);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try {
      setItems(await listFavorites());
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
  }, []);

  async function remove(id: string) {
    await deleteFavorite(id).catch(() => undefined);
    refresh();
  }

  return (
    <div className="closet-wrap">
      <div className="closet-head">
        <div>
          <h2>저장한 후보</h2>
          <p>마음에 든 후보를 저장해 두면 여러 탐색·세션에 걸쳐 모아 비교할 수 있습니다.</p>
        </div>
      </div>
      {loading ? (
        <div className="fav-grid">
          {[0, 1, 2].map((i) => (
            <div className="skeleton-card" key={i} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="closet-empty">
          <div className="closet-empty-glyph">♡</div>
          <p>아직 저장한 후보가 없습니다. 탐색 결과의 후보 카드에서 “♡ 저장”을 눌러 보세요.</p>
        </div>
      ) : (
        <div className="fav-grid">
          {items.map((f) => (
            <div className="fav-card" key={f.id}>
              <div className="fav-card-head">
                <span className="fav-name">{f.name || prettyKey(f.candidate_key)}</span>
                {f.recommendation && (
                  <span className={`rec ${f.recommendation}`}>{recLabel(f.recommendation)}</span>
                )}
              </div>
              {f.score && Object.keys(f.score).length > 0 && (
                <div className="scores">
                  {("overall" in f.score) && <span className="score-pill">종합 <b>{f.score.overall}</b></span>}
                  {("size" in f.score) && <span className="score-pill">사이즈 <b>{f.score.size}</b></span>}
                  {("material" in f.score) && <span className="score-pill">소재 <b>{f.score.material}</b></span>}
                  {("price" in f.score) && <span className="score-pill">가격 <b>{f.score.price}</b></span>}
                </div>
              )}
              {f.notes && <div className="closet-notes">{f.notes}</div>}
              <div className="cand-foot">
                {f.source_url && (
                  <a className="cand-link" href={f.source_url} target="_blank" rel="noreferrer">
                    상품 보기 ↗
                  </a>
                )}
                <button className="cand-save saved" onClick={() => remove(f.id)}>
                  삭제
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
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
  if (!key) return "저장한 후보";
  return key.replace(/^live_/, "").replace(/_/g, " ").trim();
}
