export function MapSlotLeft() {
  return (
    <div className="slot left map-slot" id="map-left" style={{ display: 'none' }}>
      <div className="panel control-panel">
        <div className="head">
          <div className="eyebrow">Street View</div>
          <h3>Street View Extraction <button className="info-btn">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
            <span className="info-tooltip">Draw a zone to plan a Street View capture. Click any existing point to inspect its image and scene analysis.</span>
          </button></h3>
        </div>
        <div className="body col" style={{ gap: '14px' }}>
          <div className="col" style={{ gap: '6px' }}>
            <div className="row between">
              <span className="meta">Spacing</span>
              <span className="meta" id="p1-spacing-label">200 m</span>
            </div>
            <input type="range" min="50" max="500" step="10" defaultValue="200" className="slider" id="p1-spacing" />
          </div>

          <div className="kpi-grid">
            <div className="kpi"><div className="val" id="p1-kpi-existing">—</div><div className="lbl">Existing</div></div>
            <div className="kpi"><div className="val" id="p1-kpi-candidates">0</div><div className="lbl">Candidates</div></div>
          </div>

          <label className="row" style={{ gap: '6px', cursor: 'pointer' }}>
            <input type="checkbox" id="p1-skip-near" defaultChecked />
            Skip existing
          </label>

          <div className="row" style={{ gap: '6px' }}>
            <button className="btn primary" id="p1-download" title="Download New Images" style={{ flex: 1, justifyContent: 'center' }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            </button>
            <button className="btn ghost" id="p1-delete-unanalyzed" title="Delete Unanalyzed" style={{ flex: 1, justifyContent: 'center' }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
            </button>
          </div>
          <div id="p1-sv-progress" style={{ display: 'none' }}>
            <div className="meta" id="p1-sv-progress-status">Preparing…</div>
            <div className="ov-progress-bar"><div className="ov-progress-fill" id="p1-sv-progress-fill" style={{ width: '0%' }}></div></div>
            <div className="ov-log" id="p1-sv-progress-log"></div>
          </div>
          <div className="meta" id="p1-status"></div>
        </div>
      </div>

      <div className="panel" id="p1-vlm-panel">
        <div className="head">
          <div className="eyebrow">VLM Analysis</div>
          <h3>Analyse Images</h3>
        </div>
        <div className="body col" style={{ gap: '10px' }}>
          <div className="row" style={{ gap: '8px', alignItems: 'stretch' }}>
            <select className="select" id="p1-vlm-model-select" style={{ flex: 1 }}></select>
            <button className="btn ghost tiny" id="p1-vlm-compare">Compare</button>
          </div>
          <div id="p1-vlm-hf-row" style={{ display: 'none', flexDirection: 'column', gap: '4px' }}>
            <span className="meta" style={{ fontSize: '11px' }}>HuggingFace repo ID</span>
            <input type="text" className="input" id="p1-vlm-hf-model" placeholder="e.g. Qwen/Qwen3-VL-8B-Instruct" style={{ fontSize: '12px', width: '100%', boxSizing: 'border-box' }} />
          </div>
          <label className="row" style={{ gap: '8px', alignItems: 'center', cursor: 'pointer', fontSize: '12px' }}>
            <input type="checkbox" id="p1-reanalyse-toggle" style={{ accentColor: 'var(--accent)' }} />
            <span className="meta" style={{ fontSize: '12px' }}>Re-analyse existing</span>
          </label>
          <button className="btn primary" id="p1-analyse-open" style={{ width: '100%' }}>Analyse Images</button>
          <button className="btn ghost" id="p1-reimport-perception" style={{ width: '100%' }}>Sync Results → DB</button>
          <span className="meta" id="p1-reimport-status"></span>
        </div>
      </div>

      <div className="panel" id="p1-sv-popover" style={{ display: 'none' }}>
        <div className="head row between">
          <div>
            <div className="eyebrow">Street View</div>
            <h3 id="p1-popover-title">Selected Point</h3>
          </div>
          <div className="row" style={{ gap: '4px' }}>
            <button className="btn ghost tiny" id="p1-popover-expand" title="View details">⤢</button>
            <button className="btn ghost tiny" id="p1-popover-close">×</button>
          </div>
        </div>
        <div className="popover-image">
          <img id="p1-popover-img" alt="Street View" style={{ filter: 'brightness(1) contrast(1) saturate(1)' }} />
          <div className="badges">
            <span className="chip" id="p1-popover-coord">—</span>
            <span className="chip accent" id="p1-popover-heading">—</span>
          </div>
        </div>
      </div>
    </div>
  );
}
