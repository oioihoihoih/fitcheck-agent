import { useEffect, useState } from "react";
import {
  createClosetItem,
  deleteClosetItem,
  listCloset,
  updateClosetItem,
  type ClosetItem,
  type ClosetItemInput,
} from "../api";

// docs/25 P1-1 (북극성 1): the closet management surface. The product premise is
// "compare online candidates against MY clothes", so this is where the user
// builds up that wardrobe: add/edit/delete owned garments and mark a baseline.

export function ClosetPage({ onUseAsBaseline }: { onUseAsBaseline: (item: ClosetItem) => void }) {
  const [items, setItems] = useState<ClosetItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ClosetItem | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setItems(await listCloset());
    } catch {
      setError("옷장을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
  }, []);

  async function save(input: ClosetItemInput) {
    setError(null);
    try {
      if (editing && editing !== "new") await updateClosetItem(editing.id, input);
      else await createClosetItem(input);
      setEditing(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장에 실패했습니다.");
    }
  }

  async function remove(id: string) {
    try {
      await deleteClosetItem(id);
      await refresh();
    } catch {
      setError("삭제에 실패했습니다.");
    }
  }

  async function toggleBaseline(item: ClosetItem) {
    // Only one baseline at a time: clear others, set this one.
    try {
      await Promise.all(
        items
          .filter((it) => it.is_baseline && it.id !== item.id)
          .map((it) => updateClosetItem(it.id, { is_baseline: false })),
      );
      await updateClosetItem(item.id, { is_baseline: !item.is_baseline });
      await refresh();
    } catch {
      setError("기준 옷 설정에 실패했습니다.");
    }
  }

  return (
    <div className="closet">
      <div className="closet-head">
        <div>
          <h2>내 옷장</h2>
          <p>평소 만족스럽게 입는 옷을 등록해 두면, 매번 설명하지 않아도 이 옷들을 기준으로 비교합니다.</p>
        </div>
        <button className="btn-accent" onClick={() => setEditing("new")}>
          ＋ 옷 추가
        </button>
      </div>

      {error && <div className="banner error">{error}</div>}

      {loading ? (
        <div className="closet-grid">
          {[0, 1, 2].map((i) => (
            <div className="skeleton-card" key={i} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="closet-empty">
          <div className="closet-empty-glyph">🧥</div>
          <p>아직 등록한 옷이 없습니다.</p>
          <button className="btn-accent" onClick={() => setEditing("new")}>
            첫 옷 추가하기
          </button>
        </div>
      ) : (
        <div className="closet-grid">
          {items.map((it) => (
            <ClosetCard
              key={it.id}
              item={it}
              onEdit={() => setEditing(it)}
              onDelete={() => remove(it.id)}
              onToggleBaseline={() => toggleBaseline(it)}
              onUseAsBaseline={() => onUseAsBaseline(it)}
            />
          ))}
        </div>
      )}

      {editing && (
        <ClosetForm
          initial={editing === "new" ? null : editing}
          onCancel={() => setEditing(null)}
          onSave={save}
        />
      )}
    </div>
  );
}

function ClosetCard({
  item,
  onEdit,
  onDelete,
  onToggleBaseline,
  onUseAsBaseline,
}: {
  item: ClosetItem;
  onEdit: () => void;
  onDelete: () => void;
  onToggleBaseline: () => void;
  onUseAsBaseline: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  return (
    <div className={`closet-card ${item.is_baseline ? "baseline" : ""}`}>
      <div className="closet-card-head">
        <span className="closet-name">{item.name || item.category || "이름 없는 옷"}</span>
        {item.is_baseline && <span className="baseline-tag">기준 옷</span>}
      </div>
      <div className="closet-meta">
        {item.category && <span className="meta-chip">{item.category}</span>}
        {item.brand && <span className="meta-chip">{item.brand}</span>}
        {item.size && <span className="meta-chip">사이즈 {item.size}</span>}
        {item.material && <span className="meta-chip">{item.material}</span>}
        {(item.colors ?? []).map((c) => (
          <span className="meta-chip" key={c}>
            {c}
          </span>
        ))}
      </div>
      {item.notes && <div className="closet-notes">{item.notes}</div>}
      <div className="closet-card-actions">
        <button className="mini-btn" onClick={onUseAsBaseline} title="이 옷 기준으로 비슷한 옷 찾기">
          이 옷으로 찾기
        </button>
        <button className="mini-btn" onClick={onToggleBaseline}>
          {item.is_baseline ? "기준 해제" : "기준 설정"}
        </button>
        <button className="mini-btn" onClick={onEdit}>
          편집
        </button>
        {confirm ? (
          <button className="mini-btn danger" onClick={onDelete}>
            정말 삭제?
          </button>
        ) : (
          <button className="mini-btn danger" onClick={() => setConfirm(true)}>
            삭제
          </button>
        )}
      </div>
    </div>
  );
}

function ClosetForm({
  initial,
  onCancel,
  onSave,
}: {
  initial: ClosetItem | null;
  onCancel: () => void;
  onSave: (input: ClosetItemInput) => void;
}) {
  const [f, setF] = useState<ClosetItemInput>({
    name: initial?.name ?? "",
    category: initial?.category ?? "",
    brand: initial?.brand ?? "",
    size: initial?.size ?? "",
    material: initial?.material ?? "",
    season: initial?.season ?? "",
    colors: initial?.colors ?? [],
    source_url: initial?.source_url ?? "",
    notes: initial?.notes ?? "",
  });
  const [colorsText, setColorsText] = useState((initial?.colors ?? []).join(", "));

  function submit() {
    const input: ClosetItemInput = {
      ...f,
      colors: colorsText
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean),
    };
    onSave(input);
  }

  return (
    <>
      <div className="drawer-scrim open" onClick={onCancel} />
      <div className="closet-form">
        <div className="drawer-head">
          <span>{initial ? "옷 편집" : "옷 추가"}</span>
          <button className="icon-btn" onClick={onCancel} aria-label="닫기">
            ✕
          </button>
        </div>
        <div className="closet-form-body">
          <Field label="이름" value={f.name} onChange={(v) => setF({ ...f, name: v })} placeholder="예: 검정 와이드 슬랙스" />
          <div className="form-row">
            <Field label="카테고리" value={f.category} onChange={(v) => setF({ ...f, category: v })} placeholder="pants, tshirt…" />
            <Field label="브랜드" value={f.brand} onChange={(v) => setF({ ...f, brand: v })} />
          </div>
          <div className="form-row">
            <Field label="사이즈" value={f.size} onChange={(v) => setF({ ...f, size: v })} />
            <Field label="소재" value={f.material} onChange={(v) => setF({ ...f, material: v })} placeholder="코튼, 울…" />
          </div>
          <div className="form-row">
            <Field label="색상 (쉼표로 구분)" value={colorsText} onChange={setColorsText} placeholder="black, navy" />
            <Field label="시즌" value={f.season} onChange={(v) => setF({ ...f, season: v })} placeholder="여름, 사계절…" />
          </div>
          <Field label="상품 URL (선택)" value={f.source_url} onChange={(v) => setF({ ...f, source_url: v })} placeholder="https://…" />
          <div className="field">
            <label>메모</label>
            <textarea
              rows={3}
              value={f.notes}
              onChange={(e) => setF({ ...f, notes: e.target.value })}
              placeholder="이 옷의 마음에 드는 점 (핏, 실루엣 등)"
            />
          </div>
        </div>
        <div className="closet-form-foot">
          <button className="btn-ghost" onClick={onCancel}>
            취소
          </button>
          <button className="btn-accent" onClick={submit}>
            저장
          </button>
        </div>
      </div>
    </>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value?: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <input value={value ?? ""} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  );
}
