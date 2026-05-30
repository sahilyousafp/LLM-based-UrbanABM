// Urban ABM — Apple HIG 5-panel pedestrian simulation lab.
// Single entry; esbuild bundles src/main.ts + dependencies to dist/main.js.

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type {
  UABMState, PanelId, PanelStatus, PerceptionFieldSpec, VLMCardSpec,
  ArchetypeMap, ArchetypeProfile, DailyPlanPhase, SingleAgentState,
  MultiAgentState, LLMSelection, MapboxMap, MapboxMapEvent, MapboxMarker,
  MapboxPopup, MapboxDraw, StreetViewFeatureProps,
} from './types';

declare const mapboxgl: typeof window.mapboxgl;
declare const MapboxDraw: typeof window.MapboxDraw;

/* =====================================================================
   STATE — defaults + localStorage round-trip
   ===================================================================== */
const DEFAULT_STATE: UABMState = {
  currentPanel: 1,
  panelStatus: { 1: 'active', 2: 'locked', 3: 'locked', 4: 'locked', 5: 'locked' },
  mapStyle: 'dark',
  theme: 'dark',
  mapboxToken: '',
  mapServerUrl: 'http://127.0.0.1:8000',
  labServerUrl: 'http://127.0.0.1:8100',
  perceptionMode: 'both',
  layers: { buildings: true, walk: true, amenities: false, streetview: true },
  zone: { bbox: null, spacing: 200 },
  selectedPoint: null,
  vlm: { provider: 'qwen25vl-3b', enabledFields: null, customPrompt: {}, customFields: [], fieldStructures: {} },
  archetypes: null,
  selectedArchetype: null,
  singleAgent: {
    id: null, archetype: 'resident',
    start: null, target: null,
    navMode: 'both', navGpsDist: 120, navCompassDist: 60,
    llm: { mode: 'local', providerId: 'ollama', model: '', apiKey: '' },
    moodHistory: [], positionHistory: [], playing: false,
  },
  multiAgent: {
    count: 15, spawnMode: 'random',
    spawnPoints: [], homePoints: [], workPoints: [],
    archetypeMix: { resident: 0.25, commuter: 0.25, tourist: 0.25, student: 0.25 },
    llm: { mode: 'local', providerId: 'ollama', model: '', apiKey: '' },
    playing: false, speed: 1.0,
  },
  recordingSession: null,
};

const STATE_KEY = 'uabm:state:v2';
function loadState(): UABMState {
  try {
    const raw = localStorage.getItem(STATE_KEY);
    if (!raw) return structuredClone(DEFAULT_STATE);
    const parsed = JSON.parse(raw) as Partial<UABMState>;
    const merged: UABMState = { ...DEFAULT_STATE, ...parsed };
    // Defensive merge of nested objects so newly-introduced keys exist.
    for (const k of Object.keys(DEFAULT_STATE) as (keyof UABMState)[]) {
      const defVal = (DEFAULT_STATE as unknown as Record<string, unknown>)[k];
      if (defVal && typeof defVal === 'object' && !Array.isArray(defVal)) {
        (merged as unknown as Record<string, unknown>)[k] = { ...(defVal as object), ...((parsed as unknown as Record<string, unknown>)[k] as object || {}) };
      }
    }
    return merged;
  } catch {
    return structuredClone(DEFAULT_STATE);
  }
}
export const state: UABMState = loadState();
export function saveState(): void {
  try { localStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch { /* quota */ }
}

/* =====================================================================
   UI helpers
   ===================================================================== */
export const $ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document): T | null =>
  root.querySelector<T>(sel);
export const $$ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document): T[] =>
  Array.from(root.querySelectorAll<T>(sel));

export const fmtPct = (n: number | null | undefined): string =>
  (n === null || n === undefined || Number.isNaN(n)) ? '—' : `${Math.round(n * 100)}%`;

export function escapeHtml(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string)
  );
}

export function toast(msg: string, type: 'success' | 'warning' | 'danger' | '' = ''): void {
  const host = $('#toasts'); if (!host) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(20px)'; }, 4000);
  setTimeout(() => el.remove(), 4500);
}

/* =====================================================================
   API client — wraps fetch to the two backends
   ===================================================================== */
const api = {
  get map(): string { return state.mapServerUrl.replace(/\/+$/, ''); },
  get lab(): string { return state.labServerUrl.replace(/\/+$/, ''); },
  async _fetch(base: string, path: string, opts: RequestInit = {}): Promise<unknown> {
    const url = base + path;
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  },
  m<T = unknown>(path: string, opts?: RequestInit): Promise<T> {
    return this._fetch(this.map, path, opts) as Promise<T>;
  },
  l<T = unknown>(path: string, opts?: RequestInit): Promise<T> {
    return this._fetch(this.lab, path, opts) as Promise<T>;
  },
  postJSON<T = unknown>(base: string, path: string, body: unknown): Promise<T> {
    return this._fetch(base, path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }) as Promise<T>;
  },
};
export { api };

/* =====================================================================
   SCHEMA — perception fields + VLM cards
   ===================================================================== */
// Real StreetPLM schema — matches SceneAnalysis in street_plm_job.py and actual result JSONs.
// Zone values: far_left | left | center | right | far_right
const PERCEPTION_FIELDS: PerceptionFieldSpec[] = [
  { key: 'scene',             label: 'Scene overview',    prompt: 'One sentence describing the overall urban scene.' },
  { key: 'lighting',          label: 'Lighting',          prompt: 'Per-zone lighting: {zone, element, condition: dark|dim|adequate|bright}.' },
  { key: 'spatial_character', label: 'Spatial character', prompt: 'Per-zone: {zone, width: narrow|moderate|wide, enclosure: open|semi|enclosed, passability: clear|obstructed, lane_type: sidewalk|road|shared|plaza, crossing: none|zebra|signalised}.' },
  { key: 'crowdedness',       label: 'Crowdedness',       prompt: 'Per-zone pedestrian density: {zone, density_level: empty|sparse|moderate|dense}.' },
  { key: 'greenery',          label: 'Greenery',          prompt: 'Per-zone vegetation: {zone, element, coverage: none|sparse|moderate|dense}.' },
  { key: 'street_amenities',  label: 'Street amenities',  prompt: 'Per-zone street furniture: {zone, element, material_and_colour, presence: none|few|several|many}.' },
  { key: 'visible_text',      label: 'Visible text',      prompt: 'Readable text per zone: {text, zone, type: sign|label|graffiti}.' },
];

const FIELD_DEFAULT_SUBFIELDS: Record<string, PerceptionSubField[]> = {
  lighting:          [{ key: 'condition',        values: ['dark','dim','adequate','bright'] }],
  spatial_character: [{ key: 'width',            values: ['narrow','moderate','wide'] },
                      { key: 'enclosure',         values: ['open','semi','enclosed'] },
                      { key: 'passability',       values: ['clear','obstructed'] },
                      { key: 'lane_type',         values: ['sidewalk','road','shared','plaza'] },
                      { key: 'crossing',          values: ['none','zebra','signalised'] }],
  crowdedness:       [{ key: 'density_level',    values: ['empty','sparse','moderate','dense'] }],
  greenery:          [{ key: 'coverage',         values: ['none','sparse','moderate','dense'] }],
  street_amenities:  [{ key: 'material_colour',  values: [] },
                      { key: 'presence',          values: ['none','few','several','many'] }],
  visible_text:      [{ key: 'type',             values: ['sign','label','graffiti'] }],
};

const VLM_CARDS: VLMCardSpec[] = [
  { id: 'qwen25vl-3b', name: 'Qwen2.5-VL 3B', active: true,
    pros: 'Best speed/quality balance. Already cached for all 300+ points.',
    cons: 'Smaller context window than 7B variant.',
    props: { Latency: '~2.5s', Memory: '6 GB', License: 'Tongyi' } },
  { id: 'qwen25vl-7b', name: 'Qwen2.5-VL 7B',
    pros: 'Stronger spatial reasoning and richer captions.',
    cons: 'Needs 12 GB VRAM; slower.',
    props: { Latency: '~6.0s', Memory: '12 GB', License: 'Tongyi' } },
  { id: 'llava-1.6-7b', name: 'LLaVA-1.6 7B',
    pros: 'Robust general-purpose VLM, well documented.',
    cons: 'Weaker structured-output adherence.',
    props: { Latency: '~5.5s', Memory: '11 GB', License: 'Apache' } },
  { id: 'pixtral-12b', name: 'Pixtral 12B',
    pros: 'Top quality on complex urban scenes.',
    cons: 'Needs 24 GB VRAM; cloud cost.',
    props: { Latency: '~9.0s', Memory: '24 GB', License: 'Apache' } },
  { id: 'custom-hf', name: '+ Custom HuggingFace',
    pros: 'Paste any HF repo_id (vision-language).',
    cons: 'Requires VLM runtime (v2).',
    props: { Latency: '—', Memory: '—', License: '—' } },
];

/* =====================================================================
   MAP — single instance shared across panels
   ===================================================================== */
let map: MapboxMap | null = null;
let mapDraw: MapboxDraw | null = null;
let mapReady = false;
let amenityPopup: MapboxPopup | null = null;

async function initMap(): Promise<void> {
  try {
    const config = await api.m<{
      mapbox_token?: string;
      available_providers?: { id: string; name: string; description?: string }[];
    }>('/api/config/frontend');
    state.mapboxToken = config.mapbox_token || state.mapboxToken;
    state.availableProviders = config.available_providers || [];
    saveState();
  } catch (e) {
    console.warn('Could not fetch /api/config/frontend:', e);
  }

  if (!state.mapboxToken || /^pk\.your_/.test(state.mapboxToken)) {
    toast('Mapbox token missing — set MAPBOX_TOKEN in .env then refresh.', 'danger');
    return;
  }
  mapboxgl.accessToken = state.mapboxToken;
  const styleUrl = state.theme === 'light'
    ? 'mapbox://styles/mapbox/light-v11'
    : 'mapbox://styles/mapbox/dark-v11';
  map = new mapboxgl.Map({
    container: 'map',
    style: styleUrl,
    center: [2.17, 41.39],
    zoom: 14,
    pitch: 0, bearing: 0,
    attributionControl: false,
  }) as unknown as MapboxMap;
  (window.UABM as Record<string, unknown>).map = map;
  map.on('load', async () => {
    mapReady = true;
    try { await loadStaticLayers(); } catch (e) { console.warn(e); }
    activatePanel(state.currentPanel);
  });
}

async function loadStaticLayers(): Promise<void> {
  if (!map) return;
  // Buildings
  try {
    const data = await api.m('/api/buildings');
    map.addSource('buildings', { type: 'geojson', data });
    const isLight = state.theme === 'light';
    map.addLayer({
      id: 'buildings-fill', type: 'fill', source: 'buildings',
      paint: {
        'fill-color': isLight ? '#ddd8cc' : '#1d232b',
        'fill-opacity': isLight ? 0.55 : 0.65,
        'fill-outline-color': isLight ? '#b8b2a8' : '#2c343d',
      },
    });
    map.addLayer({
      id: 'buildings-line', type: 'line', source: 'buildings',
      paint: {
        'line-color': isLight ? '#b8b2a8' : '#3a4350',
        'line-width': 0.5,
        'line-opacity': isLight ? 0.6 : 0.5,
      },
    });
  } catch (e) { console.warn('buildings layer failed', e); }

  // Walk network
  try {
    const data = await api.m('/api/walk_network');
    map.addSource('walk', { type: 'geojson', data });
    map.addLayer({
      id: 'walk-line', type: 'line', source: 'walk',
      paint: { 'line-color': '#5e5ce6', 'line-width': 1.0, 'line-opacity': 0.32 },
    });
  } catch (e) { console.warn('walk layer failed', e); }

  // Amenities
  try {
    const data = await api.m('/api/amenities');
    map.addSource('amenities', { type: 'geojson', data });
    map.addLayer({
      id: 'amenities-pt', type: 'circle', source: 'amenities',
      paint: {
        'circle-radius': 3,
        'circle-color': '#c4b5fd',
        'circle-stroke-width': 0,
        'circle-opacity': 0.4,
      },
    });
    map.on('click', 'amenities-pt', onAmenityClick);
    map.on('mouseenter', 'amenities-pt', () => { if (map) map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'amenities-pt', () => { if (map) map.getCanvas().style.cursor = ''; });
  } catch (e) { console.warn('amenities layer failed', e); }

  // Streetview
  try {
    const data = await api.m<{ features: unknown[] }>('/api/streetview_grid');
    map.addSource('sv', { type: 'geojson', data });
    map.addLayer({
      id: 'sv-pt', type: 'circle', source: 'sv',
      paint: {
        'circle-radius': 4,
        // cyan = analyzed (has VLM result), grey-white = downloaded image only
        'circle-color': ['match', ['get', 'schema'], 'image_only', '#aeaeb2', '#64d2ff'],
        'circle-stroke-color': ['match', ['get', 'schema'], 'image_only', '#636366', '#0a84ff'],
        'circle-stroke-width': 0.5,
        'circle-opacity': 0.85,
      },
    });
    map.on('click', 'sv-pt', onStreetviewClick);
    map.on('mouseenter', 'sv-pt', () => { if (map) map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'sv-pt', () => { if (map) map.getCanvas().style.cursor = ''; });
    // Populate Existing KPI from actual image count, not just analyzed results
    try {
      const stats = await api.m<{ images: number; results: number }>('/api/streetview/stats');
      const kpi = $('#p1-kpi-existing'); if (kpi) kpi.textContent = String(stats.images);
    } catch {
      const kpi = $('#p1-kpi-existing'); if (kpi) kpi.textContent = String(data.features.length);
    }
  } catch (e) { console.warn('streetview layer failed', e); }

  // Selected streetview point highlight
  map.addSource('sv-selected', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'sv-pt-selected', type: 'circle', source: 'sv-selected',
    paint: {
      'circle-radius': 9,
      'circle-color': '#64d2ff',
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2.5,
      'circle-opacity': 0.95,
    },
  });

  // Selected amenity highlight — darker purple, white border
  map.addSource('amenity-selected', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'amenity-pt-selected', type: 'circle', source: 'amenity-selected',
    paint: {
      'circle-radius': 8,
      'circle-color': '#7c3aed',
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2,
      'circle-opacity': 0.95,
    },
  });

  // Candidates / pins / agents / trail / planned / reachable
  map.addSource('sv-candidates', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'sv-candidates-pt', type: 'circle', source: 'sv-candidates',
    paint: {
      'circle-radius': 5,
      'circle-color': '#636366',
      'circle-stroke-color': '#48484a', 'circle-stroke-width': 1, 'circle-opacity': 0.7,
    },
  });

  // Points downloaded in the current session — shown in orange until page refresh
  map.addSource('sv-downloading', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'sv-downloading-pt', type: 'circle', source: 'sv-downloading',
    paint: {
      'circle-radius': 6,
      'circle-color': '#ff9f0a',
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.95,
    },
  });

  map.addSource('spawn-pins', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'spawn-pins-pt', type: 'circle', source: 'spawn-pins',
    paint: {
      'circle-radius': 7,
      'circle-color': ['match', ['get', 'kind'], 'home', '#30d158', 'work', '#0a84ff', '#ffa337'],
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2,
    },
  });

  map.addSource('agents', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'agents-glow', type: 'circle', source: 'agents',
    paint: {
      'circle-radius': 11,
      'circle-color': ['match', ['get', 'archetype'],
        'resident', '#30d158', 'commuter', '#0a84ff',
        'tourist', '#ff9f0a', 'student', '#ff375f',
        '#ffffff'],
      'circle-opacity': 0.2, 'circle-blur': 0.6,
    },
  });
  map.addLayer({
    id: 'agents-pt', type: 'circle', source: 'agents',
    paint: {
      'circle-radius': 5,
      'circle-color': ['match', ['get', 'archetype'],
        'resident', '#30d158', 'commuter', '#0a84ff',
        'tourist', '#ff9f0a', 'student', '#ff375f',
        '#ffffff'],
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.2,
    },
  });

  map.addSource('trail', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'trail-line', type: 'line', source: 'trail',
    paint: { 'line-color': '#ffa337', 'line-width': 3, 'line-opacity': 0.85 },
  });
  map.addSource('trail-dots', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'trail-dots-pt', type: 'circle', source: 'trail-dots',
    paint: {
      'circle-radius': 4.5, 'circle-color': '#ffa337',
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.8,
    },
  });

  map.addSource('planned', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'planned-line', type: 'line', source: 'planned',
    paint: { 'line-color': '#64d2ff', 'line-width': 4, 'line-opacity': 0.7, 'line-dasharray': [2, 1] },
  });

  map.addSource('reachable', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'reachable-pt', type: 'circle', source: 'reachable',
    paint: { 'circle-radius': 2.4, 'circle-color': '#30d158', 'circle-opacity': 0.22 },
  });

  // Zone selection rectangle (Panel 1)
  map.addSource('zone-rect', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'zone-rect-fill', type: 'fill', source: 'zone-rect',
    paint: { 'fill-color': '#0a84ff', 'fill-opacity': 0.08 },
  });
  map.addLayer({
    id: 'zone-rect-line', type: 'line', source: 'zone-rect',
    paint: { 'line-color': '#0a84ff', 'line-width': 2, 'line-dasharray': [3, 2] },
  });

  // Restore saved zone bbox if any
  if (state.zone.bbox) {
    const [w, s, e, n] = state.zone.bbox;
    (map.getSource('zone-rect') as { setData: (d: unknown) => void }).setData({
      type: 'FeatureCollection',
      features: [zoneBboxFeature(w, s, e, n)],
    });
  }

  applyLayerVisibility();
}

function applyLayerVisibility(): void {
  if (!mapReady || !map) return;
  const mapping: Record<string, string[]> = {
    buildings:  ['buildings-fill', 'buildings-line'],
    walk:       ['walk-line'],
    amenities:  ['amenities-pt'],
    streetview: ['sv-pt', 'sv-candidates-pt', 'sv-downloading-pt'],
  };
  for (const [k, ids] of Object.entries(mapping)) {
    for (const id of ids) {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, 'visibility',
          (state.layers as Record<string, boolean>)[k] ? 'visible' : 'none');
      }
    }
  }
}

function setMapStyle(theme: 'dark' | 'light'): void {
  if (!mapReady || !map) return;
  state.theme = theme; saveState();
  const url = theme === 'light' ? 'mapbox://styles/mapbox/light-v11' : 'mapbox://styles/mapbox/dark-v11';
  map.once('styledata', () => { loadStaticLayers(); });
  map.setStyle(url);
}

/* =====================================================================
   AMENITY CLICK HANDLER (Panel 1)
   ===================================================================== */
