export function ZoneFab() {
  return (
    <div className="zone-fab hidden" id="zone-fab">
      <button className="btn primary" id="p1-draw-fab">
        <svg viewBox="0 0 24 24" style={{ width: '14px', height: '14px', stroke: 'currentColor', fill: 'none', strokeWidth: '2', verticalAlign: '-2px', marginRight: '5px' }}>
          <rect x="3" y="3" width="18" height="18" rx="2" />
        </svg>
        Draw Zone
      </button>
      <button className="btn ghost tiny" id="p1-clear-fab">Clear</button>
    </div>
  );
}
