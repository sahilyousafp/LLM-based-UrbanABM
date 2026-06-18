export function OvertureModal() {
  return (
    <div className="modal-backdrop hidden" id="overture-modal">
      <div className="panel modal overture-modal-panel">
        <div className="head row between">
          <div>
            <div className="eyebrow">Overture Maps</div>
            <h3>Download Details</h3>
          </div>
          <button className="btn ghost tiny" id="overture-modal-close">×</button>
        </div>
        <div className="body col" style={{ gap: '14px' }}>
          <div className="field-row">
            <div className="field-label">Location Name</div>
            <input className="input" id="ov-name" placeholder="e.g. Eixample Barcelona" defaultValue="custom_zone" />
          </div>
          <div className="field-row">
            <div className="field-label">Bounding Box</div>
            <div className="ov-bbox-pill" id="ov-bbox-pill">No zone drawn</div>
          </div>
          <div className="field-row">
            <div className="field-label">Layers to Download</div>
            <div className="col" style={{ gap: '8px', marginTop: '4px' }}>
              <label className="ov-check-row"><input type="checkbox" id="ov-layer-buildings" defaultChecked /> <span>Buildings</span> <span className="meta">footprints, height, class</span></label>
              <label className="ov-check-row"><input type="checkbox" id="ov-layer-amenities" defaultChecked /> <span>Amenities / Places</span> <span className="meta">POIs, names, categories</span></label>
              <label className="ov-check-row"><input type="checkbox" id="ov-layer-transport" defaultChecked /> <span>Walk Network</span> <span className="meta">pedestrian edges, roads</span></label>
            </div>
          </div>
          <div className="field-row">
            <div className="field-label">GCP Project ID <span className="meta">(optional — for BigQuery fallback)</span></div>
            <input className="input" id="ov-gcp" placeholder="my-gcp-project-id" />
          </div>
          <div className="field-row">
            <div className="field-label">Overture Release <span className="meta">(from OVERTURE_RELEASE in .env, or override)</span></div>
            <input className="input" id="ov-release" placeholder="2024-11-13.0" />
          </div>
          <button className="btn primary" id="ov-start-btn">Start Download</button>
        </div>
      </div>
    </div>
  );
}