function onAmenityClick(e: MapboxMapEvent): void {
  if (bboxDrawing || state.currentPanel !== 1 || !e.features?.length || !map) return;
  const p = e.features[0].properties as Record<string, unknown>;
  const geom = (e.features[0] as unknown as { geometry: { coordinates: [number, number] } }).geometry;
  const coords = geom.coordinates;

  if (amenityPopup) amenityPopup.remove();

  const name = String(p['name'] || 'Unnamed amenity');
  const amenity = String(p['amenity'] || '');
  const address = p['address'] && String(p['address']) !== 'None' ? String(p['address']) : null;
  const phone   = p['phone']   && String(p['phone'])   !== 'None' ? String(p['phone'])   : null;
  const website = p['website'] && String(p['website']) !== 'None' ? String(p['website']) : null;
  const tagsRaw = p['amenity_tags'] && String(p['amenity_tags']) !== 'None' ? String(p['amenity_tags']) : null;

  const typeLabel = amenity
    ? `<span class="amenity-type-chip">${escapeHtml(amenity.replace(/_/g, ' '))}</span>`
    : '';

  let rows = '';
  if (address) rows += `<div class="amenity-row"><span class="amenity-icon">⌖</span>${escapeHtml(address)}</div>`;
  if (phone)   rows += `<div class="amenity-row"><span class="amenity-icon">✆</span>${escapeHtml(phone)}</div>`;
  if (website) {
    const href = website.startsWith('http') ? website : `https://${website}`;
    rows += `<div class="amenity-row"><span class="amenity-icon">⊕</span><a href="${escapeHtml(href)}" target="_blank" rel="noopener">Website ↗</a></div>`;
  }

  let tagHtml = '';
  if (tagsRaw) {
    try {
      const tags = JSON.parse(tagsRaw) as Record<string, unknown>;
      const entries = Object.entries(tags)
        .filter(([, v]) => v && String(v) !== 'None' && String(v) !== 'null')
        .slice(0, 8);
      if (entries.length) {
        tagHtml = `<div class="amenity-tags">${entries.map(([k, v]) =>
          `<span class="amenity-tag">${escapeHtml(k.replace(/_/g, ' '))}: ${escapeHtml(String(v))}</span>`
        ).join('')}</div>`;
      }
    } catch { /* not JSON — skip */ }
  }

  const html = `<div class="amenity-popup-inner">
    <div class="amenity-popup-head">
      <span class="amenity-popup-name">${escapeHtml(name)}</span>
      ${typeLabel}
    </div>
    ${rows}
    ${tagHtml}
  </div>`;

  // Highlight selected amenity on map
  const amenityHighlight = {
    type: 'FeatureCollection' as const,
    features: [{ type: 'Feature' as const, geometry: { type: 'Point' as const, coordinates: coords }, properties: {} }],
  };
  (map?.getSource('amenity-selected') as { setData: (d: unknown) => void } | undefined)
    ?.setData(amenityHighlight);

  amenityPopup = new mapboxgl.Popup({ className: 'amenity-popup', closeButton: true, maxWidth: '300px' })
    .setLngLat(coords)
    .setHTML(html)
    .addTo(map as unknown as Parameters<MapboxPopup['addTo']>[0]) as MapboxPopup;

  // Clear highlight when popup is dismissed
  amenityPopup.on('close', () => {
    (map?.getSource('amenity-selected') as { setData: (d: unknown) => void } | undefined)
      ?.setData({ type: 'FeatureCollection', features: [] });
  });
}

/* =====================================================================
   STREETVIEW CLICK HANDLER (Panel 1)
   ===================================================================== */
async function onStreetviewClick(e: MapboxMapEvent): Promise<void> {
  if (bboxDrawing || !e.features?.length) return;
  const p = e.features[0].properties;
  state.selectedPoint = { lat: p.lat, lon: p.lon, image_url: p.image_url };
  saveState();

  // Add to analyse selection if modal is open and in single/multi mode
  const analyseModal = $('#analyse-modal') as HTMLElement | null;
  if (analyseModal && !analyseModal.classList.contains('hidden') && (_analyseMode === 'single' || _analyseMode === 'multi')) {
    const key = `${p.lat}_${p.lon}`;
    if (_analyseMode === 'single') { _analysePoints.clear(); _analysePoints.add(key); }
    else { if (_analysePoints.has(key)) _analysePoints.delete(key); else _analysePoints.add(key); }
    _updateAnalyseButton();
  }

  // Highlight the clicked point on the map
  const highlightGeo = {
    type: 'FeatureCollection' as const,
    features: [{
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [p.lon, p.lat] },
      properties: {},
    }],
  };
  (map?.getSource('sv-selected') as { setData: (d: unknown) => void } | undefined)
    ?.setData(highlightGeo);

  // Populate collapsed view (left panel)
  ($('#p1-sv-popover') as HTMLElement).style.display = '';
  const title = `(${p.lat?.toFixed(5)}, ${p.lon?.toFixed(5)})`;
  const coordStr = `${p.lat?.toFixed(5)}, ${p.lon?.toFixed(5)}`;
  const headingStr = `Heading ${p.heading ?? 0}°`;
  const imgSrc = p.image_url ? api.map + p.image_url : '';
  ($('#p1-popover-title') as HTMLElement).textContent = title;
  ($('#p1-popover-coord') as HTMLElement).textContent = coordStr;
  ($('#p1-popover-heading') as HTMLElement).textContent = headingStr;
  if (imgSrc) ($('#p1-popover-img') as HTMLImageElement).src = imgSrc;

  // Populate expanded overlay
  ($('#sv-detail-title') as HTMLElement).textContent = title;
  ($('#sv-detail-coord') as HTMLElement).textContent = coordStr;
  ($('#sv-detail-heading') as HTMLElement).textContent = headingStr;
  if (imgSrc) ($('#sv-detail-img') as HTMLImageElement).src = imgSrc;

  // Build scene fields for expanded overlay
  const fields = $('#sv-detail-fields') as HTMLElement;
  fields.innerHTML = '';
  PERCEPTION_FIELDS.forEach((spec) => {
    const raw = (p as Record<string, unknown>)[spec.key];
    if (!raw || String(raw).trim().length < 2) return;
    let display = String(raw).trim();
    if (display.startsWith('[')) {
      try {
        const arr = JSON.parse(display) as Record<string, unknown>[];
        display = arr.map((obj) => {
          const zone = obj['zone'] ? `[${obj['zone']}] ` : '';
          const parts = Object.entries(obj).filter(([k]) => k !== 'zone').map(([, v]) => String(v));
          return zone + parts.join(' · ');
        }).join('\n');
      } catch { /* leave as-is */ }
    }
    const el = document.createElement('div');
    el.className = 'field';
    el.innerHTML = `<div class="k">${escapeHtml(spec.label)}</div><div style="white-space:pre-line">${escapeHtml(display)}</div>`;
    fields.appendChild(el);
  });
}

/* =====================================================================
   PANEL ROUTER
   ===================================================================== */
function activatePanel(n: PanelId): void {
  if (state.currentPanel === 3 && n !== 3) stopRenderLoop();
  state.currentPanel = n;
  if (state.panelStatus[n] === 'locked') state.panelStatus[n] = 'active';
  saveState();
  for (let i = 1; i <= 5; i++) {
    const el = document.getElementById(`panel-${i}`);
    if (el) el.setAttribute('data-active', i === n ? 'true' : 'false');
  }
  document.body.classList.toggle('dim-map', n === 3);
  refreshNavDots();
  updateZoneFab();
  if (n === 1) panel1Enter();
  if (n === 3) panel3Enter();
  if (n === 4) panel4Enter();
  if (n === 5) panel5Enter();
}

function markPanelDone(n: PanelId): void {
  state.panelStatus[n] = 'done';
  if (n < 5 && state.panelStatus[n + 1] === 'locked') {
    state.panelStatus[n + 1] = 'active';
  }
  saveState(); refreshNavDots();
}

function refreshNavDots(): void {
  $$('.pill-nav .dot').forEach((dot) => {
    const n = +dot.getAttribute('data-panel')!;
    const status: PanelStatus = n === state.currentPanel
      ? 'active'
      : (state.panelStatus[n] || 'locked');
    dot.setAttribute('data-state', status);
  });
  ($('#nav-prev') as HTMLButtonElement).disabled = state.currentPanel === 1;
  ($('#nav-next') as HTMLButtonElement).disabled = state.currentPanel === 5;
  // Keep dot numbers correct for nav order 1,3,4,5
  let dispNum = 0;
  $$('.pill-nav .dot').forEach((dot) => {
    dispNum++;
    const numEl = dot.querySelector('.num');
    if (numEl) numEl.textContent = String(dispNum);
  });
}

/* =====================================================================
   PANEL 1 — Zone Selection & Streetview catalog
   ===================================================================== */
let p1Bound = false;
let currentBbox: [number, number, number, number] | null = null;

function zoneBboxFeature(w: number, s: number, e: number, n: number) {
  return {
    type: 'Feature' as const,
    geometry: { type: 'Polygon' as const, coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
    properties: {},
  };
}

function setZoneRect(w: number, s: number, e: number, n: number): void {
  (map?.getSource('zone-rect') as { setData: (d: unknown) => void } | undefined)
    ?.setData({ type: 'FeatureCollection', features: [zoneBboxFeature(w, s, e, n)] });
}

let bboxDrawing = false;
let bboxDrawStart: [number, number] | null = null;

function startZoneDraw(): void {
  if (!map || !mapReady) { toast('Map not ready.', 'warning'); return; }
  if (bboxDrawing) return;
  bboxDrawing = true;
  map.getCanvas().style.cursor = 'crosshair';
  map.dragPan.disable();
  ($('#p1-status') as HTMLElement).textContent = 'Click and drag on the map to draw a rectangular zone.';

  function onDown(e: MapboxMapEvent) {
    bboxDrawStart = [e.lngLat.lng, e.lngLat.lat];
  }
  function onMove(e: MapboxMapEvent) {
    if (!bboxDrawStart) return;
    const [x0, y0] = bboxDrawStart;
    const x1 = e.lngLat.lng, y1 = e.lngLat.lat;
    setZoneRect(Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1));
  }
  function onUp(e: MapboxMapEvent) {
    (map as unknown as { off: (ev: string, fn: unknown) => void }).off('mousedown', onDown);
    (map as unknown as { off: (ev: string, fn: unknown) => void }).off('mousemove', onMove);
    (map as unknown as { off: (ev: string, fn: unknown) => void }).off('mouseup', onUp);
    map!.dragPan.enable();
    map!.getCanvas().style.cursor = '';
    bboxDrawing = false;

    if (!bboxDrawStart) return;
    const [x0, y0] = bboxDrawStart;
    const x1 = e.lngLat.lng, y1 = e.lngLat.lat;
    bboxDrawStart = null;
    const w = Math.min(x0, x1), east = Math.max(x0, x1);
    const s = Math.min(y0, y1), n = Math.max(y0, y1);
    if (east - w < 0.0001 || n - s < 0.0001) {
      ($('#p1-status') as HTMLElement).textContent = 'Zone too small — try again.';
      return;
    }
    currentBbox = [w, s, east, n];
    state.zone.bbox = currentBbox; saveState();
    updateZoneBboxDisplay();
    void renderCandidates();
  }
  (map as unknown as { on: (ev: string, fn: unknown) => void }).on('mousedown', onDown);
  (map as unknown as { on: (ev: string, fn: unknown) => void }).on('mousemove', onMove);
  (map as unknown as { on: (ev: string, fn: unknown) => void }).on('mouseup', onUp);
}

async function renderCandidates(): Promise<void> {
  if (!map) return;
  const src = map.getSource('sv-candidates');
  if (!src) return;
  if (!currentBbox) {
    ($('#p1-kpi-candidates') as HTMLElement).textContent = '0';
    src.setData({ type: 'FeatureCollection', features: [] });
    return;
  }
  const [w, s, e, n] = currentBbox;
  const spacing = state.zone.spacing;
  const status = $('#p1-status') as HTMLElement;
  status.textContent = 'Fetching street-aligned candidate points…';

  try {
    const data = await api.m<{ type: string; features: { geometry: { coordinates: [number, number] }; properties: Record<string, unknown> }[] }>(
      `/api/walk_network/candidates?bbox=${w},${s},${e},${n}&spacing=${spacing}`,
    );

    const skipNear = ($('#p1-skip-near') as HTMLInputElement)?.checked ?? true;
    let features = data.features;

    if (skipNear) {
      const svSrc = map.getSource('sv');
      const existing: [number, number][] = svSrc?._data
        ? svSrc._data.features.map((f) => f.geometry.coordinates as [number, number])
        : [];
      const nearThreshDeg = (spacing / 2) / 110540;
      const nearSq = nearThreshDeg * nearThreshDeg;
      features = features.filter((f) => {
        const [flon, flat] = f.geometry.coordinates;
        return !existing.some(([elon, elat]) => (elon - flon) ** 2 + (elat - flat) ** 2 < nearSq);
      });
    }

    src.setData({ type: 'FeatureCollection', features });
    ($('#p1-kpi-candidates') as HTMLElement).textContent = String(features.length);
    status.textContent = `Bbox set. ${features.length} street-aligned candidate point${features.length === 1 ? '' : 's'} at ${spacing}m spacing.`;
  } catch (e) {
    console.warn('Candidate points fetch failed', e);
    status.textContent = 'Could not fetch street-aligned candidate points.';
  }
}

function updateZoneFab(): void {
  const fab = $('#zone-fab') as HTMLElement;
  if (!fab) return;
  fab.classList.toggle('hidden', state.currentPanel !== 1);
}

function updateZoneBboxDisplay(): void {
  const bboxEl = $('#p1-zone-bbox') as HTMLElement;
  const detailsBtn = $('#p1-overture-details') as HTMLButtonElement;
  if (!bboxEl) return;
  if (currentBbox) {
    const [w, s, e, n] = currentBbox;
    bboxEl.textContent = `W ${w.toFixed(5)}  S ${s.toFixed(5)}  E ${e.toFixed(5)}  N ${n.toFixed(5)}`;
    if (detailsBtn) detailsBtn.disabled = false;
  } else {
    bboxEl.textContent = 'No zone drawn yet.';
    if (detailsBtn) detailsBtn.disabled = true;
  }
}

let p1OvertureJobId: string | null = null;
let p1OverturePoller: ReturnType<typeof setInterval> | null = null;

