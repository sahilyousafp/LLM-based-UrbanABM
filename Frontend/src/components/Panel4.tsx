export function Panel4() {
  return (
    <>
      <section className="panel-stage" id="panel-4" data-active="false">
        <div className="slot left-tall p4-left" style={{ display: 'flex', flexDirection: 'column' }}>
          <div className="panel p4-agent-panel">
            <div className="p4-profile-card" id="p4-profile-card">
              <div className="p4-profile-top">
                <div className="p4-profile-left">
                  <div className="p4-profile-left-top">
                    <div className="p4-profile-name" id="p4-profile-name">—</div>
                    <div className="p4-profile-tag" id="p4-profile-tag"></div>
                  </div>
                  <div className="p4-profile-desc" id="p4-profile-desc"></div>
                </div>
                <div className="p4-profile-right">
                  <canvas id="p4-char-canvas"></canvas>
                </div>
              </div>
              <div className="p4-profile-bottom">
                <div className="body col" style={{ gap: '8px', padding: 0, border: 'none' }}>
                  <div className="col" style={{ gap: '10px' }}>
                    <div className="field-label" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      Archetype
                      <button className="info-btn">
                        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                        <span className="info-tooltip">Archetypes shape LLM decision-making: Resident (familiar routes), Commuter (efficient, direct), Tourist (exploratory), Student (social, budget-conscious).</span>
                      </button>
                    </div>
                    <select className="input" id="p4-archetype">
                      <option value="resident">Resident</option>
                      <option value="commuter">Commuter</option>
                      <option value="tourist">Tourist</option>
                      <option value="student">Student</option>
                    </select>
                  </div>
                  <div className="row" style={{ gap: '6px', marginTop: '10px' }}>
                    <button className="btn primary" id="p4-pick-start" title="Pick Start" style={{ flex: 1, justifyContent: 'center', padding: '8px' }}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                    </button>
                    <button className="btn" id="p4-pick-target" disabled title="Pick Target" style={{ flex: 1, justifyContent: 'center', padding: '8px' }}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>
                    </button>
                    <button className="btn ghost" id="p4-reset" title="Reset" style={{ flex: 1, justifyContent: 'center', padding: '8px' }}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.03"/></svg>
                    </button>
                  </div>
                  <div className="meta" id="p4-config-status"></div>
                </div>
              </div>
            </div>
          </div>

          <div className="panel" id="p4-rec-panel" style={{ marginTop: '12px' }}>
            <div className="head"><div className="eyebrow">Session</div><h3>Record Agent</h3></div>
            <div className="body col" style={{ gap: '10px' }}>
              <div className="segmented" id="p4-rec-tabs">
                <button data-v="record" aria-selected="true">Record</button>
                <button data-v="replay" aria-selected="false">Replay</button>
              </div>
              <div id="p4-rec-tab-record" className="col" style={{ gap: '8px' }}>
                <input className="input" id="p4-rec-name" placeholder="Session name (optional)" />
                <div className="row" style={{ gap: '6px' }}>
                  <button className="btn primary" id="p4-rec-start">&#9679; Start</button>
                  <button className="btn ghost" id="p4-rec-stop" disabled>&#9632; Stop</button>
                </div>
                <div className="meta" id="p4-rec-status">Idle</div>
              </div>
              <div id="p4-rec-tab-replay" className="col" style={{ gap: '8px', display: 'none' }}>
                <div className="row" style={{ gap: '6px', alignItems: 'center' }}>
                  <select className="input" id="p4-playback-select" style={{ flex: 1, minWidth: 0 }}>
                    <option value="">— select a session —</option>
                  </select>
                  <button className="btn ghost tiny" id="p4-playback-refresh" title="Refresh list">&#8635;</button>
                </div>
                <button className="btn primary" id="p4-playback-load" disabled>Load on Map</button>
                <div className="meta" id="p4-playback-status">No recording loaded.</div>
              </div>
            </div>
          </div>

          <div className="metric-card expandable-card p4-perception-card" data-detail-id="perception" style={{ marginTop: 'auto', flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <h4>What The Agent Sees <span className="perc-mode-chip" id="p4-perc-mode-chip"></span><button className="expand-card-btn" title="Expand">⤢</button></h4>
            <div id="p4-perception" style={{ flex: 1, overflowY: 'auto' }}></div>
          </div>
        </div>

        <div className="slot right-tall">
          <div className="metric-card expandable-card" data-detail-id="emotion-cognition" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
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
                  <div className="emotion-svg-wrap"><svg viewBox="-1 -1 2 2" id="p4-emotion-svg"></svg></div>
                  <div className="emotion-legend" id="p4-emotion-legend"></div>
                </div>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="cognition-grid" style={{ gridTemplateColumns: '1fr' }}>
                  <div className="cell"><div className="v" id="p4-mood">—</div><div className="l">Mood</div></div>
                  <div className="cell"><div className="v" id="p4-curiosity">—</div><div className="l">Curiosity</div></div>
                  <div className="cell"><div className="v" id="p4-fatigue">—</div><div className="l">Fatigue</div></div>
                </div>
              </div>
            </div>
          </div>

          <div className="metric-card expandable-card" data-detail-id="needs">
            <h4>Needs <button className="expand-card-btn" title="Expand">⤢</button></h4>
            <div className="needs-bar" id="p4-needs"></div>
          </div>

          <div className="metric-card thought-stream-card expandable-card" data-detail-id="thoughts">
            <h4>Thought Stream <button className="expand-card-btn" title="Expand">⤢</button></h4>
            <div id="p4-time-phase-stats" className="time-phase-stats"></div>
            <div className="segmented stream-tab-bar" id="p4-stream-tabs">
              <button data-v="mobility" aria-selected="true">Mobility</button>
              <button data-v="amenity_visit" aria-selected="false">Amenities</button>
              <button data-v="perception" aria-selected="false">Perception</button>
              <button data-v="all" aria-selected="false">All</button>
            </div>
            <div className="stream-summary" id="p4-stream-summary"></div>
            <div className="thought-stream" id="p4-thoughts"></div>
          </div>
        </div>

        <div className="slot bottom-center">
          <div className="results-legend hidden" id="p4-results-legend">
            <span className="results-legend-title">Map Overlays</span>
            <label className="legend-toggle">
              <input type="checkbox" id="rl-decision-pts" defaultChecked />
              <span className="legend-dot" style={{ '--c': '#5e5ce6' } as React.CSSProperties}></span>Decision Points
            </label>
            <label className="legend-toggle">
              <input type="checkbox" id="rl-goal-changes" defaultChecked />
              <span className="legend-dot" style={{ '--c': '#ff9f0a' } as React.CSSProperties}></span>Goal Changes
            </label>
          </div>
          <div className="control-dock">
            <button className="btn primary" id="p4-play">&#9654; Play</button>
            <button className="btn ghost" id="p4-pause" disabled><span className="pause-ico">&#9616;&#9616;</span> Pause</button>
            <button className="btn ghost" id="p4-step" disabled>Step ›</button>
            <button className="btn ghost" id="p4-results" data-active="false" disabled>Results</button>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: '4px' }}>
              <span className="meta">Speed</span>
              <input type="range" className="slider" id="p4-speed" min="0.25" max="5" step="0.25" defaultValue="1" style={{ width: '80px' }} />
              <span className="meta" id="p4-speed-label">1.00×</span>
            </div>
          </div>
        </div>
      </section>

      <div className="modal-backdrop hidden" id="p4-detail-overlay">
        <div className="panel p4-detail-panel">
          <div className="head row between">
            <h3 id="p4-detail-title">Detail</h3>
            <button className="btn ghost tiny" id="p4-detail-close">×</button>
          </div>
          <div className="p4-detail-body" id="p4-detail-body"></div>
        </div>
      </div>
    </>
  );
}
