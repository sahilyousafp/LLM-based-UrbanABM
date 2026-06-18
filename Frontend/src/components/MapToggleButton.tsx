export function MapToggleButton() {
  return (
    <button className="map-toggle" id="map-toggle" title="Toggle Map">
      <svg viewBox="0 0 24 24" style={{ width: '18px', height: '18px', fill: 'none', stroke: 'currentColor', strokeWidth: '2', strokeLinecap: 'round', strokeLinejoin: 'round' }}>
        <path d="M1 6v16l7-4 8 4 7-4V2l-7 4-8-4L1 6z" />
        <path d="M8 2v16M16 6v16" />
      </svg>
      Map
    </button>
  );
}