function panel1Enter(): void {
  updateZoneFab();
  updateZoneBboxDisplay();
  if (state.zone.spacing) {
    ($('#p1-spacing') as HTMLInputElement).value = String(state.zone.spacing);
    ($('#p1-spacing-label') as HTMLElement).textContent = `${state.zone.spacing} m`;
  }
  if (p1Bound) return;
  p1Bound = true;
  ($('#p1-spacing') as HTMLInputElement).addEventListener('input', (e) => {
    const v = +(e.target as HTMLInputElement).value;
    state.zone.spacing = v; saveState();
    ($('#p1-spacing-label') as HTMLElement).textContent = `${v} m`;
    void renderCandidates();
  });
  $('#p1-skip-near')!.addEventListener('change', () => { void renderCandidates(); });

  // FAB buttons
  const clearZone = () => {
    currentBbox = null; state.zone.bbox = null; saveState();
    (map?.getSource('zone-rect') as { setData: (d: unknown) => void } | undefined)
      ?.setData({ type: 'FeatureCollection', features: [] });
    map?.getSource('sv-candidates')?.setData({ type: 'FeatureCollection', features: [] });
    map?.getSource('sv-selected')?.setData({ type: 'FeatureCollection', features: [] });
    ($('#p1-kpi-candidates') as HTMLElement).textContent = '0';
    ($('#p1-status') as HTMLElement).textContent = 'Cleared. Draw a new zone.';
    updateZoneBboxDisplay();
  };
  $('#p1-draw-fab')!.addEventListener('click', () => { startZoneDraw(); });
  $('#p1-clear-fab')!.addEventListener('click', clearZone);

  $('#p1-delete-unanalyzed')!.addEventListener('click', async () => {
    if (!confirm('Delete all downloaded images that have no VLM analysis? This cannot be undone.')) return;
    try {
      const r = await fetch(`${api.map}/api/streetview/images/unanalyzed`, { method: 'DELETE' });
      const res = await r.json() as { deleted?: number; freed_mb?: number; error?: string };
      if (res.error) { toast(res.error, 'danger'); return; }
      toast(`Deleted ${res.deleted} unanalyzed images (${res.freed_mb} MB freed).`, 'success');
      // Refresh existing count and map dots
      const [fresh, stats] = await Promise.all([
        api.m<{ features: unknown[] }>('/api/streetview_grid'),
        api.m<{ images: number; results: number }>('/api/streetview/stats'),
      ]);
      (map.getSource('sv') as { setData: (d: unknown) => void } | undefined)?.setData(fresh);
      ($('#p1-kpi-existing') as HTMLElement).textContent = String(stats.images);
    } catch (e) {
      toast(`Delete failed: ${e instanceof Error ? e.message : e}`, 'danger');
    }
  });

  let _svPoller: number | null = null;
  const _svDownloadedFeatures: { type: 'Feature'; geometry: { type: 'Point'; coordinates: [number, number] }; properties: Record<string, never> }[] = [];

  function _updateDownloadingLayer(): void {
    (map.getSource('sv-downloading') as { setData: (d: unknown) => void } | undefined)
      ?.setData({ type: 'FeatureCollection', features: _svDownloadedFeatures });
  }

  $('#p1-download')!.addEventListener('click', async () => {
    _svDownloadedFeatures.length = 0;
    _updateDownloadingLayer();
    if (!currentBbox) { toast('Draw a zone first.', 'warning'); return; }

    const progressDiv  = $('#p1-sv-progress')    as HTMLElement;
    const statusEl     = $('#p1-sv-progress-status') as HTMLElement;
    const fillEl       = $('#p1-sv-progress-fill')   as HTMLElement;
    const logEl        = $('#p1-sv-progress-log')    as HTMLElement;
    const mainStatus   = $('#p1-status')             as HTMLElement;
    const kpiExisting  = $('#p1-kpi-existing')       as HTMLElement;

    progressDiv.style.display = '';
    statusEl.textContent = 'Starting download…';
    fillEl.style.width   = '0%';
    logEl.innerHTML      = '';
    ($('#p1-download') as HTMLButtonElement).disabled = true;

    try {
      const res = await api.postJSON<{ error?: string; job_id?: string }>(
        api.map, '/api/streetview/download', { bbox: currentBbox, spacing: state.zone.spacing },
      );
      if (res.error || !res.job_id) {
        toast(res.error || 'Download failed', 'danger');
        mainStatus.textContent = res.error || 'Download failed';
        progressDiv.style.display = 'none';
        ($('#p1-download') as HTMLButtonElement).disabled = false;
        return;
      }

      const jobId = res.job_id;
      if (_svPoller) clearInterval(_svPoller);
      _svPoller = setInterval(async () => {
        try {
          const s = await api.m<{
            status: string; pct: number;
            downloaded: number; skipped: number; total: number;
            existing: number; log: string[];
          }>(`/api/streetview/download/status/${jobId}`);

          fillEl.style.width   = `${s.pct}%`;
          statusEl.textContent = `${s.downloaded} downloaded · ${s.skipped} skipped · ${s.total} candidates`;
          kpiExisting.textContent  = String(s.existing);

          // Append new log lines and update map for newly downloaded points
          const rendered = logEl.childElementCount;
          let mapDirty = false;
          (s.log || []).slice(rendered).forEach((line) => {
            const d = document.createElement('div');
            d.textContent = line;
            logEl.appendChild(d);
            logEl.scrollTop = logEl.scrollHeight;
            // Parse "✓ lat,lon" lines → add orange dot to map immediately
            const m = line.match(/^✓\s*(-?\d+\.\d+),(-?\d+\.\d+)/);
            if (m) {
              _svDownloadedFeatures.push({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [parseFloat(m[2]), parseFloat(m[1])] },
                properties: {},
              });
              mapDirty = true;
            }
          });
          if (mapDirty) _updateDownloadingLayer();

          if (s.status === 'done' || s.status === 'error') {
            clearInterval(_svPoller!); _svPoller = null;
            ($('#p1-download') as HTMLButtonElement).disabled = false;
            mainStatus.textContent = `Done — ${s.downloaded} new images saved.`;
            toast(`Downloaded ${s.downloaded} images, skipped ${s.skipped}.`, 'success');
            // Reload streetview dots on map and update Existing from actual image count
            try {
              const [fresh, stats] = await Promise.all([
                api.m<{ features: unknown[] }>('/api/streetview_grid'),
                api.m<{ images: number; results: number }>('/api/streetview/stats'),
              ]);
              (map.getSource('sv') as { setData: (d: unknown) => void } | undefined)?.setData(fresh);
              kpiExisting.textContent = String(stats.images);
            } catch { /* ok */ }
          }
        } catch { /* poll error — keep retrying */ }
      }, 1500) as unknown as number;

    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(`Download failed: ${msg}`, 'danger');
      mainStatus.textContent = msg;
      progressDiv.style.display = 'none';
      ($('#p1-download') as HTMLButtonElement).disabled = false;
    }
  });

  // CSS filter sliders (only in expanded overlay now)
  const setFilter = () => {
    const b = ($('#sv-flt-b') as HTMLInputElement)?.value ?? '1';
    const c = ($('#sv-flt-c') as HTMLInputElement)?.value ?? '1';
    const sa = ($('#sv-flt-s') as HTMLInputElement)?.value ?? '1';
    const filter = `brightness(${b}) contrast(${c}) saturate(${sa})`;
    ($('#p1-popover-img') as HTMLImageElement).style.filter = filter;
    ($('#sv-detail-img') as HTMLImageElement).style.filter = filter;
  };
  ['b', 'c', 's'].forEach((k) => $(`#sv-flt-${k}`)?.addEventListener('input', setFilter));
  $('#p1-popover-close')!.addEventListener('click',
    () => {
      ($('#p1-sv-popover') as HTMLElement).style.display = 'none';
      state.selectedPoint = null; saveState();
      (map?.getSource('sv-selected') as { setData: (d: unknown) => void } | undefined)
        ?.setData({ type: 'FeatureCollection', features: [] });
    });

  // Overture details button → open modal
  $('#p1-overture-details')!.addEventListener('click', () => {
    const modal = $('#overture-modal') as HTMLElement;
    modal.classList.remove('hidden');
    // Populate bbox pill in modal
    const pill = $('#ov-bbox-pill') as HTMLElement;
    if (currentBbox) {
      const [w, s, e, n] = currentBbox;
      pill.textContent = `W ${w.toFixed(5)}  S ${s.toFixed(5)}  E ${e.toFixed(5)}  N ${n.toFixed(5)}`;
    } else {
      pill.textContent = 'No zone drawn';
    }
  });
  $('#overture-modal-close')!.addEventListener('click', () => {
    ($('#overture-modal') as HTMLElement).classList.add('hidden');
  });

  // Start Overture download
  $('#ov-start-btn')!.addEventListener('click', async () => {
    if (!currentBbox) { toast('Draw a zone first.', 'warning'); return; }
    const layers: string[] = [];
    if (($('#ov-layer-buildings') as HTMLInputElement).checked) layers.push('buildings');
    if (($('#ov-layer-amenities') as HTMLInputElement).checked) layers.push('amenities');
    if (($('#ov-layer-transport') as HTMLInputElement).checked) layers.push('transport');
    if (!layers.length) { toast('Select at least one layer.', 'warning'); return; }

    const locationName = ($('#ov-name') as HTMLInputElement).value.trim() || 'custom_zone';
    const gcpProject = ($('#ov-gcp') as HTMLInputElement).value.trim() || null;

    ($('#overture-modal') as HTMLElement).classList.add('hidden');

    // Show progress UI
    const progressDiv = $('#p1-overture-progress') as HTMLElement;
    const statusEl = $('#p1-overture-status') as HTMLElement;
    const fillEl = $('#p1-overture-fill') as HTMLElement;
    const logEl = $('#p1-overture-log') as HTMLElement;
    progressDiv.style.display = '';
    statusEl.textContent = 'Starting download…';
    fillEl.style.width = '0%';
    logEl.innerHTML = '';

    try {
      const res = await api.postJSON<{ job_id?: string; error?: string }>(
        api.map, '/api/overture/download',
        { bbox: currentBbox, layers, location_name: locationName, gcp_project: gcpProject },
      );
      if (res.error || !res.job_id) {
        toast(`Overture error: ${res.error || 'unknown'}`, 'danger');
        progressDiv.style.display = 'none';
        return;
      }
      p1OvertureJobId = res.job_id;
      toast(`Overture download started (job ${res.job_id})`, 'success');

      // Poll status
      if (p1OverturePoller) clearInterval(p1OverturePoller);
      p1OverturePoller = setInterval(async () => {
        try {
          const status = await api.m<{
            pct?: number; log?: string[]; status?: string; error?: string;
          }>(`/api/overture/status/${p1OvertureJobId}`);
          if (status.error) {
            clearInterval(p1OverturePoller!); p1OverturePoller = null;
            statusEl.textContent = `Error: ${status.error}`;
            return;
          }
          const pct = status.pct ?? 0;
          fillEl.style.width = `${pct}%`;
          statusEl.textContent = status.status === 'done'
            ? 'Download complete!'
            : status.status === 'error'
            ? 'Download failed.'
            : `Running… ${Math.round(pct)}%`;
          if (status.log?.length) {
            const lastLines = status.log.slice(-6);
            logEl.innerHTML = lastLines.map((l) => `<div>${escapeHtml(l)}</div>`).join('');
            logEl.scrollTop = logEl.scrollHeight;
          }
          if (status.status === 'done' || status.status === 'error') {
            clearInterval(p1OverturePoller!); p1OverturePoller = null;
            if (status.status === 'done') {
              toast('Download complete — choose how to save.', 'success');
              const saveActionsDiv = $('#ov-save-actions') as HTMLElement;
              if (saveActionsDiv) saveActionsDiv.style.display = 'flex';
            } else {
              toast('Overture download encountered errors. Check log.', 'warning');
            }
          }
        } catch { /* ignore polling errors */ }
      }, 2000);
    } catch (e) {
      toast(`Overture request failed: ${e instanceof Error ? e.message : e}`, 'danger');
      progressDiv.style.display = 'none';
    }
  });

  // Save downloaded data
  $('#ov-save-append')!.addEventListener('click', async () => {
    if (!p1OvertureJobId) return;
    try {
      const res = await api.postJSON<{ error?: string; status?: string; message?: string }>(
        api.map, `/api/overture/save/${p1OvertureJobId}`,
        { mode: 'append' },
      );
      if (res.error) { toast(`Save error: ${res.error}`, 'danger'); return; }
      toast('Appended to map!', 'success');
      location.reload();
    } catch (e) {
      toast(`Save failed: ${e instanceof Error ? e.message : e}`, 'danger');
    }
  });

  $('#ov-save-new')!.addEventListener('click', async () => {
    if (!p1OvertureJobId) return;
    const dbName = prompt('Database name (no spaces):');
    if (!dbName || !dbName.trim()) return;
    try {
      const res = await api.postJSON<{ error?: string; status?: string; message?: string }>(
        api.map, `/api/overture/save/${p1OvertureJobId}`,
        { mode: 'new', db_name: dbName.trim() },
      );
      if (res.error) { toast(`Save error: ${res.error}`, 'danger'); return; }
      toast(`Saved as ${dbName}.duckdb`, 'success');
    } catch (e) {
      toast(`Save failed: ${e instanceof Error ? e.message : e}`, 'danger');
    }
  });

  // Upload database file
  const fileInput = $('#p1-overture-file-input') as HTMLInputElement;
  $('#p1-overture-upload')!.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    toast(`Uploading ${file.name}…`, '');
    try {
      const res = await api.m<{ status?: string; error?: string; message?: string }>(
        '/api/database/upload', { method: 'POST', body: form },
      );
      if (res.error) { toast(`Upload error: ${res.error}`, 'danger'); return; }
      toast(res.message || 'Database loaded!', 'success');
      location.reload();
    } catch (e) {
      toast(`Upload failed: ${e instanceof Error ? e.message : e}`, 'danger');
    }
    fileInput.value = '';
  });

  _initAnalyseCard();
}

/* =====================================================================
   VLM Configuration (embedded in Panel 1 Analyse card)
   ===================================================================== */
let p1VlmBound = false;
let _analyseMode: 'single' | 'multi' | 'unanalyzed' | 'all' = 'single';
let _analysePoints: Set<string> = new Set(); // keys: "lat_lon"

function _updateAnalyseButton(): void {
  const btn  = $('#p1-vlm-analyze') as HTMLButtonElement | null;
  const stat = $('#p1-sel-status') as HTMLElement | null;
  if (!btn || !stat) return;
  const count = _analysePoints.size;
  btn.disabled = count === 0;
  btn.textContent = count > 0 ? `Analyse ${count} image${count === 1 ? '' : 's'}` : 'Analyse — select points first';
  stat.textContent = count > 0 ? `${count} point${count === 1 ? '' : 's'} selected.` : 'No points selected.';
}

function _initAnalyseCard(): void {
  if (p1VlmBound) return; p1VlmBound = true;

  // Populate model dropdown from VLM_CARDS
  const modelSel = $('#p1-vlm-model-select') as HTMLSelectElement | null;
  if (modelSel) {
    modelSel.innerHTML = VLM_CARDS
      .filter((v) => v.id !== 'custom-hf')
      .map((v) => `<option value="${v.id}"${state.vlm.provider === v.id ? ' selected' : ''}>${escapeHtml(v.name)}</option>`)
      .join('') + `<option value="custom-hf"${state.vlm.provider === 'custom-hf' ? ' selected' : ''}>+ Custom HuggingFace</option>`;
    modelSel.addEventListener('change', () => { state.vlm.provider = modelSel.value; saveState(); });
  }

  // Compare button — opens model comparison overlay
  const compareModal = $('#vlm-compare-modal') as HTMLElement;
  $('#p1-vlm-compare')?.addEventListener('click', () => {
    const list = $('#vlm-compare-list') as HTMLElement;
    list.innerHTML = '';
    VLM_CARDS.forEach((v) => {
      const selected = state.vlm.provider === v.id;
      const div = document.createElement('div');
      div.className = 'vlm-card';
      div.setAttribute('data-selected', selected ? 'true' : 'false');
      const propsHtml = Object.entries(v.props).map(
        ([k, val]) => `<div class="prop"><b>${escapeHtml(k)}:</b> ${escapeHtml(val)}</div>`
      ).join('');
      div.innerHTML = `
        <div class="top">
          <span class="name">${escapeHtml(v.name)}</span>
          ${v.active ? '<span class="chip success" style="font-size:11px;">Active</span>' : ''}
          ${selected ? '<span class="chip" style="font-size:11px;">Selected</span>' : ''}
        </div>
        <div class="props">${propsHtml}</div>
        <div class="pros">${escapeHtml(v.pros)}</div>
        <div class="cons">${escapeHtml(v.cons)}</div>`;
      div.addEventListener('click', () => {
        state.vlm.provider = v.id; saveState();
        if (modelSel) modelSel.value = v.id;
        const modalSel2 = $('#p1-vlm-model-select-modal') as HTMLSelectElement | null;
        if (modalSel2) modalSel2.value = v.id;
        compareModal.classList.add('hidden');
      });
      list.appendChild(div);
    });
    compareModal.classList.remove('hidden');
  });
  $('#vlm-compare-modal-close')?.addEventListener('click', () => compareModal.classList.add('hidden'));
  compareModal.addEventListener('click', (e) => { if (e.target === compareModal) compareModal.classList.add('hidden'); });

  // Compare button inside the analyse modal — reuses the same compare overlay
  $('#p1-vlm-compare-modal-btn')?.addEventListener('click', () => {
    const list = $('#vlm-compare-list') as HTMLElement;
    const modalSel = $('#p1-vlm-model-select-modal') as HTMLSelectElement | null;
    list.innerHTML = '';
    VLM_CARDS.forEach((v) => {
      const selected = state.vlm.provider === v.id;
      const div = document.createElement('div');
      div.className = 'vlm-card';
      div.setAttribute('data-selected', selected ? 'true' : 'false');
      const propsHtml = Object.entries(v.props).map(
        ([k, val]) => `<div class="prop"><b>${escapeHtml(k)}:</b> ${escapeHtml(val)}</div>`
      ).join('');
      div.innerHTML = `
        <div class="top">
          <span class="name">${escapeHtml(v.name)}</span>
          ${v.active ? '<span class="chip success" style="font-size:11px;">Active</span>' : ''}
          ${selected ? '<span class="chip" style="font-size:11px;">Selected</span>' : ''}
        </div>
        <div class="props">${propsHtml}</div>
        <div class="pros">${escapeHtml(v.pros)}</div>
        <div class="cons">${escapeHtml(v.cons)}</div>`;
      div.addEventListener('click', () => {
        state.vlm.provider = v.id; saveState();
        const mainSel = $('#p1-vlm-model-select') as HTMLSelectElement | null;
        if (mainSel) mainSel.value = v.id;
        if (modalSel) modalSel.value = v.id;
        compareModal.classList.add('hidden');
      });
      list.appendChild(div);
    });
    compareModal.classList.remove('hidden');
  });

  // Open / close analyse modal
  const modal = $('#analyse-modal') as HTMLElement;
  const openModal = () => { modal.classList.remove('hidden'); buildParamList(); buildVLMList(); };
  $('#p1-analyse-open')?.addEventListener('click', openModal);
  $('#analyse-modal-close')?.addEventListener('click', () => modal.classList.add('hidden'));
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.add('hidden'); });

  // + Custom field button
  $('#p1-vlm-add-param')!.addEventListener('click', () => {
    const key = 'custom_' + Date.now();
    state.vlm.customFields.push({ key, label: 'New Field', prompt: 'Describe…' });
    state.vlm.enabledFields!.push(key);
    saveState(); buildParamList();
  });

  // Selection mode buttons
  (['single', 'multi', 'unanalyzed', 'all'] as const).forEach((mode) => {
    const btnId = mode === 'unanalyzed' ? '#p1-sel-unseen' : `#p1-sel-${mode}`;
    $(btnId)?.addEventListener('click', () => {
      _analyseMode = mode;
      // Highlight active button
      ['#p1-sel-single','#p1-sel-multi','#p1-sel-unseen','#p1-sel-all'].forEach((id) => {
        const el = $(id) as HTMLButtonElement | null;
        if (el) el.setAttribute('data-active', el.id.includes(mode === 'unanalyzed' ? 'unseen' : mode) ? 'true' : 'false');
      });

      // Immediate selection for bulk modes
      if (mode === 'unanalyzed' || mode === 'all') {
        _analysePoints.clear();
        const src = map?.getSource('sv') as { _data?: { features: { properties: { lat: number; lon: number; schema?: string } }[] } } | undefined;
        const features = src?._data?.features ?? [];
        features.forEach((f) => {
          if (mode === 'all' || f.properties.schema === 'image_only') {
            _analysePoints.add(`${f.properties.lat}_${f.properties.lon}`);
          }
        });
        _updateAnalyseButton();
      } else {
        // single / multi — wait for map clicks; clear for single
        if (mode === 'single') { _analysePoints.clear(); _updateAnalyseButton(); }
      }
    });
  });

  // Analyse button
  let _vlmPoller: number | null = null;
  $('#p1-vlm-analyze')!.addEventListener('click', async () => {
    if (_analysePoints.size === 0) return;
    const progressDiv  = $('#p1-vlm-progress')          as HTMLElement;
    const statusEl     = $('#p1-vlm-progress-status')   as HTMLElement;
    const fillEl       = $('#p1-vlm-progress-fill')     as HTMLElement;
    const logEl        = $('#p1-vlm-progress-log')      as HTMLElement;
    progressDiv.style.display = '';
    fillEl.style.width = '0%';
    statusEl.textContent = 'Starting analysis…';
    logEl.innerHTML = '';
    ($('#p1-vlm-analyze') as HTMLButtonElement).disabled = true;
    try {
      const res = await api.postJSON<{ job_id?: string; error?: string }>(
        api.map, '/api/streetview/analyze',
        { images: [..._analysePoints], params: state.vlm },
      );
      if (res.error || !res.job_id) {
        toast(res.error || 'Analysis failed', 'danger');
        progressDiv.style.display = 'none';
        _updateAnalyseButton();
        return;
      }
      const jobId = res.job_id;
      if (_vlmPoller) clearInterval(_vlmPoller);
      _vlmPoller = setInterval(async () => {
        try {
          const s = await api.m<{ status: string; pct: number; done: number; total: number; log: string[] }>(
            `/api/streetview/analyze/status/${jobId}`
          );
          fillEl.style.width = `${s.pct}%`;
          statusEl.textContent = `${s.done} / ${s.total} analysed`;
          const rendered = logEl.childElementCount;
          (s.log || []).slice(rendered).forEach((line) => {
            const d = document.createElement('div'); d.textContent = line;
            logEl.appendChild(d); logEl.scrollTop = logEl.scrollHeight;
          });
          if (s.status === 'done' || s.status === 'error') {
            clearInterval(_vlmPoller!); _vlmPoller = null;
            _updateAnalyseButton();
            toast(`Analysis complete — ${s.done} images processed.`, 'success');
            // Reload sv source so grey dots become cyan
            try {
              const fresh = await api.m<{ features: unknown[] }>('/api/streetview_grid');
              (map.getSource('sv') as { setData: (d: unknown) => void } | undefined)?.setData(fresh);
            } catch { /* ok */ }
          }
        } catch { /* poll error */ }
      }, 1500) as unknown as number;
    } catch (e) {
      toast(`Analysis failed: ${e instanceof Error ? e.message : e}`, 'danger');
      progressDiv.style.display = 'none';
      _updateAnalyseButton();
    }
  });
}

