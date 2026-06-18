export function Intro() {
  return (
    <div id="intro">
      <div className="intro-sticky-scene">
        <video id="intro-video" className="intro-video" muted playsInline autoPlay preload="auto">
          <source src="/video/Sequence_2.mp4" type="video/mp4" />
        </video>

        <div className="intro-scene pos-bottom-left" id="scene-aerial" data-scene="scene-aerial">
          <p className="scene-headline">Every day, <span className="s-cyan">millions</span> of people<br />move through cities.</p>
        </div>

        <div className="intro-scene pos-bottom-left" id="scene-paths" data-scene="scene-paths">
          <p className="scene-eyebrow">The City</p>
          <p className="scene-headline">Their paths shape<br /><span className="s-cyan">neighbourhoods.</span></p>
          <p className="scene-body">Their choices shape streets.</p>
        </div>

        <div className="intro-scene pos-bottom-left" id="scene-driven" data-scene="scene-driven">
          <p className="scene-headline">But human movement<br />is <span className="s-blue">not random.</span></p>
          <p className="scene-body">It is driven by <span className="s-cyan">hunger, fatigue, curiosity</span> —<br />by memory of places once visited.</p>
        </div>

        <div className="intro-scene pos-bottom-left" id="scene-street" data-scene="scene-street">
          <p className="scene-headline">Simulating this has always<br />been a <span className="s-blue">hard problem.</span></p>
        </div>

        <div className="intro-scene pos-bottom-left" id="scene-problem" data-scene="scene-problem">
          <p className="scene-eyebrow">Traditional Approach</p>
          <p className="scene-body">Traditional models move people like particles.<br />Rule-following. Predictable. <span className="s-blue">Hollow.</span></p>
          <p className="scene-headline">They miss <span className="s-cyan">the why.</span></p>
        </div>

        <div className="intro-scene pos-bottom-center" id="scene-agents-enter" data-scene="scene-agents-enter">
          <p className="scene-large">What if each pedestrian<br />could <span className="s-blue">think?</span></p>
          <p className="scene-body scene-em">— Meet the agents.</p>
        </div>

        <div className="intro-scene pos-left-mid" id="scene-see" data-scene="scene-see">
          <p className="scene-kw-label">They</p>
          <p className="scene-keyword-block">S E E</p>
          <p className="scene-body">the street around them.</p>
        </div>

        <div className="intro-scene pos-left-mid" id="scene-know" data-scene="scene-know">
          <p className="scene-kw-label">They</p>
          <p className="scene-keyword-block">K N O W</p>
          <p className="scene-body">where they've been.</p>
        </div>

        <div className="intro-scene pos-left-mid" id="scene-understand" data-scene="scene-understand">
          <p className="scene-kw-label">They</p>
          <p className="scene-keyword-block">U N D E R S T A N D</p>
          <p className="scene-body">what they need.</p>
        </div>

        <div className="intro-scene pos-left-mid" id="scene-decide" data-scene="scene-decide">
          <p className="scene-kw-label">They</p>
          <p className="scene-keyword-block">D E C I D E</p>
          <p className="scene-body">where to go next.</p>
        </div>

        <div className="intro-scene pos-left-bottom" id="scene-synthesis" data-scene="scene-synthesis">
          <p className="scene-eyebrow">The Approach</p>
          <p className="scene-body">Powered by <span className="s-purple">large language models,</span><br />each agent <span className="s-blue">reasons, adapts,</span> and remembers.</p>
          <p className="scene-headline">This is pedestrian simulation<br /><span className="s-cyan">as it should be.</span></p>
        </div>

        <div className="intro-scene pos-center" id="scene-cta" data-scene="scene-cta">
          <p className="scene-large">Now it's your turn to<br />watch the <span className="s-cyan">city</span> <span className="s-green">think.</span></p>
        </div>

        <button className="intro-skip" id="intro-skip" aria-label="Skip intro">
          Skip
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="5 12 19 12" /><polyline points="13 6 19 12 13 18" />
          </svg>
        </button>

        <button className="start-btn hidden" id="start-btn">Start Simulating</button>
      </div>
    </div>
  );
}
