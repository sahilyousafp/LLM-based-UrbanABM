export function CreateArchetypeModal() {
  return (
    <div className="modal-backdrop hidden" id="p3-create-modal">
      <div className="panel modal" style={{ width: '380px' }}>
        <div className="head row between">
          <div>
            <div className="eyebrow">New Archetype</div>
            <h3>Create Archetype</h3>
          </div>
          <button className="btn ghost tiny" id="p3-create-close">×</button>
        </div>
        <div className="body col" style={{ gap: '14px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
            <label className="field-label">Name</label>
            <input className="input" id="p3-create-name" placeholder="custom_1" defaultValue="custom_1" />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
            <label className="field-label">Age</label>
            <select className="input" id="p3-create-age">
              <option value="">Any</option>
              <option value="young">Young (18–30)</option>
              <option value="adult">Adult (31–50)</option>
              <option value="senior">Senior (51–70)</option>
            </select>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
            <label className="field-label">Gender</label>
            <select className="input" id="p3-create-gender">
              <option value="">Any</option>
              <option value="male">Male</option>
              <option value="female">Female</option>
              <option value="other">Other</option>
            </select>
          </div>
          <button className="btn primary" id="p3-create-submit" style={{ width: '100%', marginTop: '5px' }}>Create</button>
        </div>
      </div>
    </div>
  );
}