function renderSubFields(row: HTMLElement, fieldKey: string): void {
  if (!(fieldKey in state.vlm.fieldStructures)) {
    state.vlm.fieldStructures[fieldKey] = structuredClone(FIELD_DEFAULT_SUBFIELDS[fieldKey] ?? []);
    saveState();
  }
  const container = row.querySelector('.param-subfields') as HTMLElement;
  if (!container) return;
  container.innerHTML = '';

  // SVG icons for VLM parameters (Feather-style)
  const SVG = {
    zone: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.5C5.5 1.5 3.5 3.5 3.5 6c0 3.5 4.5 8.5 4.5 8.5s4.5-5 4.5-8.5C12.5 3.5 10.5 1.5 8 1.5z"/><circle cx="8" cy="6" r="1.5" fill="currentColor" stroke="none"/></svg>`,
    element: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="12" height="12" rx="2"/><line x1="2" y1="6" x2="14" y2="6"/><line x1="6" y1="2" x2="6" y2="14"/></svg>`,
    field: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8a1 1 0 100-2 1 1 0 000 2z"/><path d="M8 8a1 1 0 100-2 1 1 0 000 2z"/><path d="M13 8a1 1 0 100-2 1 1 0 000 2z"/><path d="M3 8h10"/></svg>`,
    delete: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="2 4 3 4 14 4"/><path d="M6 4v7a1 1 0 0 0 1 1h2a1 1 0 0 0 1-1V4M8 7v3M6 4l0.5-2a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1L10 4"/></svg>`,
  };

  // Locked rows: Zone and Element
  ['Zone', 'Element'].forEach((label) => {
    const locked = document.createElement('div');
    locked.className = 'subfield-row locked';
    const icon = label === 'Zone' ? SVG.zone : SVG.element;
    locked.innerHTML = `<span style="display:flex;align-items:center;line-height:0;margin-right:6px;color:rgba(245,245,247,0.5);width:16px;height:16px;flex-shrink:0;">${icon}</span><span class="sf-key">${label}</span><span class="sf-values">default</span>`;
    container.appendChild(locked);
  });

  // Custom sub-fields
  (state.vlm.fieldStructures[fieldKey] || []).forEach((sf, idx) => {
    const div = document.createElement('div');
    div.className = 'subfield-row';
    const valStr = sf.values.length > 0 ? sf.values.join(' | ') : '(free text)';
    div.innerHTML = `
      <span style="display:flex;align-items:center;line-height:0;margin-right:6px;color:rgba(245,245,247,0.5);width:16px;height:16px;flex-shrink:0;">${SVG.field}</span>
      <span class="sf-key">${escapeHtml(sf.key)}</span>
      <span class="sf-values">${escapeHtml(valStr)}</span>
      <button class="sf-del" title="Remove field" style="padding:4px 6px;display:flex;align-items:center;"><span style="display:flex;align-items:center;line-height:0;margin:0;color:rgba(245,245,247,0.5);width:16px;height:16px;flex-shrink:0;">${SVG.delete}</span></button>`;
    div.querySelector('.sf-del')!.addEventListener('click', () => {
      state.vlm.fieldStructures[fieldKey].splice(idx, 1);
      saveState();
      renderSubFields(row, fieldKey);
    });
    container.appendChild(div);
  });

  // Add form
  const addForm = document.createElement('div');
  addForm.className = 'subfield-add';
  const keyIn = document.createElement('input');
  keyIn.className = 'sf-key-in';
  keyIn.placeholder = 'Key';
  const valIn = document.createElement('input');
  valIn.className = 'sf-val-in';
  valIn.placeholder = 'value1 | value2 | …';
  const addBtn = document.createElement('button');
  addBtn.className = 'sf-add-btn btn tiny';
  addBtn.innerHTML = '<span style="display:flex;align-items:center;line-height:0;margin-right:4px;color:currentColor;width:14px;height:14px;flex-shrink:0;">' +
    `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="3" x2="8" y2="13"/><line x1="3" y1="8" x2="13" y2="8"/></svg>` +
    '</span>Add';
  addBtn.addEventListener('click', () => {
    const key = keyIn.value.trim();
    const valStr = valIn.value.trim();
    if (!key) { toast('Property name cannot be empty', 'warning'); return; }
    const values = valStr ? valStr.split('|').map((v) => v.trim()).filter(v => v) : [];
    state.vlm.fieldStructures[fieldKey].push({ key, values });
    saveState();
    keyIn.value = '';
    valIn.value = '';
    renderSubFields(row, fieldKey);
  });
  addForm.appendChild(keyIn);
  addForm.appendChild(valIn);
  addForm.appendChild(addBtn);
  container.appendChild(addForm);
}

function buildParamList(): void {
  const list = $('#p1-vlm-param-list') as HTMLElement;
  list.innerHTML = '';
  if (!state.vlm.enabledFields) {
    state.vlm.enabledFields = PERCEPTION_FIELDS.map((f) => f.key);
    saveState();
  }
  const allFields: PerceptionFieldSpec[] = [
    ...PERCEPTION_FIELDS, ...(state.vlm.customFields || []),
  ];

  const appendRow = (f: PerceptionFieldSpec, isCustom: boolean) => {
    const enabled = state.vlm.enabledFields!.includes(f.key);
    const row = document.createElement('div');
    row.className = 'param-row';
    row.setAttribute('data-enabled', enabled ? 'true' : 'false');
    row.innerHTML = `
      <div class="check"></div>
      <div class="param-body">
        <input class="param-key-input" value="${escapeHtml(f.label)}" placeholder="Field name" spellcheck="false">
        <div class="param-subfields"></div>
      </div>
      <button class="param-del" title="Remove field">×</button>`;

    // Toggle enabled via checkbox
    row.querySelector('.check')!.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const cur = state.vlm.enabledFields!;
      state.vlm.enabledFields = cur.includes(f.key)
        ? cur.filter((k) => k !== f.key)
        : [...cur, f.key];
      row.setAttribute('data-enabled', state.vlm.enabledFields.includes(f.key) ? 'true' : 'false');
      saveState();
    });

    // Editable key label
    (row.querySelector('.param-key-input') as HTMLInputElement).addEventListener('change', (ev) => {
      const newLabel = (ev.target as HTMLInputElement).value.trim() || f.label;
      if (isCustom) {
        const idx = state.vlm.customFields.findIndex((c) => c.key === f.key);
        if (idx !== -1) state.vlm.customFields[idx].label = newLabel;
      }
      f.label = newLabel;
      saveState();
    });

    // Delete — only custom fields; built-in fields just toggle off
    row.querySelector('.param-del')!.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (isCustom) {
        state.vlm.customFields = state.vlm.customFields.filter((c) => c.key !== f.key);
        state.vlm.enabledFields = (state.vlm.enabledFields || []).filter((k) => k !== f.key);
        saveState(); buildParamList();
      } else {
        // For built-in fields just disable
        state.vlm.enabledFields = (state.vlm.enabledFields || []).filter((k) => k !== f.key);
        row.setAttribute('data-enabled', 'false');
        saveState();
      }
    });

    list.appendChild(row);
    renderSubFields(row, f.key);
  };

  PERCEPTION_FIELDS.forEach((f) => appendRow(f, false));
  (state.vlm.customFields || []).forEach((f) => appendRow(f, true));
}
function buildVLMList(): void {
  const sel = $('#p1-vlm-model-select-modal') as HTMLSelectElement | null;
  if (!sel) return;
  // Populate once; after that just sync the selected value
  if (sel.options.length === 0) {
    sel.innerHTML = VLM_CARDS
      .map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`)
      .join('');
    sel.addEventListener('change', () => {
      state.vlm.provider = sel.value;
      // Keep the main panel dropdown in sync
      const mainSel = $('#p1-vlm-model-select') as HTMLSelectElement | null;
      if (mainSel) mainSel.value = sel.value;
      saveState();
    });
  }
  sel.value = state.vlm.provider;
}

/* =====================================================================
   PANEL 3 — Personality with Three.js archetype figures (GLB models)
   ===================================================================== */
const ARCHETYPE_COLORS: Record<string, string> = {
  resident: '#30d158', commuter: '#0a84ff',
  tourist: '#ff9f0a',  student: '#ff375f',
};

const ARCHETYPE_GLB: Record<string, string> = {
  resident: 'agent_res_low_512px.glb',
  commuter: 'agent_com_low_512px.glb',
  tourist:  'agent_tou_Female.glb',
  student:  'agent_stu_low_512px.glb',
};
const GENERIC_GLB = 'agent_gen_low_512px.glb';

interface ArchRenderer {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  group: THREE.Group;
  mixer?: THREE.AnimationMixer;
}
let renderers: ArchRenderer[] = [];      // thumb / big card renderers
let charViewer: ArchRenderer | null = null; // large right-panel viewer
let raf: number | null = null;
let p3Bound = false;

const _gltfLoader = new GLTFLoader();

function _syncRendererSize(renderer: THREE.WebGLRenderer, camera: THREE.PerspectiveCamera): void {
  const canvas = renderer.domElement;
  const dpr = renderer.getPixelRatio();
  const displayW = canvas.clientWidth;
  const displayH = canvas.clientHeight;
  if (displayW < 1 || displayH < 1) return;
  // Only resize if drawing buffer doesn't match CSS display size × dpr
  if (canvas.width !== Math.round(displayW * dpr) || canvas.height !== Math.round(displayH * dpr)) {
    renderer.setSize(displayW, displayH, false);
    camera.aspect = displayW / displayH;
    camera.updateProjectionMatrix();
  }
}

function buildArchetypeFigure(
  canvas: HTMLCanvasElement,
  colorHex: string,
  glbPath: string,
  card: HTMLElement,
  focusHead = false,
): ArchRenderer {
  // getBoundingClientRect is reliable even when clientWidth hasn't settled yet
  const rect = canvas.getBoundingClientRect();
  const w = rect.width  || canvas.clientWidth  || 152;
  const h = rect.height || canvas.clientHeight || 192;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w, h, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  // focusHead: tight crop on head/face area (y ≈ 1.58 after +15 % upward shift)
  const eyeY   = focusHead ? 1.59 : 0.9;
  const camZ   = focusHead ? 1.1  : 2.8;
  const fov    = focusHead ? 28   : 38;
  const aspect = w > 0 && h > 0 ? w / h : 1;
  const camera = new THREE.PerspectiveCamera(fov, aspect, 0.01, 200);
  camera.position.set(0, eyeY, camZ);
  camera.lookAt(0, eyeY, 0);

  scene.add(new THREE.AmbientLight(0xffffff, 0.8));
  const key = new THREE.DirectionalLight(0xffffff, 1.4);
  key.position.set(2, 4, 3); scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.5);
  fill.position.set(-2, 1, -1); scene.add(fill);
  const rim = new THREE.PointLight(new THREE.Color(colorHex), 1.4, 15);
  rim.position.set(-1.5, 2, -2); scene.add(rim);

  const group = new THREE.Group();
  scene.add(group);

  // Drag-to-rotate
  let dragActive = false;
  let dragStartX = 0;
  let dragStartRotY = 0;
  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointerdown', (e) => {
    dragActive = true;
    dragStartX = e.clientX;
    dragStartRotY = group.rotation.y;
    canvas.setPointerCapture(e.pointerId);
    canvas.style.cursor = 'grabbing';
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!dragActive) return;
    group.rotation.y = dragStartRotY + (e.clientX - dragStartX) * 0.01;
    _syncRendererSize(renderer, camera);
    renderer.render(scene, camera);
  });
  const endDrag = () => { dragActive = false; canvas.style.cursor = 'grab'; };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);

  // Declare ar before the async load so the callback can attach mixer to it
  const ar: ArchRenderer = { renderer, scene, camera, group };

  // Show spinner while loading
  card.classList.add('arch-loading');

  _gltfLoader.load(
    glbPath,
    (gltf) => {
      const model = gltf.scene;

      // Compute bounding box from geometry directly (safe for SkinnedMesh)
      const box = new THREE.Box3();
      model.traverse((child) => {
        const mesh = child as THREE.Mesh;
        if (mesh.isMesh && mesh.geometry) {
          mesh.geometry.computeBoundingBox();
          const gb = mesh.geometry.boundingBox;
          if (gb && !gb.isEmpty()) {
            // Apply local-to-parent transforms manually up to model root
            mesh.updateWorldMatrix(true, false);
            const wb = gb.clone().applyMatrix4(mesh.matrixWorld);
            box.union(wb);
          }
        }
      });

      if (box.isEmpty()) {
        // Fallback: use full object traversal
        box.setFromObject(model);
      }

      const size   = new THREE.Vector3(); box.getSize(size);
      const center = new THREE.Vector3(); box.getCenter(center);
      const maxDim = Math.max(size.x, size.y, size.z, 0.001);
      const scale  = 1.8 / maxDim;

      model.scale.setScalar(scale);
      model.position.set(
        -center.x * scale,
        -box.min.y * scale,
        -center.z * scale,
      );

      group.add(model);

      // Play first animation clip if present
      if (gltf.animations.length > 0) {
        const mixer = new THREE.AnimationMixer(model);
        mixer.clipAction(gltf.animations[0]).play();
        ar.mixer = mixer;
      }

      // Force one render so the SkinnedMesh skeleton binds and is visible immediately
      _syncRendererSize(renderer, camera);
      renderer.render(scene, camera);

      card.classList.remove('arch-loading');
    },
    undefined,
    (err) => {
      console.warn('GLB load error', glbPath, err);
      card.classList.remove('arch-loading');
    },
  );

  return ar;
}

function startRenderLoop(): void {
  if (raf !== null) cancelAnimationFrame(raf);
  let last = 0;
  let elapsed = 0;
  const FRAME_MS = 1000 / 30;
  const tick = (now: number) => {
    raf = requestAnimationFrame(tick);
    if (now - last < FRAME_MS) return;
    const delta = (now - last) / 1000;
    last = now;
    elapsed += delta;
    const allR = charViewer ? [...renderers, charViewer] : renderers;
    for (const r of allR) {
      if (r.mixer) {
        r.mixer.update(delta);
      } else if (r.group.children.length > 0) {
        r.group.position.y = Math.sin(elapsed * 1.1) * 0.018;
        r.group.rotation.z = Math.sin(elapsed * 0.7) * 0.006;
      }
      _syncRendererSize(r.renderer, r.camera);
      r.renderer.render(r.scene, r.camera);
    }
  };
  raf = requestAnimationFrame(tick);
}
function stopRenderLoop(): void { if (raf !== null) { cancelAnimationFrame(raf); raf = null; } }

async function loadArchetypes(): Promise<ArchetypeMap> {
  if (state.archetypes && Object.keys(state.archetypes).some((k) => k !== '_comment')) return state.archetypes;
  try {
    const res = await api.l<{ profiles: ArchetypeMap }>('/api/profiles');
    const raw = res.profiles || null;
    // Strip meta keys, keep only real archetype entries
    if (raw) {
      const cleaned: ArchetypeMap = {};
      for (const [k, v] of Object.entries(raw)) {
        if (!k.startsWith('_') && v && typeof v === 'object') cleaned[k] = v as ArchetypeProfile;
      }
      state.archetypes = Object.keys(cleaned).length > 0 ? cleaned : null;
    } else {
      state.archetypes = null;
    }
    if (state.archetypes) saveState();
  } catch (e) {
    console.warn('Could not load /api/profiles', e);
    state.archetypes = null;
  }
  return state.archetypes || {};
}

function glbUrl(arch: string): string {
  const glbFile = ARCHETYPE_GLB[arch] ?? GENERIC_GLB;
  const base = (window.location.protocol === 'http:' || window.location.protocol === 'https:')
    ? window.location.origin : 'http://localhost:8091';
  return `${base}/assets/agents/${glbFile}`;
}

function renderArchetypeCards(thumbMode = false): void {
  const container = thumbMode
    ? ($('#p3-thumbs') as HTMLElement)
    : ($('#p3-bar')   as HTMLElement);
  container.innerHTML = '';
  for (const r of renderers) try { r.renderer.dispose(); } catch { /* ignore */ }
  renderers = [];

  const archetypes = state.archetypes || {};
  const fixedOrder = ['resident', 'commuter', 'tourist', 'student'];
  const order = [
    ...fixedOrder,
    ...Object.keys(archetypes).filter((a) => !fixedOrder.includes(a)),
  ];

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cvW = thumbMode ? 64 : 190;
  const cvH = thumbMode ? 64 : 240;

  order.forEach((arch) => {
    const isCustom = !fixedOrder.includes(arch);
    const card = document.createElement('div');
    card.className = thumbMode ? 'archetype-card thumb' : 'archetype-card';
    card.setAttribute('data-archetype', arch);
    if (state.selectedArchetype === arch) card.setAttribute('data-selected', 'true');
    card.innerHTML = `
      <canvas></canvas>
      ${isCustom ? `<button class="arch-delete-btn" title="Delete" aria-label="Delete">✕</button>` : ''}
      <div class="arch-name">${escapeHtml(arch[0].toUpperCase() + arch.slice(1))}</div>
      <div class="arch-tag">${escapeHtml(archetypes[arch]?.profile?.pace || 'moderate')}</div>`;
    container.appendChild(card);

    if (isCustom) {
      card.querySelector('.arch-delete-btn')!.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!confirm(`Delete archetype "${arch}"?`)) return;
        if (state.archetypes) delete state.archetypes[arch];
        if (state.selectedArchetype === arch) state.selectedArchetype = null;
        saveState();
        renderArchetypeCards(thumbMode);
      });
    }

    const cv = card.querySelector('canvas') as HTMLCanvasElement;
    cv.width  = cvW * dpr;
    cv.height = cvH * dpr;
    renderers.push(buildArchetypeFigure(cv, ARCHETYPE_COLORS[arch] || '#5e5ce6', glbUrl(arch), card, thumbMode));
    card.addEventListener('click', () => openArchetypeEditor(arch));
  });

  const addTile = document.createElement('div');
  addTile.className = thumbMode ? 'archetype-card thumb add-tile' : 'archetype-card add-tile';
  addTile.innerHTML = `<div class="plus">+</div><div class="arch-name">Create</div>${thumbMode ? '' : '<div class="arch-tag">new archetype</div>'}`;
  addTile.addEventListener('click', () => {
    const id = prompt('New archetype id (snake_case):', 'custom_1');
    if (!id || !state.archetypes) return;
    state.archetypes[id] = {
      profile: { interests: [], pace: 'moderate', curiosity: 'moderate', social: 'moderate', description: '' },
      daily_plan: [],
    };
    saveState(); renderArchetypeCards(thumbMode);
  });
  container.appendChild(addTile);
}

function openArchetypeEditor(arch: string): void {
  state.selectedArchetype = arch; saveState();

  // Switch from card-bar to 3-column layout
  ($('#p3-bar')    as HTMLElement).classList.add('hidden');
  ($('#p3-layout') as HTMLElement).classList.remove('hidden');

  // Thumb strip + editor title
  renderArchetypeCards(true);
  ($('#p3-editor-title') as HTMLElement).textContent =
    `${arch[0].toUpperCase()}${arch.slice(1)} — Profile & Daily Plan`;
  renderProfileEditor(arch);

  // Large character viewer on the right
  const charCanvas = $('#p3-char-canvas') as HTMLCanvasElement;
  const panel = $('#p3-char-viewer') as HTMLElement;
  const W = panel.clientWidth  || 300;
  const H = panel.clientHeight || 600;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  charCanvas.width  = W * dpr;
  charCanvas.height = H * dpr;
  if (charViewer) { try { charViewer.renderer.dispose(); } catch { /**/ } }
  charViewer = buildArchetypeFigure(
    charCanvas,
    ARCHETYPE_COLORS[arch] || '#5e5ce6',
    glbUrl(arch),
    panel,
  );
  // Wider field-of-view and further back for the tall viewer
  charViewer.camera.fov = 42;
  charViewer.camera.aspect = W / H;
  charViewer.camera.position.set(0, 1.0, 3.8);
  charViewer.camera.lookAt(0, 1.0, 0);
  charViewer.camera.updateProjectionMatrix();
}

