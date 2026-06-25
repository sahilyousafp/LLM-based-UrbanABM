"""
Generate a single self-contained HTML benchmark report from JSON result files.

Usage:
    python benchmark/scripts/export_report.py

Reads from benchmark/results/*.json, writes benchmark/benchmark_report.html.
"""
import json
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent / "results"
OUTPUT_PATH = Path(__file__).parent.parent / "benchmark_report.html"

SECTIONS = [
    ("01", "Database", "01_database.json"),
    ("02", "LLM Emotion", "02_eqbench.json"),
    ("03", "Map Data", "03_map.json"),
    ("04", "VLM Perception", "04_vlm.json"),
    ("05", "System Integration", "05_system.json"),
]


def load_json(filename: str):
    path = RESULTS_DIR / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def build_html() -> str:
    data = {}
    available = []
    for sid, label, fname in SECTIONS:
        d = load_json(fname)
        if sid == "02":
            emotion = load_json("02_emotion.json")
            d = {"eqbench": d, "emotion": emotion}
        data[sid] = d
        available.append((sid, label, d is not None and (sid != "02" or d.get("eqbench") or d.get("emotion"))))

    data_json = json.dumps(data, default=str)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Urban ABM — Benchmark Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
  --bg-base: #07090c;
  --accent: #0a84ff;
  --accent-2: #5e5ce6;
  --accent-3: #64d2ff;
  --success: #30d158;
  --warning: #ff9f0a;
  --danger: #ff453a;
  --text: #f5f5f7;
  --text-2: rgba(245,245,247,0.78);
  --text-muted: rgba(245,245,247,0.55);
  --text-dim: rgba(245,245,247,0.32);
  --panel-bg: rgba(22,26,32,0.85);
  --panel-border: rgba(255,255,255,0.08);
  --panel-hl: rgba(255,255,255,0.06);
  --radius-card: 18px;
  --radius-pill: 999px;
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: "Inter", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
  background: var(--bg-base);
  color: var(--text);
  line-height: 1.5;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}}
.container {{ max-width: 1280px; margin: 0 auto; padding: 32px 24px; }}
header {{ text-align: center; margin-bottom: 32px; }}
header h1 {{ font-size: 28px; font-weight: 700; letter-spacing: -0.02em; }}
header p {{ color: var(--text-muted); font-size: 13px; margin-top: 6px; }}
.tabs {{
  display: flex; gap: 2px; background: rgba(255,255,255,0.04);
  border-radius: 11px; padding: 3px; justify-content: center;
  margin-bottom: 28px; flex-wrap: wrap;
}}
.tab-btn {{
  padding: 8px 18px; border: 1px solid transparent; background: none;
  color: var(--text-muted); border-radius: 9px; cursor: pointer;
  font-size: 13px; font-weight: 500; font-family: inherit;
  transition: background 200ms, color 200ms, box-shadow 200ms;
}}
.tab-btn:hover {{ color: var(--text-2); background: rgba(255,255,255,0.06); }}
.tab-btn.active {{
  background: rgba(255,255,255,0.13); color: var(--text);
  box-shadow: 0 1px 3px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.12);
  border-color: rgba(255,255,255,0.1);
}}
.section {{ display: none; }}
.section.active {{ display: block; }}
.card {{
  background: var(--panel-bg); border: 1px solid var(--panel-border);
  border-radius: var(--radius-card); padding: 24px;
  box-shadow: inset 0 1px 0 var(--panel-hl);
  margin-bottom: 20px;
}}
.card h3 {{ font-size: 15px; font-weight: 600; margin-bottom: 16px; color: var(--text-2); }}
.grid {{ display: grid; gap: 20px; }}
.grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); }}
.grid-3 {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
.grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }}
.stat-card {{
  background: var(--panel-bg); border: 1px solid var(--panel-border);
  border-radius: 14px; padding: 20px; text-align: center;
  box-shadow: inset 0 1px 0 var(--panel-hl);
}}
.stat-card .value {{ font-size: 32px; font-weight: 700; letter-spacing: -0.02em; }}
.stat-card .label {{ font-size: 11px; font-weight: 600; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.08em; margin-top: 4px; }}
.chip {{
  display: inline-block; padding: 3px 10px; border-radius: var(--radius-pill);
  background: rgba(255,255,255,0.06); border: 1px solid var(--panel-border);
  font-size: 11px; font-weight: 500; color: var(--text-2);
}}
.chip.accent {{ background: rgba(10,132,255,0.15); border-color: rgba(10,132,255,0.3); color: var(--accent); }}
.chip.success {{ background: rgba(48,209,88,0.15); border-color: rgba(48,209,88,0.3); color: var(--success); }}
.chip.warning {{ background: rgba(255,159,10,0.15); border-color: rgba(255,159,10,0.3); color: var(--warning); }}
.chip.danger {{ background: rgba(255,69,58,0.15); border-color: rgba(255,69,58,0.3); color: var(--danger); }}
table {{
  width: 100%; border-collapse: collapse; font-size: 13px;
}}
th {{
  text-align: left; padding: 10px 12px; font-weight: 600; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted);
  border-bottom: 1px solid var(--panel-border);
}}
td {{
  padding: 10px 12px; border-bottom: 1px solid var(--panel-border); color: var(--text-2);
}}
tr:last-child td {{ border-bottom: none; }}
.no-data {{
  text-align: center; padding: 48px 24px; color: var(--text-dim); font-size: 14px;
}}
.no-data span {{ display: block; margin-top: 8px; font-size: 12px; }}
canvas {{ max-height: 360px; }}
.chart-wrap {{ position: relative; }}
.section-desc {{
  font-size: 13px; color: var(--text-muted); margin-bottom: 20px; line-height: 1.6;
}}
</style>
</head>
<body>
<div class="container">
<header>
  <h1>Urban ABM — Benchmark Report</h1>
  <p>Generated {ts} &nbsp;·&nbsp; LLM-Based Urban Agent-Based Model</p>
