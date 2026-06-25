export function AnalyseModal() {
  return (
    <div className="modal-backdrop hidden" id="analyse-modal">
      <div className="panel analyse-modal-panel">
        <div className="head row between">
          <div>
            <div className="eyebrow">VLM Analysis</div>
            <h3>Analyse Images</h3>
          </div>
          <button className="btn ghost tiny" id="analyse-modal-close">×</button>
        </div>
        <div className="analyse-modal-body">
          <div className="analyse-modal-left">
            <div className="eyebrow" style={{ marginBottom: '10px' }}>VLM Model</div>
            <select className="select" id="p1-vlm-model-select-modal" style={{ width: '100%' }}></select>
            <div id="p1-vlm-hf-row-modal" style={{ display: 'none', flexDirection: 'column', gap: '4px', marginTop: '8px' }}>
              <span className="meta" style={{ fontSize: '11px' }}>HuggingFace repo ID</span>
              <input type="text" className="input" id="p1-vlm-hf-model-modal" placeholder="e.g. Qwen/Qwen3-VL-8B-Instruct" style={{ fontSize: '12px', width: '100%', boxSizing: 'border-box' }} />
            </div>
          </div>
          <div className="analyse-modal-right">
            <div className="row between" style={{ marginBottom: '10px' }}>
              <div className="eyebrow">Perception Fields</div>
              <button className="btn tiny" id="p1-vlm-add-param">+ Custom</button>
            </div>
            <div className="list" id="p1-vlm-param-list" style={{ overflowY: 'auto', flex: 1 }}></div>
            <div style={{ paddingTop: '12px', borderTop: '1px solid rgba(255,255,255,0.06)', marginTop: '8px' }}>
              <button className="btn primary" id="p1-vlm-analyze" style={{ width: '100%' }} disabled>Select images first</button>
              <div id="p1-vlm-progress" style={{ display: 'none', marginTop: '10px' }}>
                <div className="meta" id="p1-vlm-progress-status">Running…</div>
                <div className="ov-progress-bar"><div className="ov-progress-fill" id="p1-vlm-progress-fill" style={{ width: '0%' }}></div></div>
                <div className="ov-log" id="p1-vlm-progress-log"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
