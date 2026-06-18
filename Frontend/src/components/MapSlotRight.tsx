export function MapSlotRight() {
  return (
    <div className="slot right map-slot" id="map-right" style={{ display: 'none' }}>
      <div className="search-bar" id="search-bar">
        <svg className="search-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.35-4.35"/></svg>
        <input type="text" id="search-input" className="search-input" placeholder="Search location…" autoComplete="off" />
        <div className="search-results" id="search-results"></div>
      </div>

      <div className="panel" id="p1-overture-panel">
        <div className="head">
          <div className="eyebrow">Overture Maps</div>
          <h3>Download Zone Data <button className="info-btn">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
            <span className="info-tooltip">Download buildings, amenities and walk network for the drawn zone directly from Overture's public dataset.</span>
          </button></h3>
        </div>
        <div className="body col" style={{ gap: '12px' }}>
          <div className="zone-bbox-display" id="p1-zone-bbox">No zone drawn yet.</div>
          <button className="btn primary" id="p1-overture-details" disabled>Details &amp; Download</button>
          <button className="btn" id="p1-overture-upload">Upload Database …</button>
          <input type="file" id="p1-overture-file-input" accept=".duckdb,.db" style={{ display: 'none' }} />
          <div id="p1-overture-progress" style={{ display: 'none' }}>
            <div className="meta" id="p1-overture-status">Preparing…</div>
            <div className="ov-progress-bar"><div className="ov-progress-fill" id="p1-overture-fill" style={{ width: '0%' }}></div></div>
            <div className="ov-log" id="p1-overture-log"></div>
            <div id="ov-save-actions" style={{ display: 'none', gap: '8px', marginTop: '8px' }}>
              <button className="btn primary" id="ov-save-append">Append to Map</button>
              <button className="btn" id="ov-save-new">Save as New DB</button>
            </div>
          </div>
        </div>
      </div>

      <div className="panel" id="p1-external-panel">
        <div className="head">
          <div className="eyebrow">External Data</div>
          <h3>Data Sources <button className="info-btn">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
            <span className="info-tooltip">Enrich agent perception with transit stops, weather, noise, and custom layers.</span>
          </button></h3>
        </div>
        <div className="body col" style={{ gap: '10px' }} id="p1-ext-source-list">
          <div className="meta" id="p1-ext-loading" style={{ opacity: 0.6 }}>Loading sources…</div>
        </div>
      </div>
    </div>
  );
}
