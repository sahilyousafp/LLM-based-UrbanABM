export function VLMCompareModal() {
  return (
    <div className="modal-backdrop hidden" id="vlm-compare-modal">
      <div className="panel modal" style={{ width: 'min(1280px,95vw)', maxHeight: '92vh', display: 'flex', flexDirection: 'column' }}>
        <div className="head row between">
          <div>
            <div className="eyebrow">Vision Models · Barcelona Benchmark</div>
            <h3>Zone Analysis — Click a region to compare</h3>
          </div>
          <button className="btn ghost tiny" id="vlm-compare-modal-close">×</button>
        </div>
        <div className="body" style={{ overflowY: 'auto', flex: 1, padding: '0 18px 18px' }}>
          <div id="vlm-compare-list"></div>
        </div>
      </div>
    </div>
  );
}