</header>

<nav class="tabs" id="tabs">
  {"".join(f'<button class="tab-btn{"" if not (sid=="01") else " active"}" data-tab="{sid}">{label}</button>' for sid, label, ok in available)}
</nav>

<!-- ═══ 01 Database ═══ -->
<div class="section{" active" if True else ""}" id="sec-01">
<p class="section-desc">Spatial query latency across DuckDB, SQLite+SpatiaLite, and PostgreSQL+PostGIS — single-agent and batch (1–500 agents).</p>
<div id="db-content"></div>
</div>

<!-- ═══ 02 LLM Emotion ═══ -->
<div class="section" id="sec-02">
<p class="section-desc">EQ-Bench v2 (Paech, 2024) emotional intelligence scores + Plutchik/NRC urban emotion metrics.</p>
<div id="llm-content"></div>
</div>

<!-- ═══ 03 Map Data ═══ -->
<div class="section" id="sec-03">
<p class="section-desc">Overture Maps vs OpenStreetMap — POI coverage, walk-edge network, and query latency.</p>
<div id="map-content"></div>
</div>

<!-- ═══ 04 VLM Perception ═══ -->
<div class="section" id="sec-04">
<p class="section-desc">Vision Language Model comparison for street-scene perception (zone-based spatial analysis).</p>
<div id="vlm-content"></div>
</div>

<!-- ═══ 05 System Integration ═══ -->
<div class="section" id="sec-05">
<p class="section-desc">End-to-end system benchmark — step latency, scalability, and humanistic trajectory scoring.</p>
<div id="sys-content"></div>
</div>

</div><!-- container -->

<script>
const DATA = {data_json};
const COLORS = ['#0a84ff','#5e5ce6','#64d2ff','#30d158','#ff9f0a','#ff453a'];
const GRID_COLOR = 'rgba(255,255,255,0.06)';

Chart.defaults.color = '#f5f5f7';
Chart.defaults.borderColor = 'rgba(255,255,255,0.08)';
Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
Chart.defaults.font.size = 12;
Chart.defaults.plugins.legend.labels.boxWidth = 12;
Chart.defaults.plugins.legend.labels.padding = 16;

/* ─── Tab switching ─── */
document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('sec-' + btn.dataset.tab).classList.add('active');
  }});
}});

function noData(msg) {{
  return `<div class="no-data">No data available<span>${{msg}}</span></div>`;
}}

function statCard(value, label, color) {{
  const style = color ? `color:${{color}}` : '';
  return `<div class="stat-card"><div class="value" style="${{style}}">${{value}}</div><div class="label">${{label}}</div></div>`;
}}