function renderProfileEditor(arch: string): void {
  if (!state.archetypes) return;
  const data: ArchetypeProfile = state.archetypes[arch];
  if (!data) return;
  const body = $('#p3-editor-body') as HTMLElement;
  data.profile ||= {};
  data.daily_plan ||= [];
  const prof = data.profile!;
  const phases = data.daily_plan!;

  const paceSeg = (): string =>
    ['leisurely', 'moderate', 'fast', 'none'].map((p) =>
      `<button data-v="${p}" aria-selected="${prof.pace === p ? 'true' : 'false'}">${p}</button>`).join('');
  const levelSeg = (key: 'curiosity' | 'social'): string =>
    ['none', 'low', 'moderate', 'high'].map((p) =>
      `<button data-key="${key}" data-v="${p}" aria-selected="${prof[key] === p ? 'true' : 'false'}">${p}</button>`).join('');

  body.innerHTML = `
    <div class="field-row">
      <span class="field-label">Description</span>
      <textarea class="textarea" id="p3-desc">${escapeHtml(prof.description || '')}</textarea>
    </div>
    <div class="field-row">
      <span class="field-label">Interests</span>
      <div class="chip-input" id="p3-interests"></div>
      <span class="helper">Press Enter to add. These bias amenity selection.</span>
    </div>
    <div class="field-row"><span class="field-label">Pace</span>
      <div class="segmented" id="p3-pace">${paceSeg()}</div></div>
    <div class="field-row"><span class="field-label">Curiosity</span>
      <div class="segmented" id="p3-curiosity">${levelSeg('curiosity')}</div></div>
    <div class="field-row"><span class="field-label">Social</span>
      <div class="segmented" id="p3-social">${levelSeg('social')}</div></div>
    <div class="field-row">
      <div class="row between"><span class="field-label">Daily Plan</span>
        <button class="btn tiny" id="p3-add-phase">+ Phase</button></div>
      <div id="p3-phases"></div>
    </div>`;

  // Interests chip input
  const chipBox = $('#p3-interests') as HTMLElement;
  const renderChips = () => {
    chipBox.innerHTML = '';
    (prof.interests || []).forEach((v, i) => {
      const chip = document.createElement('span');
      chip.className = 'ci-chip';
      chip.innerHTML = `${escapeHtml(v)} <button data-i="${i}">×</button>`;
      chip.querySelector('button')!.addEventListener('click', () => {
        prof.interests!.splice(i, 1); saveState(); renderChips();
      });
      chipBox.appendChild(chip);
    });
    const inp = document.createElement('input');
    inp.placeholder = 'add interest…';
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && inp.value.trim()) {
        (prof.interests ||= []).push(inp.value.trim());
        saveState(); renderChips();
      }
    });
    chipBox.appendChild(inp);
  };
  renderChips();

  $('#p3-desc')!.addEventListener('input',
    (e) => { prof.description = (e.target as HTMLTextAreaElement).value; saveState(); });

  const wireSeg = (sel: string, key: 'pace' | 'curiosity' | 'social') => {
    $$(`${sel} button`).forEach((b) => b.addEventListener('click', () => {
      prof[key] = b.getAttribute('data-v') as string;
      saveState();
      $$(`${sel} button`).forEach((x) => x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
    }));
  };
  wireSeg('#p3-pace', 'pace');
  wireSeg('#p3-curiosity', 'curiosity');
  wireSeg('#p3-social', 'social');

  const renderPhases = () => {
    const host = $('#p3-phases') as HTMLElement;
    host.innerHTML = '';
    phases.forEach((phase: DailyPlanPhase, idx) => {
      const card = document.createElement('div');
      card.className = 'phase-card';
      const targets = (phase.target_types || []).join(', ');
      const prefs = (phase.perception_preferences || []).join(', ');
      const avoid = (phase.perception_avoid || [])
        .map((a) => `${a.field}=${a.value}`).join(', ');
      const TOD_OPTIONS = ['any','morning','afternoon','evening','night'];
      const todOpts = TOD_OPTIONS.map((t) =>
        `<option value="${t}"${(phase.time_of_day||'any')===t?' selected':''}>${t}</option>`).join('');
      // Feather-style inline SVG icons for each field
      const I = {
        goal:    `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="2.5"/><line x1="8" y1="1.5" x2="8" y2="0.5"/><line x1="8" y1="15.5" x2="8" y2="14.5"/><line x1="1.5" y1="8" x2="0.5" y2="8"/><line x1="15.5" y1="8" x2="14.5" y2="8"/></svg>`,
        targets: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.5C5.5 1.5 3.5 3.5 3.5 6c0 3.5 4.5 8.5 4.5 8.5s4.5-5 4.5-8.5C12.5 3.5 10.5 1.5 8 1.5z"/><circle cx="8" cy="6" r="1.5" fill="currentColor" stroke="none"/></svg>`,
        eye:     `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>`,
        eyeOff:  `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="2" x2="14" y2="14"/><path d="M6.5 6.5a2 2 0 0 0 3 3M4.2 4.2C2.6 5.3 1 8 1 8s2.5 5 7 5c1.1 0 2.1-.3 3-.7M7 3.1c.3 0 .6 0 1 0 4.5 0 7 5 7 5s-.6 1.1-1.7 2.3"/></svg>`,
      };
      card.innerHTML = `
        <div class="phase-head">
          <input class="input phase-id-input" placeholder="phase id" value="${escapeHtml(phase.id || `phase_${idx}`)}" data-k="id">
          <select class="select phase-tod-select" data-k="time_of_day">${todOpts}</select>
          <button class="btn ghost tiny phase-del-btn" data-i="${idx}">×</button>
        </div>
        <div class="col" style="gap: 6px;">
          <div class="input-icon-wrap"><input class="input" placeholder="goal" value="${escapeHtml(phase.goal || '')}" data-k="goal"><span class="input-icon" title="Goal / objective">${I.goal}</span></div>
          <div class="input-icon-wrap"><input class="input" placeholder="target_types (comma)" value="${escapeHtml(targets)}" data-k="target_types"><span class="input-icon" title="Target place types">${I.targets}</span></div>
          <div class="input-icon-wrap"><input class="input" placeholder="perception_preferences (comma)" value="${escapeHtml(prefs)}" data-k="perception_preferences"><span class="input-icon" title="Perception preferences">${I.eye}</span></div>
          <div class="input-icon-wrap"><input class="input" placeholder='perception_avoid (field=value, comma)' value="${escapeHtml(avoid)}" data-k="perception_avoid"><span class="input-icon" title="Perception avoid">${I.eyeOff}</span></div>
        </div>`;
      card.querySelector('.phase-del-btn')!.addEventListener('click', () => {
        phases.splice(idx, 1); saveState(); renderPhases();
      });
      // id and time_of_day
      (card.querySelector('.phase-id-input') as HTMLInputElement).addEventListener('input', (e) => {
        phase.id = (e.target as HTMLInputElement).value; saveState();
      });
      (card.querySelector('.phase-tod-select') as HTMLSelectElement).addEventListener('change', (e) => {
        phase.time_of_day = (e.target as HTMLSelectElement).value; saveState();
      });
      card.querySelectorAll('input.input:not(.phase-id-input)').forEach((inp) => {
        inp.addEventListener('input', () => {
          const k = (inp as HTMLInputElement).dataset.k!;
          const v = (inp as HTMLInputElement).value;
          if (k === 'goal') phase.goal = v;
          else if (k === 'target_types') phase.target_types = v.split(',').map((s) => s.trim()).filter(Boolean);
          else if (k === 'perception_preferences') phase.perception_preferences = v.split(',').map((s) => s.trim()).filter(Boolean);
          else if (k === 'perception_avoid') {
            phase.perception_avoid = v.split(',').map((s) => s.trim()).filter(Boolean).map((pair) => {
              const [f, val] = pair.split('=').map((x) => x.trim());
              return { field: f, value: val || '' };
            });
          }
          saveState();
        });
      });
      host.appendChild(card);
      // Upgrade the time-of-day <select> to the shared apple-select component
      const todSel = card.querySelector('.phase-tod-select') as HTMLSelectElement;
      if (todSel) appleSelect(todSel);
    });
  };
  renderPhases();
  $('#p3-add-phase')!.addEventListener('click', () => {
    phases.push({
      id: `phase_${phases.length + 1}`, time_of_day: 'any', goal: '',
      target_types: [], perception_preferences: [], perception_avoid: [],
      max_visits: 1, en_route_stops: [],
    });
    saveState(); renderPhases();
  });
}

function collapseToCardBar(): void {
  // Dispose large viewer
  if (charViewer) { try { charViewer.renderer.dispose(); } catch { /**/ } charViewer = null; }
  state.selectedArchetype = null; saveState();
  ($('#p3-layout') as HTMLElement).classList.add('hidden');
  ($('#p3-bar')    as HTMLElement).classList.remove('hidden');
  renderArchetypeCards(false);
}

async function panel3Enter(): Promise<void> {
  // Always start in unselected (big card bar) state
  ($('#p3-layout') as HTMLElement).classList.add('hidden');
  ($('#p3-bar')    as HTMLElement).classList.remove('hidden');
  state.selectedArchetype = null; saveState();
  if (!state.archetypes || !Object.keys(state.archetypes).some((k) => k !== '_comment')) await loadArchetypes();
  renderArchetypeCards(false); startRenderLoop();
  if (p3Bound) return; p3Bound = true;
  $('#p3-editor-close')!.addEventListener('click', collapseToCardBar);
  $('#p3-save')!.addEventListener('click', async () => {
    try {
      const res = await api.postJSON<{ error?: string; archetypes?: string[] }>(
        api.lab, '/api/profiles', { profiles: state.archetypes });
      if (res.error) { toast(res.error, 'danger'); return; }
      ($('#p3-status') as HTMLElement).textContent =
        `Saved to plans.local.json — ${(res.archetypes || []).length} archetypes.`;
      toast('Saved.', 'success');
    } catch (e) {
      toast(`Save failed: ${e instanceof Error ? e.message : e}`, 'danger');
    }
  });
  $('#p3-export')!.addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(state.archetypes, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'profiles.json'; a.click();
    URL.revokeObjectURL(url);
  });
  $('#p3-reload-saved')!.addEventListener('click', async () => {
    state.archetypes = null;
    await loadArchetypes();
    renderArchetypeCards();
    toast('Reloaded saved profiles.', 'info');
  });
  $('#p3-reload-defaults')!.addEventListener('click', async () => {
    try {
      const res = await api.l<{ profiles?: ArchetypeMap; error?: string }>('/api/profiles/defaults');
      if (res.error) { toast(res.error, 'danger'); return; }
      state.archetypes = res.profiles ?? null;
      saveState();
      renderArchetypeCards();
      toast('Restored original defaults.', 'success');
    } catch (e) {
      toast(`Restore failed: ${e instanceof Error ? e.message : e}`, 'danger');
    }
  });
}

/* =====================================================================
   LLM ENGINE — shared by Panels 4 and 5
   ===================================================================== */
const LOCAL_PROVIDERS = [
  { id: 'ollama',   name: 'Ollama',   desc: 'Local LLM — no GPU required.' },
  { id: 'vllm',     name: 'vLLM',     desc: 'GPU-backed Docker container.' },
  { id: 'lmdeploy', name: 'LMDeploy', desc: 'Optimised GPU inference.' },
  { id: 'docker',   name: 'Docker',   desc: 'llama.cpp via Docker Runner.' },
];
const API_PROVIDERS = [
  { id: 'gemini',     name: 'Google Gemini', desc: 'Flash 2.0 / Pro — best price/quality.' },
  { id: 'openai',     name: 'OpenAI',        desc: 'GPT-4o / o-mini — premium model.' },
  { id: 'deepseek',   name: 'DeepSeek',      desc: 'V3 / Chat — strong reasoning, low cost.' },
  { id: 'openrouter', name: 'OpenRouter',    desc: 'Multi-model gateway.' },
  { id: 'groq',       name: 'Groq',          desc: 'Ultra-fast Llama / Mixtral.' },
];
const DEFAULT_MODELS: Record<string, string> = {
  gemini: 'gemini-2.0-flash-lite', openai: 'gpt-4o-mini', deepseek: 'deepseek-chat',
  openrouter: 'openai/gpt-4o-mini', groq: 'llama-3.3-70b-versatile',
  ollama: 'qwen2.5:3b', vllm: 'Qwen/Qwen2.5-7B-Instruct',
  lmdeploy: 'qwen2.5-coder:3b', docker: 'llama3.2:3b',
};

/* ABM performance scores — 0–5 per dimension (higher = better for agent simulations) */
interface ABMScore { spatial: number; fidelity: number; json: number; speed: number; cost: number; }
const ABM_SCORES: Record<string, ABMScore> = {
  gemini:     { spatial: 4, fidelity: 4, json: 5, speed: 5, cost: 5 },
  openai:     { spatial: 4, fidelity: 5, json: 5, speed: 4, cost: 2 },
  deepseek:   { spatial: 4, fidelity: 4, json: 4, speed: 3, cost: 5 },
  openrouter: { spatial: 3, fidelity: 4, json: 4, speed: 3, cost: 3 },
  groq:       { spatial: 3, fidelity: 3, json: 4, speed: 5, cost: 5 },
  ollama:     { spatial: 2, fidelity: 3, json: 3, speed: 2, cost: 5 },
  vllm:       { spatial: 4, fidelity: 4, json: 5, speed: 4, cost: 5 },
  lmdeploy:   { spatial: 4, fidelity: 4, json: 5, speed: 5, cost: 5 },
  docker:     { spatial: 2, fidelity: 2, json: 3, speed: 2, cost: 5 },
};
const ABM_DIMS: { key: keyof ABMScore; label: string; tip: string }[] = [
  { key: 'spatial',  label: 'Urban',   tip: 'Street-level spatial reasoning' },
  { key: 'fidelity', label: 'Role',    tip: 'Persona consistency across steps' },
  { key: 'json',     label: 'JSON',    tip: 'Structured output reliability' },
  { key: 'speed',    label: 'Speed',   tip: 'Inference latency suitability' },
  { key: 'cost',     label: 'Cost',    tip: 'Operational cost efficiency' },
];

/* =====================================================================
   APPLE SELECT — custom dropdown replacing native <select>
   ===================================================================== */
function appleSelect(el: HTMLSelectElement): void {
  if (el.dataset['appleUpgraded']) return;
  el.dataset['appleUpgraded'] = '1';

  const wrap = document.createElement('div');
  wrap.className = 'asl-wrap';
  el.parentNode!.insertBefore(wrap, el);
  wrap.appendChild(el);
  el.style.cssText = 'position:absolute;pointer-events:none;opacity:0;height:0;width:0;overflow:hidden;';

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'asl-btn';
  btn.setAttribute('aria-haspopup', 'listbox');
  btn.setAttribute('aria-expanded', 'false');

  const valSpan = document.createElement('span');
  valSpan.className = 'asl-val';

  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 10 6');
  svg.setAttribute('width', '10');
  svg.setAttribute('height', '6');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'asl-chevron');
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', 'M1 1 L5 5 L9 1');
  p.setAttribute('stroke', 'currentColor');
  p.setAttribute('stroke-width', '1.5');
  p.setAttribute('stroke-linecap', 'round');
  p.setAttribute('stroke-linejoin', 'round');
  svg.appendChild(p);
  btn.appendChild(valSpan);
  btn.appendChild(svg);
  wrap.appendChild(btn);

  const menu = document.createElement('div');
  menu.className = 'asl-menu hidden';
  menu.setAttribute('role', 'listbox');
  wrap.appendChild(menu);

  function buildMenu(): void {
    menu.innerHTML = '';
    let seenCustom = false;
    Array.from(el.options).forEach((opt) => {
      if (!seenCustom && opt.text.includes(' — Custom')) {
        seenCustom = true;
        const sep = document.createElement('div');
        sep.className = 'asl-sep';
        const lbl = document.createElement('div');
        lbl.className = 'asl-sec-label';
        lbl.textContent = 'Custom';
        menu.appendChild(sep);
        menu.appendChild(lbl);
      }
      const item = document.createElement('div');
      item.className = 'asl-item' + (opt.value === el.value ? ' sel' : '');
      item.setAttribute('role', 'option');
      item.dataset['v'] = opt.value;

      const ck = document.createElement('span');
      ck.className = 'asl-check';
      ck.textContent = '✓';

      const tx = document.createElement('span');
      tx.textContent = opt.text.replace(' — Custom', '');

      item.appendChild(ck);
      item.appendChild(tx);
      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        el.value = opt.value;
        el.dispatchEvent(new Event('change', { bubbles: true }));
        syncDisplay();
        close();
      });
      menu.appendChild(item);
    });
  }

  function syncDisplay(): void {
    const opt = el.options[el.selectedIndex];
    valSpan.textContent = opt ? opt.text.replace(' — Custom', '') : '—';
    menu.querySelectorAll<HTMLElement>('.asl-item').forEach((item) =>
      item.classList.toggle('sel', item.dataset['v'] === el.value));
  }

  function open(): void {
    buildMenu();
    menu.classList.remove('hidden');
    btn.setAttribute('aria-expanded', 'true');
  }

  function close(): void {
    menu.classList.add('hidden');
    btn.setAttribute('aria-expanded', 'false');
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.classList.contains('hidden') ? open() : close();
  });

  document.addEventListener('click', (e) => {
    if (!wrap.contains(e.target as Node)) close();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
  });

  /* Intercept programmatic .value = x so the display updates */
  const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!;
  Object.defineProperty(el, 'value', {
    get() { return desc.get!.call(this); },
    set(v: string) { desc.set!.call(this, v); syncDisplay(); },
    configurable: true,
  });

  /* Rebuild when options are replaced via innerHTML = ... */
  new MutationObserver(() => {
    syncDisplay();
    if (!menu.classList.contains('hidden')) buildMenu();
  }).observe(el, { childList: true });

  syncDisplay();
}

function applyAppleSelects(): void {
  document.querySelectorAll<HTMLSelectElement>('select.input').forEach(appleSelect);
}

/* Singleton compare modal — shared across both panels */
let compareModalBound = false;
let compareModalStateRef: (SingleAgentState | MultiAgentState) | null = null;

