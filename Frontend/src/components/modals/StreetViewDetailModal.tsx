export function StreetViewDetailModal() {
  return (
    <div className="modal-backdrop hidden" id="sv-detail-modal">
      <div className="panel sv-detail-panel">
        <div className="head row between">
          <div>
            <div className="eyebrow">Street View</div>
            <h3 id="sv-detail-title">Selected Point</h3>
          </div>
          <button className="btn ghost tiny" id="sv-detail-close">×</button>
        </div>
        <div className="sv-detail-body">
          <div className="sv-detail-left">
            <div className="popover-image">
              <img id="sv-detail-img" alt="Street View" style={{ filter: 'brightness(1) contrast(1) saturate(1)' }} />
              <div className="sv-zone-highlight" id="sv-zone-highlight"></div>
              <div className="badges">
                <span className="chip" id="sv-detail-coord">—</span>
                <span className="chip accent" id="sv-detail-heading">—</span>
              </div>
            </div>
            <div className="filter-grid">
              <label className="col"><span className="lbl">Brightness</span><input className="slider" id="sv-flt-b" type="range" min="0.4" max="1.6" step="0.05" defaultValue="1" /></label>
              <label className="col"><span className="lbl">Contrast</span><input className="slider" id="sv-flt-c" type="range" min="0.4" max="1.6" step="0.05" defaultValue="1" /></label>
              <label className="col"><span className="lbl">Saturate</span><input className="slider" id="sv-flt-s" type="range" min="0" max="2" step="0.05" defaultValue="1" /></label>
            </div>
          </div>
          <div className="sv-detail-right" id="sv-detail-right">
            <div className="sv-detail-tabs hidden" id="sv-detail-tabs">
              <button className="sv-tab active" data-tab="gallery">Gallery</button>
              <button className="sv-tab" data-tab="analysis" id="sv-tab-analysis" data-disabled="true">Analysis</button>
            </div>
            <div className="scene-fields" id="sv-detail-fields"></div>
            <div className="sv-gallery hidden" id="sv-detail-gallery"></div>
          </div>
        </div>
      </div>
    </div>
  );
}