/* ═══ 01 Database ═══ */
(function() {{
  const el = document.getElementById('db-content');
  const d = DATA['01'];
  if (!d) {{ el.innerHTML = noData('Run notebook 01_database_comparison.ipynb'); return; }}

  const dbs = Object.keys(d.medians);
  const medians = dbs.map(k => d.medians[k]);
  const p95s = dbs.map(k => d.p95s[k]);

  let html = '<div class="grid grid-3" style="margin-bottom:20px">';
  dbs.forEach((db, i) => {{
    html += statCard(medians[i].toFixed(2) + ' ms', db + ' (median)', COLORS[i]);
  }});
  html += '</div>';
  html += '<div class="grid grid-2">';
  html += '<div class="card"><h3>Single-Agent Query Latency</h3><div class="chart-wrap"><canvas id="c01-bar"></canvas></div></div>';
  if (d.batch) {{
    html += '<div class="card"><h3>Batch Scalability (1–500 agents)</h3><div class="chart-wrap"><canvas id="c01-batch"></canvas></div></div>';
  }}
  html += '</div>';
  el.innerHTML = html;

  new Chart(document.getElementById('c01-bar'), {{
    type: 'bar',
    data: {{
      labels: dbs,
      datasets: [
        {{ label: 'Median (ms)', data: medians, backgroundColor: COLORS.slice(0, dbs.length).map(c => c + '99'), borderColor: COLORS.slice(0, dbs.length), borderWidth: 1 }},
        {{ label: 'p95 (ms)', data: p95s, backgroundColor: COLORS.slice(0, dbs.length).map(c => c + '44'), borderColor: COLORS.slice(0, dbs.length), borderWidth: 1 }},
      ]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }} }}, scales: {{ y: {{ beginAtZero: true, title: {{ display: true, text: 'Latency (ms)' }}, grid: {{ color: GRID_COLOR }} }}, x: {{ grid: {{ display: false }} }} }} }}
  }});

  if (d.batch) {{
    const batchDbs = Object.keys(d.batch).filter(k => k !== 'agent_counts');
    const agents = d.batch.agent_counts;
    new Chart(document.getElementById('c01-batch'), {{
      type: 'line',
      data: {{
        labels: agents.map(String),
        datasets: batchDbs.map((db, i) => ({{
          label: db, data: d.batch[db], borderColor: COLORS[i], backgroundColor: COLORS[i] + '22',
          pointRadius: 4, pointBackgroundColor: COLORS[i], tension: 0.3,
        }}))
      }},
      options: {{
        responsive: true,
        scales: {{
          y: {{ type: 'logarithmic', title: {{ display: true, text: 'Total Latency (ms)' }}, grid: {{ color: GRID_COLOR }} }},
          x: {{ title: {{ display: true, text: 'Agent Count' }}, grid: {{ color: GRID_COLOR }} }}
        }}
      }}
    }});
  }}
}})();