function renderCompareModal(stateRef: SingleAgentState | MultiAgentState, onPick: (id: string) => void): void {
  const body = $('#llm-compare-body') as HTMLElement | null;
  if (!body) return;
  const all = [...LOCAL_PROVIDERS, ...API_PROVIDERS];
  const cur = stateRef.llm.providerId;

  const dimLegend = ABM_DIMS.map((d) =>
    `<div class="cmp-legend-item"><strong>${d.label}</strong> — ${d.tip}</div>`
  ).join('');

  const colHeaders = ABM_DIMS.map((d) =>
    `<div class="cmp-col-hd" title="${d.tip}">${d.label}</div>`
  ).join('');

  const localRows = LOCAL_PROVIDERS.map((p) => providerRow(p, cur, 'Local')).join('');
  const apiRows   = API_PROVIDERS.map((p) => providerRow(p, cur, 'API')).join('');

  function providerRow(p: { id: string; name: string; desc: string }, cur: string, badge: string): string {
    const s = ABM_SCORES[p.id] ?? { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0 };
    const bars = ABM_DIMS.map((d) => {
      const pct = Math.round((s[d.key] / 5) * 100);
      const color = pct >= 80 ? 'var(--success)' : pct >= 60 ? 'var(--warning)' : 'var(--danger)';
      return `<div class="cmp-score-cell">
        <div class="cmp-bar" style="--fill:${pct}%;--color:${color}"></div>
        <span class="cmp-score-num">${s[d.key]}<span class="cmp-denom">/5</span></span>
      </div>`;
    }).join('');
    const sel = p.id === cur;
    return `<div class="cmp-row${sel ? ' cmp-selected' : ''}" data-id="${p.id}">
      <div class="cmp-info">
        <div class="cmp-name-row">
          <span class="cmp-name">${escapeHtml(p.name)}</span>
          <span class="cmp-badge ${badge === 'Local' ? 'local' : 'api'}">${badge}</span>
          ${sel ? '<span class="cmp-active-dot"></span>' : ''}
        </div>
        <div class="cmp-desc">${escapeHtml(p.desc)}</div>
        <div class="cmp-default-model">${escapeHtml(DEFAULT_MODELS[p.id] || '')}</div>
      </div>
      ${bars}
    </div>`;
  }

  body.innerHTML = `
    <div class="cmp-legend">${dimLegend}</div>
    <div class="cmp-table">
      <div class="cmp-section-label">Local Models</div>
      <div class="cmp-col-headers">
        <div class="cmp-info-hd">Provider</div>${colHeaders}
      </div>
      ${localRows}
      <div class="cmp-section-label" style="margin-top:16px;">API Models</div>
      <div class="cmp-col-headers">
        <div class="cmp-info-hd">Provider</div>${colHeaders}
      </div>
      ${apiRows}
    </div>
    <div class="cmp-footnote">
      Scores are estimated suitability for LLM-driven ABM — not live benchmarks.
      Click any row to select that provider.
    </div>`;

  body.querySelectorAll<HTMLElement>('.cmp-row').forEach((row) => {
    row.addEventListener('click', () => {
      const id = row.getAttribute('data-id')!;
      onPick(id);
      document.getElementById('llm-compare-modal')!.classList.add('hidden');
    });
  });
}

function buildLLMEngine(rootId: string, stateRef: SingleAgentState | MultiAgentState, serverBase?: string): {
  render(): void; bindApply(modelSel: string, keySel: string | null, applySel: string, statusSel: string): void;
} {
  const ROOT = document.getElementById(rootId)!;

  function populateSelect(): void {
    const isLocal = stateRef.llm.mode === 'local';
    const providers = isLocal ? LOCAL_PROVIDERS : API_PROVIDERS;
    const sel = ROOT.querySelector('select[id$="-llm-provider"]') as HTMLSelectElement | null;
    if (!sel) return;
    // If stored providerId doesn't belong to current mode's list, reset to first in list
    if (!providers.some((p) => p.id === stateRef.llm.providerId)) {
      stateRef.llm.providerId = providers[0].id;
      saveState();
    }
    sel.innerHTML = providers.map((p) =>
      `<option value="${p.id}"${stateRef.llm.providerId === p.id ? ' selected' : ''}>${escapeHtml(p.name)}</option>`
    ).join('');
    // Keep state in sync with actual DOM value
    stateRef.llm.providerId = sel.value;
    saveState();
    const keyGroup = ROOT.querySelector('[id$="-key-group"]') as HTMLElement | null;
    if (keyGroup) keyGroup.style.display = isLocal ? 'none' : '';
    // Update model placeholder for the selected provider
    const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
    if (modelInp && !modelInp.value) modelInp.placeholder = DEFAULT_MODELS[sel.value] || '';
  }

  function onPick(id: string): void {
    const isLocalProvider = LOCAL_PROVIDERS.some((p) => p.id === id);
    stateRef.llm.mode = isLocalProvider ? 'local' : 'api';
    stateRef.llm.providerId = id;
    const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
    if (modelInp) modelInp.value = stateRef.llm.model || DEFAULT_MODELS[id] || '';
    saveState();
    ROOT.querySelectorAll('.llm-tab-row button').forEach((b) =>
      b.setAttribute('aria-selected', b.getAttribute('data-v') === stateRef.llm.mode ? 'true' : 'false'));
    populateSelect();
  }

  function render(): void {
    populateSelect();
    ROOT.querySelectorAll('.llm-tab-row button').forEach((b) => {
      b.addEventListener('click', () => {
        stateRef.llm.mode = b.getAttribute('data-v') as 'local' | 'api'; saveState();
        ROOT.querySelectorAll('.llm-tab-row button').forEach((x) =>
          x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
        const providers = stateRef.llm.mode === 'local' ? LOCAL_PROVIDERS : API_PROVIDERS;
        if (!providers.some((p) => p.id === stateRef.llm.providerId)) {
          stateRef.llm.providerId = providers[0].id;
        }
        populateSelect();
      });
    });
    const sel = ROOT.querySelector('select[id$="-llm-provider"]') as HTMLSelectElement | null;
    if (sel) {
      sel.addEventListener('change', () => {
        stateRef.llm.providerId = sel.value; saveState();
        const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
        if (modelInp) modelInp.value = stateRef.llm.model || DEFAULT_MODELS[sel.value] || '';
      });
    }
    const toggleBtn = ROOT.querySelector('[id$="-compare-toggle"]') as HTMLButtonElement | null;
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        compareModalStateRef = stateRef;
        renderCompareModal(stateRef, onPick);
        document.getElementById('llm-compare-modal')!.classList.remove('hidden');
      });
    }
    /* Wire close button once, using a flag on the element */
    const closeBtn = $('#llm-compare-close');
    if (closeBtn && !closeBtn.dataset['bound']) {
      closeBtn.dataset['bound'] = '1';
      closeBtn.addEventListener('click', () => {
        document.getElementById('llm-compare-modal')!.classList.add('hidden');
      });
      $('#llm-compare-modal')!.addEventListener('click', (e) => {
        if (e.target === e.currentTarget) document.getElementById('llm-compare-modal')!.classList.add('hidden');
      });
    }
    const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
    if (modelInp && !modelInp.value) {
      modelInp.value = stateRef.llm.model || DEFAULT_MODELS[stateRef.llm.providerId] || '';
    }
  }

  function bindApply(modelSel: string, keySel: string | null, applySel: string, statusSel: string): void {
    const apply = ROOT.querySelector(applySel);
    if (!apply) return;
    apply.addEventListener('click', async () => {
      // Always read live DOM values — prevents stale state/provider mismatch
      const sel = ROOT.querySelector('select[id$="-llm-provider"]') as HTMLSelectElement | null;
      const provider = sel?.value || stateRef.llm.providerId;
      stateRef.llm.providerId = provider; saveState();
      const modelInp = ROOT.querySelector(modelSel) as HTMLInputElement | null;
      const model = modelInp?.value.trim() || DEFAULT_MODELS[provider] || '';
      const keyInp = keySel ? (ROOT.querySelector(keySel) as HTMLInputElement | null) : null;
      const apiKey = keyInp?.value.trim() || '';
      stateRef.llm.model = model; stateRef.llm.apiKey = apiKey; saveState();
      const base = serverBase || api.map;
      try {
        const q = new URLSearchParams({ provider, model });
        if (apiKey) q.set('api_key', apiKey);
        await api.postJSON(base, `/api/config/llm?${q.toString()}`, {});
        toast(`LLM set: ${provider} / ${model}`, 'success');
        const status = ROOT.querySelector(statusSel) as HTMLElement | null;
        if (status) status.textContent = `${provider} · ${model}`;
      } catch (e) {
        toast(`LLM swap failed: ${e instanceof Error ? e.message : e}`, 'danger');
      }
    });
  }
  return { render, bindApply };
}

/* =====================================================================
   PANEL 4 — Single Agent
   ===================================================================== */
const moodColors: Record<string, string> = {
  happy: '#30d158', excited: '#ff9f0a', neutral: '#5e5ce6', tired: '#a78bfa',
  frustrated: '#ff453a', curious: '#64d2ff', anxious: '#ff375f',
};
let p4Bound = false;
let pickMode: 'start' | 'target' | null = null;
let p4StepTimer: number | null = null;
let startMarker: MapboxMarker | null = null;
let targetMarker: MapboxMarker | null = null;
let trailPopup: MapboxPopup | null = null;
interface StepLogEntry { pos: [number, number]; step: number; topic: string; description: string; mood: string; }
let stepLog: StepLogEntry[] = [];
let expandedDetailId: string | null = null;

function populateArchetypeSelect(): void {
  const sel = $('#p4-archetype') as HTMLSelectElement | null;
  if (!sel) return;
  const base = ['resident', 'commuter', 'tourist', 'student'];
  const custom = state.archetypes
    ? Object.keys(state.archetypes).filter((k) => !base.includes(k))
    : [];
  const all = [...base, ...custom];
  const labels: Record<string, string> = {
    resident: 'Resident', commuter: 'Commuter', tourist: 'Tourist', student: 'Student',
  };
  sel.innerHTML = all.map((a) => {
    const label = labels[a] ?? (a[0].toUpperCase() + a.slice(1).replace(/_/g, ' '));
    const group = custom.includes(a) ? ' — Custom' : '';
    return `<option value="${a}">${label}${group}</option>`;
  }).join('');
  sel.value = all.includes(state.singleAgent.archetype) ? state.singleAgent.archetype : base[0];
}

function panel4Enter(): void {
  populateArchetypeSelect();
  ($('#p4-navmode') as HTMLSelectElement).value = state.singleAgent.navMode;
  bindPanel4();
  if (state.singleAgent.start && map) {
    startMarker = new mapboxgl.Marker({ color: '#30d158' })
      .setLngLat([state.singleAgent.start.lon, state.singleAgent.start.lat])
      .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
  }
  if (state.singleAgent.target && map) {
    targetMarker = new mapboxgl.Marker({ color: '#0a84ff' })
      .setLngLat([state.singleAgent.target.lon, state.singleAgent.target.lat])
      .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
  }
}

function bindPanel4(): void {
  if (p4Bound) return; p4Bound = true;

  ($('#p4-archetype') as HTMLSelectElement).addEventListener('change', (e) => {
    state.singleAgent.archetype = (e.target as HTMLSelectElement).value; saveState();
  });

  ($('#p4-navmode') as HTMLSelectElement).addEventListener('change', async (e) => {
    const mode = (e.target as HTMLSelectElement).value as SingleAgentState['navMode'];
    state.singleAgent.navMode = mode; saveState();
    try { await api.postJSON(api.lab, '/api/config/nav-mode', { mode }); } catch { /* lab might be down */ }
  });
  // Restore threshold display values from state
  ($('#p4-gps-val') as HTMLElement).textContent = String(state.singleAgent.navGpsDist);
  ($('#p4-compass-val') as HTMLElement).textContent = String(state.singleAgent.navCompassDist);

  // Inline threshold editors — pencil button swaps span ↔ input, Enter/blur commits
  function bindThresholdEdit(
    editBtnId: string, valSpanId: string, groupId: string,
    stateKey: 'navGpsDist' | 'navCompassDist',
  ): void {
    const btn = $('#' + editBtnId) as HTMLButtonElement;
    const valSpan = $('#' + valSpanId) as HTMLElement;
    const group = $('#' + groupId) as HTMLElement;
    btn.addEventListener('click', () => {
      if (group.querySelector('.threshold-input')) return; // already editing
      const currentVal = String((state.singleAgent as Record<string, unknown>)[stateKey]);
      const inp = document.createElement('input');
      inp.type = 'number'; inp.className = 'threshold-input';
      inp.min = '10'; inp.max = '500'; inp.value = currentVal;
      valSpan.replaceWith(inp);
      inp.focus(); inp.select();
      const commit = async () => {
        const v = Math.max(10, Math.min(500, parseInt(inp.value, 10) || +currentVal));
        const newSpan = document.createElement('span');
        newSpan.className = 'threshold-value'; newSpan.id = valSpanId;
        newSpan.textContent = String(v);
        inp.replaceWith(newSpan);
        (state.singleAgent as Record<string, unknown>)[stateKey] = v; saveState();
        try {
          await api.postJSON(api.lab, '/api/config/nav-thresholds', {
            gps_dist: state.singleAgent.navGpsDist,
            compass_dist: state.singleAgent.navCompassDist,
          });
        } catch { /* server may be down */ }
      };
      inp.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); commit(); } });
      inp.addEventListener('blur', commit);
    });
  }
  bindThresholdEdit('p4-gps-edit', 'p4-gps-val', 'p4-gps-group', 'navGpsDist');
  bindThresholdEdit('p4-compass-edit', 'p4-compass-val', 'p4-compass-group', 'navCompassDist');
  $('#p4-pick-start')!.addEventListener('click', () => {
    pickMode = 'start';
    ($('#p4-config-status') as HTMLElement).textContent = 'Click the map to place the START point.';
    map?.getCanvas().classList.add('map-pick');
  });
  $('#p4-pick-target')!.addEventListener('click', () => {
    pickMode = 'target';
    ($('#p4-config-status') as HTMLElement).textContent = 'Click the map to place the TARGET point.';
    map?.getCanvas().classList.add('map-pick');
  });
  $('#p4-reset')!.addEventListener('click', resetSingleAgent);

  // Expand metric cards into floating overlay
  document.querySelectorAll<HTMLElement>('#panel-4 .expandable-card .expand-card-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const card = btn.closest<HTMLElement>('[data-detail-id]');
      if (!card) return;
      expandedDetailId = card.dataset['detailId'] || null;
      const overlay = $('#p4-detail-overlay') as HTMLElement;
      const title = $('#p4-detail-title') as HTMLElement;
      if (!overlay || !title) return;
      const h4Text = card.querySelector('h4')?.firstChild?.textContent?.trim() || 'Detail';
      title.textContent = h4Text;
      overlay.classList.remove('hidden');   // show first so guard passes
      updateDetailOverlay(card);
    });
  });
  $('#p4-detail-close')?.addEventListener('click', () => {
    ($('#p4-detail-overlay') as HTMLElement)?.classList.add('hidden');
    expandedDetailId = null;
  });
  ($('#p4-detail-overlay') as HTMLElement)?.addEventListener('click', (e) => {
    if ((e.target as HTMLElement).id === 'p4-detail-overlay') {
      ($('#p4-detail-overlay') as HTMLElement)?.classList.add('hidden');
      expandedDetailId = null;
    }
  });

  map?.on('click', 'trail-dots-pt', (e: MapboxMapEvent) => {
    if (!map || state.currentPanel !== 4 || !e.features?.length) return;
    const p = e.features[0].properties as { step: string | number; topic: string; description: string; mood: string };
    if (trailPopup) trailPopup.remove();
    const moodStr = String(p.mood || 'neutral');
    const moodColor = (MOOD_COLORS as Record<string, string>)[moodStr] || 'var(--text-muted)';
    const stepNum = p.step;
    const html = `<div class="trail-popup-inner">
      <div class="trail-popup-header">
        <span class="step-badge">#${stepNum}</span>
        <span class="mood-dot" style="background:${moodColor}"></span>
        <span class="mood-label" style="color:${moodColor}">${escapeHtml(moodStr)}</span>
        <span class="topic-chip">${escapeHtml(String(p.topic || ''))}</span>
      </div>
      <div class="trail-popup-desc">${escapeHtml(String(p.description || '—'))}</div>
    </div>`;
    trailPopup = new mapboxgl.Popup({ className: 'trail-popup', closeButton: true, maxWidth: '300px' })
      .setLngLat([e.lngLat.lng, e.lngLat.lat])
      .setHTML(html)
      .addTo(map as unknown as Parameters<MapboxPopup['addTo']>[0]) as MapboxPopup;
  });
  map?.on('mouseenter', 'trail-dots-pt', () => { if (map) map.getCanvas().style.cursor = 'pointer'; });
  map?.on('mouseleave', 'trail-dots-pt', () => { if (map) map.getCanvas().style.cursor = ''; });
  map?.on('click', onPanel4MapClick);
  $('#p4-play')!.addEventListener('click', startSinglePlay);
  $('#p4-pause')!.addEventListener('click', pauseSinglePlay);
  $('#p4-step')!.addEventListener('click', () => stepSingle());
  $('#p4-results')!.addEventListener('click', openSingleResults);
  $('#p4-results-close')!.addEventListener('click',
    () => $('#p4-results-modal')!.classList.add('hidden'));

  const engine = buildLLMEngine('panel-4', state.singleAgent, api.lab);
  engine.render();
  engine.bindApply('#p4-llm-model', '#p4-llm-key', '#p4-llm-apply', '#p4-llm-current');
  ($('#p4-llm-current') as HTMLElement).textContent = state.singleAgent.llm.providerId;
}

async function onPanel4MapClick(e: MapboxMapEvent): Promise<void> {
  if (state.currentPanel !== 4 || !pickMode || !map) return;
  const { lng, lat } = e.lngLat;
  if (pickMode === 'start') {
    state.singleAgent.start = { lon: lng, lat };
    if (startMarker) startMarker.remove();
    startMarker = new mapboxgl.Marker({ color: '#30d158' })
      .setLngLat([lng, lat])
      .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
    ($('#p4-pick-target') as HTMLButtonElement).disabled = false;
    ($('#p4-config-status') as HTMLElement).textContent =
      'Start set. Click <b>Pick Target</b> next.';
    try {
      const data = await api.l(`/api/reachable-area?lon=${lng}&lat=${lat}&max_nodes=600`);
      map.getSource('reachable')?.setData(data);
    } catch { /* okay */ }
  } else if (pickMode === 'target') {
    state.singleAgent.target = { lon: lng, lat };
    if (targetMarker) targetMarker.remove();
    targetMarker = new mapboxgl.Marker({ color: '#0a84ff' })
      .setLngLat([lng, lat])
      .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
    await configureSingleAgent();
  }
  pickMode = null;
  map.getCanvas().classList.remove('map-pick');
  saveState();
}

async function configureSingleAgent(): Promise<void> {
  const sa = state.singleAgent;
  if (!sa.start || !sa.target) return;
  try {
    const res = await api.postJSON<{
      error?: string; agent_id?: number;
    }>(api.lab, '/api/single-agent/configure', {
      start_lon: sa.start.lon, start_lat: sa.start.lat,
      target_lon: sa.target.lon, target_lat: sa.target.lat,
      archetype: sa.archetype,
    });
    if (res.error) {
      toast(res.error, 'danger');
      ($('#p4-config-status') as HTMLElement).textContent = res.error; return;
    }
    sa.id = res.agent_id ?? null; saveState();
    ($('#p4-config-status') as HTMLElement).textContent =
      `Agent #${res.agent_id} (${sa.archetype}) configured. Press Play.`;
    ($('#p4-play') as HTMLButtonElement).disabled = false;
    sa.positionHistory = []; sa.moodHistory = []; stepLog = [];
    if (trailPopup) { trailPopup.remove(); trailPopup = null; }
    map?.getSource('trail')?.setData({ type: 'FeatureCollection', features: [] });
    map?.getSource('trail-dots')?.setData({ type: 'FeatureCollection', features: [] });
    try {
      const planned = await api.l<{ geometry?: unknown }>(`/api/agent/${res.agent_id}/planned-path`);
      if (planned?.geometry) {
        map?.getSource('planned')?.setData({
          type: 'FeatureCollection',
          features: [{ type: 'Feature', geometry: planned.geometry, properties: {} }],
        });
      }
    } catch { /* nothing */ }
    await refreshSingleAgent();
  } catch (e) {
    toast(`Configure failed: ${e instanceof Error ? e.message : e}`, 'danger');
  }
}

async function stepSingle(): Promise<void> {
  if (state.singleAgent.id === null) return;
  try {
    await api.postJSON(api.lab, '/api/step_continuous', {});
    await refreshSingleAgent();
  } catch (e) { console.warn('step error', e); }
}

