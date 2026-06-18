export function Panel3() {
  return (
    <section className="panel-stage" id="panel-3" data-active="false">
      <p className="p3-pick-heading">Pick your character.</p>

      <div className="archetype-bar" id="p3-bar"></div>

      <div className="p3-layout hidden" id="p3-layout">
        <div className="p3-col-left">
          <div className="panel p3-info-panel">
            <div className="head">
              <h3>Personality <button className="info-btn">
                <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                <span className="info-tooltip">Tune the four archetypes that drive agent decisions. Click an archetype card to edit its profile and daily plan.</span>
              </button></h3>
            </div>
            <div className="body col" style={{ gap: '8px' }}>
              <button className="btn primary" id="p3-save" style={{ width: '100%' }}>Save</button>
              <div className="row" style={{ gap: '6px' }}>
                <button className="btn tiny" id="p3-export">Export JSON</button>
                <button className="btn ghost tiny" id="p3-reload-saved">Reload Saved</button>
                <button className="btn ghost tiny" id="p3-reload-defaults">Restore Defaults</button>
              </div>
            </div>
          </div>
          <div className="p3-thumb-strip" id="p3-thumbs"></div>
        </div>

        <div className="p3-col-center">
          <div className="panel p3-editor-panel" id="p3-editor">
            <div className="head row between">
              <div>
                <div className="eyebrow">Profile Editor</div>
                <h3 id="p3-editor-title">Archetype</h3>
              </div>
              <button className="btn ghost tiny" id="p3-editor-close">×</button>
            </div>
            <div className="body" id="p3-editor-body"></div>
          </div>
        </div>

        <div className="p3-col-right p3-char-panel" id="p3-char-viewer">
          <canvas id="p3-char-canvas"></canvas>
        </div>
      </div>
    </section>
  );
}