/* ═══ 02 LLM Emotion ═══ */
(function() {{
  const el = document.getElementById('llm-content');
  const d = DATA['02'];
  if (!d || (!d.eqbench && !d.emotion)) {{ el.innerHTML = noData('Run notebook 02_llm_provider_comparison.ipynb'); return; }}

  let html = '';

  /* EQ-Bench */
  if (d.eqbench && d.eqbench.providers) {{
    const ok = d.eqbench.providers.filter(p => p.score !== null && p.score !== undefined);
    const blocked = d.eqbench.providers.filter(p => p.score === null || p.score === undefined);

    if (ok.length) {{
      html += '<div class="grid grid-' + Math.min(ok.length + 1, 4) + '" style="margin-bottom:20px">';
      ok.forEach((p, i) => {{
        const chipClass = p.score >= 70 ? 'success' : p.score >= 50 ? 'accent' : 'warning';
        html += statCard(p.score.toFixed(1), p.label + ' <span class="chip ' + chipClass + '">' + p.questions + ' questions</span>', COLORS[i]);
      }});
      html += '</div>';
      html += '<div class="card"><h3>EQ-Bench v2 — Emotional Intelligence Score (Paech, 2024)</h3><div class="chart-wrap"><canvas id="c02-eq"></canvas></div>';
      html += '<p style="font-size:11px;color:var(--text-dim);margin-top:12px">Leaderboard context: GPT-4o ~82 · Claude 3.5 ~78 · Llama-3.1-8B ~52 · Mistral-7B ~46</p></div>';
    }}
    if (blocked.length) {{
      html += '<div style="margin-bottom:16px">';
      blocked.forEach(p => {{
        html += `<span class="chip danger" style="margin-right:6px">${{p.label}}: ${{p.error || p.status}}</span>`;
      }});
      html += '</div>';
    }}
  }}

  /* Emotion / Plutchik */
  if (d.emotion && d.emotion.providers && d.emotion.providers.length) {{
    html += '<div class="card"><h3>Urban Emotion Test — Plutchik + NRC Metrics (supplementary)</h3><div class="chart-wrap"><canvas id="c02-radar"></canvas></div></div>';
  }}

  el.innerHTML = html;

  /* Draw EQ-Bench chart */
  if (d.eqbench && d.eqbench.providers) {{
    const ok = d.eqbench.providers.filter(p => p.score !== null && p.score !== undefined);
    if (ok.length && document.getElementById('c02-eq')) {{
      new Chart(document.getElementById('c02-eq'), {{
        type: 'bar',
        data: {{
          labels: ok.map(p => p.label),
          datasets: [{{ label: 'EQ-Bench Score', data: ok.map(p => p.score),
            backgroundColor: COLORS.slice(0, ok.length).map(c => c + '99'),
            borderColor: COLORS.slice(0, ok.length), borderWidth: 1 }}]
        }},
        options: {{
          indexAxis: 'y', responsive: true,
          scales: {{
            x: {{ min: 0, max: 100, grid: {{ color: GRID_COLOR }}, title: {{ display: true, text: 'Score (0 = random, 100 = perfect)' }} }},
            y: {{ grid: {{ display: false }} }}
          }},
          plugins: {{
            annotation: undefined,
            legend: {{ display: false }}
          }}
        }}
      }});
    }}
  }}

  /* Draw Plutchik radar */
  if (d.emotion && d.emotion.providers && d.emotion.providers.length && document.getElementById('c02-radar')) {{
    const axes = ['plutchik_pct','composite_pct','intensity_pct','nrc_coherence_pct','behavior_pct','json_ok_pct'];
    const axisLabels = ['Plutchik Match','Composite Detection','Intensity Calibration','NRC Coherence','Behavior Alignment','JSON Compliance'];
    new Chart(document.getElementById('c02-radar'), {{
      type: 'radar',
      data: {{
        labels: axisLabels,
        datasets: d.emotion.providers.map((p, i) => ({{
          label: p.label, data: axes.map(a => p[a] || 0),
          borderColor: COLORS[i], backgroundColor: COLORS[i] + '22', pointBackgroundColor: COLORS[i],
        }}))
      }},
      options: {{
        responsive: true,
        scales: {{ r: {{ min: 0, max: 100, ticks: {{ stepSize: 20, color: 'rgba(245,245,247,0.4)' }},
          grid: {{ color: GRID_COLOR }}, angleLines: {{ color: GRID_COLOR }},
          pointLabels: {{ font: {{ size: 11 }} }} }} }}
      }}
    }});
  }}
}})();