async function refreshSingleAgent(): Promise<void> {
  const sa = state.singleAgent;
  if (sa.id === null) return;
  try {
    const [cog, info, stream] = await Promise.all([
      api.l<{ cognition_state?: { mood?: string; curiosity?: number; fatigue?: number }; needs?: Record<string, number> }>(
        `/api/agent/${sa.id}/cognition`).catch(() => ({})),
      api.l<{ location?: { lon: number; lat: number } }>(`/api/agent/${sa.id}`).catch(() => ({})),
      api.l<{ events?: { step: number; topic: string; description: string; metadata?: Record<string, unknown> }[] }>(
        `/api/agent/${sa.id}/stream?n=10000`).catch(() => ({ events: [] })),
    ]);

    renderSingleCognition(cog as Parameters<typeof renderSingleCognition>[0]);
    renderThoughts((stream as { events?: { step: number; topic: string; description: string; metadata?: Record<string, unknown> }[] }).events || []);

    const loc = (info as { location?: { lon: number; lat: number } }).location;
    if (loc) {
      const pos: [number, number] = [loc.lon, loc.lat];
      sa.positionHistory.push(pos);

      const events = (stream as { events?: { step: number; topic: string; description: string }[] }).events || [];
      const latest = events.reduce(
        (best, ev) => ev.step > (best?.step ?? -1) ? ev : best,
        null as typeof events[0] | null,
      );
      stepLog.push({
        pos, step: latest?.step ?? sa.positionHistory.length - 1,
        topic: latest?.topic ?? 'step',
        description: latest?.description ?? '',
        mood: (cog as { cognition_state?: { mood?: string } }).cognition_state?.mood || 'neutral',
      });

      const trail = sa.positionHistory.length > 1 ? {
        type: 'FeatureCollection',
        features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: sa.positionHistory }, properties: {} }],
      } : { type: 'FeatureCollection', features: [] };
      map?.getSource('trail')?.setData(trail);
      map?.getSource('trail-dots')?.setData({
        type: 'FeatureCollection',
        features: stepLog.map((e) => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: e.pos },
          properties: { step: e.step, topic: e.topic, description: e.description, mood: e.mood },
        })),
      });
    }

    const [perc, stats] = await Promise.all([
      api.l<{ image_url?: string; perception?: Record<string, string> }>(
        `/api/agent/${sa.id}/perception-text`).catch(() => ({})),
      api.l<{ requests?: number }>('/api/llm/stats').catch(() => ({})),
    ]);
    renderSinglePerception(perc as Parameters<typeof renderSinglePerception>[0]);
    ($('#p4-llm-stats') as HTMLElement).textContent = `${(stats as { requests?: number }).requests || 0} calls`;
    updateDetailOverlay();
    saveState();
  } catch (e) { console.warn('refreshSingleAgent', e); }
}

function updateDetailOverlay(sourceCard?: HTMLElement): void {
  if (!expandedDetailId) return;
  const overlay = $('#p4-detail-overlay') as HTMLElement;
  if (!overlay) return;
  const body = $('#p4-detail-body') as HTMLElement;
  if (!body) return;
  const card = sourceCard || document.querySelector<HTMLElement>(`#panel-4 [data-detail-id="${expandedDetailId}"]`);
  if (!card) return;
  body.innerHTML = '';
  Array.from(card.children).forEach((child) => {
    if (child.tagName !== 'H4') body.appendChild(child.cloneNode(true));
  });
  // Re-attach live SVG for emotion pie
  if (expandedDetailId === 'emotion') {
    const svgSrc = card.querySelector<SVGElement>('#p4-emotion-svg');
    const legSrc = card.querySelector<HTMLElement>('#p4-emotion-legend');
    const svgDest = body.querySelector<SVGElement>('svg');
    const legDest = body.querySelector<HTMLElement>('.emotion-legend');
    if (svgSrc && svgDest) svgDest.innerHTML = svgSrc.innerHTML;
    if (legSrc && legDest) legDest.innerHTML = legSrc.innerHTML;
  }
}

function renderSingleCognition(cog: { cognition_state?: { mood?: string; curiosity?: number; fatigue?: number }; needs?: Record<string, number> }): void {
  const cs = cog?.cognition_state || {};
  const needs = cog?.needs || {};
  ($('#p4-mood') as HTMLElement).textContent = cs.mood || '—';
  ($('#p4-curiosity') as HTMLElement).textContent = fmtPct(cs.curiosity ?? null);
  ($('#p4-fatigue') as HTMLElement).textContent = fmtPct(cs.fatigue ?? null);

  const host = $('#p4-needs') as HTMLElement;
  host.innerHTML = '';
  (['hunger', 'energy', 'social', 'comfort'] as const).forEach((k) => {
    const v = +(needs[k] || 0);
    const row = document.createElement('div');
    row.className = 'need-row';
    row.setAttribute('data-key', k);
    row.innerHTML = `
      <span style="text-transform:capitalize">${k}</span>
      <div class="bar"><div class="fill" style="width: ${Math.round(v * 100)}%;"></div></div>
      <span class="val">${v.toFixed(2)}</span>`;
    host.appendChild(row);
  });

  state.singleAgent.moodHistory.push(cs.mood || 'neutral');
  renderEmotionPie('#p4-emotion-svg', '#p4-emotion-legend', state.singleAgent.moodHistory);
}

function renderEmotionPie(svgSel: string, legSel: string, hist: string[]): void {
  const counts: Record<string, number> = {};
  hist.forEach((m) => { counts[m] = (counts[m] || 0) + 1; });
  const total = hist.length || 1;
  const svg = $(svgSel) as unknown as SVGSVGElement;
  svg.innerHTML = '';
  let acc = 0;
  Object.entries(counts).forEach(([m, c]) => {
    const a0 = (acc / total) * Math.PI * 2 - Math.PI / 2;
    const a1 = ((acc + c) / total) * Math.PI * 2 - Math.PI / 2;
    const large = (c / total) > 0.5 ? 1 : 0;
    const x0 = Math.cos(a0), y0 = Math.sin(a0);
    const x1 = Math.cos(a1), y1 = Math.sin(a1);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M 0 0 L ${x0} ${y0} A 1 1 0 ${large} 1 ${x1} ${y1} Z`);
    path.setAttribute('fill', moodColors[m] || '#5e5ce6');
    svg.appendChild(path);
    acc += c;
  });
  ($(legSel) as HTMLElement).innerHTML = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([m, c]) =>
      `<div class="leg"><span class="sw" style="background:${moodColors[m] || '#5e5ce6'}"></span>${escapeHtml(m)} <span class="meta">${Math.round(c / total * 100)}%</span></div>`)
    .join('');
}

function renderThoughts(events: { step: number; topic: string; description: string; metadata?: Record<string, unknown> }[]): void {
  const host = $('#p4-thoughts') as HTMLElement;
  host.innerHTML = '';
  if (!events.length) {
    host.innerHTML = '<div class="meta">No thoughts yet — press Play.</div>';
    return;
  }
  events.slice().reverse().forEach((ev) => {
    const div = document.createElement('div');
    div.className = 'thought';
    div.setAttribute('data-topic', ev.topic || '');
    const m = ev.metadata || {};
    const badges: string[] = [];
    if (m.fallback) badges.push('<span class="chip danger" style="font-size:10px;">fallback</span>');
    if (m.on_path === false) badges.push('<span class="chip warning" style="font-size:10px;">off-path</span>');
    if (m.on_path === true)  badges.push('<span class="chip success" style="font-size:10px;">on-path</span>');
    if (m.perception_available) badges.push('<span class="chip accent" style="font-size:10px;">perc</span>');
    div.innerHTML = `
      <div class="row1">
        <span class="step">#${ev.step}</span>
        <span class="topic">${escapeHtml(ev.topic)}</span>
        <div class="badges">${badges.join('')}</div>
      </div>
      <div class="desc">${escapeHtml(ev.description)}</div>`;
    host.appendChild(div);
  });
}

function renderSinglePerception(perc: { image_url?: string; perception?: Record<string, unknown> }): void {
  const host = $('#p4-perception') as HTMLElement;
  if (!perc?.perception) { host.innerHTML = '<div class="meta">No perception data yet.</div>'; return; }
  const p = perc.perception;
  const priorityFields: Array<{ key: string; label: string }> = [
    { key: 'scene',       label: 'Scene' },
    { key: 'crowdedness', label: 'Crowdedness' },
    { key: 'greenery',    label: 'Greenery' },
    { key: 'lighting',    label: 'Lighting' },
  ];
  const html = priorityFields.map(({ key, label }) => {
    const raw = p[key];
    if (!raw) return '';
    let display = String(raw).trim();
    if (display.startsWith('[')) {
      try {
        const arr = JSON.parse(display) as Record<string, unknown>[];
        display = arr.map((obj) => {
          const zone = obj['zone'] ? `[${obj['zone']}] ` : '';
          const parts = Object.entries(obj).filter(([k]) => k !== 'zone').map(([, v]) => String(v));
          return zone + parts.join(' · ');
        }).join(', ');
      } catch { /* leave as-is */ }
    }
    if (display.length < 2) return '';
    return `<div style="margin-bottom: 6px;">
      <span class="meta" style="font-size:10px;text-transform:uppercase;letter-spacing:0.08em">${escapeHtml(label)}</span><br/>
      <span style="font-size:12px;">${escapeHtml(display)}</span></div>`;
  }).join('');
  const imgHtml = perc.image_url
    ? `<img src="${api.map}${escapeHtml(String(perc.image_url))}" style="width:100%; border-radius:8px; margin-bottom:8px;" alt="agent view">`
    : '';
  host.innerHTML = imgHtml + (html || '<div class="meta">Perception unavailable here.</div>');
}

function startSinglePlay(): void {
  if (state.singleAgent.id === null) { toast('Configure the agent first.', 'warning'); return; }
  state.singleAgent.playing = true;
  ($('#p4-play') as HTMLButtonElement).disabled = true;
  ($('#p4-pause') as HTMLButtonElement).disabled = false;
  ($('#p4-step') as HTMLButtonElement).disabled = true;
  ($('#p4-results') as HTMLButtonElement).disabled = true;
  if (p4StepTimer !== null) clearInterval(p4StepTimer);
  p4StepTimer = window.setInterval(stepSingle, 1100);
}
function pauseSinglePlay(): void {
  state.singleAgent.playing = false;
  ($('#p4-play') as HTMLButtonElement).disabled = false;
  ($('#p4-pause') as HTMLButtonElement).disabled = true;
  ($('#p4-step') as HTMLButtonElement).disabled = false;
  ($('#p4-results') as HTMLButtonElement).disabled = false;
  if (p4StepTimer !== null) { clearInterval(p4StepTimer); p4StepTimer = null; }
}
async function resetSingleAgent(): Promise<void> {
  try { await api.postJSON(api.lab, '/api/single-agent/reset', {}); } catch { /* ok */ }
  pauseSinglePlay();
  state.singleAgent.id = null;
  state.singleAgent.positionHistory = [];
  state.singleAgent.moodHistory = []; stepLog = [];
  state.singleAgent.start = null; state.singleAgent.target = null;
  if (startMarker)  { startMarker.remove();  startMarker = null; }
  if (targetMarker) { targetMarker.remove(); targetMarker = null; }
  if (trailPopup) { trailPopup.remove(); trailPopup = null; }
  map?.getSource('trail')?.setData({ type: 'FeatureCollection', features: [] });
  map?.getSource('trail-dots')?.setData({ type: 'FeatureCollection', features: [] });
  map?.getSource('planned')?.setData({ type: 'FeatureCollection', features: [] });
  map?.getSource('reachable')?.setData({ type: 'FeatureCollection', features: [] });
  ($('#p4-config-status') as HTMLElement).textContent = 'Reset. Click Pick Start to begin again.';
  saveState();
}

async function openSingleResults(): Promise<void> {
  const sa = state.singleAgent;
  if (sa.id === null) return;
  $('#p4-results-modal')!.classList.remove('hidden');
  const body = $('#p4-results-body') as HTMLElement;
  body.innerHTML = '<div class="shimmer" style="height: 80px;"></div>';
  try {
    const [adh, narr] = await Promise.all([
      api.l<{ adherence?: { pct_followed?: number; steps_followed?: number; total_steps?: number } }>(
        `/api/agent/${sa.id}/path-adherence`).catch(() => null),
      api.l<{ generic?: string; history_aware?: string }>(
        `/api/agent/${sa.id}/narrative-compare`).catch(() => null),
    ]);
    const stats = adh?.adherence || {};
    body.innerHTML = `
      <div class="cognition-grid">
        <div class="cell"><div class="v">${fmtPct((stats.pct_followed ?? 0) / 100)}</div><div class="l">Path adherence</div></div>
        <div class="cell"><div class="v">${stats.steps_followed ?? '—'}/${stats.total_steps ?? '—'}</div><div class="l">Steps on path</div></div>
        <div class="cell"><div class="v">${sa.moodHistory.length}</div><div class="l">Mood samples</div></div>
      </div>
      <div class="metric-card"><h4>Emotion distribution</h4>
        <div class="emotion-pie">
          <svg viewBox="-1 -1 2 2" id="p4-results-pie"></svg>
          <div class="emotion-legend" id="p4-results-leg"></div>
        </div>
      </div>
      <div class="metric-card"><h4>Generic narrative (no spatial memory)</h4>
        <div style="font-size:13px; line-height:1.5;">${escapeHtml(narr?.generic || '—')}</div></div>
      <div class="metric-card"><h4>History-aware narrative</h4>
        <div style="font-size:13px; line-height:1.5;">${escapeHtml(narr?.history_aware || '—')}</div></div>`;
    renderEmotionPie('#p4-results-pie', '#p4-results-leg', sa.moodHistory);
  } catch (e) {
    body.innerHTML = `<div class="meta">Could not load results: ${escapeHtml(e instanceof Error ? e.message : e)}</div>`;
  }
}

/* =====================================================================
   PANEL 5 — Multi Agent
   ===================================================================== */
const archetypeColors: Record<string, string> = {
  resident: '#30d158', commuter: '#0a84ff',
  tourist: '#ff9f0a',  student: '#ff375f',
};
let p5Bound = false;
let p5StepTimer: number | null = null;
let recordPollTimer: number | null = null;

function panel5Enter(): void {
  bindPanel5();
  refreshSpawnPins();
  refreshAgentList();
}

function bindPanel5(): void {
  if (p5Bound) return; p5Bound = true;
  $('#p5-count')!.addEventListener('input', (e) => {
    state.multiAgent.count = +(e.target as HTMLInputElement).value; saveState();
    ($('#p5-count-label') as HTMLElement).textContent = (e.target as HTMLInputElement).value;
  });
  $$('#p5-spawn-mode button').forEach((b) => b.addEventListener('click', () => {
    state.multiAgent.spawnMode = b.getAttribute('data-v') as MultiAgentState['spawnMode'];
    saveState();
    $$('#p5-spawn-mode button').forEach((x) =>
      x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
    updateSpawnHint();
  }));
  updateSpawnHint();
  map?.on('click', onPanel5MapClick);
  (['resident', 'commuter', 'tourist', 'student'] as const).forEach((k) => {
    const inp = $(`#p5-mix-${k}`) as HTMLInputElement;
    const lbl = $(`#p5-mix-${k}-v`) as HTMLElement;
    inp.addEventListener('input', (e) => {
      const v = +(e.target as HTMLInputElement).value;
      state.multiAgent.archetypeMix[k] = v;
      lbl.textContent = `${Math.round(v * 100)}%`;
      saveState();
    });
  });
  $('#p5-spawn')!.addEventListener('click', doMultiSpawn);
  $('#p5-clear-pins')!.addEventListener('click', () => {
    state.multiAgent.spawnPoints = [];
    state.multiAgent.homePoints = [];
    state.multiAgent.workPoints = [];
    saveState(); refreshSpawnPins();
  });
  $('#p5-play')!.addEventListener('click', startMultiPlay);
  $('#p5-pause')!.addEventListener('click', pauseMultiPlay);
  $('#p5-step')!.addEventListener('click', stepMulti);
  $('#p5-speed')!.addEventListener('input', (e) => {
    state.multiAgent.speed = +(e.target as HTMLInputElement).value; saveState();
    ($('#p5-speed-label') as HTMLElement).textContent =
      `${(state.multiAgent.speed).toFixed(2)}×`;
    if (p5StepTimer !== null) { pauseMultiPlay(); startMultiPlay(); }
  });
  $('#p5-rec-start')!.addEventListener('click', startRecording);
  $('#p5-rec-stop')!.addEventListener('click', stopRecording);
  const engine = buildLLMEngine('panel-5', state.multiAgent);
  engine.render();
  engine.bindApply('#p5-llm-model', '#p5-llm-key', '#p5-llm-apply', '#p5-llm-current');
  ($('#p5-llm-current') as HTMLElement).textContent = state.multiAgent.llm.providerId;
}

function updateSpawnHint(): void {
  const mode = state.multiAgent.spawnMode;
  const hints: Record<MultiAgentState['spawnMode'], string> = {
    random:    'Random respawn on the walk network.',
    click:     'Click anywhere on the map to drop spawn pins.',
    poi:       'Spawn agents at random amenities (mix-weighted).',
    home_work: 'Click to drop HOME pins (resident). Press Shift+Click for WORK (commuter).',
  };
  ($('#p5-spawn-hint') as HTMLElement).textContent = hints[mode];
  ($('#p5-mix-panel') as HTMLElement).style.display = (mode === 'poi') ? '' : 'none';
}

function onPanel5MapClick(e: MapboxMapEvent): void {
  if (state.currentPanel !== 5) return;
  const mode = state.multiAgent.spawnMode;
  if (mode === 'click') {
    state.multiAgent.spawnPoints.push({ lon: e.lngLat.lng, lat: e.lngLat.lat });
    refreshSpawnPins(); saveState();
  } else if (mode === 'home_work') {
    const pt = { lon: e.lngLat.lng, lat: e.lngLat.lat };
    if (e.originalEvent?.shiftKey) state.multiAgent.workPoints.push(pt);
    else state.multiAgent.homePoints.push(pt);
    refreshSpawnPins(); saveState();
  }
}

function refreshSpawnPins(): void {
  const features: unknown[] = [];
  state.multiAgent.spawnPoints.forEach((p) => features.push({
    type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: { kind: 'spawn' },
  }));
  state.multiAgent.homePoints.forEach((p) => features.push({
    type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: { kind: 'home' },
  }));
  state.multiAgent.workPoints.forEach((p) => features.push({
    type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: { kind: 'work' },
  }));
  map?.getSource('spawn-pins')?.setData({ type: 'FeatureCollection', features });
}

