export interface RunControlsForm {
  maxTurns: string;
  toolBudget: string;
  searchProvider: string;
  refreshWeb: boolean;
}

export function ControlsDrawer({
  form,
  onChange,
  disabled,
}: {
  form: RunControlsForm;
  onChange: (next: RunControlsForm) => void;
  disabled: boolean;
}) {
  return (
    <div className="controls-drawer">
      <div className="ctl">
        <label>최대 턴</label>
        <input
          type="number"
          min={1}
          max={40}
          placeholder="기본 18"
          value={form.maxTurns}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, maxTurns: e.target.value })}
        />
        <div className="hint">루프 반복 상한</div>
      </div>
      <div className="ctl">
        <label>도구 예산</label>
        <input
          type="number"
          min={1}
          max={200}
          placeholder="제한 없음"
          value={form.toolBudget}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, toolBudget: e.target.value })}
        />
        <div className="hint">총 도구 호출 상한</div>
      </div>
      <div className="ctl">
        <label>검색 제공자</label>
        <select
          value={form.searchProvider}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, searchProvider: e.target.value })}
        >
          <option value="">기본값</option>
          <option value="brave_search">Brave</option>
          <option value="tavily_search">Tavily</option>
          <option value="duckduckgo_html">DuckDuckGo</option>
          <option value="searxng">SearXNG</option>
        </select>
        <div className="hint">이 실행에만 적용</div>
      </div>
      <div className="ctl checkbox">
        <label>
          <input
            type="checkbox"
            checked={form.refreshWeb}
            disabled={disabled}
            onChange={(e) => onChange({ ...form, refreshWeb: e.target.checked })}
          />
          웹 캐시 무시
        </label>
      </div>
    </div>
  );
}
