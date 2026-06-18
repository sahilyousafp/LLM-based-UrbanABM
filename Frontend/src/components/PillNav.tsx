export function PillNav() {
  return (
    <div className="bottom-group">
      <div className="pill-row">
        <button className="panel-nav-btn" id="panel-prev-btn" title="Previous panel">
          <svg viewBox="0 0 24 24" style={{ width: '18px', height: '18px', fill: 'none', stroke: 'currentColor', strokeWidth: '2', strokeLinecap: 'round', strokeLinejoin: 'round' }}>
            <path d="M15 18l-6-6 6-6" />
          </svg>
          Prev
        </button>
        <nav className="pill-nav">
          <div className="dots">
            <div className="dot" data-panel="3" data-state="active">Personality</div>
            <div className="dot" data-panel="4" data-state="locked">Single Agent</div>
            <div className="dot" data-panel="5" data-state="locked">Multi Agent</div>
          </div>
        </nav>
        <button className="panel-nav-btn" id="panel-next-btn" title="Next panel">
          Next
          <svg viewBox="0 0 24 24" style={{ width: '18px', height: '18px', fill: 'none', stroke: 'currentColor', strokeWidth: '2', strokeLinecap: 'round', strokeLinejoin: 'round' }}>
            <path d="M9 18l6-6-6-6" />
          </svg>
        </button>
      </div>
    </div>
  );
}
