export function LLMCompareModal() {
  return (
    <div className="modal-backdrop hidden" id="llm-compare-modal">
      <div className="panel modal llm-compare-modal-panel">
        <div className="head row between">
          <div>
            <div className="eyebrow">Model Comparison</div>
            <h3>Choose the right LLM for your simulation</h3>
          </div>
          <button className="btn ghost tiny" id="llm-compare-close">×</button>
        </div>
        <div className="body llm-compare-modal-body" id="llm-compare-body"></div>
      </div>
    </div>
  );
}