async function doMultiSpawn(): Promise<void> {
  const m = state.multiAgent;
  ($('#p5-spawn-status') as HTMLElement).textContent = 'Spawning…';
  try {
    let res: { error?: string; count?: number; skipped?: number };
    if (m.spawnMode === 'random') {
      res = await api.postJSON(api.map, '/api/agents/respawn_advanced', {
        spawn_mode: 'random', count: m.count,
      });
    } else if (m.spawnMode === 'poi') {
      const amenities = await api.m<{ features: { geometry: { coordinates: [number, number] } }[] }>(
        '/api/amenities');
      const pool = amenities.features.map((f) => f.geometry.coordinates);
      const points: { lon: number; lat: number }[] = [];
      for (let i = 0; i < m.count && pool.length; i++) {
        const idx = Math.floor(Math.random() * pool.length);
        const [lon, lat] = pool.splice(idx, 1)[0];
        points.push({ lon, lat });
      }
      res = await api.postJSON(api.map, '/api/agents/respawn_advanced', {
        spawn_mode: 'poi', count: m.count, points,
        archetype_mix: m.archetypeMix,
      });
    } else if (m.spawnMode === 'click') {
      if (!m.spawnPoints.length) {
        toast('Drop at least one pin first.', 'warning');
        ($('#p5-spawn-status') as HTMLElement).textContent = 'No pins.';
        return;
      }
      const archetypes = ['resident', 'commuter', 'tourist', 'student'];
      const points = m.spawnPoints.map((p, i) => ({ ...p, archetype: archetypes[i % 4] }));
      res = await api.postJSON(api.map, '/api/agents/respawn_advanced', {
        spawn_mode: 'click', count: points.length, points,
      });
    } else {
      if (!m.homePoints.length && !m.workPoints.length) {
        toast('Drop at least one home or work pin.', 'warning'); return;
      }
      res = await api.postJSON(api.map, '/api/agents/respawn_advanced', {
        spawn_mode: 'home_work',
        count: m.homePoints.length + m.workPoints.length,
        home_points: m.homePoints, work_points: m.workPoints,
      });
    }
    if (res?.error) {
      toast(res.error, 'danger');
      ($('#p5-spawn-status') as HTMLElement).textContent = res.error; return;
    }
    ($('#p5-spawn-status') as HTMLElement).textContent =
      `Spawned ${res.count} (skipped ${res.skipped ?? 0}).`;
    toast(`Spawned ${res.count} agents.`, 'success');
    await refreshAgentList();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    toast(`Spawn failed: ${msg}`, 'danger');
    ($('#p5-spawn-status') as HTMLElement).textContent = msg;
  }
}

async function refreshAgentList(): Promise<void> {
  try {
    const data = await api.m<{ features: { properties: { id: number; nearby_count: number; archetype: string } }[] }>(
      '/api/agents');
    map?.getSource('agents')?.setData(data);
    ($('#p5-agent-count') as HTMLElement).textContent = String(data.features.length);
    const grid = $('#p5-agent-grid') as HTMLElement;
    grid.innerHTML = '';
    data.features.slice(0, 30).forEach((f) => {
      const p = f.properties;
      const div = document.createElement('div');
      div.className = 'agent-mini';
      div.innerHTML = `
        <div class="row between"><span class="id">#${p.id}</span><span class="meta" style="font-size:10px;">${p.nearby_count} near</span></div>
        <div class="arch" style="color:${archetypeColors[p.archetype] || 'var(--text-muted)'}">${escapeHtml(p.archetype || 'unknown')}</div>`;
      grid.appendChild(div);
    });
    const stats = await api.m<{ requests?: number }>('/api/llm/stats').catch(() => ({ requests: 0 }));
    ($('#p5-llm-stats') as HTMLElement).textContent = `${stats.requests || 0} calls`;
  } catch { /* okay */ }
}

async function stepMulti(): Promise<void> {
  try {
    await api.postJSON(api.map, '/api/step_continuous', {});
    await refreshAgentList();
  } catch (e) { console.warn(e); }
}
function startMultiPlay(): void {
  state.multiAgent.playing = true;
  ($('#p5-play') as HTMLButtonElement).disabled = true;
  ($('#p5-pause') as HTMLButtonElement).disabled = false;
  if (p5StepTimer !== null) clearInterval(p5StepTimer);
  const interval = Math.max(120, 1100 / (state.multiAgent.speed || 1));
  p5StepTimer = window.setInterval(stepMulti, interval);
}
function pauseMultiPlay(): void {
  state.multiAgent.playing = false;
  ($('#p5-play') as HTMLButtonElement).disabled = false;
  ($('#p5-pause') as HTMLButtonElement).disabled = true;
  if (p5StepTimer !== null) { clearInterval(p5StepTimer); p5StepTimer = null; }
}

async function startRecording(): Promise<void> {
  const name = ($('#p5-rec-name') as HTMLInputElement).value || '';
  try {
    const q = new URLSearchParams({ include_thoughts: 'true', include_perception: 'true' });
    if (name) q.set('session_name', name);
    const res = await api.postJSON<{ session_id: string; session_name?: string }>(
      api.map, `/api/recording/start?${q.toString()}`, {});
    state.recordingSession = res.session_id; saveState();
    ($('#p5-rec-start') as HTMLButtonElement).disabled = true;
    ($('#p5-rec-stop') as HTMLButtonElement).disabled = false;
    ($('#p5-rec-status') as HTMLElement).textContent =
      `Recording · ${res.session_name || res.session_id}`;
    if (recordPollTimer !== null) clearInterval(recordPollTimer);
    recordPollTimer = window.setInterval(async () => {
      const s = await api.m<{ steps_recorded?: number; total_records?: number }>(
        '/api/recording/status').catch(() => null);
      if (s) ($('#p5-rec-status') as HTMLElement).textContent =
        `Recording · ${s.steps_recorded || 0} steps · ${s.total_records || 0} records`;
    }, 2000);
  } catch (e) { toast(`Recording start failed: ${e instanceof Error ? e.message : e}`, 'danger'); }
}
async function stopRecording(): Promise<void> {
  try {
    const res = await api.postJSON<{ file_name?: string; total_records?: number; message?: string }>(
      api.map, '/api/recording/stop', {});
    if (recordPollTimer !== null) { clearInterval(recordPollTimer); recordPollTimer = null; }
    ($('#p5-rec-start') as HTMLButtonElement).disabled = false;
    ($('#p5-rec-stop') as HTMLButtonElement).disabled = true;
    const status = $('#p5-rec-status') as HTMLElement;
    if (res.file_name) {
      status.textContent = `Saved ${res.file_name} (${res.total_records} records)`;
      const a = document.createElement('a');
      a.href = `${api.map}/api/recording/download/${encodeURIComponent(res.file_name)}`;
      a.target = '_blank'; a.textContent = 'download';
      a.style.cssText = 'color:var(--accent-3); margin-left:8px;';
      status.appendChild(a);
    } else {
      status.textContent = res.message || 'Stopped.';
    }
  } catch (e) { toast(`Recording stop failed: ${e instanceof Error ? e.message : e}`, 'danger'); }
}

/* =====================================================================
   SETTINGS DRAWER + GLOBAL CONTROLS
   ===================================================================== */
function bindGlobalControls(): void {
  $('#btn-settings')!.addEventListener('click', () => {
    const dr = $('#drawer') as HTMLElement;
    dr.setAttribute('data-open', dr.getAttribute('data-open') !== 'true' ? 'true' : 'false');
  });
  $('#drawer-close')!.addEventListener('click',
    () => $('#drawer')!.setAttribute('data-open', 'false'));

  $$('#s-theme button').forEach((b) => b.addEventListener('click', () => {
    $$('#s-theme button').forEach((x) =>
      x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
    setMapStyle(b.getAttribute('data-v') as 'dark' | 'light');
  }));
  ($('#s-map-url') as HTMLInputElement).value = state.mapServerUrl;
  ($('#s-lab-url') as HTMLInputElement).value = state.labServerUrl;
  $('#s-map-url')!.addEventListener('change',
    (e) => { state.mapServerUrl = (e.target as HTMLInputElement).value; saveState(); });
  $('#s-lab-url')!.addEventListener('change',
    (e) => { state.labServerUrl = (e.target as HTMLInputElement).value; saveState(); });

  $$('#s-perception button').forEach((b) => b.addEventListener('click', async () => {
    $$('#s-perception button').forEach((x) =>
      x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
    state.perceptionMode = b.getAttribute('data-v') as UABMState['perceptionMode']; saveState();
    try { await api.postJSON(api.map, '/api/config/perception-mode', { mode: state.perceptionMode }); } catch { /* ok */ }
    try { await api.postJSON(api.lab, '/api/config/perception-mode', { mode: state.perceptionMode }); } catch { /* ok */ }
  }));
  (['buildings', 'walk', 'amenities', 'streetview'] as const).forEach((k) => {
    const cb = $(`#s-layer-${k}`) as HTMLInputElement;
    cb.checked = !!state.layers[k];
    cb.addEventListener('change', () => {
      state.layers[k] = cb.checked; saveState();
      applyLayerVisibility();
    });
  });

  // Database import
  const dbFileInput = $('#s-db-file-input') as HTMLInputElement;
  $('#s-db-import-btn')!.addEventListener('click', () => dbFileInput.click());
  dbFileInput.addEventListener('change', async () => {
    const file = dbFileInput.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    toast(`Importing ${file.name}…`, '');
    try {
      const res = await api.m<{ status?: string; error?: string; message?: string }>(
        '/api/database/upload', { method: 'POST', body: form },
      );
      if (res.error) { toast(`Import error: ${res.error}`, 'danger'); return; }
      toast(res.message || 'Database loaded!', 'success');
      location.reload();
    } catch (e) {
      toast(`Import failed: ${e instanceof Error ? e.message : e}`, 'danger');
    }
    dbFileInput.value = '';
  });

  $('#s-restart')!.addEventListener('click', () => {
    if (!confirm('Reset all wizard state? This will clear localStorage.')) return;
    localStorage.removeItem(STATE_KEY);
    location.reload();
  });
  $('#btn-theme')!.addEventListener('click',
    () => setMapStyle(state.theme === 'light' ? 'dark' : 'light'));
  $('#btn-llm-stats')!.addEventListener('click', async () => {
    try {
      const s = await api.m<{ requests?: number; tokens_in?: number; tokens_out?: number }>(
        '/api/llm/stats');
      toast(`LLM: ${s.requests || 0} calls · ${s.tokens_in || 0}↓ / ${s.tokens_out || 0}↑ tokens`, '');
    } catch (e) {
      toast(`Stats unavailable: ${e instanceof Error ? e.message : e}`, 'warning');
    }
  });
  $$('.pill-nav .dot').forEach((d) => d.addEventListener('click', () => {
    const n = +d.getAttribute('data-panel')! as PanelId;
    if (state.panelStatus[n] !== 'locked') activatePanel(n);
  }));
  // Panel 2 removed — nav skips from 1→3 and 3→1
  const _NAV_ORDER: PanelId[] = [1, 3, 4, 5];
  $('#nav-prev')!.addEventListener('click', () => {
    const idx = _NAV_ORDER.indexOf(state.currentPanel as PanelId);
    if (idx > 0) activatePanel(_NAV_ORDER[idx - 1]);
  });
  $('#nav-next')!.addEventListener('click', () => {
    const idx = _NAV_ORDER.indexOf(state.currentPanel as PanelId);
    if (idx < _NAV_ORDER.length - 1) {
      markPanelDone(state.currentPanel as PanelId);
      activatePanel(_NAV_ORDER[idx + 1]);
    }
  });
  $('#p1-popover-expand')?.addEventListener('click',
    () => ($('#sv-detail-modal') as HTMLElement)?.classList.remove('hidden'));
  $('#sv-detail-close')?.addEventListener('click',
    () => ($('#sv-detail-modal') as HTMLElement)?.classList.add('hidden'));
  document.addEventListener('keydown', (e) => {
    const n = parseInt(e.key);
    if (n >= 1 && n <= 5 && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;
      if (state.panelStatus[n as PanelId] !== 'locked') activatePanel(n as PanelId);
    }
  });
}

/* =====================================================================
   GEOCODER — Mapbox Geocoding API search bar
   ===================================================================== */
function initGeocoder(): void {
  const inp = $('#search-input') as HTMLInputElement;
  const res = $('#search-results') as HTMLElement;
  if (!inp || !res) return;
  let timer: ReturnType<typeof setTimeout>;
  inp.addEventListener('input', () => {
    clearTimeout(timer);
    const q = inp.value.trim();
    if (q.length < 2) { res.classList.remove('open'); return; }
    timer = setTimeout(async () => {
      if (!state.mapboxToken) return;
      try {
        const data = await fetch(
          `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(q)}.json?access_token=${state.mapboxToken}&limit=5&language=en`
        ).then((r) => r.json()) as { features: { place_name: string; text: string; center: [number, number] }[] };
        res.innerHTML = data.features.map((f) =>
          `<div class="search-result-item" data-lon="${f.center[0]}" data-lat="${f.center[1]}">
            <div class="result-name">${escapeHtml(f.text)}</div>
            <div class="result-place">${escapeHtml(f.place_name)}</div>
          </div>`
        ).join('');
        res.classList.toggle('open', data.features.length > 0);
      } catch { /* ignore */ }
    }, 300);
  });
  res.addEventListener('click', (e) => {
    const item = (e.target as Element).closest('.search-result-item') as HTMLElement | null;
    if (!item) return;
    const lon = parseFloat(item.dataset['lon']!);
    const lat = parseFloat(item.dataset['lat']!);
    map?.flyTo({ center: [lon, lat], zoom: 14, duration: 1200 });
    inp.value = item.querySelector('.result-name')!.textContent || '';
    res.classList.remove('open');
  });
  document.addEventListener('click', (e) => {
    if (!(e.target as Element).closest('.search-bar')) res.classList.remove('open');
  });
}

/* =====================================================================
   EXTERNAL DATA SOURCES — plugin card (Panel 1)
   ===================================================================== */

interface ExtSourceStatus { loaded: boolean; row_count: number; last_updated: string | null }
interface ExtSource {
  name: string; display_name: string; description: string;
  is_live: boolean; required_env_vars: string[];
  status: ExtSourceStatus;
}

const _extJobs: Record<string, { timer?: ReturnType<typeof setInterval>; rowEl?: HTMLElement }> = {};

async function loadExternalSources(): Promise<void> {
  const listEl = document.getElementById('p1-ext-source-list');
  const loadingEl = document.getElementById('p1-ext-loading');
  if (!listEl) return;
  try {
    const data = await api.m<{ sources: ExtSource[]; error?: string }>('/api/external/sources');
    if (loadingEl) loadingEl.remove();
    (data.sources || []).forEach((src) => {
      listEl.appendChild(buildSourceRow(src));
    });
  } catch {
    if (loadingEl) loadingEl.textContent = 'Backend offline — start map_server.py first.';
  }
}

function buildSourceRow(src: ExtSource): HTMLElement {
  const row = document.createElement('div');
  row.id = `ext-row-${src.name}`;
  row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);';

  const info = document.createElement('div');
  info.style.flex = '1';
  info.innerHTML = `<div style="font-weight:600;font-size:13px;">${src.display_name}</div>
    <div class="meta" style="font-size:11px;opacity:0.6;margin-top:2px;">${src.description}</div>`;

  const badge = document.createElement('span');
  badge.id = `ext-badge-${src.name}`;
  badge.style.cssText = 'font-size:11px;font-weight:600;padding:3px 8px;border-radius:999px;white-space:nowrap;';
  _updateBadge(badge, src.status);

  const btn = document.createElement('button');
  btn.id = `ext-btn-${src.name}`;
  btn.className = 'btn' + (src.status.loaded ? '' : ' primary');
  btn.style.cssText = 'font-size:12px;padding:6px 12px;';
  btn.textContent = src.status.loaded ? 'Reload' : 'Load';

  const progressWrap = document.createElement('div');
  progressWrap.id = `ext-progress-${src.name}`;
  progressWrap.style.display = 'none';
  progressWrap.innerHTML = `
    <div class="ov-progress-bar" style="margin:6px 0 2px;"><div class="ov-progress-fill" id="ext-fill-${src.name}" style="width:0%;"></div></div>
    <div class="meta" id="ext-log-${src.name}" style="font-size:11px;opacity:0.6;"></div>`;

  btn.addEventListener('click', () => startExtDownload(src.name, btn, badge, progressWrap));

  row.append(info, badge, btn);

  const wrap = document.createElement('div');
  wrap.appendChild(row);
  wrap.appendChild(progressWrap);
  return wrap;
}

function _updateBadge(badge: HTMLElement, status: ExtSourceStatus): void {
  if (status.loaded) {
    badge.textContent = `● ${status.row_count.toLocaleString()} rows`;
    badge.style.background = 'rgba(52,199,89,0.15)';
    badge.style.color = '#34c759';
    badge.style.border = '1px solid rgba(52,199,89,0.3)';
  } else {
    badge.textContent = '○ not loaded';
    badge.style.background = 'rgba(255,255,255,0.06)';
    badge.style.color = 'rgba(255,255,255,0.4)';
    badge.style.border = '1px solid rgba(255,255,255,0.1)';
  }
}

async function startExtDownload(name: string, btn: HTMLButtonElement, badge: HTMLElement, progressWrap: HTMLElement): Promise<void> {
  const bbox = state.zone?.bbox;
  if (!bbox) { alert('Draw a zone on the map first (Panel 1).'); return; }

  btn.disabled = true;
  btn.textContent = 'Loading…';
  progressWrap.style.display = '';
  const fillEl = document.getElementById(`ext-fill-${name}`) as HTMLElement;
  const logEl = document.getElementById(`ext-log-${name}`) as HTMLElement;

  try {
    const res = await api.postJSON<{ job_id?: string; error?: string }>(
      api.map, `/api/external/${name}/download`, { bbox }
    );
    if (res.error || !res.job_id) throw new Error(res.error || 'No job_id');

    const jobId = res.job_id;
    const timer = setInterval(async () => {
      try {
        const s = await api.m<{ pct?: number; status?: string; log?: string[]; row_count?: number; error?: string }>(
          `/api/external/${name}/status/${jobId}`
        );
        const pct = s.pct ?? 0;
        fillEl.style.width = `${pct}%`;
        if (s.log?.length) logEl.textContent = s.log[s.log.length - 1];

        if (s.status === 'done') {
          clearInterval(timer);
          badge.dataset.count = String(s.row_count ?? 0);
          _updateBadge(badge, { loaded: true, row_count: s.row_count ?? 0, last_updated: null });
          btn.textContent = 'Reload';
          btn.className = 'btn';
          btn.disabled = false;
          logEl.textContent = '✓ Loaded';
        } else if (s.status === 'error') {
          clearInterval(timer);
          btn.textContent = 'Retry';
          btn.disabled = false;
          logEl.textContent = `Error: ${s.error}`;
          logEl.style.color = '#ff453a';
        }
      } catch { /* network blip — keep polling */ }
    }, 2000);
    _extJobs[name] = { timer };
  } catch (e) {
    btn.textContent = 'Load';
    btn.disabled = false;
    progressWrap.style.display = 'none';
    alert(`Failed to start download: ${e}`);
  }
}

/* =====================================================================
   BOOT
   ===================================================================== */
function boot(): void {
  window.UABM = { state, api, activatePanel, saveState };
  applyAppleSelects();
  bindGlobalControls();
  refreshNavDots();
  $$('#s-perception button').forEach((x) =>
    x.setAttribute('aria-selected', x.getAttribute('data-v') === state.perceptionMode ? 'true' : 'false'));
  $$('#s-theme button').forEach((x) =>
    x.setAttribute('aria-selected', x.getAttribute('data-v') === state.theme ? 'true' : 'false'));
  initMap();
  initGeocoder();
  loadExternalSources();
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