/* ═══ 03 Map Data ═══ */
(function() {{
  const el = document.getElementById('map-content');
  const d = DATA['03'];
  if (!d) {{ el.innerHTML = noData('Run notebook 03_map_data_comparison.ipynb'); return; }}

  let html = '<div class="grid grid-4" style="margin-bottom:20px">';
  html += statCard(d.overture_edges?.toLocaleString() || '—', 'Overture Walk Edges', COLORS[0]);
  html += statCard(d.osm_edges?.toLocaleString() || '—', 'OSM Walk Edges', COLORS[1]);
  html += statCard((d.overture_median_ms?.toFixed(2) || '—') + ' ms', 'Overture Query Latency', COLORS[0]);
  html += statCard((d.osm_median_ms?.toFixed(2) || '—') + ' ms', 'OSM Query Latency', COLORS[1]);
  html += '</div>';

  html += '<div class="card"><h3>POI Count by Category (Top 10)</h3><div class="chart-wrap"><canvas id="c03-poi"></canvas></div></div>';
  el.innerHTML = html;

  /* Merge POI data */
  const allAmenities = {{}};
  (d.overture_pois || []).forEach(p => {{ allAmenities[p.amenity] = {{ o: p.count, s: 0 }}; }});
  (d.osm_pois || []).forEach(p => {{
    if (!allAmenities[p.amenity]) allAmenities[p.amenity] = {{ o: 0, s: 0 }};
    allAmenities[p.amenity].s = p.count;
  }});
  const sorted = Object.entries(allAmenities).sort((a,b) => (b[1].o + b[1].s) - (a[1].o + a[1].s)).slice(0, 10);

  new Chart(document.getElementById('c03-poi'), {{
    type: 'bar',
    data: {{
      labels: sorted.map(s => s[0]),
      datasets: [
        {{ label: 'Overture', data: sorted.map(s => s[1].o), backgroundColor: COLORS[0] + '99', borderColor: COLORS[0], borderWidth: 1 }},
        {{ label: 'OSM', data: sorted.map(s => s[1].s), backgroundColor: COLORS[1] + '99', borderColor: COLORS[1], borderWidth: 1 }},
      ]
    }},
    options: {{
      indexAxis: 'y', responsive: true,
      scales: {{
        x: {{ grid: {{ color: GRID_COLOR }}, title: {{ display: true, text: 'Count' }} }},
        y: {{ grid: {{ display: false }} }}
      }}
    }}
  }});
}})();

/* ═══ 04 VLM Perception ═══ */
(function() {{
  const el = document.getElementById('vlm-content');
  const d = DATA['04'];
  if (!d || !d.models || !d.models.length) {{ el.innerHTML = noData('Run notebook 04_vlm_perception_comparison.ipynb'); return; }}

  let html = '<div class="card"><h3>VLM Model Comparison</h3><table><thead><tr>';
  html += '<th>Model</th><th>Latency (s)</th><th>Zones OK</th><th>Texts</th><th>Architecture Styles</th><th>Scene Description</th>';
  html += '</tr></thead><tbody>';
  d.models.forEach(m => {{
    const zoneChip = m.zones_populated >= 4 ? 'success' : m.zones_populated >= 2 ? 'warning' : 'danger';
    html += `<tr>
      <td style="font-weight:600">${{m.name}}</td>
      <td>${{m.latency_s}}s</td>
      <td><span class="chip ${{zoneChip}}">${{m.zones_populated}}/${{m.total_zones}}</span></td>
      <td>${{m.texts_found}}</td>
      <td style="font-size:12px">${{(m.arch_styles || []).join(', ') || '—'}}</td>
      <td style="font-size:12px;max-width:300px;color:var(--text-muted)">${{m.scene || '—'}}</td>
    </tr>`;
  }});
  html += '</tbody></table></div>';
  el.innerHTML = html;
}})();

