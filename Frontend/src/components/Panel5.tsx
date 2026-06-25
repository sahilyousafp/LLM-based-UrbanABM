export function Panel5() {
  return (
    <>
      <section className="panel-stage" id="panel-5" data-active="false">
        <div className="slot top-center">
          <div className="p5-tod-pill">
            <span className="p5-tod-label">Time of Day</span>
            <span className="p5-tod-value" id="p5-time-of-day">—</span>
          </div>
        </div>

        <div className="slot left">
          <div className="panel">
            <div className="head"><h3>Multi Agent</h3></div>
            <div className="body col" style={{ gap: '14px' }}>
              <div className="col" style={{ gap: '6px' }}>
                <div className="row between"><span className="field-label">Agent Count</span><span className="meta" id="p5-count-label">15</span></div>
                <input type="range" className="slider" id="p5-count" min="1" max="100" step="1" defaultValue="15" />
              </div>

              <div className="col" style={{ gap: '6px' }}>
                <div className="field-label">Spawn Mode</div>
                <div className="segmented" id="p5-spawn-mode">
                  <button data-v="random" aria-selected="true">Random</button>
                  <button data-v="click" aria-selected="false">Click</button>
                  <button data-v="poi" aria-selected="false">POIs</button>
                  <button data-v="home_work" aria-selected="false">Home/Work</button>
                </div>
                <div className="meta" id="p5-spawn-hint">Random respawn on the walk network.</div>
              </div>

              <div className="col" id="p5-homework-panel" style={{ gap: '6px', display: 'none' }}>
                <div className="field-label">Pin Type</div>
                <div className="segmented" id="p5-pin-mode">
                  <button data-v="home" aria-selected="true">Home</button>
                  <button data-v="work" aria-selected="false">Work</button>
                </div>
                <div className="meta" id="p5-pin-status">Home: 0 · Work: 0</div>
              </div>

              <div className="col" id="p5-mix-panel" style={{ gap: '6px', display: 'none' }}>
                <div className="field-label">Archetype Mix</div>
                <div className="slider-row"><span>Resident</span><input className="slider" id="p5-mix-resident" min="0" max="1" step="0.05" defaultValue="0.25" type="range" /><span id="p5-mix-resident-v">25%</span></div>
                <div className="slider-row"><span>Commuter</span><input className="slider" id="p5-mix-commuter" min="0" max="1" step="0.05" defaultValue="0.25" type="range" /><span id="p5-mix-commuter-v">25%</span></div>
                <div className="slider-row"><span>Tourist</span><input className="slider" id="p5-mix-tourist" min="0" max="1" step="0.05" defaultValue="0.25" type="range" /><span id="p5-mix-tourist-v">25%</span></div>
                <div className="slider-row"><span>Student</span><input className="slider" id="p5-mix-student" min="0" max="1" step="0.05" defaultValue="0.25" type="range" /><span id="p5-mix-student-v">25%</span></div>
              </div>

              <div className="row" style={{ gap: '6px' }}>
                <button className="btn primary" id="p5-spawn">Spawn</button>
                <button className="btn ghost" id="p5-clear-pins">Clear pins</button>
              </div>
              <div className="meta" id="p5-spawn-status">Ready</div>
            </div>
          </div>

          <div className="panel" id="p5-rec-panel" style={{ marginTop: '12px' }}>
            <div className="head"><div className="eyebrow">Session</div><h3>Record Agents</h3></div>
            <div className="body col" style={{ gap: '10px' }}>
              <div className="segmented" id="p5-rec-tabs">
                <button data-v="record" aria-selected="true">Record</button>
                <button data-v="replay" aria-selected="false">Replay</button>
              </div>
              <div id="p5-rec-tab-record" className="col" style={{ gap: '8px' }}>
                <input className="input" id="p5-rec-name" placeholder="Session name (optional)" />
                <div className="row" style={{ gap: '6px' }}>
                  <button className="btn primary" id="p5-rec-start">&#9679; Start</button>
                  <button className="btn ghost" id="p5-rec-stop" disabled>&#9632; Stop</button>
                </div>
                <div className="meta" id="p5-rec-status">Idle</div>
              </div>
              <div id="p5-rec-tab-replay" className="col" style={{ gap: '8px', display: 'none' }}>
                <input type="file" id="p5-import-file" accept=".parquet" style={{ display: 'none' }} />
                <button className="btn primary" id="p5-import-btn" style={{ justifyContent: 'center' }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                  &nbsp;Import Recording
                </button>
                <div className="meta" id="p5-playback-status">No recording loaded.</div>
                <div id="p5-playback-overlays" style={{ display: 'none', flexDirection: 'column', gap: '6px', marginTop: '2px' }}>
                  <div id="p5-dataset-list" className="dataset-list"></div>
                  <div id="p5-playback-filters" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}></div>
                  <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', paddingTop: '2px' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: 'var(--text-muted)', cursor: 'pointer' }}>
                      <input type="checkbox" id="p5-rec-rl-heatmap" defaultChecked /> Heatmap
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: 'var(--text-muted)', cursor: 'pointer' }}>
                      <input type="checkbox" id="p5-rec-rl-trails" defaultChecked /> Trails
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: 'var(--text-muted)', cursor: 'pointer' }}>
                      <input type="checkbox" id="p5-rec-rl-decision-pts" defaultChecked /> Decision Pts
                    </label>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="slot right-tall">
          <div id="p5-results-stats" className="metric-card" style={{ display: 'none', flexShrink: 0, gap: '6px' }}></div>
          <div className="metric-card" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', boxShadow: 'none' }}>
            <h4>Agents (<span id="p5-agent-count">0</span>) <span className="chip ghost" id="p5-step-count" style={{ fontSize: '10px' }}>Step 0</span> <span className="chip ghost" id="p5-llm-stats" style={{ float: 'right', fontSize: '10px' }}>0 calls</span></h4>
            <div className="agent-grid" id="p5-agent-grid"></div>
            <div id="p5-agent-detail" style={{ display: 'none', flexDirection: 'column', gap: '8px', overflowY: 'auto', flex: 1, minHeight: 0 }}>
              <div className="p5-det-header">
                <button className="btn ghost tiny" id="p5-det-back">← Grid</button>
                <span className="p5-det-name" id="p5-det-name">Agent —</span>
                <span className="p4-profile-tag" id="p5-det-tags"></span>
              </div>

              <div className="metric-card p4-perception-card expandable-card" data-detail-id="p5-perception" style={{ flexShrink: 0 }}>
                <h4>What The Agent Sees <span className="perc-mode-chip" id="p5-perc-mode-chip"></span><button className="expand-card-btn" title="Expand">⤢</button></h4>
                <div id="p5-perception" style={{ overflowY: 'auto', maxHeight: '200px' }}></div>
              </div>

              <div className="metric-card expandable-card" data-detail-id="p5-emotion-cognition" style={{ display: 'flex', flexDirection: 'column', gap: '8px', flexShrink: 0 }}>
                <div className="row between" style={{ alignItems: 'center' }}>
                  <div className="row" style={{ gap: '16px' }}>
                    <h4 style={{ margin: 0 }}>Emotion Mix</h4>
                    <h4 style={{ margin: 0, opacity: 0.45 }}>Cognition</h4>
                  </div>
                  <button className="expand-card-btn" title="Expand">⤢</button>
                </div>
                <div style={{ display: 'flex', flexDirection: 'row', gap: '12px' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="emotion-pie">
                      <div className="emotion-svg-wrap"><svg viewBox="-1 -1 2 2" id="p5-emotion-svg"></svg></div>
                      <div className="emotion-legend" id="p5-emotion-legend"></div>
                    </div>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="cognition-grid" style={{ gridTemplateColumns: '1fr' }}>
                      <div className="cell"><div className="v" id="p5-det-mood">—</div><div className="l">Mood</div></div>
                      <div className="cell"><div className="v" id="p5-det-curiosity">—</div><div className="l">Curiosity</div></div>
                      <div className="cell"><div className="v" id="p5-det-fatigue">—</div><div className="l">Fatigue</div></div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="metric-card expandable-card" data-detail-id="p5-needs" style={{ flexShrink: 0 }}>
                <h4>Needs <button className="expand-card-btn" title="Expand">⤢</button></h4>
                <div className="needs-bar" id="p5-det-needs"></div>
              </div>

              <div className="metric-card thought-stream-card expandable-card" data-detail-id="p5-thoughts" style={{ flex: 1, minHeight: '160px', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                <h4>Thought Stream <button className="expand-card-btn" title="Expand">⤢</button></h4>
                <div id="p5-time-phase-stats" className="time-phase-stats"></div>
                <div className="segmented stream-tab-bar" id="p5-det-tabs">
                  <button data-v="mobility" aria-selected="true">Mobility</button>
                  <button data-v="amenity_visit" aria-selected="false">Amenities</button>
                  <button data-v="perception" aria-selected="false">Perception</button>
                  <button data-v="cognition" aria-selected="false">Cognition</button>
                  <button data-v="all" aria-selected="false">All</button>
                </div>
                <div className="stream-summary" id="p5-det-stream-summary"></div>
                <div className="thought-stream" id="p5-det-thoughts"></div>
              </div>
            </div>
          </div>
        </div>

        <div className="slot bottom-center">
          <div className="results-legend hidden" id="p5-results-legend">
            <span className="results-legend-title">Agent Trails</span>
            <label className="legend-toggle"><input type="checkbox" id="p5-rl-heatmap" defaultChecked /><span className="legend-dot" style={{ '--c': '#ff9f0a' } as React.CSSProperties}></span>Heatmap</label>
            <label className="legend-toggle"><input type="checkbox" id="p5-rl-trails" defaultChecked /><span className="legend-dot" style={{ '--c': '#30d158' } as React.CSSProperties}></span>Trails</label>
            <label className="legend-toggle"><input type="checkbox" id="p5-rl-decision-pts" defaultChecked /><span className="legend-dot" style={{ '--c': '#5e5ce6' } as React.CSSProperties}></span>Decision Pts</label>
          </div>
          <div className="control-dock">
            <button className="btn primary" id="p5-play" disabled title="Spawn agents first">&#9654; Play</button>
            <button className="btn ghost" id="p5-pause" disabled><span className="pause-ico">&#9616;&#9616;</span> Pause</button>
            <button className="btn ghost" id="p5-step" disabled title="Spawn agents first">Step ›</button>
            <button className="btn ghost" id="p5-results" data-active="false" disabled>Results</button>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: '4px' }}>
              <span className="meta">Speed</span>
              <input type="range" className="slider" id="p5-speed" min="0.25" max="5" step="0.25" defaultValue="1" style={{ width: '80px' }} />
              <span className="meta" id="p5-speed-label">1.00×</span>
            </div>
          </div>
        </div>
      </section>

      <div className="modal-backdrop hidden" id="p5-detail-overlay">
        <div className="panel p5-detail-panel">
          <div className="head row between">
            <h3 id="p5-detail-title">Detail</h3>
            <button className="btn ghost tiny" id="p5-detail-close">×</button>
          </div>
          <div className="p5-detail-body" id="p5-detail-body"></div>
        </div>
      </div>
    </>
  );
}
