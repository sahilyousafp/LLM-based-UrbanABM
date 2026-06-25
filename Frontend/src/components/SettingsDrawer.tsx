export function SettingsDrawer() {
  return (
    <aside className="drawer" id="drawer" data-open="false">
      <div className="head row between">
        <h3>Settings</h3>
        <button className="btn ghost tiny" id="drawer-close">×</button>
      </div>
      <div className="drawer-tabs" id="s-tabs">
        <button data-v="sim" aria-selected="true">Sim</button>
        <button data-v="llm" aria-selected="false">LLM</button>
        <button data-v="map" aria-selected="false">Map</button>
      </div>
      <div className="body" style={{ overflowY: 'auto', flex: '1', padding: '16px 22px 22px' }}>

        {/* Tab: Map */}
        <div className="s-tab-content" data-tab="map" data-active="false">
          <div className="col" style={{ gap: '8px' }}>
            <div className="field-label">Map theme</div>
            <div className="segmented" id="s-theme">
              <button data-v="dark" aria-selected="true">Dark</button>
              <button data-v="light" aria-selected="false">Light</button>
            </div>
          </div>
          <div className="col" style={{ gap: '8px' }}>
            <div className="field-label">Map layers</div>
            <label className="row" style={{ fontSize: '12px' }}><input type="checkbox" id="s-layer-buildings3d" /> 3D Buildings</label>
          </div>
          <div className="col" style={{ gap: '8px' }}>
            <div className="field-label">Backend URLs</div>
            <input className="input" id="s-map-url" defaultValue="http://127.0.0.1:8000" />
            <input className="input" id="s-lab-url" defaultValue="http://127.0.0.1:8100" />
          </div>
        </div>

        {/* Tab: Sim */}
        <div className="s-tab-content" data-tab="sim" data-active="true">
          <div className="col" style={{ gap: '8px' }}>
            <div className="field-label">Perception Mode</div>
            <div className="segmented" id="s-perception">
              <button data-v="both" aria-selected="true">Both</button>
              <button data-v="amenities" aria-selected="false">Amenities</button>
              <button data-v="perception" aria-selected="false">Perception</button>
              <button data-v="rule_based" aria-selected="false">Rule-Based</button>
            </div>
          </div>
          <div className="col" style={{ gap: '8px' }}>
            <div className="field-label">Navigation Aid</div>
            <div className="segmented" id="s-nav-arch-tabs" style={{ fontSize: '11px' }}>
              <button data-v="resident"  aria-selected="true">Resident</button>
              <button data-v="commuter"  aria-selected="false">Commuter</button>
              <button data-v="tourist"   aria-selected="false">Tourist</button>
              <button data-v="student"   aria-selected="false">Student</button>
            </div>
            <select className="input" id="p4-navmode">
              <option value="both">Both — GPS + Compass</option>
              <option value="gps">GPS only</option>
              <option value="direction_sense">Compass only</option>
              <option value="none">None — free roam</option>
            </select>
            <div className="nav-threshold-row" id="p4-gps-row">
              <span className="threshold-label">GPS switch</span>
              <div className="threshold-edit-group" id="p4-gps-group">
                <span className="threshold-value" id="p4-gps-val">120</span>
                <span className="threshold-unit">m</span>
                <button className="threshold-edit-btn" id="p4-gps-edit" title="Edit GPS threshold">✎</button>
              </div>
            </div>
            <div className="nav-threshold-row" id="p4-compass-row">
              <span className="threshold-label">Compass switch</span>
              <div className="threshold-edit-group" id="p4-compass-group">
                <span className="threshold-value" id="p4-compass-val">60</span>
                <span className="threshold-unit">m</span>
                <button className="threshold-edit-btn" id="p4-compass-edit" title="Edit compass threshold">✎</button>
              </div>
            </div>
            <div className="row" style={{ gap: '8px', alignItems: 'center', marginTop: '2px' }}>
              <button className="btn primary tiny" id="s-nav-save">Save</button>
              <span className="meta" id="s-nav-save-status" style={{ transition: 'opacity 0.4s' }}></span>
            </div>
          </div>
          <div className="col" style={{ gap: '8px' }}>
            <div className="field-label">Map layers</div>
            <label className="row" style={{ fontSize: '12px' }}><input type="checkbox" id="s-layer-buildings" defaultChecked /> Buildings</label>
            <label className="row" style={{ fontSize: '12px' }}><input type="checkbox" id="s-layer-walk" defaultChecked /> Walk network</label>
            <label className="row" style={{ fontSize: '12px' }}><input type="checkbox" id="s-layer-amenities" defaultChecked /> Amenities</label>
            <label className="row" style={{ fontSize: '12px' }}><input type="checkbox" id="s-layer-streetview" defaultChecked /> Street View dots</label>
          </div>
          <div className="col" style={{ gap: '8px' }}>
            <div className="field-label">Database</div>
            <button className="btn" id="s-db-import-btn">Import Database…</button>
            <input type="file" id="s-db-file-input" accept=".duckdb,.db" style={{ display: 'none' }} />
          </div>
          <button className="btn danger" id="s-restart">Restart Wizard</button>
          <div className="meta">State persists in <span className="kbd">localStorage</span>. Restart clears all settings.</div>
        </div>

        {/* Tab: LLM */}
        <div className="s-tab-content" data-tab="llm" data-active="false">
          <div className="row between" style={{ alignItems: 'center' }}>
            <div>
              <div className="eyebrow">LLM Engine</div>
              <h3 id="p4-llm-current">—</h3>
            </div>
            <span className="chip ghost" id="p4-llm-stats">0 calls</span>
          </div>
          <div className="llm-tab-row" id="p4-llm-tabs">
            <button data-v="local" aria-selected="true">Local</button>
            <button data-v="api" aria-selected="false">API</button>
          </div>
          <select className="input" id="p4-llm-provider" />
          <div className="llm-field-group">
            <div className="field-label llm-field-label">Model</div>
            <input className="input" id="p4-llm-model" placeholder="auto-fills on selection" />
            <select className="input" id="p4-llm-model-select" style={{ display: 'none' }} />
          </div>
          <div className="lmdeploy-launch-row" style={{ display: 'none', alignItems: 'center', gap: '8px' }}>
            <button className="btn ghost tiny" id="p4-lmdeploy-launch">Start Container</button>
            <span className="meta" id="p4-lmdeploy-status" />
          </div>
          <div className="row" style={{ gap: '6px' }}>
            <button className="btn primary tiny" id="p4-llm-apply">Apply</button>
            <button className="btn ghost tiny" id="p4-compare-toggle">Compare</button>
          </div>
        </div>

      </div>
    </aside>
  );
}