/* ═══ 05 System Integration ═══ */
(function() {{
  const el = document.getElementById('sys-content');
  const d = DATA['05'];
  if (!d) {{ el.innerHTML = noData('Run notebook 05_system_integration_benchmark.ipynb'); return; }}

  let html = '';

  /* Stat cards from score summary */
  if (d.score_summary) {{
    html += '<div class="grid grid-2" style="margin-bottom:20px">';
    Object.entries(d.score_summary).forEach(([mode, dims]) => {{
      const avg = Object.values(dims).reduce((a,b) => a+b, 0) / Object.values(dims).length;
      const color = mode === 'LLM-driven' ? COLORS[0] : COLORS[3];
      html += statCard((avg * 100).toFixed(1) + '%', mode + ' — Avg Humanistic Score', color);
    }});
    html += '</div>';
  }}

  html += '<div class="grid grid-2">';
  if (d.timing && d.timing.length) html += '<div class="card"><h3>Step Latency Over Time</h3><div class="chart-wrap"><canvas id="c05-latency"></canvas></div></div>';
  if (d.scale && d.scale.length) html += '<div class="card"><h3>Scalability (Agent Count)</h3><div class="chart-wrap"><canvas id="c05-scale"></canvas></div></div>';
  html += '</div>';

  if (d.score_summary) {{
    html += '<div class="card"><h3>Humanistic Dimensions Radar</h3><div class="chart-wrap" style="max-width:500px;margin:0 auto"><canvas id="c05-radar"></canvas></div></div>';
  }}

  el.innerHTML = html;

  /* Step latency */
  if (d.timing && d.timing.length && document.getElementById('c05-latency')) {{
    const modes = [...new Set(d.timing.map(t => t.mode))];
    new Chart(document.getElementById('c05-latency'), {{
      type: 'line',
      data: {{
        datasets: modes.map((mode, i) => {{
          const pts = d.timing.filter(t => t.mode === mode);
          return {{
            label: mode, data: pts.map(t => ({{ x: t.step, y: t.latency_ms }})),
            borderColor: i === 0 ? COLORS[0] : COLORS[3], backgroundColor: (i === 0 ? COLORS[0] : COLORS[3]) + '22',
            pointRadius: 2, tension: 0.3,
          }};
        }})
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ type: 'linear', title: {{ display: true, text: 'Step' }}, grid: {{ color: GRID_COLOR }} }},
          y: {{ title: {{ display: true, text: 'Latency (ms)' }}, grid: {{ color: GRID_COLOR }} }}
        }}
      }}
    }});
  }}

  /* Scalability */
  if (d.scale && d.scale.length && document.getElementById('c05-scale')) {{
    const modes = [...new Set(d.scale.map(s => s.mode))];
    new Chart(document.getElementById('c05-scale'), {{
      type: 'line',
      data: {{
        datasets: modes.map((mode, i) => {{
          const pts = d.scale.filter(s => s.mode === mode).sort((a,b) => a.n_agents - b.n_agents);
          return {{
            label: mode, data: pts.map(s => ({{ x: s.n_agents, y: s.step_ms }})),
            borderColor: i === 0 ? COLORS[0] : COLORS[3], backgroundColor: (i === 0 ? COLORS[0] : COLORS[3]) + '22',
            pointRadius: 5, pointBackgroundColor: i === 0 ? COLORS[0] : COLORS[3], tension: 0.3,
          }};
        }})
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ type: 'linear', title: {{ display: true, text: 'Number of Agents' }}, grid: {{ color: GRID_COLOR }} }},
          y: {{ title: {{ display: true, text: 'Step Time (ms)' }}, grid: {{ color: GRID_COLOR }} }}
        }}
      }}
    }});
  }}

  /* Humanistic radar */
  if (d.score_summary && document.getElementById('c05-radar')) {{
    const dims = d.dims || Object.keys(Object.values(d.score_summary)[0]);
    const dimLabels = dims.map(d => d.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase()));
    const modes = Object.keys(d.score_summary);
    new Chart(document.getElementById('c05-radar'), {{
      type: 'radar',
      data: {{
        labels: dimLabels,
        datasets: modes.map((mode, i) => ({{
          label: mode, data: dims.map(dim => (d.score_summary[mode][dim] || 0) * 100),
          borderColor: i === 0 ? COLORS[0] : COLORS[3],
          backgroundColor: (i === 0 ? COLORS[0] : COLORS[3]) + '22',
          pointBackgroundColor: i === 0 ? COLORS[0] : COLORS[3],
        }}))
      }},
      options: {{
        responsive: true,
        scales: {{ r: {{ min: 0, max: 100, ticks: {{ stepSize: 20, color: 'rgba(245,245,247,0.4)' }},
          grid: {{ color: GRID_COLOR }}, angleLines: {{ color: GRID_COLOR }},
          pointLabels: {{ font: {{ size: 12 }} }} }} }}
      }}
    }});
  }}
}})();
</script>
</body>
</html>"""


def main():
    html = build_html()
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Report generated: {OUTPUT_PATH} ({size_kb:.0f} KB)")

    for sid, label, fname in SECTIONS:
        path = RESULTS_DIR / fname
        status = "OK" if path.exists() else "MISSING"
        if sid == "02":
            eq = (RESULTS_DIR / "02_eqbench.json").exists()
            em = (RESULTS_DIR / "02_emotion.json").exists()
            status = f"eqbench={'OK' if eq else 'MISSING'}, emotion={'OK' if em else 'MISSING'}"
        print(f"  [{sid}] {label:25s} {status}")


if __name__ == "__main__":
    main()
