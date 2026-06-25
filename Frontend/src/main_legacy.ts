// Urban ABM — Apple HIG 5-panel pedestrian simulation lab.
// Legacy entry — kept intact during TSX migration. New modules
// are imported from their dedicated files below.

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type {
  UABMState, PanelId, PanelStatus, PerceptionFieldSpec, PerceptionSubField, VLMCardSpec,
  ArchetypeMap, ArchetypeProfile, DailyPlanPhase, SingleAgentState,
  MultiAgentState, LLMSelection, MapboxMap, MapboxMapEvent, MapboxMarker,
  MapboxPopup, MapboxDraw, StreetViewFeatureProps,
} from './types';

// ── Extracted modules ──────────────────────────────────────────────────────
import { api, apiConfig } from './api/client';
import { fmtPct, escapeHtml } from './utils/format';
import { calcBearing } from './utils/geo';
import { getMoodColor } from './utils/moodColors';
import { makeAgentIconData } from './utils/agentIcon';
import {
  PERCEPTION_FIELDS, FIELD_DEFAULT_SUBFIELDS, VLM_CARDS,
} from './constants/perceptionSchema';
import {
  LOCAL_PROVIDERS, API_PROVIDERS, DEFAULT_MODELS,
  ABM_SCORES, ABM_DIMS, type ABMScore,
  PROVIDER_COLORS, CUSTOM_COLORS,
} from './constants/llmProviders';
import {
  ARCHETYPE_COLORS, ARCHETYPE_GLB, GENERIC_GLB,
  ARCHETYPE_DESCRIPTIONS, ARCHETYPE_NAV_DEFAULTS,
} from './constants/archetypes';

/* =====================================================================
   STATE — defaults + localStorage round-trip
   ===================================================================== */
const DEFAULT_STATE: UABMState = {
  currentPanel: 2,
  panelStatus: { 2: 'locked', 3: 'active', 4: 'locked', 5: 'locked' },
  mapMode: false,
  mapStyle: 'dark',
  theme: 'dark',
  mapboxToken: '',
  mapServerUrl: 'http://127.0.0.1:8000',
  labServerUrl: 'http://127.0.0.1:8100',
  perceptionMode: 'both',
  layers: { buildings: true, buildings3d: false, walk: true, amenities: false, streetview: true },
  zone: { bbox: null, spacing: 200 },
  selectedPoint: null,
  vlm: { provider: 'qwen3vl-8b', hfModel: '', enabledFields: null, customPrompt: {}, customFields: [], fieldStructures: {} },
  llm: { mode: 'local', providerId: 'ollama', model: '' },
  archetypes: null,
  selectedArchetype: null,
  singleAgent: {
    id: null, archetype: 'resident',
    start: null, target: null,
    navMode: 'both', navGpsDist: 120, navCompassDist: 60,
    moodHistory: [], positionHistory: [], playing: false, speed: 1.0,
    timeOverride: null,
  },
  multiAgent: {
    count: 15, spawnMode: 'random', pinMode: 'home',
    spawnPoints: [], homePoints: [], workPoints: [],
    archetypeMix: { resident: 0.25, commuter: 0.25, tourist: 0.25, student: 0.25 },
    playing: false, speed: 1.0,
  },
  recordingSession: null,
  recordingBase: null,
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
    const ps = merged.panelStatus as Record<string, PanelStatus>;
    delete ps['1']; delete ps['2'];
    return merged;
  } catch {
    return structuredClone(DEFAULT_STATE);
  }
}
export const state: UABMState = loadState();
export function saveState(): void {
  try { localStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch { /* quota */ }
}

// Keep extracted api client in sync with persisted URLs.
apiConfig.mapServerUrl = state.mapServerUrl;
apiConfig.labServerUrl = state.labServerUrl;

/* =====================================================================
   UI helpers
   ===================================================================== */
export const $ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document): T | null =>
  root.querySelector<T>(sel);
export const $$ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document): T[] =>
  Array.from(root.querySelectorAll<T>(sel));

export function toast(msg: string, type: 'success' | 'warning' | 'danger' | '' = ''): void {
  const host = $('#toasts'); if (!host) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(20px)'; }, 4000);
  setTimeout(() => el.remove(), 4500);
}

let archetypeNavMap: Record<string, string> = { ...ARCHETYPE_NAV_DEFAULTS };
let navArchConfigs: Record<string, { nav_mode: string; gps_dist: number; compass_dist: number }> = {
  resident: { nav_mode: 'both',             gps_dist: 120, compass_dist: 60 },
  commuter: { nav_mode: 'gps',              gps_dist: 120, compass_dist: 60 },
  tourist:  { nav_mode: 'direction_sense',  gps_dist: 120, compass_dist: 60 },
  student:  { nav_mode: 'both',             gps_dist: 120, compass_dist: 60 },
};

function applyNavThresholdVisibility(mode: string): void {
  const gpsRow = $('#p4-gps-row') as HTMLElement | null;
  const compassRow = $('#p4-compass-row') as HTMLElement | null;
  if (gpsRow)     gpsRow.style.display     = (mode === 'both' || mode === 'gps')              ? '' : 'none';
  if (compassRow) compassRow.style.display = (mode === 'both' || mode === 'direction_sense')  ? '' : 'none';
}

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

  // 3D buildings from Mapbox composite source
  try {
    const is3dLight = state.theme === 'light';
    map.addLayer({
      id: 'buildings-3d',
      source: 'composite',
      'source-layer': 'building',
      type: 'fill-extrusion',
      minzoom: 14,
      filter: ['==', 'extrude', 'true'],
      paint: {
        'fill-extrusion-color': is3dLight ? '#d4d4d8' : '#27272a',
        'fill-extrusion-height': ['get', 'height'],
        'fill-extrusion-base': ['get', 'min_height'],
        'fill-extrusion-opacity': 0.6,
      },
    });
  } catch (e) { console.warn('3D buildings layer failed', e); }

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

  // Load archetype icons into Mapbox image atlas
  const archetypeIconDefs: Array<[string, string]> = [
    ['resident', '#30d158'], ['commuter', '#0a84ff'],
    ['tourist', '#ff9f0a'],  ['student', '#ff375f'],
  ];
  for (const [arch, color] of archetypeIconDefs) {
    const imgData = makeAgentIconData(arch, color);
    (map as unknown as { addImage(id: string, d: { width: number; height: number; data: Uint8ClampedArray }): void })
      .addImage(`icon-${arch}`, imgData);
  }

  map.addSource('agents', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });

  // Subtle glow ring (keep as circle so it blends smoothly)
  map.addLayer({
    id: 'agents-glow', type: 'circle', source: 'agents',
    paint: {
      'circle-radius': 14,
      'circle-color': ['match', ['get', 'archetype'],
        'resident', '#30d158', 'commuter', '#0a84ff',
        'tourist', '#ff9f0a', 'student', '#ff375f', '#ffffff'],
      'circle-opacity': 0.18, 'circle-blur': 0.7,
    },
  });

  // Archetype icon — nav-pin shape (arrow + circle) rotates with bearing as one object
  map.addLayer({
    id: 'agents-pt', type: 'symbol', source: 'agents',
    layout: {
      'icon-image': ['match', ['get', 'archetype'],
        'resident', 'icon-resident', 'commuter', 'icon-commuter',
        'tourist', 'icon-tourist', 'student', 'icon-student', 'icon-resident'],
      'icon-size': 0.72,
      'icon-rotate': ['get', 'bearing'],
      'icon-rotation-alignment': 'map',
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
    } as Record<string, unknown>,
  });

  // Click agent icon on map → open detail panel (panel 5 only)
  map.on('click', 'agents-pt', (e) => {
    if (state.currentPanel !== 5) return;
    const feat = (e as MapboxMapEvent & { features?: { properties: Record<string, unknown> }[] }).features?.[0];
    if (!feat) return;
    const id = Number(feat.properties['id']);
    if (!isNaN(id)) void selectAgentDetail(id);
  });
  map.on('mouseenter', 'agents-pt', () => { if (state.currentPanel === 5 && map) map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'agents-pt', () => { if (map) map.getCanvas().style.cursor = ''; });

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
    paint: { 'line-color': '#ffd4a0', 'line-width': 3.5, 'line-opacity': 0.6, 'line-dasharray': [2, 1.5] },
  });

  map.addSource('reachable', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'reachable-pt', type: 'circle', source: 'reachable',
    paint: { 'circle-radius': 2.4, 'circle-color': '#30d158', 'circle-opacity': 0.22 },
  });

  // Results mode overlays (hidden until toggled on)
  map.addSource('results-heatmap', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'results-heatmap-layer', type: 'heatmap', source: 'results-heatmap',
    layout: { visibility: 'none' },
    paint: {
      // Larger, zoom-aware radius so hotspots stay visible when zoomed in
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 12, 18, 18, 45],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 12, 0.8, 18, 2.5],
      'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
        0, 'rgba(0,0,0,0)',
        0.08, 'rgba(0,0,255,0.55)',
        0.2, 'rgba(0,200,255,0.75)',
        0.35, 'rgba(0,228,0,0.82)',
        0.5, 'rgba(255,255,0,0.88)',
        0.65, 'rgba(255,160,0,0.93)',
        0.8, 'rgba(255,60,0,0.97)',
        1, 'rgba(255,0,0,1)'],
      'heatmap-opacity': 0.9,
    },
  });

  map.addSource('results-decision-src', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'results-decision-src-layer', type: 'line', source: 'results-decision-src',
    layout: { visibility: 'none', 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-width': 4,
      'line-color': ['match', ['get', 'src'],
        'llm', '#30d158', 'perception', '#64d2ff', 'fallback', '#ff375f', '#aaa'],
      'line-opacity': 0.8,
    },
  });

  map.addSource('results-deviations', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'results-deviations-layer', type: 'line', source: 'results-deviations',
    layout: { visibility: 'none', 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-width': 3.5, 'line-color': '#ff375f', 'line-dasharray': [2, 1.5], 'line-opacity': 0.85 },
  });

  map.addSource('results-goal-changes', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'results-goal-changes-layer', type: 'circle', source: 'results-goal-changes',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': 10, 'circle-color': '#ff9f0a',
      'circle-stroke-color': '#fff', 'circle-stroke-width': 2, 'circle-opacity': 0.9,
    },
  });

  map.addSource('results-decision-pts', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'results-decision-pts-layer', type: 'circle', source: 'results-decision-pts',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': 7, 'circle-color': '#5e5ce6',
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.88,
    },
  });

  // P5 multi-agent trails (hidden until results mode)
  const archetypeColorExpr = ['match', ['get', 'archetype'],
    'resident', '#30d158', 'commuter', '#0a84ff',
    'tourist', '#ff9f0a', 'student', '#ff375f', '#aaa'] as unknown as string;
  map.addSource('p5-trails', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'p5-trails-layer', type: 'line', source: 'p5-trails',
    layout: { visibility: 'none', 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-width': 1.5, 'line-color': archetypeColorExpr, 'line-opacity': 0.6 },
  });
  map.addSource('p5-decision-pts', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'p5-decision-pts-layer', type: 'circle', source: 'p5-decision-pts',
    layout: { visibility: 'none' },
    paint: { 'circle-radius': 4, 'circle-color': '#5e5ce6', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1, 'circle-opacity': 0.8 },
  });

  // Recording playback trails + overlays
  map.addSource('recording-trails', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'recording-trails-layer', type: 'line', source: 'recording-trails',
    layout: { visibility: 'none', 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-width': 2, 'line-color': archetypeColorExpr, 'line-opacity': 0.7 },
  });
  map.addSource('recording-heatmap', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'recording-heatmap-layer', type: 'heatmap', source: 'recording-heatmap',
    layout: { visibility: 'none' },
    paint: {
      // Per-cell log-normalized dwell weight (set in buildRecordingOverlays), boosted 1.5x
      'heatmap-weight': ['*', ['get', 'w'], 1.5],
      // Larger, zoom-aware radius so hotspots stay visible when zoomed in
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 12, 8, 16, 18, 18, 30, 20, 45],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 12, 0.6, 18, 2.2],
      // Colors appear earlier and more opaque so clusters read as hotspots sooner
      'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
        0, 'rgba(0,0,0,0)',
        0.1, 'rgba(0,40,180,0.5)',
        0.25, 'rgba(0,160,255,0.7)',
        0.42, 'rgba(0,220,120,0.78)',
        0.6, 'rgba(220,235,0,0.85)',
        0.75, 'rgba(255,150,0,0.92)',
        0.88, 'rgba(255,60,0,0.97)',
        1, 'rgba(255,0,0,1)'],
      'heatmap-opacity': 0.85,
    },
  });
  map.addSource('recording-decision-pts', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'recording-decision-pts-layer', type: 'circle', source: 'recording-decision-pts',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': 4, 'circle-color': '#5e5ce6',
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1, 'circle-opacity': 0.85,
    },
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
    buildings:   ['buildings-fill', 'buildings-line'],
    buildings3d: ['buildings-3d'],
    walk:        ['walk-line'],
    amenities:   ['amenities-pt'],
    streetview:  ['sv-pt', 'sv-candidates-pt', 'sv-downloading-pt'],
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
  if (bboxDrawing || !e.features?.length || !map) return;
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
  const p = e.features[0].properties as Record<string, unknown>;
  const isCtrl = !!(e.originalEvent as MouseEvent)?.ctrlKey || !!(e.originalEvent as MouseEvent)?.metaKey;
  const key = `${p.lat}_${p.lon}`;
  const lat = p.lat as number; const lon = p.lon as number;
  const imgSrc = p.image_url ? api.map + (p.image_url as string) : '';

  if (isCtrl) {
    if (_analysePoints.has(key)) _analysePoints.delete(key);
    else _analysePoints.add(key);
  } else {
    _analysePoints.clear();
    _analysePoints.add(key);
    state.selectedPoint = { lat, lon, image_url: p.image_url as string | undefined };
    saveState();
  }

  _lastClickedProps = p;
  _updateAnalyseButton();
  _updateSelectionHighlight();

  if (state.mapMode) {
    // Map mode: show compact popover in left panel
    ($('#p1-sv-popover') as HTMLElement).style.display = '';
    ($('#p1-popover-title') as HTMLElement).textContent = `(${lat?.toFixed(5)}, ${lon?.toFixed(5)})`;
    ($('#p1-popover-coord') as HTMLElement).textContent = `${lat?.toFixed(5)}, ${lon?.toFixed(5)}`;
    ($('#p1-popover-heading') as HTMLElement).textContent = `Heading ${p.heading ?? 0}°`;
    if (imgSrc) ($('#p1-popover-img') as HTMLImageElement).src = imgSrc;
  } else {
    // Non-map mode: show Mapbox popup on the map with expand button
    const popupHtml = `
      <div style="display:flex;flex-direction:column;gap:4px;align-items:center;">
        ${imgSrc ? `<img class="popup-sv-img" src="${imgSrc}" alt="Street View">` : ''}
        <button class="popup-sv-expand" data-lat="${lat}" data-lon="${lon}">⤢ Expand</button>
      </div>`;
    const popup = new mapboxgl.Popup({ offset: 25, className: 'map-perception-popup' })
      .setLngLat([lon, lat])
      .setHTML(popupHtml)
      .addTo(map as unknown as Parameters<MapboxPopup['addTo']>[0]) as MapboxPopup;
  }
}

/* =====================================================================
   PANEL ROUTER
   ===================================================================== */
function activatePanel(n: PanelId): void {
  if ([3, 4].includes(state.currentPanel as number) && ![3, 4].includes(n as number)) stopRenderLoop();
  state.currentPanel = n;
  if (state.panelStatus[n] === 'locked') state.panelStatus[n] = 'active';
  saveState();
  for (let i = 3; i <= 5; i++) {
    const el = document.getElementById(`panel-${i}`);
    if (el) el.setAttribute('data-active', i === n ? 'true' : 'false');
  }
  refreshNavDots();
  updateZoneFab();
  if (!state.mapMode) {
    document.body.classList.toggle('panel-active', n >= 3);
    document.body.classList.toggle('dim-map', n === 3);
  }
  if (n !== 3) document.body.classList.remove('p3-card-view');
  // Legend is only visible on panel 4 while in results mode
  $('#p4-results-legend')?.classList.toggle('hidden', !(n === 4 && resultsMode));
  if (n === 3) panel3Enter();
  if (n === 4) void panel4Enter();
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
  // Map toggle active state
  const mapBtn = $('#map-toggle') as HTMLButtonElement | null;
  if (mapBtn) mapBtn.setAttribute('data-active', state.mapMode ? 'true' : 'false');
  // Display step numbers 1,2,3 for panels 3,4,5
  let dispNum = 0;
  $$('.pill-nav .dot').forEach((dot) => {
    dispNum++;
    const numEl = dot.querySelector('.num');
    if (numEl) numEl.textContent = String(dispNum);
  });
  // Pill arrow enabled state — disable at boundaries only, all panels are navigable
  const panels: PanelId[] = [3, 4, 5];
  const idx = panels.indexOf(state.currentPanel as PanelId);
  const panelPrevBtn = $('#panel-prev-btn') as HTMLButtonElement | null;
  const panelNextBtn = $('#panel-next-btn') as HTMLButtonElement | null;
  if (panelPrevBtn) panelPrevBtn.disabled = idx <= 0;
  if (panelNextBtn) panelNextBtn.disabled = idx === -1 || idx >= panels.length - 1;
  // Blue highlight: Next on all panels except the last; Prev on the last panel
  const onLast = idx === panels.length - 1;
  panelPrevBtn?.classList.toggle('highlighted', onLast);
  panelNextBtn?.classList.toggle('highlighted', !onLast && idx !== -1);
}

/* =====================================================================
   PANEL 1 — Zone Selection & Streetview catalog
   ===================================================================== */
let p1Bound = false;
let currentBbox: [number, number, number, number] | null = null;
let _lastCandidates: { lat: number; lon: number; heading: number; street_name: string; highway_type: string; edge_id: string }[] = [];

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
  map.scrollZoom.disable();
  ($('#p1-status') as HTMLElement).textContent = 'Click and drag on the map to draw a rectangular zone.';

  function cleanup() {
    (map as unknown as { off: (ev: string, fn: unknown) => void }).off('mousedown', onDown);
    (map as unknown as { off: (ev: string, fn: unknown) => void }).off('mousemove', onMove);
    (map as unknown as { off: (ev: string, fn: unknown) => void }).off('mouseup', onUp);
    document.removeEventListener('mouseup', onUpOutside);
    document.removeEventListener('keydown', onEscape);
    window.removeEventListener('blur', onBlur);
    map!.dragPan.enable();
    map!.scrollZoom.enable();
    map!.getCanvas().style.cursor = '';
    bboxDrawing = false;
  }
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
    cleanup();
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
  // Fallback: mouseup outside the canvas (Mapbox never fires) → reset draw state so the user can retry.
  function onUpOutside() {
    if (!bboxDrawing) return; // already handled by onUp (Mapbox fired first)
    cleanup();
    bboxDrawStart = null;
    ($('#p1-status') as HTMLElement).textContent = 'Draw cancelled — try again.';
  }
  // Escape key cancels an in-progress draw.
  function onEscape(e: KeyboardEvent) {
    if (e.key !== 'Escape' || !bboxDrawing) return;
    cleanup();
    bboxDrawStart = null;
    ($('#p1-status') as HTMLElement).textContent = 'Draw cancelled — try again.';
  }
  // Window blur (alt-tab, click outside browser) cancels draw so dragPan is never left disabled.
  function onBlur() {
    if (!bboxDrawing) return;
    cleanup();
    bboxDrawStart = null;
    ($('#p1-status') as HTMLElement).textContent = 'Draw cancelled — try again.';
  }
  (map as unknown as { on: (ev: string, fn: unknown) => void }).on('mousedown', onDown);
  (map as unknown as { on: (ev: string, fn: unknown) => void }).on('mousemove', onMove);
  (map as unknown as { on: (ev: string, fn: unknown) => void }).on('mouseup', onUp);
  document.addEventListener('mouseup', onUpOutside);
  document.addEventListener('keydown', onEscape);
  window.addEventListener('blur', onBlur);
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
      const nearThreshDeg = spacing / 110540;
      const nearSq = nearThreshDeg * nearThreshDeg;
      features = features.filter((f) => {
        const [flon, flat] = f.geometry.coordinates;
        return !existing.some(([elon, elat]) => (elon - flon) ** 2 + (elat - flat) ** 2 < nearSq);
      });
    }

    src.setData({ type: 'FeatureCollection', features });
    _lastCandidates = features.map((f) => f.properties as { lat: number; lon: number; heading: number; street_name: string; highway_type: string; edge_id: string });
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
  // Show zone-fab only in map mode (panels are hidden, so no overlap with control dock)
  fab.classList.toggle('hidden', !state.mapMode);
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
      (map?.getSource('sv') as { setData: (d: unknown) => void } | undefined)?.setData(fresh);
      ($('#p1-kpi-existing') as HTMLElement).textContent = String(stats.images);
    } catch (e) {
      toast(`Delete failed: ${e instanceof Error ? e.message : e}`, 'danger');
    }
  });

  let _svPoller: number | null = null;
  let _svActiveJobId: string | null = null;
  const _svDownloadedFeatures: { type: 'Feature'; geometry: { type: 'Point'; coordinates: [number, number] }; properties: Record<string, never> }[] = [];

  const _DL_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`;
  const _STOP_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`;

  function _setSvDownloading(jobId: string | null): void {
    const btn = $('#p1-download') as HTMLButtonElement;
    _svActiveJobId = jobId;
    if (jobId) {
      btn.innerHTML = _STOP_ICON;
      btn.title = 'Stop Download';
      btn.classList.remove('primary');
      btn.classList.add('danger');
      btn.disabled = false;
    } else {
      btn.innerHTML = _DL_ICON;
      btn.title = 'Download New Images';
      btn.classList.remove('danger');
      btn.classList.add('primary');
      btn.disabled = false;
    }
  }

  function _updateDownloadingLayer(): void {
    (map?.getSource('sv-downloading') as { setData: (d: unknown) => void } | undefined)
      ?.setData({ type: 'FeatureCollection', features: _svDownloadedFeatures });
  }

  $('#p1-download')!.addEventListener('click', async () => {
    // ── Stop in-progress download ─────────────────────────────────────
    if (_svActiveJobId) {
      const jobId = _svActiveJobId;
      try { await fetch(`${api.map}/api/streetview/download/${jobId}`, { method: 'DELETE' }); } catch { /* ok */ }
      if (_svPoller) { clearInterval(_svPoller); _svPoller = null; }
      _setSvDownloading(null);
      ($('#p1-sv-progress') as HTMLElement).style.display = 'none';
      ($('#p1-status') as HTMLElement).textContent = 'Download cancelled.';
      return;
    }

    // ── Start new download ────────────────────────────────────────────
    _svDownloadedFeatures.length = 0;
    _updateDownloadingLayer();
    if (!currentBbox) { toast('Draw a zone first.', 'warning'); return; }

    const progressDiv  = $('#p1-sv-progress')        as HTMLElement;
    const statusEl     = $('#p1-sv-progress-status') as HTMLElement;
    const fillEl       = $('#p1-sv-progress-fill')   as HTMLElement;
    const logEl        = $('#p1-sv-progress-log')    as HTMLElement;
    const mainStatus   = $('#p1-status')             as HTMLElement;
    const kpiExisting  = $('#p1-kpi-existing')       as HTMLElement;

    progressDiv.style.display = '';
    statusEl.textContent = 'Starting download…';
    fillEl.style.width   = '0%';
    logEl.innerHTML      = '';

    try {
      const res = await api.postJSON<{ error?: string; job_id?: string }>(
        api.map, '/api/streetview/download', { bbox: currentBbox, spacing: state.zone.spacing, candidates: _lastCandidates },
      );
      if (res.error || !res.job_id) {
        toast(res.error || 'Download failed', 'danger');
        mainStatus.textContent = res.error || 'Download failed';
        progressDiv.style.display = 'none';
        return;
      }

      const jobId = res.job_id;
      _setSvDownloading(jobId);
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

          if (s.status === 'done' || s.status === 'error' || s.status === 'cancelled') {
            clearInterval(_svPoller!); _svPoller = null;
            _setSvDownloading(null);
            if (s.status === 'cancelled') {
              mainStatus.textContent = 'Download cancelled.';
            } else {
              mainStatus.textContent = `Done — ${s.downloaded} new images saved.`;
              toast(`Downloaded ${s.downloaded} images, skipped ${s.skipped}.`, 'success');
            }
            progressDiv.style.display = 'none';
            // Reload streetview dots and KPI
            try {
              const [fresh, stats] = await Promise.all([
                api.m<{ features: unknown[] }>('/api/streetview_grid'),
                api.m<{ images: number; results: number }>('/api/streetview/stats'),
              ]);
              (map?.getSource('sv') as { setData: (d: unknown) => void } | undefined)?.setData(fresh);
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
      _setSvDownloading(null);
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
      state.selectedPoint = null; _analysePoints.clear(); _lastClickedProps = {};
      _updateAnalyseButton(); _updateSelectionHighlight(); saveState();
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
}

/* =====================================================================
   VLM Configuration (embedded in Panel 1 Analyse card)
   ===================================================================== */
let p1VlmBound = false;
let _analyseMode: 'single' | 'multi' | 'unanalyzed' | 'all' = 'single';
let _analysePoints: Set<string> = new Set(); // keys: "lat_lon"
let _lastClickedProps: Record<string, unknown> = {};
let _activeDetailProps: Record<string, unknown> = {}; // props of the gallery-focused image

function _updateAnalyseButton(): void {
  const openBtn = $('#p1-analyse-open') as HTMLButtonElement | null;
  const runBtn  = $('#p1-vlm-analyze')  as HTMLButtonElement | null;
  const count = _analysePoints.size;

  if (openBtn) {
    if (count === 0)      openBtn.textContent = 'Analyse Images';
    else if (count === 1) openBtn.textContent = 'Analyse Current Image';
    else                  openBtn.textContent = `Analyse Selected Images (${count})`;
  }

  if (runBtn) {
    runBtn.disabled = count === 0;
    if (count === 0)      runBtn.textContent = 'Select images first';
    else if (count === 1) runBtn.textContent = 'Run Analysis';
    else                  runBtn.textContent = `Run Analysis (${count})`;
  }
}

function _updateSelectionHighlight(): void {
  const src = map?.getSource('sv') as { _data?: { features: { properties: Record<string, unknown>; geometry: unknown; type: string }[] } } | undefined;
  const allFeatures = src?._data?.features ?? [];
  const selected = allFeatures.filter((f) => _analysePoints.has(`${f.properties?.lat}_${f.properties?.lon}`));
  (map?.getSource('sv-selected') as { setData: (d: unknown) => void } | undefined)?.setData({
    type: 'FeatureCollection', features: selected,
  });
}

function _hasAnalysisData(p: Record<string, unknown>): boolean {
  return PERCEPTION_FIELDS.some((spec) => {
    const raw = p[spec.key];
    return raw && String(raw).trim().length > 2;
  });
}

function _sfaValueClass(val: string): string {
  const v = val.toLowerCase().trim();
  if (['dark','obstructed','empty','none','enclosed','absent'].includes(v)) return 'c-danger';
  if (['dim','sparse','narrow','semi','few','partial'].includes(v)) return 'c-warning';
  if (['bright','dense','wide','open','adequate','clear','signalised','many','several'].includes(v)) return 'c-success';
  if (['sidewalk','plaza','shared','road','zebra','sign','label','graffiti','moderate',
       'pedestrian'].some(w => v.includes(w))) return 'c-accent';
  return 'c-neutral';
}

const _ZONE_HL: Record<string, [string, string, string, string, string, string]> = {
  far_left:    ['0%',  '0%',  '20%', '100%', 'rgba(94,92,230,0.22)',  'rgba(94,92,230,0.75)'],
  left:        ['0%',  '0%',  '40%', '100%', 'rgba(10,132,255,0.20)', 'rgba(10,132,255,0.75)'],
  center:      ['30%', '0%',  '40%', '100%', 'rgba(210,215,255,0.14)','rgba(210,215,255,0.5)'],
  right:       ['60%', '0%',  '40%', '100%', 'rgba(255,159,10,0.20)', 'rgba(255,159,10,0.75)'],
  far_right:   ['80%', '0%',  '20%', '100%', 'rgba(255,55,95,0.20)',  'rgba(255,55,95,0.75)'],
  bottom_left: ['0%',  '55%', '45%', '45%',  'rgba(10,132,255,0.20)', 'rgba(10,132,255,0.75)'],
};

document.addEventListener('click', () => {
  document.querySelectorAll('.sfa-info-tip.visible').forEach((t) => t.classList.remove('visible'));
  document.querySelectorAll('.sfa-info-btn.active').forEach((b) => b.classList.remove('active'));
});

function _buildSceneFields(p: Record<string, unknown>, container: HTMLElement): void {
  document.querySelectorAll('.sfa-info-tip').forEach((t) => t.remove());
  container.innerHTML = '';
  PERCEPTION_FIELDS.forEach((spec) => {
    const raw = p[spec.key];
    if (!raw || String(raw).trim().length < 2) return;
    const rawStr = String(raw).trim();

    const section = document.createElement('div');
    section.className = 'sfa-section';

    const labelEl = document.createElement('div');
    labelEl.className = 'sfa-label';
    labelEl.textContent = spec.label;
    section.appendChild(labelEl);

    if (rawStr.startsWith('[')) {
      try {
        const arr = JSON.parse(rawStr) as Record<string, unknown>[];
        const ARCH_KEYS = new Set(['architectural_style', 'building_condition', 'storefront_type', 'architectural_details']);
        const allKeys = new Set<string>();
        arr.forEach((obj) => Object.keys(obj).filter(k => k !== 'zone').forEach(k => allKeys.add(k)));
        const cols = Array.from(allKeys).filter(k => !ARCH_KEYS.has(k));
        const archCols = Array.from(allKeys).filter(k => ARCH_KEYS.has(k));
        const hasArch = archCols.length > 0;

        const grid = document.createElement('div');
        grid.className = 'sfa-grid-table';
        grid.style.gridTemplateColumns = `auto ${cols.map(() => 'minmax(60px, 1fr)').join(' ')}${hasArch ? ' 28px' : ''}`;

        const thead = document.createElement('div');
        thead.className = 'sfa-grid-head';
        const thZone = document.createElement('span');
        thZone.className = 'sfa-gh-cell';
        thZone.textContent = 'zone';
        thead.appendChild(thZone);
        cols.forEach((c) => {
          const th = document.createElement('span');
          th.className = 'sfa-gh-cell';
          th.textContent = c.replace(/_/g, ' ');
          thead.appendChild(th);
        });
        if (hasArch) {
          const thInfo = document.createElement('span');
          thInfo.className = 'sfa-gh-cell';
          thead.appendChild(thInfo);
        }
        grid.appendChild(thead);

        arr.forEach((obj) => {
          const row = document.createElement('div');
          row.className = 'sfa-grid-row';
          const zone = obj['zone'] ? String(obj['zone']) : null;
          const zoneEl = document.createElement('span');
          zoneEl.className = `sfa-zone sfa-zone-${(zone || 'center').replace(/_/g, '-')}`;
          zoneEl.textContent = (zone || '—').replace(/_/g, ' ');
          row.appendChild(zoneEl);

          cols.forEach((key) => {
            const val = obj[key] != null ? String(obj[key]) : '—';
            const cell = document.createElement('span');
            cell.className = `sfa-grid-val sfa-v-${_sfaValueClass(val)}`;
            cell.textContent = val;
            row.appendChild(cell);
          });

          if (hasArch) {
            const wrap = document.createElement('span');
            wrap.className = 'sfa-info-wrap';
            const btn = document.createElement('button');
            btn.className = 'sfa-info-btn';
            btn.textContent = 'i';
            btn.type = 'button';
            const tip = document.createElement('div');
            tip.className = 'sfa-info-tip';
            archCols.forEach((key) => {
              const val = obj[key] != null ? String(obj[key]) : '—';
              if (val === '—') return;
              const line = document.createElement('div');
              line.className = 'sfa-info-line';
              const kEl = document.createElement('span');
              kEl.className = 'sfa-info-key';
              kEl.textContent = key.replace(/_/g, ' ');
              const vEl = document.createElement('span');
              vEl.className = `sfa-info-val sfa-v-${_sfaValueClass(val)}`;
              vEl.textContent = val;
              line.appendChild(kEl);
              line.appendChild(vEl);
              tip.appendChild(line);
            });
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              document.querySelectorAll('.sfa-info-tip.visible').forEach((t) => { if (t !== tip) t.classList.remove('visible'); });
              document.querySelectorAll('.sfa-info-btn.active').forEach((b) => { if (b !== btn) b.classList.remove('active'); });
              const open = tip.classList.toggle('visible');
              btn.classList.toggle('active', open);
              if (open) {
                const r = btn.getBoundingClientRect();
                tip.style.top = `${r.top + r.height / 2}px`;
                tip.style.left = `${r.left - 8}px`;
                tip.style.transform = 'translate(-100%, -50%)';
              }
            });
            wrap.appendChild(btn);
            document.body.appendChild(tip);
            row.appendChild(wrap);
          }

          if (zone) {
            const hl = document.getElementById('sv-zone-highlight') as HTMLElement | null;
            if (hl) {
              row.addEventListener('mouseenter', () => {
                const r = _ZONE_HL[zone];
                if (!r) return;
                hl.style.left       = r[0];
                hl.style.top        = r[1];
                hl.style.width      = r[2];
                hl.style.height     = r[3];
                hl.style.background = r[4];
                hl.style.border     = `2px solid ${r[5]}`;
                hl.classList.add('visible');
              });
              row.addEventListener('mouseleave', () => hl.classList.remove('visible'));
            }
          }
          grid.appendChild(row);
        });
        section.appendChild(grid);
      } catch {
        const plain = document.createElement('div');
        plain.className = 'sfa-plain';
        plain.textContent = rawStr;
        section.appendChild(plain);
      }
    } else {
      const plain = document.createElement('div');
      plain.className = 'sfa-plain';
      plain.textContent = rawStr;
      section.appendChild(plain);
    }
    container.appendChild(section);
  });
}

function _switchDetailTab(tab: 'gallery' | 'analysis'): void {
  ($('#sv-detail-tabs') as HTMLElement).querySelectorAll<HTMLElement>('.sv-tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  const fields  = $('#sv-detail-fields')  as HTMLElement;
  const gallery = $('#sv-detail-gallery') as HTMLElement;
  if (tab === 'analysis') {
    fields.classList.remove('hidden'); gallery.classList.add('hidden');
  } else {
    fields.classList.add('hidden'); gallery.classList.remove('hidden');
  }
}

function _buildGallery(activePropKey: string): void {
  const gallery = $('#sv-detail-gallery') as HTMLElement;
  gallery.innerHTML = '';
  const src = map?.getSource('sv') as { _data?: { features: { properties: Record<string, unknown> }[] } } | undefined;
  const allFeatures = src?._data?.features ?? [];
  _analysePoints.forEach((key) => {
    const feat = allFeatures.find((f) => `${f.properties?.lat}_${f.properties?.lon}` === key);
    if (!feat) return;
    const p = feat.properties;
    const imgSrc = p.image_url ? api.map + (p.image_url as string) : '';
    const isActive = key === activePropKey;
    const thumb = document.createElement('div');
    thumb.className = 'sv-thumb' + (isActive ? ' active' : '');
    if (imgSrc) { const img = document.createElement('img'); img.src = imgSrc; img.alt = key; thumb.appendChild(img); }
    thumb.addEventListener('click', () => {
      gallery.querySelectorAll('.sv-thumb').forEach((t) => t.classList.remove('active'));
      thumb.classList.add('active');
      _activeDetailProps = p;  // track for Analysis tab — don't build fields yet
      const is = p.image_url ? api.map + (p.image_url as string) : '';
      ($('#sv-detail-img') as HTMLImageElement).src = is;
      ($('#sv-detail-title') as HTMLElement).textContent = `(${(p.lat as number)?.toFixed(5)}, ${(p.lon as number)?.toFixed(5)})`;
      ($('#sv-detail-coord') as HTMLElement).textContent = `${(p.lat as number)?.toFixed(5)}, ${(p.lon as number)?.toFixed(5)}`;
      ($('#sv-detail-heading') as HTMLElement).textContent = `Heading ${p.heading ?? 0}°`;
      const analysisTab = $('#sv-tab-analysis') as HTMLElement;
      const hasAnalysis = _hasAnalysisData(p);
      analysisTab.dataset.disabled = hasAnalysis ? 'false' : 'true';
      const activeTab = ($('#sv-detail-tabs') as HTMLElement).querySelector<HTMLElement>('.sv-tab.active');
      if (activeTab?.dataset.tab === 'analysis' && !hasAnalysis) _switchDetailTab('gallery');
    });
    gallery.appendChild(thumb);
  });
}

function _openDetailModal(p: Record<string, unknown>): void {
  const key = `${p.lat}_${p.lon}`;
  const imgSrc = p.image_url ? api.map + (p.image_url as string) : '';
  ($('#sv-detail-img') as HTMLImageElement).src = imgSrc;
  ($('#sv-detail-title') as HTMLElement).textContent = `(${(p.lat as number)?.toFixed(5)}, ${(p.lon as number)?.toFixed(5)})`;
  ($('#sv-detail-coord') as HTMLElement).textContent = `${(p.lat as number)?.toFixed(5)}, ${(p.lon as number)?.toFixed(5)}`;
  ($('#sv-detail-heading') as HTMLElement).textContent = `Heading ${p.heading ?? 0}°`;
  _buildSceneFields(p, $('#sv-detail-fields') as HTMLElement);

  const tabs    = $('#sv-detail-tabs')    as HTMLElement;
  const gallery = $('#sv-detail-gallery') as HTMLElement;
  const fields  = $('#sv-detail-fields')  as HTMLElement;
  const analysisTab = $('#sv-tab-analysis') as HTMLElement;

  if (_analysePoints.size > 1) {
    _activeDetailProps = p;  // initialize with the first-opened image
    tabs.classList.remove('hidden');
    _buildGallery(key);
    _switchDetailTab('gallery');
    fields.classList.add('hidden');
    gallery.classList.remove('hidden');
    analysisTab.dataset.disabled = _hasAnalysisData(p) ? 'false' : 'true';
    tabs.querySelectorAll<HTMLElement>('.sv-tab').forEach((t) => {
      t.onclick = () => {
        if (t.dataset.tab === 'analysis') {
          _buildSceneFields(_activeDetailProps, fields);
        }
        _switchDetailTab(t.dataset.tab as 'gallery' | 'analysis');
      };
    });
  } else {
    tabs.classList.add('hidden');
    gallery.classList.add('hidden');
    fields.classList.remove('hidden');
  }

  ($('#sv-detail-modal') as HTMLElement).classList.remove('hidden');
}

function _initAnalyseCard(): void {
  if (p1VlmBound) return; p1VlmBound = true;

  // Populate model dropdown from VLM_CARDS and upgrade to apple-select style
  const modelSel = $('#p1-vlm-model-select') as HTMLSelectElement | null;
  const hfRow   = document.getElementById('p1-vlm-hf-row')   as HTMLElement | null;
  const hfInput = document.getElementById('p1-vlm-hf-model') as HTMLInputElement | null;
  function _syncHfRow(): void {
    if (!hfRow || !modelSel) return;
    hfRow.style.display = modelSel.value === 'custom-hf' ? 'flex' : 'none';
  }
  if (modelSel) {
    modelSel.innerHTML = VLM_CARDS
      .filter((v) => v.id !== 'custom-hf')
      .map((v) => `<option value="${v.id}"${state.vlm.provider === v.id ? ' selected' : ''}>${escapeHtml(v.name)}</option>`)
      .join('') + `<option value="custom-hf"${state.vlm.provider === 'custom-hf' ? ' selected' : ''}>Custom HuggingFace</option>`;
    if (hfInput) {
      hfInput.value = state.vlm.hfModel || '';
      hfInput.addEventListener('input', () => {
        state.vlm.hfModel = hfInput.value.trim();
        saveState();
      });
    }
    modelSel.addEventListener('change', () => {
      state.vlm.provider = modelSel.value;
      _syncHfRow();
      saveState();
    });
    _syncHfRow();
    appleSelect(modelSel);
    const wrap = modelSel.closest<HTMLElement>('.asl-wrap');
    if (wrap) { wrap.style.flex = '1'; wrap.style.width = 'auto'; }
  }

  // ── VLM Compare helpers ───────────────────────────────────────────────────
  type BenchmarkEntry = {
    model_id: string; display_name: string; timestamp?: string;
    pros?: string; cons?: string;
    scores: {
      json_valid: number; ocr_iou: number;
      traffic_iou: number; furniture_iou: number;
      vehicle_iou: number; markings_iou: number;
      keypoint_dist: number; composite: number;
    };
  };
  type BenchmarkData = {
    benchmark_image?: string; benchmark_location?: string; benchmark_description?: string;
    last_updated?: string;
    models: Record<string, BenchmarkEntry>;
  };

  function _scoreBar(value: number, max = 1, invert = false): string {
    const pct = Math.round((invert ? (1 - Math.min(value, max) / max) : Math.min(value, max)) * 100);
    const colour = pct >= 70 ? 'var(--accent)' : pct >= 40 ? '#f0a500' : '#e05c5c';
    return `<div style="display:flex;align-items:center;gap:6px;font-size:11px;">
      <div style="flex:1;height:4px;border-radius:2px;background:rgba(255,255,255,0.1);">
        <div style="width:${pct}%;height:100%;border-radius:2px;background:${colour};"></div>
      </div>
      <span style="width:32px;text-align:right;color:${colour};">${pct}%</span>
    </div>`;
  }

  const CATEGORY_LABELS = [
    { key: 'ocr_iou',        label: 'OCR' },
    { key: 'traffic_iou',    label: 'Traffic' },
    { key: 'furniture_iou',  label: 'Furniture' },
    { key: 'vehicle_iou',    label: 'Vehicle' },
    { key: 'markings_iou',   label: 'Markings' },
    { key: 'keypoint_dist',  label: 'Keypoint', invert: true },
  ] as const;

  const MODEL_COLORS = [
    '#4ecdc4', '#ff6b6b', '#45b7d1', '#f9ca24', '#a29bfe', '#fd79a8',
  ];

  function _buildRadarChart(modelEntries: BenchmarkEntry[], width = 440, height = 380): HTMLElement {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'text-align:center;margin:0 0 12px 0;';
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    canvas.style.cssText = 'max-width:100%;height:auto;';
    wrap.appendChild(canvas);
    const ctx = canvas.getContext('2d')!;
    const cx = width / 2, cy = height / 2 - 10, radius = Math.min(cx, cy) - 40;

    // Background rings
    for (let ring = 1; ring <= 5; ring++) {
      const r = (radius * ring) / 5;
      ctx.beginPath();
      for (let i = 0; i < CATEGORY_LABELS.length; i++) {
        const angle = (Math.PI / 180) * (i * 60 - 90);
        const x = cx + r * Math.cos(angle), y = cy + r * Math.sin(angle);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.strokeStyle = 'rgba(255,255,255,0.08)';
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // Axis lines + labels
    ctx.font = '11px system-ui,-apple-system,sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.6)';
    ctx.textAlign = 'center';
    for (let i = 0; i < CATEGORY_LABELS.length; i++) {
      const angle = (Math.PI / 180) * (i * 60 - 90);
      const x = cx + radius * Math.cos(angle), y = cy + radius * Math.sin(angle);
      ctx.strokeStyle = 'rgba(255,255,255,0.12)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
      const lx = cx + (radius + 24) * Math.cos(angle);
      const ly = cy + (radius + 24) * Math.sin(angle);
      ctx.fillText(CATEGORY_LABELS[i].label, lx, ly + 4);
    }

    // Model polygons
    modelEntries.forEach((m, mi) => {
      const color = MODEL_COLORS[mi % MODEL_COLORS.length];
      const values = CATEGORY_LABELS.map(c => {
        const v = (m.scores as Record<string, number>)[c.key] ?? 0;
        return (c as { key: string; label: string; invert?: boolean }).invert ? (1000 - Math.min(v, 1000)) / 1000 : v;
      });
      ctx.beginPath();
      values.forEach((v, i) => {
        const angle = (Math.PI / 180) * (i * 60 - 90);
        const r = v * radius;
        const x = cx + r * Math.cos(angle), y = cy + r * Math.sin(angle);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.closePath();
      ctx.fillStyle = color + '30';
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
      // Vertex dots
      values.forEach((v, i) => {
        const angle = (Math.PI / 180) * (i * 60 - 90);
        const r = v * radius;
        const x = cx + r * Math.cos(angle), y = cy + r * Math.sin(angle);
        ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fillStyle = color; ctx.fill();
      });
    });

    // Legend
    const leg = document.createElement('div');
    leg.style.cssText = 'display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin-top:4px;font-size:11px;';
    modelEntries.forEach((m, mi) => {
      const color = MODEL_COLORS[mi % MODEL_COLORS.length];
      const item = document.createElement('span');
      item.style.cssText = `display:flex;align-items:center;gap:4px;`;
      item.innerHTML = `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${color};"></span>${escapeHtml(m.display_name)}`;
      leg.appendChild(item);
    });
    wrap.appendChild(leg);
    return wrap;
  }

  function _buildWinnerCallout(modelEntries: BenchmarkEntry[]): HTMLElement {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'margin-bottom:14px;padding:10px 14px;border-radius:8px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.06);';
    const catWinners = CATEGORY_LABELS.map(c => {
      const best = modelEntries.reduce((a, b) => {
        const va = (a.scores as Record<string, number>)[c.key] ?? 0;
        const vb = (b.scores as Record<string, number>)[c.key] ?? 0;
        if ((c as { key: string; label: string; invert?: boolean }).invert) return va <= vb ? a : b;
        return va >= vb ? a : b;
      });
      const val = (best.scores as Record<string, number>)[c.key] ?? 0;
      const display = (c as { key: string; label: string; invert?: boolean }).invert ? `${(1000 - Math.min(val as number, 1000)) / 10}%` : `${((val as number) * 100).toFixed(0)}%`;
      return `${c.label}: <strong style="color:#4ecdc4">${escapeHtml(best.display_name)}</strong> (${display})`;
    });
    const top = modelEntries[0];
    wrap.innerHTML = `
      <div style="font-size:12px;font-weight:600;margin-bottom:6px;">Category Winners</div>
      <div style="font-size:11px;display:flex;flex-wrap:wrap;gap:4px 16px;">${catWinners.map(w => `<span>${w}</span>`).join('')}</div>
      <div style="font-size:11px;margin-top:6px;opacity:.7;">Overall: <strong style="color:#4ecdc4">${escapeHtml(top.display_name)}</strong> — ${(top.scores.composite * 100).toFixed(1)}% composite</div>`;
    return wrap;
  }

  function _buildBenchmarkSections(bench: BenchmarkData, selectedOnly?: BenchmarkEntry[]): HTMLElement[] {
    const modelEntries = (selectedOnly ?? Object.values(bench.models)).sort((a, b) => b.scores.composite - a.scores.composite);
    if (!modelEntries.length) return [];

    const sections: HTMLElement[] = [];

    // 1 — Winner callout
    sections.push(_buildWinnerCallout(modelEntries));

    // 2 — Radar chart
    const radarWrap = document.createElement('div');
    radarWrap.style.cssText = 'margin-bottom:14px;';
    const radarTitle = document.createElement('div');
    radarTitle.className = 'eyebrow';
    radarTitle.style.cssText = 'margin-bottom:6px;';
    radarTitle.textContent = 'Multi-Category Radar';
    radarWrap.appendChild(radarTitle);
    radarWrap.appendChild(_buildRadarChart(modelEntries, 420, 360));
    sections.push(radarWrap);

    // 3 — Detailed benchmark table
    const tableWrap = document.createElement('div');
    tableWrap.style.cssText = 'margin-bottom:14px;overflow-x:auto;';
    const loc = bench.benchmark_location ? `<span class="meta" style="opacity:.5;font-size:11px;">${escapeHtml(bench.benchmark_location)}</span>` : '';
    const desc = bench.benchmark_description ? `<div class="meta" style="opacity:.4;font-size:10px;margin-top:2px;">${escapeHtml(bench.benchmark_description)}</div>` : '';
    tableWrap.innerHTML = `
      <div class="row between" style="margin-bottom:6px;">
        <div class="eyebrow">Per-Category Scores</div>${loc}
      </div>${desc}
      <table style="width:100%;border-collapse:collapse;font-size:11px;min-width:600px;">
        <thead><tr style="opacity:.5;">
          <th style="text-align:left;padding:4px 6px;font-weight:500;position:sticky;left:0;background:var(--surface);">Model</th>
          <th style="padding:4px 6px;font-weight:500;">JSON</th>
          <th style="padding:4px 6px;font-weight:500;">OCR</th>
          <th style="padding:4px 6px;font-weight:500;">Traffic</th>
          <th style="padding:4px 6px;font-weight:500;">Furniture</th>
          <th style="padding:4px 6px;font-weight:500;">Vehicle</th>
          <th style="padding:4px 6px;font-weight:500;">Markings</th>
          <th style="padding:4px 6px;font-weight:500;">Keypoint</th>
          <th style="padding:4px 6px;font-weight:500;color:var(--accent);">Score</th>
        </tr></thead>
        <tbody>${modelEntries.map((m, i) => `
          <tr style="border-top:1px solid rgba(255,255,255,0.06);${i === 0 ? 'background:rgba(78,205,196,0.06);' : ''}">
            <td style="padding:6px 6px;white-space:nowrap;position:sticky;left:0;background:var(--surface);${i===0?'font-weight:600;':''}">${escapeHtml(m.display_name)}${i===0?' <span style="color:#4ecdc4;">★</span>':''}</td>
            <td style="padding:6px;">${_scoreBar(m.scores.json_valid)}</td>
            <td style="padding:6px;">${_scoreBar(m.scores.ocr_iou)}</td>
            <td style="padding:6px;">${_scoreBar(m.scores.traffic_iou)}</td>
            <td style="padding:6px;">${_scoreBar(m.scores.furniture_iou)}</td>
            <td style="padding:6px;">${_scoreBar(m.scores.vehicle_iou)}</td>
            <td style="padding:6px;">${_scoreBar(m.scores.markings_iou)}</td>
            <td style="padding:6px;">${_scoreBar(m.scores.keypoint_dist, 1000, true)}</td>
            <td style="padding:6px;font-weight:600;color:var(--accent);">${(m.scores.composite * 100).toFixed(1)}%</td>
          </tr>`).join('')}
        </tbody>
      </table>`;
    sections.push(tableWrap);

    // 4 — Annotated image grid
    const imgSection = document.createElement('div');
    imgSection.style.cssText = 'margin-bottom:14px;';
    const imgTitle = document.createElement('div');
    imgTitle.className = 'eyebrow';
    imgTitle.style.cssText = 'margin-bottom:8px;';
    imgTitle.textContent = 'Visual Comparison';
    imgSection.appendChild(imgTitle);
    const imgGrid = document.createElement('div');
    imgGrid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;';
    modelEntries.forEach((m) => {
      const slug = _imgSlug(m.display_name);
      const imgUrl = `${api.map}/api/vlm/compare-images/${slug}.png`;
      const card = document.createElement('div');
      card.style.cssText = 'border-radius:6px;overflow:hidden;border:1px solid rgba(255,255,255,0.06);background:rgba(0,0,0,0.2);';
      card.innerHTML = `
        <img src="${imgUrl}" style="width:100%;display:block;" onerror="this.outerHTML='<div style=\\'padding:20px;text-align:center;font-size:10px;opacity:.4;\\'>No image</div>'">
        <div style="padding:6px 8px;font-size:11px;display:flex;justify-content:space-between;align-items:center;">
          <span>${escapeHtml(m.display_name)}</span>
          <span style="color:var(--accent);font-weight:600;">${(m.scores.composite * 100).toFixed(0)}%</span>
        </div>`;
      imgGrid.appendChild(card);
    });
    imgSection.appendChild(imgGrid);
    sections.push(imgSection);

    // 5 — Recommendation narrative
    const top = modelEntries[0];
    const runnerUp = modelEntries[1];
    const narrative = document.createElement('div');
    narrative.style.cssText = 'padding:12px 14px;border-radius:8px;background:rgba(78,205,196,0.06);border:1px solid rgba(78,205,196,0.15);margin-bottom:14px;';
    narrative.innerHTML = `
      <div style="font-size:12px;font-weight:600;margin-bottom:4px;">Recommendation: ${escapeHtml(top.display_name)}</div>
      <div style="font-size:11px;opacity:.8;line-height:1.5;">
        Selected as production VLM for the urban perception pipeline. 
        <strong>${escapeHtml(top.display_name)}</strong> leads the composite score (${(top.scores.composite * 100).toFixed(1)}% vs ${(runnerUp ? runnerUp.scores.composite * 100 : 0).toFixed(1)}% runner-up)
        with ${top.scores.json_valid ? 'reliable JSON schema compliance' : 'known schema issues'}.
        ${top.pros ? escapeHtml(top.pros) : ''}
        ${top.cons ? '<br><span style="opacity:.6;">Limitation: ' + escapeHtml(top.cons) + '</span>' : ''}
      </div>`;
    sections.push(narrative);

    return sections;
  }

  function _imgSlug(name: string): string {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/, '');
  }

  function _buildVLMCard(
    v: VLMCardSpec, hasData: boolean,
    bench: BenchmarkData | null,
    onToggle: (checked: boolean) => void,
    useCheckbox = false
  ): HTMLElement {
    const entry   = bench?.models[v.name] ?? null;
    const slug    = _imgSlug(v.name);
    const imgUrl  = `${api.map}/api/vlm/compare-images/${slug}.png`;

    const propsHtml = Object.entries(v.props).map(
      ([k, val]) => `<div class="prop"><b>${escapeHtml(k)}:</b> ${escapeHtml(val)}</div>`
    ).join('');

    const scoreHtml = entry
      ? `<div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">
           <span class="chip" style="font-size:10px;">Score ${(entry.scores.composite * 100).toFixed(0)}%</span>
           <span class="chip" style="font-size:10px;">JSON ${entry.scores.json_valid ? '✓' : '✗'}</span>
           <span class="chip" style="font-size:10px;">OCR ${(entry.scores.ocr_iou * 100).toFixed(0)}%</span>
         </div>`
      : '';

    // Benchmark image thumbnail — only shown when benchmark data exists for this model
    const imgHtml = entry
      ? `<a href="${imgUrl}" target="_blank" title="Click to view full annotated image" style="display:block;margin:8px 0 4px;">
           <img src="${imgUrl}"
                style="width:100%;border-radius:5px;border:1px solid rgba(255,255,255,0.08);display:block;"
                onerror="this.parentElement.style.display='none'">
           <div class="meta" style="font-size:10px;opacity:.5;margin-top:3px;">↗ Click to open full image</div>
         </a>`
      : '';

    const div = document.createElement('div');
    div.className = 'vlm-card';
    div.setAttribute('data-model-id', v.id);
    div.setAttribute('data-selected', 'false');

    const cbHtml = useCheckbox
      ? `<input type="checkbox" data-model-name="${escapeHtml(v.name)}" style="accent-color:var(--accent);flex-shrink:0;${entry ? 'cursor:pointer' : 'opacity:.3;cursor:not-allowed'}" ${entry ? '' : 'disabled'}>`
      : '';

    div.innerHTML = `
      <div class="top">
        ${cbHtml}
        <span class="name">${escapeHtml(v.name)}</span>
        ${v.active  ? '<span class="chip success" style="font-size:11px;">Active</span>'          : ''}
        ${entry     ? '<span class="chip" style="font-size:11px;opacity:.7;">Benchmarked</span>' : '<span class="chip" style="font-size:11px;opacity:.4;">No data</span>'}
      </div>
      <div class="props">${propsHtml}</div>
      ${imgHtml}
      ${scoreHtml}
      ${!entry ? '<div class="meta" style="font-size:10px;opacity:.4;margin-top:4px;">Run the benchmark notebook on Lightning AI to generate results for this model.</div>' : ''}
      <div class="pros">${escapeHtml(v.pros)}</div>
      <div class="cons">${escapeHtml(v.cons)}</div>`;

    if (useCheckbox && entry) {
      const cb = div.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
      cb.addEventListener('change', () => {
        div.setAttribute('data-selected', cb.checked ? 'true' : 'false');
        onToggle(cb.checked);
      });
      // Click on card toggles checkbox
      div.addEventListener('click', (e) => {
        if ((e.target as HTMLElement).closest('a')) return;
        if ((e.target as HTMLElement).closest('input')) return;
        cb.checked = !cb.checked;
        cb.dispatchEvent(new Event('change'));
      });
    } else if (!useCheckbox) {
      div.addEventListener('click', (e) => {
        if ((e.target as HTMLElement).closest('a')) return;
        onToggle(true);
      });
    }
    return div;
  }

  // ── Model metadata (derived from JSON files at runtime) ─────────────────────
  const _BRAND_COLORS = ['#7B5CF5','#3B82F6','#10B981','#0078D4','#FF9D00','#F43F5E','#8B5CF6','#14B8A6','#F59E0B','#6366F1'];

  function _deriveModelMeta(modelId: string, idx: number): { displayName: string; org: string; brandColor: string; initials: string } {
    const parts = modelId.split('/');
    const org = parts.length > 1 ? parts[0] : 'Unknown';
    const name = parts.length > 1 ? parts[1] : parts[0];
    const displayName = name
      .replace(/-Instruct$/, '')
      .replace(/-hf$/, '')
      .replace(/^llava-onevision-qwen2-/, 'LLaVA-OV-')
      .replace(/Qwen2_5-VL/i, 'Qwen2.5-VL')
      .replace(/Qwen3-VL/i, 'Qwen3-VL')
      .replace(/Phi-3.5-vision/i, 'Phi-3.5-Vision')
      .replace(/Idefics3-8B-Llama3/i, 'Idefics3-8B')
      .replace(/InternVL2_5/i, 'InternVL2.5');
    const initials = displayName.replace(/[^A-Z0-9]/g, '').slice(0, 3) || displayName.slice(0, 2).toUpperCase();
    return { displayName, org, brandColor: _BRAND_COLORS[idx % _BRAND_COLORS.length], initials };
  }

  type LightingEntry  = { zone:string; element:string; condition:string };
  type SpatialEntry   = { zone:string; width:string; enclosure:string; passability:string; lane_type:string; crossing:string; architectural_style:string; building_condition:string; storefront_type:string; architectural_details?:string };
  type CrowdEntry     = { zone:string; density_level:string };
  type GreenEntry     = { zone:string; element:string; coverage:string };
  type AmenityEntry   = { zone:string; element:string; material_and_colour:string; presence:string };
  type TextEntry      = { text:string; zone:string; type:string };
  type SceneAnalysis  = { scene:string; lighting:LightingEntry[]; spatial_character:SpatialEntry[]; crowdedness:CrowdEntry[]; greenery:GreenEntry[]; street_amenities:AmenityEntry[]; visible_text:TextEntry[] };
  type BenchModel     = { key:string; displayName:string; org:string; brandColor:string; initials:string; modelId:string; latencyMs:number; sa:SceneAnalysis };

  async function _loadBenchData(): Promise<{ models: Record<string, BenchModel>; order: string[] }> {
    try {
      const resp = await fetch(`${api.map}/api/vlm/analysis-outputs`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const all: Record<string, any> = await resp.json();
      const models: Record<string, BenchModel> = {};
      const order: string[] = [];
      let idx = 0;
      for (const [slug, data] of Object.entries(all)) {
        if (!data || data.error || !data.scene_analysis) continue;
        const meta = data.metadata || {};
        const modelId: string = meta.model || slug;
        const derived = _deriveModelMeta(modelId, idx++);
        const sa = data.scene_analysis || {};
        models[slug] = {
          key: slug,
          displayName: derived.displayName,
          org: derived.org,
          brandColor: derived.brandColor,
          initials: derived.initials,
          modelId,
          latencyMs: meta.latency_ms || 0,
          sa: {
            scene: sa.scene || '',
            lighting: sa.lighting || [],
            spatial_character: sa.spatial_character || [],
            crowdedness: sa.crowdedness || [],
            greenery: sa.greenery || [],
            street_amenities: sa.street_amenities || [],
            visible_text: sa.visible_text || [],
          },
        };
        order.push(slug);
      }
      return { models, order };
    } catch (e) {
      console.warn('Failed to load VLM analysis data:', e);
      return { models: {}, order: [] };
    }
  }

  async function _openVLMCompareModal(): Promise<void> {
    const list = $('#vlm-compare-list') as HTMLElement;
    compareModal.classList.remove('hidden');
    list.innerHTML = '<div style="padding:20px;text-align:center;opacity:.5;">Loading benchmark data…</div>';
    list.style.cssText = 'display:flex;flex-direction:column;gap:0;';

    const { models: BENCH, order: ORDER } = await _loadBenchData();
    list.innerHTML = '';
    if (ORDER.length === 0) {
      list.innerHTML = '<div style="padding:20px;text-align:center;opacity:.5;color:#ff453a;">Could not load benchmark data — is the backend running?</div>';
      return;
    }
    const activeKeys = new Set<string>(ORDER);
    let selectedZone = 'center';
    const ZONES = ['far_left','left','center','right','far_right'] as const;
    const ZONE_LABEL: Record<string,string> = { far_left:'Far Left', left:'Left', center:'Center', right:'Right', far_right:'Far Right' };

    const _logoSvg = (m: BenchModel, size: number) => {
      const r = Math.round(size * 0.25);
      const fs = m.initials.length > 2 ? Math.round(size * 0.25) : Math.round(size * 0.42);
      const ty = Math.round(size * (m.initials.length > 2 ? 0.62 : 0.7));
      return `<svg viewBox="0 0 ${size} ${size}" fill="none"><rect width="${size}" height="${size}" rx="${r}" fill="${m.brandColor}" fill-opacity="0.18"/><text x="${size/2}" y="${ty}" text-anchor="middle" font-size="${fs}" font-weight="800" font-family="system-ui,sans-serif" fill="${m.brandColor}">${m.initials}</text></svg>`;
    };

    // ── Helpers ───────────────────────────────────────────────────────────────
    const _dash = () => `<span style="opacity:.22;font-size:11px;font-family:monospace;">—</span>`;
    const _badge = (t: string, color = '') =>
      `<span style="display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.09);font-family:monospace;white-space:nowrap;${color?`color:${color};`:''}">${escapeHtml(t)}</span>`;
    const _chip = (t: string, fg: string, bg: string) =>
      `<span style="display:inline-block;font-size:10px;padding:2px 7px;border-radius:4px;font-family:monospace;color:${fg};background:${bg};border:1px solid ${fg}44;white-space:nowrap;">${escapeHtml(t)}</span>`;

    const condColor: Record<string,string> = { bright:'#fbbf24', adequate:'#0a84ff', dim:'#8b8fa3', dark:'#6b7280' };
    const densColor: Record<string,string> = { empty:'#8b8fa3', sparse:'#fbbf24', moderate:'#f97316', dense:'#ff453a' };
    const covColor:  Record<string,string> = { sparse:'#86efac', moderate:'#30d158', dense:'#16a34a' };
    const passC:     Record<string,string> = { clear:'#30d158', caution:'#fbbf24', obstructed:'#f97316', blocked:'#ff453a' };

    const ZONE_SHORT: Record<string,string> = { far_left:'FL', left:'L', center:'C', right:'R', far_right:'FR' };

    // spatial_character — filtered to selected zone
    const _laneHtml = (m: BenchModel, sel: string) => {
      const items = m.sa.spatial_character.filter(sp => sp.zone === sel);
      if (!items.length) return _dash();
      return items.map(sp => {
        const pc = passC[sp.passability] ?? '#8b8fa3';
        return `<div style="display:flex;flex-wrap:wrap;gap:3px;align-items:center;padding:3px 0;">
          ${_badge(sp.lane_type,'var(--accent)')}${_badge(sp.width)}${_chip(sp.passability,pc,pc+'18')}${sp.crossing!=='none'?_badge('⊕ '+sp.crossing):''}
        </div>`;
      }).join('');
    };

    // lighting + crowdedness + greenery — filtered to selected zone
    const _envHtml = (m: BenchModel, sel: string) => {
      const rows: string[] = [];
      m.sa.lighting.filter(li => li.zone === sel).forEach(li => {
        const lc = condColor[li.condition] ?? '#8b8fa3';
        rows.push(`<div style="display:flex;align-items:center;gap:4px;padding:2px 0;">
          <span style="opacity:.38;width:36px;font-size:9px;flex-shrink:0;">light</span>${_chip(li.condition,lc,lc+'18')}${li.element?`<span style="opacity:.28;font-size:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:90px;" title="${escapeHtml(li.element)}">${escapeHtml(li.element)}</span>`:''}
        </div>`);
      });
      m.sa.crowdedness.filter(cr => cr.zone === sel).forEach(cr => {
        const dc = densColor[cr.density_level] ?? '#8b8fa3';
        rows.push(`<div style="display:flex;align-items:center;gap:4px;padding:2px 0;">
          <span style="opacity:.38;width:36px;font-size:9px;flex-shrink:0;">crowd</span>${_chip(cr.density_level,dc,dc+'18')}
        </div>`);
      });
      m.sa.greenery.filter(gr => gr.zone === sel).forEach(gr => {
        const gc = covColor[gr.coverage] ?? '#86efac';
        rows.push(`<div style="display:flex;align-items:center;gap:4px;padding:2px 0;">
          <span style="opacity:.38;width:36px;font-size:9px;flex-shrink:0;">green</span>${gr.coverage==='none'?_dash():_chip(gr.coverage,gc,gc+'18')}${gr.element?`<span style="opacity:.28;font-size:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:80px;" title="${escapeHtml(gr.element)}">${escapeHtml(gr.element)}</span>`:''}
        </div>`);
      });
      return rows.length ? `<div style="display:flex;flex-direction:column;gap:2px;">${rows.join('')}</div>` : _dash();
    };

    // architecture from spatial_character — filtered to selected zone
    const _builtHtml = (m: BenchModel, sel: string) => {
      const items = m.sa.spatial_character.filter(sp => sp.zone === sel);
      if (!items.length) return _dash();
      return items.map(sp => `<div style="padding:2px 0;">
        <div style="display:flex;flex-wrap:wrap;gap:2px;margin-bottom:2px;">
          ${_badge(sp.architectural_style,'var(--accent)')}${_badge(sp.building_condition)}${_badge(sp.storefront_type)}
        </div>
        ${sp.architectural_details?`<div style="opacity:.28;font-style:italic;font-size:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:140px;" title="${escapeHtml(sp.architectural_details)}">"${escapeHtml(sp.architectural_details)}"</div>`:''}
      </div>`).join('');
    };

    // street_amenities — filtered to selected zone
    const _amenitiesHtml = (m: BenchModel, sel: string) => {
      const items = m.sa.street_amenities.filter(a => a.zone === sel);
      if (!items.length) return _dash();
      return items.map(a => `<div style="display:flex;align-items:flex-start;gap:4px;padding:2px 0;">
        <div style="flex:1;min-width:0;">
          ${_badge(a.element,'var(--accent)')}
          ${a.material_and_colour?`<span style="display:block;opacity:.28;font-size:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(a.material_and_colour)}">${escapeHtml(a.material_and_colour)}</span>`:''}
        </div>
        <span style="opacity:.45;font-size:9px;flex-shrink:0;">${a.presence}</span>
      </div>`).join('');
    };

    // visible_text — filtered to selected zone
    const _textHtml = (m: BenchModel, sel: string) => {
      const items = m.sa.visible_text.filter(t => t.zone === sel);
      if (!items.length) return _dash();
      return items.map(t => `<div style="display:flex;align-items:center;gap:4px;padding:2px 0;">
        <span style="font-family:monospace;font-weight:600;font-size:11px;">"${escapeHtml(t.text)}"</span>${_badge(t.type)}
      </div>`).join('');
    };

    const SECTIONS: Array<{ icon:string; label:string; fn:(m:BenchModel,z:string)=>string }> = [
      { icon:'🛣️',  label:'spatial_character',  fn: _laneHtml      },
      { icon:'🏗️',  label:'architecture',        fn: _builtHtml     },
      { icon:'🌿',  label:'environment',          fn: _envHtml       },
      { icon:'🪑',  label:'street_amenities',     fn: _amenitiesHtml },
      { icon:'🪧',  label:'visible_text',         fn: _textHtml      },
    ];

    // ── Model pills ───────────────────────────────────────────────────────────
    const pillRow = document.createElement('div');
    pillRow.style.cssText = 'display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid rgba(255,255,255,0.06);';
    const pillsWrap = document.createElement('div');
    pillsWrap.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;flex:1;';
    ORDER.forEach(k => {
      const m = BENCH[k];
      if (!m) return;
      const pill = document.createElement('button');
      pill.className = 'vlm-zone-pill active';
      pill.dataset.key = k;
      pill.style.setProperty('--pc', m.brandColor);
      pill.innerHTML = `<span class="vzp-logo">${_logoSvg(m, 24)}</span><span class="vzp-name">${escapeHtml(m.displayName)}</span><span class="vzp-lat">${(m.latencyMs/1000).toFixed(1)}s</span>`;
      pill.addEventListener('click', () => {
        if (activeKeys.has(k) && activeKeys.size <= 1) return;
        if (activeKeys.has(k)) { activeKeys.delete(k); pill.classList.remove('active'); }
        else { activeKeys.add(k); pill.classList.add('active'); }
        _renderCols();
      });
      pillsWrap.appendChild(pill);
    });
    const ctrlRow = document.createElement('div');
    ctrlRow.style.cssText = 'display:flex;gap:6px;flex-shrink:0;';
    (['All','None'] as const).forEach(label => {
      const b = document.createElement('button'); b.className = 'btn ghost tiny'; b.textContent = label;
      b.addEventListener('click', () => {
        if (label === 'All') { ORDER.forEach(k => { activeKeys.add(k); pillsWrap.querySelector<HTMLElement>(`[data-key="${k}"]`)?.classList.add('active'); }); }
        else { ORDER.forEach(k => { activeKeys.delete(k); pillsWrap.querySelector<HTMLElement>(`[data-key="${k}"]`)?.classList.remove('active'); }); activeKeys.add(ORDER[0]); pillsWrap.querySelector<HTMLElement>(`[data-key="${ORDER[0]}"]`)?.classList.add('active'); }
        _renderCols();
      }); ctrlRow.appendChild(b);
    });
    pillRow.appendChild(pillsWrap); pillRow.appendChild(ctrlRow);

    // ── Two-pane layout: image left | comparison right (no outer scroll) ────────
    const mainArea = document.createElement('div');
    mainArea.style.cssText = 'display:flex;gap:12px;flex:1;min-height:0;overflow:hidden;height:calc(92vh - 175px);';

    // ── LEFT PANE: image + zone selector ─────────────────────────────────────
    const leftPane = document.createElement('div');
    leftPane.style.cssText = 'width:36%;flex-shrink:0;display:flex;flex-direction:column;gap:8px;min-height:0;overflow:hidden;';

    const imgWrap = document.createElement('div');
    imgWrap.style.cssText = 'position:relative;border-radius:10px;overflow:hidden;border:1px solid rgba(255,255,255,0.07);background:#000;flex:1;min-height:0;';
    const imgEl = document.createElement('img');
    imgEl.src = `${api.map}/api/vlm/barcelona-image`;
    imgEl.style.cssText = 'width:100%;height:100%;display:block;object-fit:cover;user-select:none;pointer-events:none;';
    imgEl.alt = 'Barcelona Streetview — Passeig de Gràcia';
    imgEl.onerror = () => { imgEl.style.opacity = '0.08'; };

    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:absolute;inset:0;display:flex;';
    const zoneEls: Record<string, HTMLElement> = {};
    ZONES.forEach(z => {
      const seg = document.createElement('div');
      seg.className = 'vlm-zone-seg' + (z === selectedZone ? ' active' : '');
      seg.dataset.zone = z;
      seg.innerHTML = `<span class="vlm-zone-lbl">${ZONE_LABEL[z]}</span>`;
      seg.addEventListener('click', () => {
        selectedZone = z;
        Object.values(zoneEls).forEach(el => el.classList.remove('active'));
        seg.classList.add('active');
        zoneTitle.textContent = `Zone: ${ZONE_LABEL[z]}`;
        _renderCols();
      });
      overlay.appendChild(seg);
      zoneEls[z] = seg;
    });
    imgWrap.appendChild(imgEl); imgWrap.appendChild(overlay);

    const zoneTitle = document.createElement('div');
    zoneTitle.style.cssText = 'font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;opacity:.4;flex-shrink:0;padding:2px 0 4px;';
    zoneTitle.textContent = `Zone: ${ZONE_LABEL[selectedZone]}`;

    leftPane.appendChild(imgWrap);
    leftPane.appendChild(zoneTitle);

    // ── RIGHT PANE: comparison columns (internal scroll) ──────────────────────
    const rightPane = document.createElement('div');
    rightPane.style.cssText = 'flex:1;overflow-y:auto;overflow-x:hidden;min-width:0;';

    const colsWrap = document.createElement('div');
    colsWrap.style.cssText = 'display:flex;gap:10px;align-items:flex-start;';
    rightPane.appendChild(colsWrap);

    mainArea.appendChild(leftPane);
    mainArea.appendChild(rightPane);
    list.appendChild(pillRow);
    list.appendChild(mainArea);

    // ── Column renderer ───────────────────────────────────────────────────────
    function _renderCols() {
      colsWrap.innerHTML = '';
      const active = ORDER.filter(k => activeKeys.has(k));
      if (!active.length) { colsWrap.innerHTML = '<div class="meta" style="opacity:.35;padding:20px 0;">No models selected</div>'; return; }
      active.forEach(k => {
        const m = BENCH[k];
        if (!m) return; // skip models without data
        const col = document.createElement('div');
        col.style.cssText = 'min-width:190px;flex:1;display:flex;flex-direction:column;gap:7px;animation:fadeSlideUp 0.2s ease;';
        // Header
        const spatialZones = new Set(m.sa.spatial_character.map(e => e.zone));
        const coverageBar = (['far_left','left','center','right','far_right'] as const).map(z =>
          `<span title="${z}" style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:14px;border-radius:3px;font-size:8px;font-family:monospace;font-weight:700;background:${spatialZones.has(z)?m.brandColor+'33':'rgba(255,255,255,0.04)'};color:${spatialZones.has(z)?m.brandColor:'rgba(255,255,255,0.18)'};border:1px solid ${spatialZones.has(z)?m.brandColor+'55':'rgba(255,255,255,0.06)'};">${ZONE_SHORT[z]}</span>`
        ).join('');
        const hdr = document.createElement('div');
        hdr.style.cssText = `border-radius:9px;padding:9px 12px;background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.07);border-left:3px solid ${m.brandColor};`;
        hdr.innerHTML = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;">
          <span style="width:22px;height:22px;display:inline-flex;flex-shrink:0;">${_logoSvg(m, 36)}</span>
          <span style="font-weight:700;font-size:12px;color:${m.brandColor};">${escapeHtml(m.displayName)}</span>
        </div>
        <div style="font-size:10px;opacity:.35;font-family:monospace;margin-bottom:6px;">${escapeHtml(m.org)} &middot; &#8987; ${(m.latencyMs/1000).toFixed(1)}s</div>
        <div style="display:flex;gap:3px;align-items:center;">${coverageBar}</div>`;
        col.appendChild(hdr);
        // Scene summary
        const scenePill = document.createElement('div');
        scenePill.style.cssText = 'font-size:10px;line-height:1.55;opacity:.55;padding:5px 2px;border-bottom:1px solid rgba(255,255,255,0.05);';
        scenePill.textContent = m.sa.scene;
        col.appendChild(scenePill);
        // Section cards
        SECTIONS.forEach(sec => {
          const card = document.createElement('div');
          card.style.cssText = 'border-radius:7px;border:1px solid rgba(255,255,255,0.06);background:rgba(255,255,255,0.018);overflow:hidden;';
          card.innerHTML = `<div style="padding:4px 9px;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.09em;opacity:.32;border-bottom:1px solid rgba(255,255,255,0.04);display:flex;align-items:center;gap:4px;">${sec.icon} ${sec.label}</div>
            <div style="padding:7px 9px;">${sec.fn(m, selectedZone)}</div>`;
          col.appendChild(card);
        });
        colsWrap.appendChild(col);
      });
    }

    _renderCols();
  }

  // Compare button — opens model comparison overlay
  const compareModal = $('#vlm-compare-modal') as HTMLElement;
  $('#p1-vlm-compare')?.addEventListener('click', () => _openVLMCompareModal());
  $('#btn-benchmark')?.addEventListener('click', () => _openVLMCompareModal());
  $('#vlm-compare-modal-close')?.addEventListener('click', () => compareModal.classList.add('hidden'));
  compareModal.addEventListener('click', (e) => { if (e.target === compareModal) compareModal.classList.add('hidden'); });

  // Compare button inside the analyse modal — reuses the same overlay
  $('#p1-vlm-compare-modal-btn')?.addEventListener('click', () => _openVLMCompareModal());

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

  // Analyse button (run button inside the modal)
  let _vlmPoller: number | null = null;
  $('#p1-vlm-analyze')?.addEventListener('click', async () => {
    if (_analysePoints.size === 0) return;
    // Respect re-analyse toggle: if unchecked, skip already-analysed images
    const reanalyse = ($('#p1-reanalyse-toggle') as HTMLInputElement | null)?.checked ?? false;
    let pointsToSend = [..._analysePoints];
    if (!reanalyse) {
      const svSrc = map?.getSource('sv') as { _data?: { features: { properties: Record<string, unknown> }[] } } | undefined;
      const analysed = new Set(
        (svSrc?._data?.features ?? [])
          .filter((f) => f.properties.schema && f.properties.schema !== 'image_only')
          .map((f) => `${f.properties.lat}_${f.properties.lon}`)
      );
      pointsToSend = pointsToSend.filter((k) => !analysed.has(k));
    }
    if (pointsToSend.length === 0) { toast('All selected images are already analysed. Enable Re-analyse to reprocess.', 'warning'); return; }
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
        { images: pointsToSend, params: state.vlm },
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
              (map?.getSource('sv') as { setData: (d: unknown) => void } | undefined)?.setData(fresh);
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

  // Sync Results → DB: re-import all output/results/*.json into streetview_perception table
  ($('#p1-reimport-perception') as HTMLButtonElement | null)?.addEventListener('click', async () => {
    const btn = $('#p1-reimport-perception') as HTMLButtonElement;
    const statusEl = $('#p1-reimport-status') as HTMLElement | null;
    btn.disabled = true;
    btn.textContent = 'Importing…';
    if (statusEl) statusEl.textContent = '';
    try {
      const res = await api.postJSON<{ ok: boolean; records_in_table?: number; error?: string }>(
        api.map, '/api/streetview/reimport-perception', {}
      );
      if (!res.ok) {
        const errMsg = res.error ?? 'Unknown error — check backend logs';
        if (statusEl) statusEl.textContent = `Failed: ${errMsg.split('\n')[0]}`;
        toast(`Import failed: ${errMsg.split('\n')[0]}`, 'danger');
      } else {
        if (statusEl) statusEl.textContent = `${res.records_in_table} records in DB`;
        toast(`Perception import complete — ${res.records_in_table} records.`, 'success');
      }
    } catch (e) {
      if (statusEl) statusEl.textContent = 'Import failed';
      toast(`Perception import failed: ${e instanceof Error ? e.message : e}`, 'danger');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Sync Results → DB';
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
  const hfRowModal = $('#p1-vlm-hf-row-modal') as HTMLElement | null;
  const hfInputModal = $('#p1-vlm-hf-model-modal') as HTMLInputElement | null;
  function _syncHfRowModal(): void {
    if (!hfRowModal) return;
    hfRowModal.style.display = sel!.value === 'custom-hf' ? 'flex' : 'none';
  }
  // Populate once; after that just sync the selected value
  if (sel.options.length === 0) {
    sel.innerHTML = VLM_CARDS
      .filter((v) => v.id !== 'custom-hf')
      .map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`)
      .join('') + `<option value="custom-hf">Custom HuggingFace</option>`;
    if (hfInputModal) {
      hfInputModal.value = state.vlm.hfModel || '';
      hfInputModal.addEventListener('input', () => {
        state.vlm.hfModel = hfInputModal.value.trim();
        const mainHf = $('#p1-vlm-hf-model') as HTMLInputElement | null;
        if (mainHf) mainHf.value = hfInputModal.value;
        saveState();
      });
    }
    sel.addEventListener('change', () => {
      state.vlm.provider = sel.value;
      const mainSel = $('#p1-vlm-model-select') as HTMLSelectElement | null;
      if (mainSel) mainSel.value = sel.value;
      _syncHfRowModal();
      saveState();
    });
    appleSelect(sel);
    const wrap = sel.closest<HTMLElement>('.asl-wrap');
    if (wrap) { wrap.style.flex = '1'; wrap.style.width = 'auto'; }
  }
  sel.value = state.vlm.provider;
  _syncHfRowModal();
}

/* =====================================================================
   PANEL 3 — Personality with Three.js archetype figures (GLB models)
   ===================================================================== */
interface ArchRenderer {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  group: THREE.Group;
  mixer?: THREE.AnimationMixer;
  active: boolean; // set to false before dispose to cancel in-flight loads
  isStatic?: boolean; // skip idle bob and mixer updates
}
let renderers: ArchRenderer[] = [];
let introRenderers: ArchRenderer[] = [];
let charViewer: ArchRenderer | null = null;
let p4CharViewer: ArchRenderer | null = null;
let raf: number | null = null;
let p3Bound = false;

function _disposeRenderer(r: ArchRenderer | null): void {
  if (!r) return;
  r.active = false;
  try { r.renderer.dispose(); } catch { /* ignore */ }
}



// Disable Three.js global cache permanently for character viewers.
// The cache is keyed by URL: the first GLB to load wins and every subsequent
// model with the same internal texture names gets the wrong cached blob.
THREE.Cache.enabled = false;

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
  const rect = canvas.getBoundingClientRect();
  const w = rect.width  || canvas.clientWidth  || 152;
  const h = rect.height || canvas.clientHeight || 192;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w, h, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.autoClear = true;

  const scene = new THREE.Scene();
  const eyeY = focusHead ? 1.59 : 0.9;
  const camZ = focusHead ? 1.1  : 2.8;
  const fov  = focusHead ? 28   : 38;
  const camera = new THREE.PerspectiveCamera(fov, w / Math.max(h, 1), 0.01, 200);
  camera.position.set(0, eyeY, camZ);
  camera.lookAt(0, eyeY, 0);

  scene.add(new THREE.AmbientLight(0xffffff, 0.8));
  const key  = new THREE.DirectionalLight(0xffffff, 1.4); key.position.set(2, 4, 3);   scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.5); fill.position.set(-2, 1, -1); scene.add(fill);
  const rim  = new THREE.PointLight(new THREE.Color(colorHex), 1.4, 15); rim.position.set(-1.5, 2, -2); scene.add(rim);

  const group = new THREE.Group();
  scene.add(group);

  let dragActive = false, dragStartX = 0, dragStartRotY = 0;
  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointerdown', (e) => {
    dragActive = true; dragStartX = e.clientX; dragStartRotY = group.rotation.y;
    canvas.setPointerCapture(e.pointerId); canvas.style.cursor = 'grabbing';
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

  const ar: ArchRenderer = { renderer, scene, camera, group, active: true };
  card.classList.add('arch-loading');

  // Deep-clone a material AND all its texture slots so each renderer owns
  // independent GPU objects. m.clone() shares texture references — if the
  // source renderer is disposed, its texture GPU state can corrupt other
  // renderers that reference the same THREE.Texture object.
  const cloneMat = (m: THREE.Material): THREE.Material => {
    const c = m.clone();
    const texSlots = ['map', 'normalMap', 'roughnessMap', 'metalnessMap',
                      'emissiveMap', 'aoMap', 'lightMap', 'specularMap'] as const;
    for (const slot of texSlots) {
      const tex = (c as any)[slot] as THREE.Texture | null | undefined;
      if (tex) {
        const t = tex.clone();
        t.needsUpdate = true;
        (c as any)[slot] = t;
      }
    }
    return c;
  };

  // Stage the parsed model here; only add to the scene in onLoad, after every
  // texture ImageBitmap is fully decoded. Adding to the scene earlier lets the
  // render loop render with un-decoded (blank) textures, which marks them as
  // "uploaded" in the renderer's properties map and prevents proper re-upload.
  let stagedModel: THREE.Object3D | null = null;
  let stagedMixer: THREE.AnimationMixer | null = null;

  const manager = new THREE.LoadingManager();

  // Cache-bust the URL so Three.js's FileLoader does NOT deduplicate concurrent
  // requests for the same GLB (e.g. thumb card + charViewer for the same archetype).
  // Without this, both loaders share one ArrayBuffer response, which can lead to
  // entangled texture state even when THREE.Cache.enabled = false.
  const uniqueUrl = `${glbPath}?_=${Date.now()}_${Math.random().toString(36).slice(2)}`;

  new GLTFLoader(manager).load(uniqueUrl, (gltf) => {
    if (!ar.active) return; // renderer was disposed before this completed

    const model = gltf.scene;
    model.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.frustumCulled = false;
      if (Array.isArray(mesh.material)) {
        mesh.material = mesh.material.map(cloneMat);
      } else if (mesh.material) {
        mesh.material = cloneMat(mesh.material as THREE.Material);
      }
    });

    if (gltf.animations.length > 0) {
      stagedMixer = new THREE.AnimationMixer(model);
      stagedMixer.clipAction(gltf.animations[0]).play();
    }

    stagedModel = model; // held out of the scene until onLoad
  }, undefined, (err) => {
    console.warn('GLB load error', glbPath, err);
    card.classList.remove('arch-loading');
  });

  // onLoad fires once every asset tracked by this manager is fully decoded.
  // Only now is it safe to add the model to the scene and render with real textures.
  manager.onLoad = () => {
    if (!ar.active || !stagedModel) return;

    if (stagedMixer) ar.mixer = stagedMixer;
    group.add(stagedModel);

    // Force a fresh GPU upload for every texture — the clones already set
    // needsUpdate = true, but re-assert here in case anything reset the flag
    // between the GLTF callback and this point.
    stagedModel.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh) return;
      const mats = (Array.isArray(mesh.material) ? mesh.material : [mesh.material]) as THREE.Material[];
      for (const mat of mats) {
        for (const slot of ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap']) {
          const tex = (mat as any)[slot] as THREE.Texture | null | undefined;
          if (tex) tex.needsUpdate = true;
        }
      }
    });

    _syncRendererSize(renderer, camera);
    renderer.render(scene, camera); // bind skeleton + upload all textures to GPU

    const model = group.children[0] as THREE.Object3D | undefined;
    if (model) {
      const box    = new THREE.Box3().setFromObject(group);
      const size   = new THREE.Vector3(); box.getSize(size);
      const center = new THREE.Vector3(); box.getCenter(center);
      const scale  = 1.8 / Math.max(size.y, 0.001);
      model.scale.setScalar(scale);
      model.position.set(-center.x * scale, -box.min.y * scale, -center.z * scale);
      renderer.render(scene, camera); // final correctly-framed render
    }

    card.classList.remove('arch-loading');
  };

  return ar;
}

function buildStaticGroupFigure(canvas: HTMLCanvasElement, card: HTMLElement, glbPath: string): ArchRenderer {
  const w = canvas.clientWidth  || 620;
  const h = canvas.clientHeight || 310;
  const camZ = 6.0;
  const eyeY = 1.0;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w, h, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.autoClear = true;

  const scene  = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(52, w / Math.max(h, 1), 0.01, 200);
  camera.position.set(0, eyeY, camZ);
  camera.lookAt(0, eyeY, 0);

  scene.add(new THREE.AmbientLight(0xffffff, 1.6));
  const key  = new THREE.DirectionalLight(0xffffff, 2.6); key.position.set(3, 5, 4);   scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 1.1); fill.position.set(-3, 2, -2); scene.add(fill);

  const group = new THREE.Group();
  scene.add(group);

  const ar: ArchRenderer = { renderer, scene, camera, group, active: true, isStatic: true };
  card.classList.add('arch-loading');

  const cloneMat = (m: THREE.Material): THREE.Material => {
    const c = m.clone();
    for (const slot of ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap', 'lightMap', 'specularMap'] as const) {
      const tex = (c as any)[slot] as THREE.Texture | null | undefined;
      if (tex) { const t = tex.clone(); t.needsUpdate = true; (c as any)[slot] = t; }
    }
    return c;
  };

  let stagedModel: THREE.Object3D | null = null;
  const manager    = new THREE.LoadingManager();
  const uniqueUrl  = `${glbPath}?_=${Date.now()}_${Math.random().toString(36).slice(2)}`;

  new GLTFLoader(manager).load(uniqueUrl, (gltf) => {
    if (!ar.active) return;
    const model = gltf.scene;
    model.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.frustumCulled = false;
      if (Array.isArray(mesh.material)) {
        mesh.material = mesh.material.map(cloneMat);
      } else if (mesh.material) {
        mesh.material = cloneMat(mesh.material as THREE.Material);
      }
    });
    stagedModel = model;
  }, undefined, (err) => {
    console.warn('Group GLB load error', glbPath, err);
    card.classList.remove('arch-loading');
  });

  manager.onLoad = () => {
    if (!ar.active || !stagedModel) return;
    group.add(stagedModel);

    stagedModel.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh) return;
      const mats = (Array.isArray(mesh.material) ? mesh.material : [mesh.material]) as THREE.Material[];
      for (const mat of mats) {
        for (const slot of ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap']) {
          const tex = (mat as any)[slot] as THREE.Texture | null | undefined;
          if (tex) tex.needsUpdate = true;
        }
      }
    });

    _syncRendererSize(renderer, camera);
    renderer.render(scene, camera);

    // Auto-fit: scale so the group fills ~82% of the view (whichever axis binds)
    const model = group.children[0] as THREE.Object3D | undefined;
    if (model) {
      const box    = new THREE.Box3().setFromObject(group);
      const size   = new THREE.Vector3(); box.getSize(size);
      const center = new THREE.Vector3(); box.getCenter(center);
      const aspect    = canvas.clientWidth / Math.max(canvas.clientHeight, 1);
      const vFovRad   = (camera.fov * Math.PI) / 180;
      const viewH     = 2 * Math.tan(vFovRad / 2) * camZ * 0.82;
      const viewW     = viewH * aspect * 0.82;
      const scale     = Math.min(viewH / Math.max(size.y, 0.001), viewW / Math.max(size.x, 0.001));
      model.scale.setScalar(scale);
      model.position.set(-center.x * scale, -box.min.y * scale, -center.z * scale);
      // Point camera at the model's world-space vertical center so nothing is clipped
      const worldCenterY = (center.y - box.min.y) * scale;
      camera.position.setY(worldCenterY);
      camera.lookAt(0, worldCenterY, 0);
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
    }

    card.classList.remove('arch-loading');
  };

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
    const allR = [
      ...renderers,
      ...introRenderers,
      ...(charViewer   ? [charViewer]   : []),
      ...(p4CharViewer ? [p4CharViewer] : []),
    ];
    for (const r of allR) {
      if (!r.isStatic) {
        if (r.mixer) {
          r.mixer.update(delta);
        } else if (r.group.children.length > 0) {
          r.group.position.y = Math.sin(elapsed * 1.1) * 0.018;
          r.group.rotation.z = Math.sin(elapsed * 0.7) * 0.006;
        }
      }
      _syncRendererSize(r.renderer, r.camera);
      r.renderer.render(r.scene, r.camera);
    }
  };
  raf = requestAnimationFrame(tick);
}
function stopRenderLoop(): void { if (raf !== null) { cancelAnimationFrame(raf); raf = null; } }

function initIntro(): void {
  const video    = $('#intro-video') as HTMLVideoElement | null;
  if (!video) return;

  // ── Timeline: each scene's in/out window (seconds) ───────────────────
  const INTRO_SCENES: { id: string; inTime: number; outTime: number }[] = [
    { id: 'scene-aerial',       inTime:  0.0, outTime:  6.0 },  //  6s — Aerial
    { id: 'scene-paths',        inTime:  6.0, outTime: 10.0 },  //  4s — Paths
    { id: 'scene-driven',       inTime: 10.0, outTime: 14.0 },  //  4s — Driven
    { id: 'scene-problem',      inTime: 14.0, outTime: 18.0 },  //  4s — Street/Problem
    { id: 'scene-agents-enter', inTime: 18.0, outTime: 23.0 },  //  5s — Agents Enter
    { id: 'scene-see',          inTime: 23.0, outTime: 26.0 },  //  3s — SEE
    { id: 'scene-know',         inTime: 26.0, outTime: 30.0 },  //  4s — KNOW
    { id: 'scene-understand',   inTime: 30.0, outTime: 35.0 },  //  5s — UNDERSTAND
    { id: 'scene-decide',       inTime: 35.0, outTime: 40.0 },  //  5s — DECIDE
    { id: 'scene-synthesis',    inTime: 40.0, outTime: 44.0 },  //  4s — Synthesis
    { id: 'scene-cta',          inTime: 44.0, outTime: Infinity }, //  5s — CTA
  ];

  const skipBtn  = $('#intro-skip') as HTMLElement | null;
  const startBtn = $('#start-btn')  as HTMLElement | null;

  video.loop = false;
  video.currentTime = 0;

  // Guard: once skip/ended fires, timeupdate must not re-show scenes
  let ctaLocked = false;

  function revealCta(): void {
    ctaLocked = true;
    INTRO_SCENES.forEach(s => ($(`#${s.id}`) as HTMLElement | null)?.classList.remove('active'));
    ($('#scene-cta') as HTMLElement | null)?.classList.add('active');
    startBtn?.classList.remove('hidden');
    startBtn?.classList.add('visible');
    skipBtn?.classList.add('hidden');
  }

  // Drive scene text from video position on every timeupdate
  video.addEventListener('timeupdate', () => {
    if (ctaLocked) return;
    const t = video.currentTime;
    for (let i = 0; i < INTRO_SCENES.length; i++) {
      const s = INTRO_SCENES[i];
      ($(`#${s.id}`) as HTMLElement | null)?.classList.toggle('active', t >= s.inTime && t < s.outTime);
    }
  });

  // Video ended naturally — show CTA
  video.addEventListener('ended', revealCta);

  // Skip button — jump to end, show CTA
  skipBtn?.addEventListener('click', () => {
    video.pause();
    revealCta();
    video.currentTime = video.duration > 0 ? video.duration - 0.1 : 41.0;
  });

  // Auto-play
  video.play().catch(() => {});
}

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

function _pickModelForProfile(prof?: { age?: string | null; gender?: string | null }): string | undefined {
  const age    = prof?.age    ?? '';
  const gender = prof?.gender ?? '';
  const ageKey =
    age === 'young'  ? '18to30' :
    age === 'adult'  ? '30to50' :
    age === 'senior' ? '50to70' : null;
  if (!ageKey) return undefined;
  const genderKey = gender === 'female' ? 'Female' : 'Male';
  return `${ageKey}_${genderKey}.glb`;
}

function glbUrl(arch: string, customModel?: string): string {
  const base = (window.location.protocol === 'http:' || window.location.protocol === 'https:')
    ? window.location.origin : 'http://localhost:8091';
  if (customModel) {
    return `${base}/reference/agents_glb/General/${customModel}`;
  }
  if (ARCHETYPE_GLB[arch]) {
    return `${base}/${ARCHETYPE_GLB[arch]}`;
  }
  // Custom archetype: auto-select Pick model from age + gender profile
  const autoModel = _pickModelForProfile(state.archetypes?.[arch]?.profile);
  if (autoModel) return `${base}/assets/agents/General/${autoModel}`;
  return `${base}/${GENERIC_GLB}`;
}

function renderArchetypeCards(thumbMode = false): void {
  const container = thumbMode
    ? ($('#p3-thumbs') as HTMLElement)
    : ($('#p3-bar')   as HTMLElement);
  container.innerHTML = '';
  renderers.forEach(_disposeRenderer);
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
  addTile.innerHTML = `<div class="plus">+</div><div class="arch-name">Create</div>${thumbMode ? '' : '<div class="arch-tag">new archetype</div>'}${thumbMode ? '' : `
    <svg class="create-annotation" xmlns="http://www.w3.org/2000/svg" width="225" height="300" viewBox="0 0 225 300">
      <path class="ann-stroke" d="M 235 148 C 236 59 182 -11 113 -9 C 44 -7 -10 61 -9 150 C -8 239 44 310 113 308 C 182 306 235 241 235 148"/>
      <g transform="rotate(-8, 196, 32)">
        <path class="ann-stroke" d="M 196 32 C 218 -5 240 -36 262 -62"/>
        <path class="ann-stroke" d="M 262 -62 L 250 -51"/>
        <path class="ann-stroke" d="M 262 -62 L 253 -70"/>
        <text class="ann-text" x="266" y="-68">Create</text>
      </g>
    </svg>`}`;
  addTile.addEventListener('click', () => {
    const modal = $('#p3-create-modal') as HTMLElement;
    const nameInp = $('#p3-create-name') as HTMLInputElement;
    const ageInp = $('#p3-create-age') as HTMLSelectElement;
    const genderInp = $('#p3-create-gender') as HTMLSelectElement;
    nameInp.value = 'custom_' + Math.floor(Math.random() * 999);
    ageInp.value = '';
    genderInp.value = '';
    modal.classList.remove('hidden');
    nameInp.focus();
    nameInp.select();
  });
  container.appendChild(addTile);
}

function openArchetypeEditor(arch: string): void {
  state.selectedArchetype = arch; saveState();
  document.body.classList.remove('p3-card-view');

  // Switch from card-bar to 3-column layout
  ($('#p3-bar')    as HTMLElement).classList.add('hidden');
  ($('#p3-layout') as HTMLElement).classList.remove('hidden');

  // Thumb strip: full rebuild only on first entry; otherwise just flip data-selected
  const thumbsEl = $('#p3-thumbs') as HTMLElement;
  if (thumbsEl.querySelectorAll('.archetype-card[data-archetype]').length === 0) {
    renderArchetypeCards(true);
  } else {
    thumbsEl.querySelectorAll<HTMLElement>('.archetype-card[data-archetype]').forEach((c) => {
      c.setAttribute('data-selected', c.dataset.archetype === arch ? 'true' : 'false');
    });
  }
  ($('#p3-editor-title') as HTMLElement).textContent =
    `${arch[0].toUpperCase()}${arch.slice(1)} — Profile & Daily Plan`;
  renderProfileEditor(arch);

  requestAnimationFrame(() => {
    let charCanvas = $('#p3-char-canvas') as HTMLCanvasElement;
    const panel = $('#p3-char-viewer') as HTMLElement;
    _disposeRenderer(charViewer);
    charViewer = null;
    const parent = charCanvas.parentNode;
    if (parent) {
      const newCanvas = document.createElement('canvas');
      newCanvas.id = 'p3-char-canvas';
      parent.replaceChild(newCanvas, charCanvas);
      charCanvas = newCanvas;
    }
    const W = panel.clientWidth  || 300;
    const H = panel.clientHeight || 600;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    charCanvas.width  = W * dpr;
    charCanvas.height = H * dpr;
    charViewer = buildArchetypeFigure(
      charCanvas,
      ARCHETYPE_COLORS[arch] || '#5e5ce6',
      glbUrl(arch),
      panel,
    );
    charViewer.renderer.setClearColor(0x000000, 0);
    charViewer.camera.fov = 38;
    charViewer.camera.aspect = W / H;
    charViewer.camera.position.set(0, 0.9, 3.3);
    charViewer.camera.lookAt(0, 0.9, 0);
    charViewer.camera.updateProjectionMatrix();
  });
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
      const tod = phase.time_of_day || 'any';
      card.className = 'phase-card tod-' + tod;
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
        phase.time_of_day = (e.target as HTMLSelectElement).value;
        card.className = 'phase-card tod-' + phase.time_of_day;
        saveState();
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
  _disposeRenderer(charViewer); charViewer = null;
  state.selectedArchetype = null; saveState();
  ($('#p3-layout') as HTMLElement).classList.add('hidden');
  ($('#p3-bar')    as HTMLElement).classList.remove('hidden');
  document.body.classList.add('p3-card-view');
  renderArchetypeCards(false);
}

async function panel3Enter(): Promise<void> {
  // Always start in unselected (big card bar) state
  ($('#p3-layout') as HTMLElement).classList.add('hidden');
  ($('#p3-bar')    as HTMLElement).classList.remove('hidden');
  document.body.classList.add('p3-card-view');
  state.selectedArchetype = null; saveState();
  if (!state.archetypes || !Object.keys(state.archetypes).some((k) => k !== '_comment')) await loadArchetypes();
  renderArchetypeCards(false); if (raf === null) startRenderLoop();
  if (p3Bound) return; p3Bound = true;
  $('#p3-editor-close')!.addEventListener('click', collapseToCardBar);
  $('#p3-save')!.addEventListener('click', async () => {
    try {
      const res = await api.postJSON<{ error?: string; archetypes?: string[] }>(
        api.lab, '/api/profiles', { profiles: state.archetypes });
      if (res.error) { toast(res.error, 'danger'); return; }
      const statusEl = $('#p3-status');
      if (statusEl) statusEl.textContent = `Saved to plans.local.json — ${(res.archetypes || []).length} archetypes.`;
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
    toast('Reloaded saved profiles.', 'success');
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

  // ── Create archetype modal ───────────────────────────
  const crModal = $('#p3-create-modal') as HTMLElement;
  const crName  = $('#p3-create-name')  as HTMLInputElement;
  const crAge   = $('#p3-create-age')   as HTMLSelectElement;
  const crGender = $('#p3-create-gender') as HTMLSelectElement;
  $('#p3-create-close')!.addEventListener('click', () => crModal.classList.add('hidden'));
  crModal.addEventListener('click', (e) => { if (e.target === crModal) crModal.classList.add('hidden'); });
  $('#p3-create-submit')!.addEventListener('click', () => {
    const id = crName.value.trim();
    if (!id || !state.archetypes) return;
    if (state.archetypes[id]) { toast(`Archetype "${id}" already exists.`, 'warning'); return; }
    const age = crAge.value || null;
    const gender = crGender.value || null;
    state.archetypes[id] = {
      profile: { interests: [], pace: 'moderate', curiosity: 'moderate', social: 'moderate', description: '',
        age, gender },
      daily_plan: [],
    };
    crModal.classList.add('hidden');
    const mode = ($('#p3-layout') as HTMLElement).classList.contains('hidden') ? false : true;
    saveState(); renderArchetypeCards(mode);
    openArchetypeEditor(id);
  });
}

/* =====================================================================
   LLM ENGINE — shared by Panels 4 and 5
   ===================================================================== */
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
  document.body.appendChild(menu);  // portal: outside backdrop-filter ancestors

  function buildMenu(): void {
    const isTod = el.classList.contains('phase-tod-select');
    menu.className = 'asl-menu hidden' + (isTod ? ' tod-menu' : '');
    menu.innerHTML = '';
    let seenCustom = false;
    let hfOpt: HTMLOptionElement | null = null;
    Array.from(el.options).forEach((opt) => {
      // custom-hf gets a special "+" footer button instead of a regular item
      if (opt.value === 'custom-hf') { hfOpt = opt; return; }

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

      if (isTod) {
        item.dataset['tod'] = opt.value;
        const bar = document.createElement('span');
        bar.className = 'asl-bar';
        item.appendChild(bar);
      }

      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        el.value = opt.value;
        el.dispatchEvent(new Event('change', { bubbles: true }));
        syncDisplay();
        close();
      });
      menu.appendChild(item);
    });

    // "+" footer for custom HuggingFace option
    if (hfOpt) {
      const sep = document.createElement('div');
      sep.className = 'asl-sep';
      menu.appendChild(sep);
      const addBtn = document.createElement('div');
      addBtn.className = 'asl-item asl-add-hf' + (el.value === 'custom-hf' ? ' sel' : '');
      addBtn.setAttribute('role', 'option');
      addBtn.dataset['v'] = 'custom-hf';
      const ck2 = document.createElement('span');
      ck2.className = 'asl-check';
      ck2.textContent = '✓';
      const tx2 = document.createElement('span');
      tx2.textContent = 'Custom HuggingFace';
      const plusIcon = document.createElement('span');
      plusIcon.className = 'asl-plus-icon';
      plusIcon.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`;
      addBtn.appendChild(ck2);
      addBtn.appendChild(tx2);
      addBtn.appendChild(plusIcon);
      addBtn.addEventListener('mousedown', (e) => {
        e.preventDefault();
        el.value = 'custom-hf';
        el.dispatchEvent(new Event('change', { bubbles: true }));
        syncDisplay();
        close();
      });
      menu.appendChild(addBtn);
    }
  }

  function syncDisplay(): void {
    const opt = el.options[el.selectedIndex];
    valSpan.textContent = opt ? opt.text.replace(' — Custom', '') : '—';
    menu.querySelectorAll<HTMLElement>('.asl-item').forEach((item) =>
      item.classList.toggle('sel', item.dataset['v'] === el.value));
  }

  function open(): void {
    buildMenu();
    const rect = btn.getBoundingClientRect();
    menu.style.top   = `${rect.bottom + 5}px`;
    menu.style.left  = `${rect.left}px`;
    menu.style.width = `${rect.width}px`;
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
    if (!wrap.contains(e.target as Node) && !menu.contains(e.target as Node)) close();
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
let compareModalStateRef: LLMSelection | null = null;

let _cmpBenchInterval: ReturnType<typeof setInterval> | null = null;

function renderCompareModal(stateRef: LLMSelection, onPick: (id: string) => void): void {
  const body = $('#llm-compare-body') as HTMLElement | null;
  if (!body) return;
  if (_cmpBenchInterval) { clearInterval(_cmpBenchInterval); _cmpBenchInterval = null; }

  type ProviderEntry = { id: string; name: string; scores: ABMScore; custom?: boolean };

  // 3 top EQ-Bench v2 frontier models from the web leaderboard
  // + 3 models benchmarked in 02_llm_provider_comparison.ipynb
  const entries: ProviderEntry[] = [
    { id: 'claude-3-5-sonnet', name: 'Claude 3.5 Sonnet', scores: { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0, eqbench: 86.36 } },
    { id: 'gpt-4-turbo',       name: 'GPT-4 Turbo',       scores: { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0, eqbench: 86.35 } },
    { id: 'gpt-4-1106',        name: 'GPT-4 (1106)',      scores: { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0, eqbench: 86.05 } },
    { id: 'deepseek-v4-fast',  name: 'DeepSeek V4 Fast',  scores: { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0, eqbench: 82.57 } },
    { id: 'ollama-llama-3-1',  name: 'Ollama Llama 3.1',  scores: { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0, eqbench: 58.80 } },
    { id: 'ollama-qwen-2-5',   name: 'Ollama Qwen 2.5-Coder 3B', scores: { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0, eqbench: 55.10 } },
  ];
  function colorFor(id: string, idx: number): string {
    return PROVIDER_COLORS[id] || CUSTOM_COLORS[idx % CUSTOM_COLORS.length];
  }

  function drawBarChart(canvas: HTMLCanvasElement): void {
    const w = canvas.width, h = canvas.height;
    const ctx = canvas.getContext('2d')!;
    ctx.clearRect(0, 0, w, h);

    const padLeft = 56, padRight = 24, padTop = 32, padBottom = 80;
    const plotW = w - padLeft - padRight;
    const plotH = h - padTop - padBottom;
    const maxScore = 100;

    // Horizontal grid lines and y-axis labels (0-100)
    ctx.font = '11px system-ui,-apple-system,sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let i = 0; i <= 5; i++) {
      const val = i * 20;
      const y = padTop + plotH - (val / maxScore) * plotH;
      ctx.strokeStyle = 'rgba(255,255,255,0.08)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padLeft, y); ctx.lineTo(padLeft + plotW, y); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,0.5)';
      ctx.fillText(`${val}`, padLeft - 8, y);
    }

    // Axis lines
    ctx.strokeStyle = 'rgba(255,255,255,0.2)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padLeft, padTop);
    ctx.lineTo(padLeft, padTop + plotH);
    ctx.lineTo(padLeft + plotW, padTop + plotH);
    ctx.stroke();

    // Bars
    const barGap = 12;
    const barW = (plotW - (entries.length + 1) * barGap) / entries.length;
    entries.forEach((entry, i) => {
      const score = entry.scores.eqbench;
      const x = padLeft + barGap + i * (barW + barGap);
      const barH = (score / maxScore) * plotH;
      const y = padTop + plotH - barH;
      const color = colorFor(entry.id, i);

      ctx.fillStyle = color;
      ctx.fillRect(x, y, barW, barH);
      ctx.strokeStyle = 'rgba(255,255,255,0.2)';
      ctx.lineWidth = 1;
      ctx.strokeRect(x, y, barW, barH);

      // Score label on top of bar
      ctx.fillStyle = 'rgba(255,255,255,0.85)';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(`${score.toFixed(1)}`, x + barW / 2, y - 4);

      // Model name below x-axis (rotated for readability)
      ctx.save();
      ctx.translate(x + barW / 2, padTop + plotH + 14);
      ctx.rotate(-Math.PI / 6);
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillStyle = 'rgba(255,255,255,0.7)';
      ctx.fillText(entry.name, 0, 0);
      ctx.restore();
    });
  }

  body.innerHTML = `
    <div class="cmp-chart-header">
      <span class="cmp-chart-tag">EQ Bench V2</span>
      <h3 class="cmp-chart-title">Emotional Intelligence Benchmark</h3>
      <p class="cmp-chart-subtitle">Scores from the EQ-Bench v2 leaderboard + local notebook runs</p>
    </div>
    <div class="cmp-radar-wrap"><canvas id="cmp-radar" width="500" height="420"></canvas></div>
    <div class="cmp-bench-card">
      <div class="cmp-bench-header">
        <span class="cmp-bench-title">Add your own model</span>
        <span class="cmp-bench-subtitle">Run EQ-Bench v2 on Ollama or HuggingFace</span>
      </div>
      <div class="cmp-bench-form">
        <div class="cmp-bench-field">
          <label for="cmp-bench-source">Source</label>
          <select class="input" id="cmp-bench-source">
            <option value="ollama">Ollama (local)</option>
            <option value="huggingface">HuggingFace</option>
          </select>
        </div>
        <div class="cmp-bench-field" id="cmp-bench-model-wrap">
          <label for="cmp-bench-model">Model</label>
          <select class="input" id="cmp-bench-model"><option>Loading…</option></select>
        </div>
        <div class="cmp-bench-field hidden" id="cmp-bench-hf-wrap">
          <label for="cmp-bench-hf-model">Model ID</label>
          <input type="text" class="input" id="cmp-bench-hf-model" placeholder="e.g. meta-llama/Llama-3.1-8B-Instruct">
        </div>
        <div class="cmp-bench-field hidden" id="cmp-bench-key-wrap">
          <label for="cmp-bench-key">HF Token</label>
          <input type="password" class="input" id="cmp-bench-key" placeholder="hf_...">
        </div>
        <div class="cmp-bench-field cmp-bench-field--narrow">
          <label for="cmp-bench-qcount">Questions</label>
          <select class="input" id="cmp-bench-qcount">
            <option value="20" selected>20</option>
            <option value="50">50</option>
            <option value="171">Full 171</option>
          </select>
        </div>
        <div class="cmp-bench-field cmp-bench-field--action">
          <label>&nbsp;</label>
          <button class="btn primary cmp-bench-run" id="cmp-bench-run">Run Benchmark</button>
        </div>
      </div>
      <div class="cmp-bench-progress" id="cmp-bench-progress" style="display:none">
        <div class="cmp-bench-bar"><div class="cmp-bench-bar-fill" id="cmp-bench-fill" style="width:0%"></div></div>
        <div class="cmp-bench-status" id="cmp-bench-status"></div>
      </div>
    </div>`;

  const canvas = body.querySelector('#cmp-radar') as HTMLCanvasElement;

  // Load persisted custom benchmarks so previously-run models appear on the chart
  api._fetch(api.map, '/api/benchmark/results').then((data: unknown) => {
    const d = data as { results?: Array<{ label: string; model: string; score: number }> };
    for (const r of d.results || []) {
      const customId = `custom_${r.model}`;
      if (entries.some(e => e.id === customId)) continue;
      entries.push({ id: customId, name: r.label, scores: { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0, eqbench: r.score }, custom: true });
    }
    drawBarChart(canvas);
  }).catch(() => drawBarChart(canvas));

  // Upgrade native selects to Apple Select dropdowns
  body.querySelectorAll<HTMLSelectElement>('select.input').forEach(appleSelect);

  // Source toggle
  const sourceSel = body.querySelector('#cmp-bench-source') as HTMLSelectElement;
  const ollamaWrap = body.querySelector('#cmp-bench-model-wrap') as HTMLElement;
  const hfWrap = body.querySelector('#cmp-bench-hf-wrap') as HTMLElement;
  const keyWrap = body.querySelector('#cmp-bench-key-wrap') as HTMLElement;
  function syncSourceFields(): void {
    const isOllama = sourceSel.value === 'ollama';
    ollamaWrap.classList.toggle('hidden', !isOllama);
    hfWrap.classList.toggle('hidden', isOllama);
    keyWrap.classList.toggle('hidden', isOllama);
  }
  sourceSel.addEventListener('change', syncSourceFields);
  syncSourceFields();

  // Load Ollama models
  const modelSel = body.querySelector('#cmp-bench-model') as HTMLSelectElement;
  api._fetch(api.map, '/api/ollama/models').then((data: unknown) => {
    const d = data as { models?: string[]; error?: string };
    const models = d.models || [];
    if (models.length === 0) {
      modelSel.innerHTML = '<option disabled>Ollama not available</option>';
    } else {
      modelSel.innerHTML = models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
    }
  }).catch(() => {
    modelSel.innerHTML = '<option disabled>Ollama offline</option>';
  });

  // Run benchmark
  const hfModelInput = body.querySelector('#cmp-bench-hf-model') as HTMLInputElement;
  const keyInput = body.querySelector('#cmp-bench-key') as HTMLInputElement;
  const qcountSel = body.querySelector('#cmp-bench-qcount') as HTMLSelectElement;
  const runBtn = body.querySelector('#cmp-bench-run') as HTMLButtonElement;
  const progWrap = body.querySelector('#cmp-bench-progress') as HTMLElement;
  const fill = body.querySelector('#cmp-bench-fill') as HTMLElement;
  const status = body.querySelector('#cmp-bench-status') as HTMLElement;

  runBtn.addEventListener('click', async () => {
    const isOllama = sourceSel.value === 'ollama';
    const model = isOllama ? modelSel.value : hfModelInput.value.trim();
    if (!model) return;

    runBtn.disabled = true;
    runBtn.textContent = 'Running…';
    progWrap.style.display = 'block';
    fill.style.width = '0%';
    status.textContent = 'Starting…';

    try {
      const payload: Record<string, unknown> = {
        provider: sourceSel.value,
        model,
        max_questions: parseInt(qcountSel.value, 10),
      };
      if (!isOllama) {
        payload.base_url = 'https://api-inference.huggingface.co/v1';
        payload.api_key = keyInput.value.trim();
      }

      const start = await api.postJSON<{ job_id?: string; error?: string }>(api.map, '/api/benchmark/eqbench', payload);
      if (!start.job_id) {
        status.textContent = `Error: ${start.error || 'Unknown'}`;
        runBtn.disabled = false;
        runBtn.textContent = 'Run Benchmark';
        return;
      }
      const jobId = start.job_id;

      _cmpBenchInterval = setInterval(async () => {
        try {
          const s = await api._fetch(api.map, `/api/benchmark/eqbench/status/${jobId}`) as Record<string, unknown>;
          const pct = (s.pct as number) || 0;
          fill.style.width = `${pct}%`;
          if (s.status === 'running') {
            status.textContent = `${s.scored}/${s.total} scored | avg: ${s.running_avg ?? '—'}`;
          } else if (s.status === 'done') {
            clearInterval(_cmpBenchInterval!);
            _cmpBenchInterval = null;
            fill.style.width = '100%';
            status.textContent = `Done — EQ-Bench score: ${s.score}/100 (${s.scored} questions, ${(s.median_latency_ms as number)?.toFixed(0) ?? '?'}ms median)`;
            runBtn.disabled = false;
            runBtn.textContent = 'Run Benchmark';

            const customId = `custom_${model}`;
            const existing = entries.findIndex(e => e.id === customId);
            const entry: ProviderEntry = { id: customId, name: model, scores: { spatial: 0, fidelity: 0, json: 0, speed: 0, cost: 0, eqbench: s.score as number }, custom: true };
            if (existing >= 0) entries[existing] = entry; else entries.push(entry);
            drawBarChart(canvas);
          } else if (s.status === 'error') {
            clearInterval(_cmpBenchInterval!);
            _cmpBenchInterval = null;
            status.textContent = `Error: ${s.error || 'Unknown'}`;
            runBtn.disabled = false;
            runBtn.textContent = 'Run Benchmark';
          }
        } catch { /* ignore poll errors */ }
      }, 2000);
    } catch {
      status.textContent = 'Failed to start benchmark';
      runBtn.disabled = false;
      runBtn.textContent = 'Run Benchmark';
    }
  });
}

function buildLLMEngine(rootId: string, stateRef: LLMSelection, serverBase?: string): {
  render(): void; bindApply(modelSel: string, applySel: string, statusSel: string): void;
} {
  const ROOT = document.getElementById(rootId)!;

  async function refreshOllamaModels(): Promise<void> {
    const modSel = ROOT.querySelector('select[id$="-model-select"]') as HTMLSelectElement | null;
    if (!modSel) return;
    modSel.innerHTML = '<option value="" disabled selected>Loading…</option>';
    try {
      const base = serverBase || api.map;
      const data = await api._fetch(base, '/api/ollama/models') as { models?: string[]; error?: string };
      const models = data.models || [];
      if (!models.length) {
        modSel.innerHTML = '<option value="" disabled selected>No models — run: ollama pull &lt;model&gt;</option>';
        return;
      }
      modSel.innerHTML = models.map(m =>
        `<option value="${escapeHtml(m)}"${stateRef.model === m ? ' selected' : ''}>${escapeHtml(m)}</option>`
      ).join('');
      if (!models.includes(modSel.value)) modSel.value = models[0];
      const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
      if (modelInp) modelInp.value = modSel.value;
      stateRef.model = modSel.value; saveState();
    } catch {
      modSel.innerHTML = '<option value="" disabled selected>Ollama offline?</option>';
    }
  }

  const DOCKER_PROVIDERS = new Set(['lmdeploy', 'vllm']);

  function syncModelUI(providerId: string): void {
    const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
    const modSel = ROOT.querySelector('select[id$="-model-select"]') as HTMLSelectElement | null;
    const launchRow = ROOT.querySelector<HTMLElement>('.lmdeploy-launch-row');
    const isOllama = providerId === 'ollama';
    const isDocker = DOCKER_PROVIDERS.has(providerId);
    if (modelInp) modelInp.style.display = isOllama ? 'none' : '';
    if (modSel) modSel.style.display = isOllama ? '' : 'none';
    if (launchRow) launchRow.style.display = isDocker ? 'flex' : 'none';
    if (isOllama) void refreshOllamaModels();
    if (isDocker) bindContainerLaunch(providerId);
  }

  function bindContainerLaunch(providerId: string): void {
    const btn = ROOT.querySelector<HTMLButtonElement>('#p4-lmdeploy-launch');
    if (!btn) return;
    // Re-bind when provider changes so the correct provider is sent
    btn.dataset['provider'] = providerId;
    if (btn.dataset['bound']) return;
    btn.dataset['bound'] = '1';
    btn.addEventListener('click', async () => {
      const statusEl = ROOT.querySelector<HTMLElement>('#p4-lmdeploy-status');
      if (statusEl) statusEl.textContent = 'Starting…';
      btn.disabled = true;
      const base = serverBase || api.map;
      const res = await api._fetch(base, '/api/container/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: btn.dataset['provider'] }),
      }).catch((e: unknown) => ({ status: 'error', message: String(e) })) as { status: string; message?: string };
      btn.disabled = false;
      if (statusEl) {
        statusEl.textContent = res.status === 'ok' ? 'Container running ✓'
          : res.status === 'starting' ? 'Launching… (~30s)'
          : `Error: ${res.message ?? 'unknown'}`;
      }
    });
  }

  function populateSelect(): void {
    const isLocal = stateRef.mode === 'local';
    const providers = isLocal ? LOCAL_PROVIDERS : API_PROVIDERS;
    const sel = ROOT.querySelector('select[id$="-llm-provider"]') as HTMLSelectElement | null;
    if (!sel) return;
    // If stored providerId doesn't belong to current mode's list, reset to first in list
    if (!providers.some((p) => p.id === stateRef.providerId)) {
      stateRef.providerId = providers[0].id;
      saveState();
    }
    sel.innerHTML = providers.map((p) =>
      `<option value="${p.id}"${stateRef.providerId === p.id ? ' selected' : ''}>${escapeHtml(p.name)}</option>`
    ).join('');
    // Keep state in sync with actual DOM value
    stateRef.providerId = sel.value;
    saveState();
    // Update model placeholder for the selected provider
    const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
    if (modelInp && !modelInp.value) modelInp.placeholder = DEFAULT_MODELS[sel.value] || '';
    syncModelUI(sel.value);
  }

  function onPick(id: string): void {
    const isLocalProvider = LOCAL_PROVIDERS.some((p) => p.id === id);
    stateRef.mode = isLocalProvider ? 'local' : 'api';
    stateRef.providerId = id;
    const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
    if (modelInp) modelInp.value = stateRef.model || DEFAULT_MODELS[id] || '';
    saveState();
    ROOT.querySelectorAll('.llm-tab-row button').forEach((b) =>
      b.setAttribute('aria-selected', b.getAttribute('data-v') === stateRef.mode ? 'true' : 'false'));
    populateSelect();
  }

  function render(): void {
    populateSelect();
    ROOT.querySelectorAll('.llm-tab-row button').forEach((b) => {
      b.addEventListener('click', () => {
        stateRef.mode = b.getAttribute('data-v') as 'local' | 'api'; saveState();
        ROOT.querySelectorAll('.llm-tab-row button').forEach((x) =>
          x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
        const providers = stateRef.mode === 'local' ? LOCAL_PROVIDERS : API_PROVIDERS;
        if (!providers.some((p) => p.id === stateRef.providerId)) {
          stateRef.providerId = providers[0].id;
        }
        populateSelect();
      });
    });
    const sel = ROOT.querySelector('select[id$="-llm-provider"]') as HTMLSelectElement | null;
    if (sel) {
      sel.addEventListener('change', () => {
        stateRef.providerId = sel.value; saveState();
        const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
        if (modelInp) modelInp.value = stateRef.model || DEFAULT_MODELS[sel.value] || '';
        syncModelUI(sel.value);
      });
    }
    // Model dropdown (Ollama) — sync value back to state and hidden text input
    const modSel = ROOT.querySelector('select[id$="-model-select"]') as HTMLSelectElement | null;
    if (modSel) {
      modSel.addEventListener('change', () => {
        const modelInp = ROOT.querySelector('input[id$="-llm-model"]') as HTMLInputElement | null;
        if (modelInp) modelInp.value = modSel.value;
        stateRef.model = modSel.value; saveState();
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
      modelInp.value = stateRef.model || DEFAULT_MODELS[stateRef.providerId] || '';
    }
  }

  function bindApply(modelSel: string, applySel: string, statusSel: string): void {
    const apply = ROOT.querySelector(applySel);
    if (!apply) return;
    apply.addEventListener('click', async () => {
      // Always read live DOM values — prevents stale state/provider mismatch
      const sel = ROOT.querySelector('select[id$="-llm-provider"]') as HTMLSelectElement | null;
      const provider = sel?.value || stateRef.providerId;
      stateRef.providerId = provider; saveState();
      const modelInp = ROOT.querySelector(modelSel) as HTMLInputElement | null;
      const modDropdown = ROOT.querySelector('select[id$="-model-select"]') as HTMLSelectElement | null;
      const model = (modDropdown?.style.display !== 'none' && modDropdown?.value
        ? modDropdown.value
        : modelInp?.value.trim()) || DEFAULT_MODELS[provider] || '';
      stateRef.model = model; saveState();
      const base = serverBase || api.map;
      try {
        const res = await api.postJSON<{ error?: string; hint?: string; available_models?: string[] }>(
          base, '/api/config/llm', { provider, model, base_url: '' });
        if (res?.error) {
          const hint = res.hint ? ` (${res.hint})` : '';
          toast(`LLM error: ${res.error}${hint}`, 'danger');
          return;
        }
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
let p4Bound = false;
let pickMode: 'start' | 'target' | null = null;
const p4LoadedRecordings = new Map<string, P5RecordingData>();
let p4ActiveRecording: string | null = null;
let p4StepTimer: number | null = null;
let p4Stepping = false;
const p4StepDelay = () => Math.max(0, Math.round(1100 / (state.singleAgent.speed || 1)));
let startMarker: MapboxMarker | null = null;
let targetMarker: MapboxMarker | null = null;
let trailPopup: MapboxPopup | null = null;
let agentNavMarker: MapboxMarker | null = null;
let agentNavArrowEl: HTMLElement | null = null;

interface StepLogEntry {
  pos: [number, number]; step: number; topic: string; description: string; mood: string;
  curiosity: number | null; fatigue: number | null;
  needs: Record<string, number> | null;
}
let stepLog: StepLogEntry[] = [];
let expandedDetailId: string | null = null;
let streamTab = 'mobility';
type StreamEvent = { step: number; topic: string; description: string; metadata?: Record<string, unknown> };
let lastStreamEvents: StreamEvent[] = [];
let lastPercData: { image_url?: string; perception?: Record<string, unknown>; closest_distance_km?: number | null } = {};
let thoughtMarker: MapboxMarker | null = null;
let resultsMode = false;
let resultsSummaries: Record<string, string | null> = {};
let resultsNarratives: { generic: string | null | undefined; historyAware: string | null | undefined } =
  { generic: undefined, historyAware: undefined };
let p5SelectedAgent: number | null = null;
let p5DetTab = 'all';

const PACE_COLORS: Record<string, string> = {
  leisurely: '#30d158', moderate: '#ff9f0a', fast: '#ff375f', none: 'rgba(255,255,255,0.25)',
};
const CURIOSITY_COLORS: Record<string, string> = {
  low: '#64d2ff', moderate: '#0a84ff', high: '#bf5af2', none: 'rgba(255,255,255,0.25)',
};
const SOCIAL_COLORS: Record<string, string> = {
  low: '#64d2ff', moderate: '#ff9f0a', high: '#ff375f', none: 'rgba(255,255,255,0.25)',
};

function buildProfileTagHTML(profile: { pace?: string; curiosity?: string; social?: string; archetype?: string }): string {
  const pace = profile.pace ?? '';
  const curiosity = profile.curiosity ?? '';
  const social = profile.social ?? '';
  const icons: { icon: string; label: string; color: string }[] = [];
  if (pace && pace !== 'none')
    icons.push({ color: PACE_COLORS[pace] ?? '#ff9f0a', label: pace + ' pace',
      icon: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>' });
  if (curiosity && curiosity !== 'none')
    icons.push({ color: CURIOSITY_COLORS[curiosity] ?? '#0a84ff', label: curiosity + ' curiosity',
      icon: '<svg viewBox="0 0 24 24" width="14" height="14"><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z" fill="none" stroke="currentColor" stroke-width="2"/></svg>' });
  if (social && social !== 'none')
    icons.push({ color: SOCIAL_COLORS[social] ?? '#ff375f', label: social + ' social',
      icon: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="9" cy="7" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M23 21v-2a4 4 0 0 0-3-3.87" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M16 3.13a4 4 0 0 1 0 7.75" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' });
  if (icons.length > 0)
    return icons.map(i => `<span class="p4-icon-tip" title="${i.label}" style="color:${i.color}">${i.icon}</span>`).join('');
  return escapeHtml(profile.archetype ?? '');
}

function renderNeedsBar(host: HTMLElement, needs: Record<string, number>): void {
  host.innerHTML = '';
  (['energy', 'comfort'] as const).forEach((k) => {
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
  const divider = document.createElement('div');
  divider.className = 'needs-divider';
  host.appendChild(divider);
  (['hunger', 'social'] as const).forEach((k) => {
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
}

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


function updateP4Profile(arch: string): void {
  const card    = $('#p4-profile-card') as HTMLElement | null;
  let canvas    = $('#p4-char-canvas')  as HTMLCanvasElement | null;
  const nameEl  = $('#p4-profile-name') as HTMLElement | null;
  const tagEl   = $('#p4-profile-tag')  as HTMLElement | null;
  const dotEl   = $('#p4-profile-dot')  as HTMLElement | null;
  const descEl  = $('#p4-profile-desc') as HTMLElement | null;
  if (!card || !canvas) return;

  const label   = arch[0].toUpperCase() + arch.slice(1).replace(/_/g, ' ');
  const color   = ARCHETYPE_COLORS[arch] || '#5e5ce6';
  const profile = state.archetypes?.[arch]?.profile;

  const pace      = profile?.pace      ?? '';
  const curiosity = profile?.curiosity ?? '';
  const social    = profile?.social    ?? '';
  const desc      = profile?.description || ARCHETYPE_DESCRIPTIONS[arch] || '';

  card.style.setProperty('--p4-accent', color);
  if (nameEl) nameEl.textContent = label;
  if (dotEl)  { dotEl.style.background = color; dotEl.style.boxShadow = `0 0 8px ${color}`; }
  if (tagEl) {
    const html = buildProfileTagHTML({ pace, curiosity, social, archetype: arch });
    tagEl.innerHTML = html || arch;
  }
  if (descEl) descEl.textContent = desc;

  // Dispose previous panel-4 viewer and build a fresh one for the new archetype
  if (p4CharViewer) { _disposeRenderer(p4CharViewer); p4CharViewer = null; }
  // Replace canvas element to avoid WebGL context issues from forceContextLoss
  const parent = canvas.parentNode;
  if (parent) {
    const newCanvas = document.createElement('canvas');
    newCanvas.id = 'p4-char-canvas';
    parent.replaceChild(newCanvas, canvas);
    canvas = newCanvas;
  }
  requestAnimationFrame(() => {
    const W   = canvas.clientWidth  || 400;
    const H   = canvas.clientHeight || 240;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width  = W * dpr;
    canvas.height = H * dpr;
    p4CharViewer = buildArchetypeFigure(canvas, color, glbUrl(arch), card);
    p4CharViewer.renderer.setClearColor(0x000000, 0); // transparent backdrop
    p4CharViewer.camera.fov    = 38;
    p4CharViewer.camera.aspect = W / H;
    p4CharViewer.camera.position.set(0, 0.9, 3.0);
    p4CharViewer.camera.lookAt(0, 0.9, 0);
    p4CharViewer.camera.updateProjectionMatrix();
    if (raf === null) startRenderLoop();
  });
}

async function panel4Enter(): Promise<void> {
  // Reset pick buttons to default state
  ($('#p4-pick-start') as HTMLButtonElement).classList.add('primary');
  ($('#p4-pick-target') as HTMLButtonElement).classList.remove('primary');
  ($('#p4-pick-target') as HTMLButtonElement).disabled = true;

  // Sync LLM panel with what the map server is actually running (not stale localStorage)
  try {
    const cfg = await api.m<{ llm_provider?: string; llm_model?: string }>('/api/config/frontend');
    if (cfg.llm_provider) {
      const isLocal = LOCAL_PROVIDERS.some((p) => p.id === cfg.llm_provider);
      state.llm.mode = isLocal ? 'local' : 'api';
      state.llm.providerId = cfg.llm_provider!;
      if (cfg.llm_model) state.llm.model = cfg.llm_model;
      saveState();
    }
  } catch { /* map server not reachable — leave localStorage value */ }

  try {
    const cfgs = await api.m<Record<string, { nav_mode: string; gps_dist?: number; compass_dist?: number }>>('/api/config/archetypes');
    for (const [arch, cfg] of Object.entries(cfgs)) {
      if (cfg.nav_mode) archetypeNavMap[arch] = cfg.nav_mode;
      navArchConfigs[arch] = {
        nav_mode:    cfg.nav_mode    ?? navArchConfigs[arch]?.nav_mode    ?? 'both',
        gps_dist:    cfg.gps_dist    ?? navArchConfigs[arch]?.gps_dist    ?? 120,
        compass_dist: cfg.compass_dist ?? navArchConfigs[arch]?.compass_dist ?? 60,
      };
    }
  } catch { /* map server not running — use hardcoded defaults */ }

  populateArchetypeSelect();
  updateP4Profile(state.singleAgent.archetype);
  ($('#p4-navmode') as HTMLSelectElement).value = state.singleAgent.navMode;
  applyNavThresholdVisibility(state.singleAgent.navMode);
  bindPanel4();
  // Sync speed slider + label every visit (mirrors panel5Enter pattern)
  const spd = state.singleAgent.speed ?? 1;
  const speedEl = $('#p4-speed') as HTMLInputElement | null;
  const speedLbl = $('#p4-speed-label') as HTMLElement | null;
  if (speedEl) speedEl.value = String(spd);
  if (speedLbl) speedLbl.textContent = `${spd.toFixed(2)}×`;
  // Remove any stale markers before restoring from state (loadPanel4 can be called
  // multiple times when switching panels, which would stack orphaned markers).
  if (startMarker)  { startMarker.remove();  startMarker = null; }
  if (targetMarker) { targetMarker.remove(); targetMarker = null; }
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

async function fetchP4RecordingList(): Promise<void> {
  const sel = $('#p4-playback-select') as HTMLSelectElement | null;
  const status = $('#p4-playback-status') as HTMLElement | null;
  const loadBtn = $('#p4-playback-load') as HTMLButtonElement | null;
  if (!sel) return;
  if (status) status.textContent = 'Fetching…';
  try {
    const res = await api.l<{ files: { filename: string; rel_path: string; size_kb: number; modified: number }[] }>(
      '/api/recording/list');
    const files = res.files ?? [];
    const loadedKeys = new Set(p4LoadedRecordings.keys());
    sel.innerHTML = '<option value="">— select a session —</option>';
    files.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.rel_path;
      const label = f.filename.replace('.parquet', '');
      opt.textContent = loadedKeys.has(f.rel_path) ? `✓ ${label} (${f.size_kb}kb)` : `${label} (${f.size_kb}kb)`;
      sel.appendChild(opt);
    });
    if (p4ActiveRecording) sel.value = p4ActiveRecording;
    if (status) status.textContent = files.length > 0 ? `${files.length} session${files.length !== 1 ? 's' : ''} found.` : 'No recordings yet.';
    if (loadBtn) loadBtn.disabled = !sel.value;
  } catch (e) {
    if (status) status.textContent = `Error: ${e instanceof Error ? e.message : e}`;
  }
}

async function loadP4Recording(filename: string): Promise<void> {
  const status = $('#p4-playback-status') as HTMLElement | null;
  const loadBtn = $('#p4-playback-load') as HTMLButtonElement | null;
  if (!filename) return;

  if (p4LoadedRecordings.has(filename)) {
    p4ActiveRecording = filename;
    showRecordingOnMap(filename);
    return;
  }

  if (loadBtn) loadBtn.disabled = true;
  if (status) status.textContent = 'Loading…';
  try {
    const res = await api.l<{ session?: string; total_steps?: number; agents?: P5RecAgent[]; error?: string }>(
      `/api/recording/load?filename=${encodeURIComponent(filename)}`);
    if (res.error) { if (status) status.textContent = `Error: ${res.error}`; return; }
    const data: P5RecordingData = {
      session: res.session ?? filename,
      totalSteps: res.total_steps ?? 0,
      agents: res.agents ?? [],
    };
    p4LoadedRecordings.set(filename, data);
    p4ActiveRecording = filename;
    showRecordingOnMap(filename);
    const sel = $('#p4-playback-select') as HTMLSelectElement | null;
    if (sel) {
      for (const opt of Array.from(sel.options)) {
        if (opt.value === filename) opt.textContent = `✓ ${opt.textContent?.replace(/^✓ /, '')}`;
      }
    }
    if (status) status.textContent = `Loaded: ${data.agents.length} agent(s), ${data.totalSteps} steps`;
  } catch (e) {
    if (status) status.textContent = `Load failed: ${e instanceof Error ? e.message : e}`;
  } finally {
    if (loadBtn) loadBtn.disabled = false;
  }
}

function bindPanel4(): void {
  if (p4Bound) return; p4Bound = true;


  // Toast on custom model load failure
  const profileCard = $('#p4-profile-card') as HTMLElement | null;
  profileCard?.addEventListener('model-error', ((e: CustomEvent) => {
    toast(e.detail, 'danger');
  }) as EventListener);

  ($('#p4-archetype') as HTMLSelectElement).addEventListener('change', async (e) => {
    const archetype = (e.target as HTMLSelectElement).value;
    state.singleAgent.archetype = archetype; saveState();
    updateP4Profile(archetype);
    const navMode = (archetypeNavMap[archetype] ?? 'both') as SingleAgentState['navMode'];
    state.singleAgent.navMode = navMode; saveState();
    ($('#p4-navmode') as HTMLSelectElement).value = navMode;
    applyNavThresholdVisibility(navMode);
    try { await api.postJSON(api.lab, '/api/config/nav-mode', { mode: navMode }); } catch { /* lab might be down */ }
  });

  ($('#p4-navmode') as HTMLSelectElement).addEventListener('change', async (e) => {
    const mode = (e.target as HTMLSelectElement).value as SingleAgentState['navMode'];
    state.singleAgent.navMode = mode; saveState();
    applyNavThresholdVisibility(mode);
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
      const currentVal = String((state.singleAgent as unknown as Record<string, unknown>)[stateKey]);
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
        (state.singleAgent as unknown as Record<string, unknown>)[stateKey] = v; saveState();
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

  // Use a general click handler with a 10 px tolerance box instead of a
  // layer-specific click, because Mapbox's layer-click hit detection on small
  // circles (4.5 px radius) requires pixel-perfect accuracy while mouseenter
  // uses a wider tolerance — causing the "cursor shows pointer but click does
  // nothing" bug.
  map?.on('click', (e: MapboxMapEvent) => {
    if (!map || state.currentPanel !== 4 || pickMode) return;
    const px = e.point;
    const features = map.queryRenderedFeatures(
      [[px.x - 10, px.y - 10], [px.x + 10, px.y + 10]],
      { layers: ['trail-dots-pt'] },
    );
    if (!features.length) return;
    const p = features[0].properties as {
      step: string | number; topic: string; description: string; mood: string;
      curiosity: number | null; fatigue: number | null; needs: string | null;
    };
    if (thoughtMarker) { thoughtMarker.remove(); thoughtMarker = null; }
    if (trailPopup) trailPopup.remove();
    const moodStr = String(p.mood || 'neutral');
    const moodColor = getMoodColor(moodStr);
    const fmtPctLocal = (v: number | null) => v == null ? '—' : `${Math.round(v * 100)}%`;
    const needs: Record<string, number> | null = p.needs ? (() => { try { return JSON.parse(p.needs!); } catch { return null; } })() : null;
    const needsHtml = needs
      ? Object.entries(needs).map(([k, v]) => `
          <div class="trail-need-row">
            <span class="trail-need-label">${escapeHtml(k)}</span>
            <div class="trail-need-bar"><div class="trail-need-fill" style="width:${Math.round((v as number) * 100)}%;background:${(v as number) < 0.3 ? 'var(--danger)' : (v as number) < 0.6 ? 'var(--warning)' : 'var(--success)'}"></div></div>
            <span class="trail-need-val">${fmtPctLocal(v as number)}</span>
          </div>`).join('')
      : '<span style="color:var(--text-muted);font-size:11px">not recorded</span>';
    const html = `<div class="trail-popup-inner">
      <div class="trail-popup-header">
        <span class="step-badge">#${escapeHtml(String(p.step))}</span>
        <span class="mood-dot" style="background:${moodColor}"></span>
        <span class="mood-label" style="color:${moodColor}">${escapeHtml(moodStr)}</span>
        <span class="topic-chip">${escapeHtml(String(p.topic || ''))}</span>
      </div>
      <div class="trail-popup-desc">${escapeHtml(String(p.description || '—'))}</div>
      <div class="trail-popup-section-title">Cognition</div>
      <div class="trail-cognition-row">
        <span>Curiosity</span><span>${fmtPctLocal(p.curiosity)}</span>
        <span>Fatigue</span><span>${fmtPctLocal(p.fatigue)}</span>
      </div>
      <div class="trail-popup-section-title">Needs</div>
      ${needsHtml}
    </div>`;
    trailPopup = new mapboxgl.Popup({ className: 'trail-popup', closeButton: true, maxWidth: '320px' })
      .setLngLat(e.lngLat as unknown as [number, number])
      .setHTML(html)
      .addTo(map as unknown as Parameters<MapboxPopup['addTo']>[0]) as MapboxPopup;
  });
  map?.on('mouseenter', 'trail-dots-pt', () => { if (map) map.getCanvas().style.cursor = 'pointer'; });
  map?.on('mouseleave', 'trail-dots-pt', () => { if (map) map.getCanvas().style.cursor = ''; });
  map?.on('click', onPanel4MapClick);
  $('#p4-play')!.addEventListener('click', startSinglePlay);
  $('#p4-pause')!.addEventListener('click', pauseSinglePlay);
  $('#p4-step')!.addEventListener('click', () => stepSingle());
  $('#p4-results')!.addEventListener('click', toggleResultsMode);
  $('#p4-rec-start')!.addEventListener('click', () => startRecording('p4'));
  $('#p4-rec-stop')!.addEventListener('click', stopRecording);

  // P4 recording playback layer toggles
  $('#p4-rec-rl-heatmap')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('recording-heatmap-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });
  $('#p4-rec-rl-trails')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('recording-trails-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });
  $('#p4-rec-rl-decision-pts')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('recording-decision-pts-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });

  // P4 rec panel tab switching
  $('#p4-rec-tabs')?.querySelectorAll<HTMLButtonElement>('button').forEach(btn => {
    btn.addEventListener('click', () => {
      const v = btn.dataset.v;
      $('#p4-rec-tabs')?.querySelectorAll('button').forEach(b => b.setAttribute('aria-selected', 'false'));
      btn.setAttribute('aria-selected', 'true');
      const recTab    = $('#p4-rec-tab-record')  as HTMLElement | null;
      const replayTab = $('#p4-rec-tab-replay')  as HTMLElement | null;
      if (recTab)    recTab.style.display    = v === 'record' ? '' : 'none';
      if (replayTab) replayTab.style.display = v === 'replay' ? '' : 'none';
      if (v === 'replay') { /* noop — user imports file */ }
    });
  });

  // P4 file import
  $('#p4-import-btn')?.addEventListener('click', () => {
    ($('#p4-import-file') as HTMLInputElement)?.click();
  });
  $('#p4-import-file')?.addEventListener('change', (e) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (file) void importRecordingFile(file, 'p4');
    (e.target as HTMLInputElement).value = '';
  });
  $('#p4-speed')?.addEventListener('input', (e) => {
    const spd = +(e.target as HTMLInputElement).value;
    state.singleAgent.speed = spd; saveState();
    const lbl = $('#p4-speed-label') as HTMLElement | null;
    if (lbl) lbl.textContent = `${spd.toFixed(2)}×`;
    if (p4StepTimer !== null) {
      clearTimeout(p4StepTimer);
      p4StepTimer = window.setTimeout(stepSingle, p4StepDelay());
    }
  });

  // Legend overlay toggles
  (['heatmap', 'decision-pts', 'decision-src', 'goal-changes', 'deviations'] as const).forEach((key) => {
    $(`#rl-${key}`)?.addEventListener('change', (e) => {
      const vis = (e.target as HTMLInputElement).checked ? 'visible' : 'none';
      map?.setLayoutProperty(`results-${key}-layer`, 'visibility', vis);
    });
  });

  // Popup interactions for results overlay markers
  map?.on('click', 'results-goal-changes-layer', (e: MapboxMapEvent) => {
    if (!resultsMode || !map) return;
    const feat = map.queryRenderedFeatures([e.point.x, e.point.y] as [number, number], { layers: ['results-goal-changes-layer'] })[0];
    if (!feat) return;
    const p = feat.properties as { description?: string; step?: number };
    new mapboxgl.Popup({ maxWidth: '280px', closeButton: true })
      .setLngLat(e.lngLat as unknown as [number, number])
      .setHTML(`<div class="thought-popup"><b>Goal change · step ${p.step ?? '?'}</b>${escapeHtml(p.description || '')}</div>`)
      .addTo(map as unknown as Parameters<MapboxPopup['addTo']>[0]);
  });
  map?.on('click', 'results-decision-pts-layer', (e: MapboxMapEvent) => {
    if (!resultsMode || !map) return;
    const feat = map.queryRenderedFeatures([e.point.x, e.point.y] as [number, number], { layers: ['results-decision-pts-layer'] })[0];
    if (!feat) return;
    const p = feat.properties as { step?: number; bearing_change?: number };
    new mapboxgl.Popup({ maxWidth: '220px', closeButton: true })
      .setLngLat(e.lngLat as unknown as [number, number])
      .setHTML(`<div class="thought-popup"><b>Decision point · step ${p.step ?? '?'}</b>Direction change: ${p.bearing_change ?? '?'}°</div>`)
      .addTo(map as unknown as Parameters<MapboxPopup['addTo']>[0]);
  });
  map?.on('mouseenter', 'results-goal-changes-layer', () => { if (map && resultsMode) map.getCanvas().style.cursor = 'pointer'; });
  map?.on('mouseleave', 'results-goal-changes-layer', () => { if (map) map.getCanvas().style.cursor = ''; });
  map?.on('mouseenter', 'results-decision-pts-layer', () => { if (map && resultsMode) map.getCanvas().style.cursor = 'pointer'; });
  map?.on('mouseleave', 'results-decision-pts-layer', () => { if (map) map.getCanvas().style.cursor = ''; });

  $('#p4-stream-tabs')?.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      $('#p4-stream-tabs')?.querySelectorAll('button').forEach(b => b.setAttribute('aria-selected', 'false'));
      btn.setAttribute('aria-selected', 'true');
      streamTab = btn.getAttribute('data-v') || 'all';
      if (resultsMode) {
        renderResultsThoughtSummary(streamTab);
      } else {
        const filtered = streamTab === 'all' ? lastStreamEvents : lastStreamEvents.filter(e => e.topic === streamTab);
        renderThoughts(filtered);
      }
      renderStreamSummary(streamTab, lastStreamEvents);
    });
  });

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
    const startBtn = $('#p4-pick-start') as HTMLButtonElement;
    const targetBtn = $('#p4-pick-target') as HTMLButtonElement;
    startBtn.classList.remove('primary');
    targetBtn.classList.add('primary');
    targetBtn.disabled = false;
    ($('#p4-config-status') as HTMLElement).textContent = 'Start placed. Click Pick Target to place the target.';
    try {
      const data = await api.l<{ type?: string; error?: string }>(`/api/reachable-area?lon=${lng}&lat=${lat}&max_nodes=600`);
      if (data?.type === 'FeatureCollection') {
        map.getSource('reachable')?.setData(data as unknown);
      } else if (data?.error) {
        toast(data.error, 'warning');
      }
    } catch (e) { console.warn('reachable-area fetch failed:', e); }
    saveState();
    pickMode = null;
    map.getCanvas().classList.remove('map-pick');
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
    sa.positionHistory = []; sa.moodHistory = []; stepLog = []; lastStreamEvents = [];
    thoughtMarker?.remove(); thoughtMarker = null;
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
  if (state.singleAgent.id === null || p4Stepping) return;
  p4Stepping = true;
  try {
    const res = await api.postJSON<{ agent_state?: Record<string, unknown> }>(
      api.lab, '/api/step_continuous', {});
    if (res?.agent_state) {
      // Server bundled all refresh data — no extra GET calls needed
      await refreshSingleAgent(res.agent_state);
    } else {
      // Fallback: old server without bundled state
      await refreshSingleAgent();
    }
  } catch (e) { console.warn('step error', e); }
  finally {
    p4Stepping = false;
    if (state.singleAgent.playing) {
      p4StepTimer = window.setTimeout(stepSingle, p4StepDelay());
    }
  }
}

async function refreshSingleAgent(prefetched?: Record<string, unknown>): Promise<void> {
  const sa = state.singleAgent;
  if (sa.id === null) return;
  try {
    type CogType = { cognition_state?: { mood?: string; curiosity?: number; fatigue?: number }; needs?: Record<string, number> };
    type InfoType = { location?: { lon: number; lat: number } };
    type StreamType = { events?: StreamEvent[] };

    let cog: CogType, info: InfoType, stream: StreamType;
    if (prefetched) {
      cog    = prefetched as CogType;
      info   = { location: prefetched.location as { lon: number; lat: number } | undefined };
      stream = { events: (prefetched.stream_events as StreamEvent[] | undefined) ?? [] };
    } else {
      [cog, info, stream] = await Promise.all([
        api.l<CogType>(`/api/agent/${sa.id}/cognition`).catch(() => ({} as CogType)),
        api.l<InfoType>(`/api/agent/${sa.id}`).catch(() => ({} as InfoType)),
        api.l<StreamType>(`/api/agent/${sa.id}/stream?n=10000`).catch(() => ({ events: [] })),
      ]);
    }

    renderSingleCognition(cog as Parameters<typeof renderSingleCognition>[0]);
    lastStreamEvents = stream.events || [];
    let filteredEvents = streamTab === 'all' ? lastStreamEvents : lastStreamEvents.filter(e => e.topic === streamTab);
    if (state.singleAgent.timeOverride) {
      filteredEvents = filteredEvents.filter(e => (e.metadata as Record<string, unknown>)?.time_of_day === state.singleAgent.timeOverride);
    }
    renderThoughts(filteredEvents);
    renderStreamSummary(streamTab, filteredEvents);
    void renderTimePhaseBanner('#p4-time-phase-stats');

    const loc = info.location;
    if (loc) {
      const pos: [number, number] = [loc.lon, loc.lat];
      sa.positionHistory.push(pos);

      const events = stream.events || [];
      const latest = events.reduce(
        (best, ev) => ev.step > (best?.step ?? -1) ? ev : best,
        null as typeof events[0] | null,
      );
      stepLog.push({
        pos, step: latest?.step ?? sa.positionHistory.length - 1,
        topic: latest?.topic ?? 'step',
        description: latest?.description ?? '',
        mood: cog.cognition_state?.mood || 'neutral',
        curiosity: cog.cognition_state?.curiosity ?? null,
        fatigue: cog.cognition_state?.fatigue ?? null,
        needs: cog.needs ? { ...cog.needs } : null,
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
          properties: {
            step: e.step, topic: e.topic, description: e.description, mood: e.mood,
            curiosity: e.curiosity, fatigue: e.fatigue,
            needs: e.needs ? JSON.stringify(e.needs) : null,
          },
        })),
      });

      // Create or update the navigation marker showing current position + travel direction
      if (!agentNavMarker && map) {
        const navColor = ARCHETYPE_COLORS[sa.archetype] ?? '#64d2ff';
        const el = document.createElement('div');
        el.className = 'agent-nav-marker';
        el.innerHTML = `<div class="agent-nav-pulse" style="background:${navColor}33"></div>
<svg class="agent-nav-arrow" viewBox="0 0 24 32" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 2 L22 28 L12 22.5 L2 28 Z" fill="${navColor}" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"/>
</svg>`;
        agentNavArrowEl = el.querySelector('.agent-nav-arrow') as HTMLElement;
        agentNavMarker = new mapboxgl.Marker({ element: el, anchor: 'center' })
          .setLngLat(pos)
          .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
      } else if (agentNavMarker) {
        agentNavMarker.setLngLat(pos);
      }

      if (agentNavArrowEl && sa.positionHistory.length >= 2) {
        const prev = sa.positionHistory[sa.positionHistory.length - 2];
        const bearing = calcBearing(prev, pos);
        agentNavArrowEl.style.transform = `rotate(${bearing}deg)`;
      }
    }

    const [perc, stats] = await Promise.all([
      api.l<{ image_url?: string; perception?: Record<string, unknown>; closest_distance_km?: number | null }>(
        `/api/agent/${sa.id}/perception-text`).catch(() => ({})),
      api.l<{ total_calls?: number }>('/api/llm/stats').catch(() => ({})),
    ]);
    lastPercData = perc as typeof lastPercData;
    if (!resultsMode) renderSinglePerception(lastPercData);
    ($('#p4-llm-stats') as HTMLElement).textContent = `${(stats as { total_calls?: number }).total_calls || 0} calls`;

    // Update proposed Dijkstra path on map
    if (prefetched?.proposed_path) {
      const nodes = (prefetched.proposed_path as { nodes?: unknown[] }).nodes;
      if (Array.isArray(nodes) && nodes.length >= 2) {
        const coords = nodes.map((n: unknown) => {
          if (Array.isArray(n)) return n as [number, number];
          const t = n as [number, number];
          return [t[0], t[1]] as [number, number];
        });
        map?.getSource('planned')?.setData({
          type: 'FeatureCollection',
          features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} }],
        });
      } else {
        map?.getSource('planned')?.setData({ type: 'FeatureCollection', features: [] });
      }
    }

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
    const el = child as HTMLElement;
    if (el.tagName === 'H4') return;
    if (el.classList.contains('expand-card-btn')) return;
    body.appendChild(child.cloneNode(true));
  });
  // Re-attach live SVG for emotion pie (works for both old 'emotion' and new combined id)
  if (expandedDetailId === 'emotion-cognition' || expandedDetailId === 'emotion') {
    const svgSrc = document.querySelector<SVGElement>('#p4-emotion-svg');
    const svgDest = body.querySelector<SVGElement>('svg');
    if (svgSrc && svgDest) svgDest.innerHTML = svgSrc.innerHTML;
  }
  // Re-bind stream tab clicks in the overlay (cloned elements lose event listeners)
  if (expandedDetailId === 'thoughts') {
    const tabBar = body.querySelector('.stream-tab-bar');
    if (tabBar) {
      tabBar.querySelectorAll('button').forEach((btn) => {
        btn.addEventListener('click', () => {
          tabBar.querySelectorAll('button').forEach((b) => b.setAttribute('aria-selected', 'false'));
          btn.setAttribute('aria-selected', 'true');
          streamTab = btn.getAttribute('data-v') || 'all';
          if (resultsMode) {
            renderResultsThoughtSummary(streamTab);
          } else {
            const filtered = streamTab === 'all' ? lastStreamEvents : lastStreamEvents.filter((e) => e.topic === streamTab);
            renderThoughts(filtered);
          }
          renderStreamSummary(streamTab, lastStreamEvents);
          const srcThoughts = $('#p4-thoughts');
          const srcSummary = $('#p4-stream-summary');
          const dstThoughts = body.querySelector('#p4-thoughts') as HTMLElement;
          const dstSummary = body.querySelector('.stream-summary') as HTMLElement;
          if (srcThoughts && dstThoughts) dstThoughts.innerHTML = srcThoughts.innerHTML;
          if (srcSummary && dstSummary) dstSummary.innerHTML = srcSummary.innerHTML;
        });
      });
    }
  }
}

function renderSingleCognition(cog: { cognition_state?: { mood?: string; curiosity?: number; fatigue?: number }; needs?: Record<string, number> }): void {
  const cs = cog?.cognition_state || {};
  const needs = cog?.needs || {};
  ($('#p4-mood') as HTMLElement).textContent = cs.mood || '—';
  ($('#p4-curiosity') as HTMLElement).textContent = fmtPct(cs.curiosity ?? null);
  ($('#p4-fatigue') as HTMLElement).textContent = fmtPct(cs.fatigue ?? null);

  renderNeedsBar($('#p4-needs') as HTMLElement, needs);

  state.singleAgent.moodHistory.push(cs.mood || 'neutral');
  renderEmotionPie('#p4-emotion-svg', '#p4-emotion-legend', state.singleAgent.moodHistory);
}

function makeSvgText(x: number, y: number, label: string): SVGTextElement {
  const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  text.setAttribute('x', String(x));
  text.setAttribute('y', String(y));
  text.setAttribute('text-anchor', 'middle');
  text.setAttribute('dominant-baseline', 'central');
  text.setAttribute('fill', '#fff');
  text.setAttribute('font-size', '0.19');
  text.setAttribute('font-weight', '600');
  text.setAttribute('font-family', 'system-ui, sans-serif');
  text.textContent = label;
  return text;
}

function renderEmotionPie(svgSel: string, legSel: string, hist: string[]): void {
  const counts: Record<string, number> = {};
  hist.forEach((m) => { counts[m] = (counts[m] || 0) + 1; });
  const total = hist.length || 1;
  const svg = $(svgSel) as unknown as SVGSVGElement;
  svg.innerHTML = '';
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);

  if (entries.length === 1) {
    // Single mood — arc with identical start/end is degenerate; use a full circle instead
    const [m] = entries[0];
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('cx', '0'); circle.setAttribute('cy', '0'); circle.setAttribute('r', '1');
    circle.setAttribute('fill', getMoodColor(m));
    svg.appendChild(circle);
    svg.appendChild(makeSvgText(0, 0, '100%'));
  } else {
    let acc = 0;
    entries.forEach(([m, c]) => {
      const pct = c / total;
      const a0 = (acc / total) * Math.PI * 2 - Math.PI / 2;
      const a1 = ((acc + c) / total) * Math.PI * 2 - Math.PI / 2;
      const large = pct > 0.5 ? 1 : 0;
      const x0 = Math.cos(a0), y0 = Math.sin(a0);
      const x1 = Math.cos(a1), y1 = Math.sin(a1);
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', `M 0 0 L ${x0} ${y0} A 1 1 0 ${large} 1 ${x1} ${y1} Z`);
      path.setAttribute('fill', getMoodColor(m));
      svg.appendChild(path);
      if (pct >= 0.13) {
        const mid = (a0 + a1) / 2;
        svg.appendChild(makeSvgText(0.55 * Math.cos(mid), 0.55 * Math.sin(mid), `${Math.round(pct * 100)}%`));
      }
      acc += c;
    });
  }

  ($(legSel) as HTMLElement).innerHTML = entries
    .map(([m]) =>
      `<div class="leg"><span class="sw" style="background:${getMoodColor(m)}"></span>${escapeHtml(m)}</div>`)
    .join('');
}

function thoughtsEmptyMessage(tab: string): string {
  if (tab === 'perception') {
    const modeOn = state.perceptionMode === 'both' || state.perceptionMode === 'perception';
    if (!modeOn) {
      return '<div class="meta">Perception mode is off — switch to <b>Both</b> or <b>Perception</b> in Settings.</div>';
    }
    return '<div class="meta">No VLM scene data found near the agent\'s location. Re-run the analysis pipeline in Panel 2.</div>';
  }
  if (tab === 'amenity_visit') return '<div class="meta">No amenity visits yet — agent hasn\'t been within 30m of a POI.</div>';
  if (tab === 'mobility') return '<div class="meta">No movement events yet — press Play.</div>';
  return '<div class="meta">No events yet — press Play.</div>';
}

function renderThoughtsInto(host: HTMLElement, events: StreamEvent[], tab: string, allEvents: StreamEvent[], clickable = true): void {
  host.innerHTML = '';
  if (!events.length) {
    host.innerHTML = thoughtsEmptyMessage(tab);
    return;
  }
  events.slice().reverse().filter(ev => !(ev.topic === 'perception' && ev.metadata?.source === 'visual_satisfaction')).forEach((ev) => {
    const div = document.createElement('div');
    div.className = 'thought';
    div.setAttribute('data-topic', ev.topic || '');
    if (clickable) div.title = 'Click to locate on map';
    const m = ev.metadata || {};
    const badges: string[] = [];
    if (m.fallback) badges.push('<span class="chip danger" style="font-size:10px;">fallback</span>');
    if (m.on_path === false) badges.push('<span class="chip warning" style="font-size:10px;">off-path</span>');
    if (m.on_path === true)  badges.push('<span class="chip success" style="font-size:10px;">on-path</span>');
    if (m.perception_available) badges.push('<span class="chip accent" style="font-size:10px;">perc</span>');
    let extraHtml = '';
    if (ev.topic === 'perception' && m.source !== 'visual_satisfaction') {
      const tod = (m.time_of_day as string) || '';
      const isNightTime = tod === 'evening' || tod === 'night';
      const sceneFields: [string, string][] = [
        ['spatial_character', 'Spatial character'],
        ['greenery', 'Greenery'],
        ['crowdedness', 'Crowdedness'],
        ...(!isNightTime ? [['lighting', 'Lighting'] as [string, string]] : []),
        ['street_amenities', 'Street amenities'],
        ['visible_text', 'Signage/text'],
      ];
      const rows = sceneFields
        .map(([key, label]) => {
          const val = (m[key] as string | undefined) || '';
          return val ? `<div class="thought-scene-field"><span class="sf-label">${escapeHtml(label)}</span>${escapeHtml(val)}</div>` : '';
        })
        .filter(Boolean)
        .join('');
      if (rows) extraHtml += `<div class="thought-scene-fields">${rows}</div>`;

      const mobEv = allEvents.find(e => e.topic === 'mobility' && e.step === ev.step);
      if (mobEv) {
        const mobMeta = mobEv.metadata || {};
        const onPath = mobMeta.on_path === true ? '<span class="chip success" style="font-size:9px;">on-path</span>'
          : mobMeta.on_path === false ? '<span class="chip warning" style="font-size:9px;">off-path</span>' : '';
        const fallbackChip = mobMeta.fallback ? '<span class="chip danger" style="font-size:9px;">fallback</span>' : '';
        extraHtml += `<div class="perc-decision-block">
          <div class="perc-decision-label">Decided ${onPath}${fallbackChip}</div>
          <div class="perc-decision-text">${escapeHtml(mobEv.description)}</div>
        </div>`;
      }

      const vsEv = allEvents.find(e => e.topic === 'perception' && e.step === ev.step && e.metadata?.source === 'visual_satisfaction');
      if (vsEv) {
        extraHtml += `<div class="perc-decision-block perc-needs-block">
          <div class="perc-decision-label">Felt</div>
          <div class="perc-decision-text">${escapeHtml(vsEv.description)}</div>
        </div>`;
      }
    }

    const amenityMeta = ev.topic === 'amenity_visit' ? (m.amenity as Record<string, string> | undefined) : undefined;
    const amenityChip = amenityMeta
      ? `<span class="chip danger" style="font-size:10px;">${escapeHtml(amenityMeta.type || '')} · ${escapeHtml(amenityMeta.name || '')}</span>`
      : '';

    const _phaseNames = ['morning', 'afternoon', 'evening', 'night'] as const;
    const _phase = state.singleAgent.timeOverride ?? _phaseNames[Math.floor(ev.step / 24) % 4];
    const phaseBadge = `<span class="chip phase-${_phase}" style="font-size:10px;">${_phase}</span>`;
    div.innerHTML = `
      <div class="row1">
        <span class="step">#${ev.step}</span>
        ${phaseBadge}
        <span class="topic">${escapeHtml(ev.topic)}</span>
        <div class="badges">${amenityChip}${badges.join('')}</div>
      </div>
      <div class="desc">${escapeHtml(ev.description)}</div>${extraHtml}`;
    if (clickable) div.addEventListener('click', () => flyToThoughtLocation(ev));
    host.appendChild(div);
  });
}

function renderThoughts(events: StreamEvent[]): void {
  const host = $('#p4-thoughts') as HTMLElement;
  renderThoughtsInto(host, events, streamTab, lastStreamEvents);
}

async function renderTimePhaseBanner(hostSel = '#p4-time-phase-stats'): Promise<void> {
  const host = $(hostSel) as HTMLElement | null;
  if (!host) return;
  const isP4 = hostSel.includes('p4');

  if (isP4) {
    const phaseOrder = ['morning', 'afternoon', 'evening', 'night'] as const;
    const _phases = ['morning', 'afternoon', 'evening', 'night'];
    const counts: Record<string, number> = { morning: 0, afternoon: 0, evening: 0, night: 0 };
    for (const ev of lastStreamEvents) {
      const tod = (ev.metadata as Record<string, unknown>)?.time_of_day as string | undefined;
      const phase = tod && tod in counts ? tod : _phases[Math.floor(ev.step / 24) % 4];
      counts[phase]++;
    }
    const override = state.singleAgent.timeOverride;
    host.classList.toggle('has-override', override !== null);
    host.innerHTML = phaseOrder.map(p => {
      const isActive = override ? p === override : false;
      const isLocked = override === p;
      return `<div class="tps-cell phase-${p}${isActive ? ' active' : ''}${isLocked ? ' locked' : ''}"
        data-phase="${p}" title="${p}: ${counts[p]} events${isLocked ? ' (locked)' : ''}">
        ${p} <span style="opacity:0.6">${counts[p]}</span>
      </div>`;
    }).join('');
    host.querySelectorAll<HTMLElement>('.tps-cell[data-phase]').forEach(cell => {
      cell.addEventListener('click', () => {
        const phase = cell.dataset['phase']!;
        void toggleTimeOverride(phase);
      });
    });
  } else {
    try {
      const data = await api.m<{
        phases: Record<string, { total: number; by_topic: Record<string, number>; samples: string[] }>;
        current_phase: string;
      }>('/api/time_stats');
      const phaseOrder = ['morning', 'afternoon', 'evening', 'night'] as const;
      host.innerHTML = phaseOrder.map(p => {
        const bucket = data.phases[p] ?? { total: 0, by_topic: {} };
        const isActive = p === data.current_phase;
        return `<div class="tps-cell phase-${p}${isActive ? ' active' : ''}" title="${p}: ${bucket.total} events">
          ${p} <span style="opacity:0.6">${bucket.total}</span>
        </div>`;
      }).join('');
    } catch {
      host.innerHTML = '';
    }
  }
}

async function toggleTimeOverride(phase: string): Promise<void> {
  const current = state.singleAgent.timeOverride;
  const newPhase = current === phase ? null : phase;
  try {
    await api.postJSON(api.lab, '/api/time_override', { phase: newPhase });
    state.singleAgent.timeOverride = newPhase;
    saveState();
    void renderTimePhaseBanner('#p4-time-phase-stats');
    toast(newPhase ? `Time locked to ${newPhase}` : 'Time auto-advancing', 'success');
  } catch (e) {
    toast(`Failed to set time: ${e instanceof Error ? e.message : e}`, 'danger');
  }
}

function renderStreamSummary(tab: string, events: StreamEvent[], hostSel = '#p4-stream-summary'): void {
  const host = $(hostSel) as HTMLElement | null;
  if (!host) return;
  const ev = tab === 'all' ? events : events.filter(e => e.topic === tab);
  if (!ev.length) {
    if (tab === 'perception') {
      const modeOn = state.perceptionMode === 'both' || state.perceptionMode === 'perception';
      host.innerHTML = modeOn
        ? '<span style="color:var(--warning);font-size:10px;">No VLM scene data in this area — re-run the analysis pipeline in Panel 2</span>'
        : '<span style="color:var(--danger);font-size:10px;">Perception mode off — enable in Settings</span>';
    } else {
      host.innerHTML = '';
    }
    return;
  }

  if (tab === 'mobility') {
    const total = ev.length;
    const fallbacks = ev.filter(e => e.metadata?.fallback).length;
    const onPath = ev.filter(e => e.metadata?.on_path === true).length;
    const llmGuided = total - fallbacks;
    host.innerHTML =
      `<span><span class="ss-label">Moves</span><b>${total}</b></span>` +
      `<span><span class="ss-label">LLM</span><b>${total ? Math.round(llmGuided / total * 100) : 0}%</b></span>` +
      `<span><span class="ss-label">Fallback</span><b>${total ? Math.round(fallbacks / total * 100) : 0}%</b></span>` +
      `<span><span class="ss-label">On-path</span><b>${total ? Math.round(onPath / total * 100) : 0}%</b></span>`;
  } else if (tab === 'amenity_visit') {
    const total = ev.length;
    const types = new Set(ev.map(e => (e.metadata?.amenity as Record<string, string> | undefined)?.type).filter(Boolean));
    const llmCount = ev.filter(e => e.metadata?.llm_used).length;
    host.innerHTML =
      `<span><span class="ss-label">Visits</span><b>${total}</b></span>` +
      `<span><span class="ss-label">Unique types</span><b>${types.size}</b></span>` +
      `<span><span class="ss-label">LLM-eval</span><b>${total ? Math.round(llmCount / total * 100) : 0}%</b></span>`;
  } else if (tab === 'perception') {
    const scenes = ev.filter(e => e.metadata?.source !== 'visual_satisfaction').length;
    const visualNeeds = ev.filter(e => e.metadata?.source === 'visual_satisfaction').length;
    host.innerHTML =
      `<span><span class="ss-label">Scenes</span><b>${scenes}</b></span>` +
      `<span><span class="ss-label">Visual-needs</span><b>${visualNeeds}</b></span>`;
  } else {
    const byTopic: Record<string, number> = {};
    ev.forEach(e => { byTopic[e.topic] = (byTopic[e.topic] || 0) + 1; });
    host.innerHTML = Object.entries(byTopic)
      .map(([t, c]) => `<span><span class="ss-label">${escapeHtml(t)}</span><b>${c}</b></span>`)
      .join('');
  }
}

function flyToThoughtLocation(ev: StreamEvent): void {
  if (!map || !stepLog.length) return;
  const entry = stepLog.find(e => e.step === ev.step)
    ?? stepLog.reduce((best, e) =>
        Math.abs(e.step - ev.step) < Math.abs(best.step - ev.step) ? e : best, stepLog[0]);
  if (!entry) return;
  (map as unknown as { flyTo(opts: object): void }).flyTo({ center: entry.pos, zoom: 17, duration: 800 });
  thoughtMarker?.remove();
  const popup = new mapboxgl.Popup({ closeButton: true, maxWidth: '280px' })
    .setHTML(`<div class="thought-popup"><b>${escapeHtml(ev.topic)} · step ${ev.step}</b>${escapeHtml(ev.description)}</div>`);
  thoughtMarker = new mapboxgl.Marker({ color: '#5e5ce6', scale: 0.8 })
    .setLngLat(entry.pos as [number, number])
    .setPopup(popup)
    .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
  (thoughtMarker as unknown as { togglePopup(): void }).togglePopup();
}

function renderSinglePerception(
  perc: { image_url?: string; perception?: Record<string, unknown>; closest_distance_km?: number | null },
  opts?: { hostSel?: string; chipSel?: string; archetype?: string; apiBase?: string },
): void {
  const hostSel  = opts?.hostSel  ?? '#p4-perception';
  const chipSel  = opts?.chipSel  ?? '#p4-perc-mode-chip';
  const archetype = opts?.archetype ?? state.singleAgent.archetype;
  const imgBase  = opts?.apiBase  ?? api.lab;
  const host = $(hostSel) as HTMLElement;
  if (!host) return;
  const chip = $(chipSel) as HTMLElement | null;
  const mode = state.perceptionMode;
  const modeOn = mode === 'perception' || mode === 'both';

  // Update header chip to reflect active mode
  if (chip) {
    if (modeOn) {
      chip.textContent = mode === 'both' ? 'both' : 'perc';
      chip.className = 'perc-mode-chip perc-mode-on';
    } else {
      chip.textContent = mode === 'rule_based' ? 'rule' : 'amenities';
      chip.className = 'perc-mode-chip perc-mode-off';
    }
  }

  // Three distinct empty states
  if (!modeOn) {
    host.innerHTML = `<div class="perc-status-msg">Perception mode is off — switch to <b>Perception</b> or <b>Both</b> in Settings.</div>`;
    return;
  }
  const hasData = perc?.perception && Object.keys(perc.perception).length > 0;
  if (!hasData) {
    const distKm = perc?.closest_distance_km;
    if (distKm == null) {
      host.innerHTML = `<div class="perc-status-msg">No street-view analysis loaded.<br><span class="perc-status-hint">Run the download + VLM pipeline in Panel 2 first.</span></div>`;
    } else {
      const distM = Math.round(distKm * 1000);
      host.innerHTML = `<div class="perc-status-msg">Nearest data point is <b>${distM} m</b> away — agent is outside the analysed area.</div>`;
    }
    return;
  }

  const p = perc.perception!;

  // The perception contract no longer carries archetype-specific perspectives.
  const archHtml = '';

  // Generic scene fields — try DuckDB keys first, fall back to old JSON keys
  const formatField = (raw: unknown): string => {
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
    return display;
  };
  const priorityFields: Array<{ keys: string[]; label: string }> = [
    { keys: ['scene', 'scene_overview'],                label: 'Scene' },
    { keys: ['spatial_character', 'spatial_enclosure'], label: 'Spatial character' },
    { keys: ['greenery', 'vegetation'],                 label: 'Greenery' },
    { keys: ['crowdedness', 'pedestrian_activity'],     label: 'Crowdedness' },
    { keys: ['lighting', 'lighting_atmosphere'],        label: 'Lighting' },
    { keys: ['street_amenities'],                       label: 'Street amenities' },
    { keys: ['visible_text'],                           label: 'Signage/text' },
  ];
  const fieldsHtml = priorityFields.map(({ keys, label }) => {
    const raw = keys.map(k => p[k]).find(v => v != null && v !== '');
    if (!raw) return '';
    const display = formatField(raw);
    if (display.length < 2) return '';
    return `<div class="perc-field">
      <span class="perc-field-label">${escapeHtml(label)}</span>
      <span class="perc-field-value">${escapeHtml(display)}</span>
    </div>`;
  }).join('');

  const imgHtml = perc.image_url
    ? `<img src="${imgBase}${escapeHtml(String(perc.image_url))}" class="perc-image" alt="agent view">`
    : '';

  host.innerHTML = imgHtml + archHtml + (fieldsHtml || '');
}

function startSinglePlay(): void {
  if (state.singleAgent.id === null) { toast('Configure the agent first.', 'warning'); return; }
  if (resultsMode) exitResultsMode();
  state.singleAgent.playing = true;
  ($('#p4-play') as HTMLButtonElement).disabled = true;
  ($('#p4-pause') as HTMLButtonElement).disabled = false;
  ($('#p4-step') as HTMLButtonElement).disabled = true;
  ($('#p4-results') as HTMLButtonElement).disabled = true;
  if (p4StepTimer !== null) clearTimeout(p4StepTimer);
  p4StepTimer = window.setTimeout(stepSingle, 0);
}
function pauseSinglePlay(): void {
  state.singleAgent.playing = false;
  ($('#p4-play') as HTMLButtonElement).disabled = false;
  ($('#p4-pause') as HTMLButtonElement).disabled = true;
  ($('#p4-step') as HTMLButtonElement).disabled = false;
  ($('#p4-results') as HTMLButtonElement).disabled = false;
  if (p4StepTimer !== null) { clearTimeout(p4StepTimer); p4StepTimer = null; }
}
async function resetSingleAgent(): Promise<void> {
  try { await api.postJSON(api.lab, '/api/single-agent/reset', {}); } catch { /* ok */ }
  if (resultsMode) exitResultsMode();
  pauseSinglePlay();
  state.singleAgent.id = null;
  state.singleAgent.positionHistory = [];
  state.singleAgent.moodHistory = []; state.singleAgent.timeOverride = null;
  stepLog = []; lastStreamEvents = []; lastPercData = {};
  thoughtMarker?.remove(); thoughtMarker = null;
  state.singleAgent.start = null; state.singleAgent.target = null;
  if (startMarker)    { startMarker.remove();    startMarker = null; }
  if (targetMarker)   { targetMarker.remove();   targetMarker = null; }
  if (agentNavMarker) { agentNavMarker.remove(); agentNavMarker = null; agentNavArrowEl = null; }
  if (trailPopup) { trailPopup.remove(); trailPopup = null; }
  pickMode = null;
  map?.getCanvas().classList.remove('map-pick');
  map?.getSource('trail')?.setData({ type: 'FeatureCollection', features: [] });
  map?.getSource('trail-dots')?.setData({ type: 'FeatureCollection', features: [] });
  map?.getSource('planned')?.setData({ type: 'FeatureCollection', features: [] });
  map?.getSource('reachable')?.setData({ type: 'FeatureCollection', features: [] });
  map?.getSource('agents')?.setData({ type: 'FeatureCollection', features: [] });
  ($('#p4-pick-start') as HTMLButtonElement).classList.add('primary');
  ($('#p4-pick-target') as HTMLButtonElement).classList.remove('primary');
  ($('#p4-pick-target') as HTMLButtonElement).disabled = true;
  ($('#p4-config-status') as HTMLElement).textContent = 'Reset. Click Pick Start to begin again.';
  saveState();
}

/* =====================================================================
   RESULTS MODE — toggle that transforms panel 4 in-place
   ===================================================================== */

function toggleResultsMode(): void {
  if (resultsMode) exitResultsMode(); else void enterResultsMode();
}

function exitResultsMode(): void {
  resultsMode = false;
  resultsSummaries = {};
  resultsNarratives = { generic: undefined, historyAware: undefined };
  const btn = $('#p4-results') as HTMLButtonElement;
  btn.dataset['active'] = 'false';
  btn.textContent = 'Results';
  $('#panel-4')?.classList.remove('results-active');
  $('#p4-results-legend')?.classList.add('hidden');
  clearResultsOverlays();
  renderSinglePerception(lastPercData);
  const filtered = streamTab === 'all' ? lastStreamEvents : lastStreamEvents.filter(e => e.topic === streamTab);
  renderThoughts(filtered);
  renderStreamSummary(streamTab, lastStreamEvents);
}

async function enterResultsMode(): Promise<void> {
  if (state.singleAgent.id === null) return;
  resultsMode = true;
  resultsSummaries = {};
  const btn = $('#p4-results') as HTMLButtonElement;
  btn.dataset['active'] = 'true';
  btn.disabled = true;
  btn.textContent = 'Loading…';
  $('#panel-4')?.classList.add('results-active');
  $('#p4-results-legend')?.classList.remove('hidden');
  // Show shimmers immediately
  resultsNarratives = { generic: undefined, historyAware: undefined };
  renderResultsLeftPanel(undefined);
  renderResultsThoughtSummary(streamTab);
  buildResultsOverlays();
  // Fetch topic summaries + narratives in parallel
  const id = state.singleAgent.id;
  const [sums, narr] = await Promise.all([
    api.l<Record<string, string | null>>(`/api/agent/${id}/results-summary`).catch(() => null),
    api.l<{ generic?: string; history_aware?: string }>(`/api/agent/${id}/narrative-compare`).catch(() => null),
  ]);
  if (!resultsMode) return;
  resultsSummaries = sums || {};
  resultsNarratives = {
    generic: narr?.generic ?? null,
    historyAware: narr?.history_aware ?? null,
  };
  // Fill left panel vision summary
  renderResultsLeftPanel(resultsSummaries['vision'] ?? null);
  // Fill current tab thoughts summary (will inline narratives if tab === 'all')
  renderResultsThoughtSummary(streamTab);
  if (resultsMode) { btn.disabled = false; btn.textContent = 'Live View'; }
}

function renderResultsLeftPanel(visionSummary?: string | null): void {
  const host = $('#p4-perception') as HTMLElement;
  const percEvents = lastStreamEvents.filter(
    e => e.topic === 'perception' && e.metadata?.['source'] !== 'visual_satisfaction'
  );
  const amenityEvents = lastStreamEvents.filter(e => e.topic === 'amenity_visit');
  const mobEvents = lastStreamEvents.filter(e => e.topic === 'mobility');
  const onPath = mobEvents.filter(e => e.metadata?.['on_path'] === true);
  const llmMoves = mobEvents.filter(e => !e.metadata?.['fallback']);

  const statsHtml = `<div class="analysis-summary">
    <div class="analysis-summary-stat"><span class="lbl">Scene analyses</span><span class="val">${percEvents.length}</span></div>
    <div class="analysis-summary-stat"><span class="lbl">Amenity visits</span><span class="val">${amenityEvents.length}</span></div>
    <div class="analysis-summary-stat"><span class="lbl">Path adherence</span><span class="val">${mobEvents.length ? Math.round(onPath.length / mobEvents.length * 100) : 0}%</span></div>
    <div class="analysis-summary-stat"><span class="lbl">LLM-guided moves</span><span class="val">${mobEvents.length ? Math.round(llmMoves.length / mobEvents.length * 100) : 0}%</span></div>
  </div>`;

  let summaryHtml: string;
  if (visionSummary === undefined) {
    summaryHtml = `<div class="results-topic-summary loading"></div>`;
  } else if (visionSummary) {
    summaryHtml = `<div class="results-topic-summary">${escapeHtml(visionSummary)}</div>`;
  } else {
    summaryHtml = `<div class="results-topic-summary muted">No VLM scene analyses recorded yet.</div>`;
  }

  host.innerHTML = statsHtml + summaryHtml;
}

function renderResultsThoughtSummary(tab: string): void {
  const host = $('#p4-thoughts') as HTMLElement | null;
  if (!host) return;
  const text = resultsSummaries[tab];
  let summaryHtml: string;
  if (text === undefined) {
    summaryHtml = `<div class="results-topic-summary loading"></div>`;
  } else if (text) {
    summaryHtml = `<div class="results-topic-summary">${escapeHtml(text)}</div>`;
  } else {
    summaryHtml = `<div class="results-topic-summary muted">No events recorded for this topic.</div>`;
  }

  let narrativeHtml = '';
  if (tab === 'all') {
    const { generic, historyAware } = resultsNarratives;
    const gnHtml = generic === undefined
      ? `<div class="results-narrative-block loading" id="rn-generic"></div>`
      : `<div class="results-narrative-block" id="rn-generic"><h5>Generic narrative</h5>${escapeHtml(generic || 'Narrative unavailable.')}</div>`;
    const haHtml = historyAware === undefined
      ? `<div class="results-narrative-block loading" id="rn-history"></div>`
      : `<div class="results-narrative-block" id="rn-history"><h5>History-aware narrative</h5>${escapeHtml(historyAware || 'Narrative unavailable.')}</div>`;
    narrativeHtml = `<div class="results-narrative">${gnHtml}${haHtml}</div>`;
  }

  host.innerHTML = summaryHtml + narrativeHtml;
}


function buildResultsOverlays(): void {
  const ph = state.singleAgent.positionHistory;
  const mobEvents = lastStreamEvents.filter(e => e.topic === 'mobility');

  // Heatmap — every recorded position
  map?.getSource('results-heatmap')?.setData({
    type: 'FeatureCollection',
    features: ph.map(pos => ({ type: 'Feature', geometry: { type: 'Point', coordinates: pos }, properties: {} })),
  });
  map?.setLayoutProperty('results-heatmap-layer', 'visibility', 'visible');

  // Decision source lines — color each segment by LLM/perception/fallback
  const srcFeatures: object[] = [];
  for (let i = 0; i < Math.min(mobEvents.length, ph.length - 1); i++) {
    const ev = mobEvents[i];
    const from = ph[i];
    const to = ph[i + 1];
    if (!from || !to) continue;
    const m = ev.metadata || {};
    const src = m['fallback'] ? 'fallback' : m['perception_available'] ? 'perception' : 'llm';
    srcFeatures.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [from, to] },
      properties: { src },
    });
  }
  map?.getSource('results-decision-src')?.setData({ type: 'FeatureCollection', features: srcFeatures });
  map?.setLayoutProperty('results-decision-src-layer', 'visibility', 'visible');

  // Path deviations — group consecutive off-path steps into line segments
  const devFeatures: object[] = [];
  let devSeg: [number, number][] = [];
  for (let i = 0; i < Math.min(mobEvents.length, ph.length); i++) {
    const m = mobEvents[i]?.metadata || {};
    if (m['on_path'] === false && ph[i]) {
      devSeg.push(ph[i] as [number, number]);
    } else {
      if (devSeg.length >= 2) devFeatures.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: devSeg }, properties: {} });
      devSeg = [];
    }
  }
  if (devSeg.length >= 2) devFeatures.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: devSeg }, properties: {} });
  map?.getSource('results-deviations')?.setData({ type: 'FeatureCollection', features: devFeatures });
  map?.setLayoutProperty('results-deviations-layer', 'visibility', 'visible');

  // Goal changes — cognition/mobility events mentioning destination change, placed at matching stepLog position
  const goalFeatures: object[] = [];
  const cognEvents = lastStreamEvents.filter(e => e.topic === 'cognition');
  cognEvents.forEach(ev => {
    const entry = stepLog.find(s => s.step === ev.step)
      ?? (stepLog.length ? stepLog.reduce((b, s) => Math.abs(s.step - ev.step) < Math.abs(b.step - ev.step) ? s : b, stepLog[0]) : null);
    if (!entry) return;
    goalFeatures.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: entry.pos },
      properties: { description: ev.description, step: ev.step },
    });
  });
  map?.getSource('results-goal-changes')?.setData({ type: 'FeatureCollection', features: goalFeatures });
  map?.setLayoutProperty('results-goal-changes-layer', 'visibility', 'visible');

  // Decision points — mobility events where bearing changes significantly (> 45°)
  const decPtFeatures: object[] = [];
  for (let i = 1; i < ph.length - 1; i++) {
    const b1 = calcBearing(ph[i - 1], ph[i]);
    const b2 = calcBearing(ph[i], ph[i + 1]);
    let diff = Math.abs(b2 - b1);
    if (diff > 180) diff = 360 - diff;
    if (diff > 45) {
      decPtFeatures.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: ph[i] },
        properties: { step: i, bearing_change: Math.round(diff) },
      });
    }
  }
  map?.getSource('results-decision-pts')?.setData({ type: 'FeatureCollection', features: decPtFeatures });
  map?.setLayoutProperty('results-decision-pts-layer', 'visibility', 'visible');

  // Sync checkboxes with visibility state
  (['heatmap', 'decision-pts', 'decision-src', 'goal-changes', 'deviations'] as const).forEach(key => {
    const cb = $(`#rl-${key}`) as HTMLInputElement | null;
    if (cb) cb.checked = true;
  });
}

function clearResultsOverlays(): void {
  const empty = { type: 'FeatureCollection', features: [] };
  (['results-heatmap', 'results-decision-pts', 'results-decision-src',
    'results-goal-changes', 'results-deviations'] as const).forEach(src => {
    map?.getSource(src)?.setData(empty);
    const layerId = `${src}-layer`;
    map?.setLayoutProperty(layerId, 'visibility', 'none');
  });
}

/* =====================================================================
   PANEL 5 — Results Mode
   ===================================================================== */

function toggleP5ResultsMode(): void {
  if (p5ResultsMode) exitP5ResultsMode(); else void enterP5ResultsMode();
}

function exitP5ResultsMode(): void {
  p5ResultsMode = false;
  p5ResultsSummaries = {};
  const btn = $('#p5-results') as HTMLButtonElement | null;
  if (btn) { btn.dataset['active'] = 'false'; btn.textContent = 'Results'; }
  ($('#panel-5') as HTMLElement)?.classList.remove('results-active');
  $('#p5-results-legend')?.classList.add('hidden');
  const statsEl = $('#p5-results-stats') as HTMLElement | null;
  if (statsEl) statsEl.style.display = 'none';
  // Clear p5 trail overlays
  const empty = { type: 'FeatureCollection', features: [] };
  (['p5-trails', 'p5-decision-pts', 'results-heatmap'] as const).forEach(src => {
    map?.getSource(src)?.setData(empty);
  });
  map?.setLayoutProperty('p5-trails-layer', 'visibility', 'none');
  map?.setLayoutProperty('p5-decision-pts-layer', 'visibility', 'none');
  map?.setLayoutProperty('results-heatmap-layer', 'visibility', 'none');
}

async function enterP5ResultsMode(): Promise<void> {
  if (p5AgentHistories.size === 0) { toast('Spawn and run agents first.', 'warning'); return; }
  p5ResultsMode = true;
  const btn = $('#p5-results') as HTMLButtonElement | null;
  if (btn) { btn.dataset['active'] = 'true'; btn.disabled = true; btn.textContent = 'Loading…'; }
  ($('#panel-5') as HTMLElement)?.classList.add('results-active');
  $('#p5-results-legend')?.classList.remove('hidden');

  buildP5ResultsOverlays();
  renderP5ResultsStats();

  // If an agent is selected, also fetch their per-agent summary
  if (p5SelectedAgent !== null) {
    const id = p5SelectedAgent;
    try {
      const sums = await api.m<Record<string, string | null>>(`/api/agent/${id}/results-summary`).catch(() => null);
      p5ResultsSummaries = sums || {};
    } catch { /* ok */ }
  }

  if (btn) { btn.disabled = false; btn.textContent = 'Live View'; }
}

function buildP5ResultsOverlays(): void {
  // Aggregate heatmap from all agent histories
  const heatPoints: object[] = [];
  const trailFeatures: object[] = [];
  const decPtFeatures: object[] = [];

  p5AgentHistories.forEach((history, agentId) => {
    if (history.length < 2) return;
    const archChip = document.querySelector<HTMLElement>(`.agent-mini[data-id="${agentId}"]`);
    const arch = archChip?.dataset['arch'] ?? 'unknown';
    history.forEach(h => {
      heatPoints.push({ type: 'Feature', geometry: { type: 'Point', coordinates: h.pos }, properties: {} });
    });
    trailFeatures.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: history.map(h => h.pos) },
      properties: { archetype: arch },
    });
    // Decision points — bearing changes > 45°
    for (let i = 1; i < history.length - 1; i++) {
      const b1 = calcBearing(history[i - 1].pos, history[i].pos);
      const b2 = calcBearing(history[i].pos, history[i + 1].pos);
      let diff = Math.abs(b2 - b1);
      if (diff > 180) diff = 360 - diff;
      if (diff > 45) {
        decPtFeatures.push({ type: 'Feature', geometry: { type: 'Point', coordinates: history[i].pos }, properties: { archetype: arch } });
      }
    }
  });

  map?.getSource('results-heatmap')?.setData({ type: 'FeatureCollection', features: heatPoints });
  map?.setLayoutProperty('results-heatmap-layer', 'visibility', 'visible');

  map?.getSource('p5-trails')?.setData({ type: 'FeatureCollection', features: trailFeatures });
  const trailVisible = ($('#p5-rl-trails') as HTMLInputElement | null)?.checked !== false;
  map?.setLayoutProperty('p5-trails-layer', 'visibility', trailVisible ? 'visible' : 'none');

  map?.getSource('p5-decision-pts')?.setData({ type: 'FeatureCollection', features: decPtFeatures });
  const decPtVisible = ($('#p5-rl-decision-pts') as HTMLInputElement | null)?.checked !== false;
  map?.setLayoutProperty('p5-decision-pts-layer', 'visibility', decPtVisible ? 'visible' : 'none');
}

function renderP5ResultsStats(): void {
  const statsEl = $('#p5-results-stats') as HTMLElement | null;
  if (!statsEl) return;
  statsEl.style.display = 'flex';
  statsEl.style.flexDirection = 'column';
  statsEl.style.gap = '6px';

  const counts: Record<string, number> = {};
  let totalHistory = 0;
  p5AgentHistories.forEach((history, agentId) => {
    const archChip = document.querySelector<HTMLElement>(`.agent-mini[data-id="${agentId}"]`);
    const arch = archChip?.dataset['arch'] ?? 'unknown';
    counts[arch] = (counts[arch] ?? 0) + 1;
    totalHistory += history.length;
  });
  const agentCount = p5AgentHistories.size;
  const avgSteps = agentCount > 0 ? Math.round(totalHistory / agentCount) : 0;

  const archRows = Object.entries(counts).map(([arch, n]) => {
    const color = { resident: '#30d158', commuter: '#0a84ff', tourist: '#ff9f0a', student: '#ff375f' }[arch] ?? '#aaa';
    return `<span style="display:inline-flex;align-items:center;gap:4px;"><span style="width:8px;height:8px;border-radius:50%;background:${color};display:inline-block;"></span>${arch} ${n}</span>`;
  }).join(' · ');

  statsEl.innerHTML = `<div class="analysis-summary">
    <div class="analysis-summary-stat"><span class="lbl">Agents tracked</span><span class="val">${agentCount}</span></div>
    <div class="analysis-summary-stat"><span class="lbl">Avg steps/agent</span><span class="val">${avgSteps}</span></div>
    <div class="analysis-summary-stat"><span class="lbl">Current step</span><span class="val">${p5CurrentStep}</span></div>
  </div>
  <div style="font-size:11px;color:var(--text-muted);line-height:1.6;">${archRows || 'No agents'}</div>`;
}

/* =====================================================================
   PANEL 5 — Recording Playback
   ===================================================================== */

async function fetchRecordingList(): Promise<void> {
  const sel = $('#p5-playback-select') as HTMLSelectElement | null;
  const status = $('#p5-playback-status') as HTMLElement | null;
  const loadBtn = $('#p5-playback-load') as HTMLButtonElement | null;
  if (!sel) return;
  if (status) status.textContent = 'Fetching…';
  try {
    const res = await api.m<{ files: { filename: string; rel_path: string; size_kb: number; modified: number }[] }>(
      '/api/recording/list');
    const files = res.files ?? [];
    // Preserve existing options that were already loaded this session
    const loadedKeys = new Set(p5LoadedRecordings.keys());
    sel.innerHTML = '<option value="">— select a session —</option>';
    files.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.rel_path;
      const label = f.filename.replace('.parquet', '');
      opt.textContent = loadedKeys.has(f.rel_path) ? `✓ ${label} (${f.size_kb}kb)` : `${label} (${f.size_kb}kb)`;
      sel.appendChild(opt);
    });
    // Re-select active recording if any
    if (p5ActiveRecording) sel.value = p5ActiveRecording;
    if (status) status.textContent = files.length > 0 ? `${files.length} session${files.length !== 1 ? 's' : ''} found.` : 'No recordings saved yet.';
    if (loadBtn) loadBtn.disabled = !sel.value;
  } catch (e) {
    if (status) status.textContent = `Error: ${e instanceof Error ? e.message : e}`;
  }
}

async function loadAndShowRecording(filename: string): Promise<void> {
  const status = $('#p5-playback-status') as HTMLElement | null;
  const loadBtn = $('#p5-playback-load') as HTMLButtonElement | null;
  if (!filename) return;

  if (p5LoadedRecordings.has(filename)) {
    showRecordingOnMap(filename);
    return;
  }

  if (loadBtn) loadBtn.disabled = true;
  if (status) status.textContent = 'Loading…';
  try {
    const res = await api.m<{ session?: string; total_steps?: number; agents?: P5RecAgent[]; error?: string }>(
      `/api/recording/load?filename=${encodeURIComponent(filename)}`);
    if (res.error) { if (status) status.textContent = `Error: ${res.error}`; return; }
    const data: P5RecordingData = {
      session: res.session ?? filename,
      totalSteps: res.total_steps ?? 0,
      agents: res.agents ?? [],
    };
    p5LoadedRecordings.set(filename, data);
    p5ActiveRecording = filename;
    showRecordingOnMap(filename);
    // Update the dropdown option to mark as loaded
    const sel = $('#p5-playback-select') as HTMLSelectElement | null;
    if (sel) {
      const opt = sel.querySelector<HTMLOptionElement>(`option[value="${CSS.escape(filename)}"]`);
      if (opt) opt.textContent = `✓ ${opt.textContent?.replace(/^✓\s*/, '')}`;
    }
  } catch (e) {
    if (status) status.textContent = `Load failed: ${e instanceof Error ? e.message : e}`;
  } finally {
    if (loadBtn) loadBtn.disabled = false;
  }
}

const p5EnabledDatasets = new Set<string>();
let p5TrailClickBound = false;
let recTrailPopup: MapboxPopup | null = null;

function getMergedRecordingData(store: Map<string, P5RecordingData>): P5RecordingData {
  const agents: P5RecAgent[] = [];
  let maxSteps = 0;
  const sessions: string[] = [];
  for (const [key, data] of store) {
    if (!p5EnabledDatasets.has(key)) continue;
    sessions.push(data.session);
    agents.push(...data.agents.map(a => Object.assign({}, a, { _datasetKey: key, _datasetSession: data.session })));
    maxSteps = Math.max(maxSteps, data.totalSteps);
  }
  return { session: sessions.join(' + '), totalSteps: maxSteps, agents };
}

interface RecordingSummary {
  moodHistory: string[];
  dominantMood: string;
  avgCuriosity: number | null;
  avgFatigue: number | null;
  avgNeeds: Record<string, number>;
}

function computeRecordingSummary(agents: P5RecAgent[]): RecordingSummary {
  const moodHistory = agents.flatMap(a => a.moodHistory ?? []);
  const counts: Record<string, number> = {};
  moodHistory.forEach(m => { counts[m] = (counts[m] || 0) + 1; });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const dominantMood = sorted[0]?.[0] ?? '—';

  const cogHist = agents.flatMap(a => a.cognitionHistory ?? []);
  const avgCuriosity = cogHist.length
    ? cogHist.reduce((s, c) => s + c.curiosity, 0) / cogHist.length
    : null;
  const avgFatigue = cogHist.length
    ? cogHist.reduce((s, c) => s + c.fatigue, 0) / cogHist.length
    : null;

  const allLastNeeds = agents
    .map(a => a.needsHistory?.[a.needsHistory.length - 1])
    .filter((n): n is Record<string, number> => n !== undefined);
  const avgNeeds: Record<string, number> = {};
  if (allLastNeeds.length) {
    for (const n of allLastNeeds) {
      for (const [k, v] of Object.entries(n)) {
        avgNeeds[k] = (avgNeeds[k] ?? 0) + v;
      }
    }
    for (const k of Object.keys(avgNeeds)) avgNeeds[k] /= allLastNeeds.length;
  }

  return { moodHistory, dominantMood, avgCuriosity, avgFatigue, avgNeeds };
}

function renderP4RecordingSummary(agents: P5RecAgent[]): void {
  if (!agents.length) return;
  const summary = computeRecordingSummary(agents);
  if (summary.moodHistory.length) {
    state.singleAgent.moodHistory = summary.moodHistory;
    renderEmotionPie('#p4-emotion-svg', '#p4-emotion-legend', summary.moodHistory);
  }
  ($('#p4-mood') as HTMLElement).textContent = summary.dominantMood;
  ($('#p4-curiosity') as HTMLElement).textContent = fmtPct(summary.avgCuriosity);
  ($('#p4-fatigue') as HTMLElement).textContent = fmtPct(summary.avgFatigue);
  renderNeedsBar($('#p4-needs') as HTMLElement, summary.avgNeeds);
}

function renderP5RecordingSummary(agents: P5RecAgent[]): void {
  if (!agents.length) return;
  const summary = computeRecordingSummary(agents);
  if (summary.moodHistory.length) {
    renderEmotionPie('#p5-emotion-svg', '#p5-emotion-legend', summary.moodHistory);
  }
  ($('#p5-det-mood') as HTMLElement).textContent = summary.dominantMood;
  ($('#p5-det-curiosity') as HTMLElement).textContent = fmtPct(summary.avgCuriosity);
  ($('#p5-det-fatigue') as HTMLElement).textContent = fmtPct(summary.avgFatigue);
  renderNeedsBar($('#p5-det-needs') as HTMLElement, summary.avgNeeds);
}

function findAgentInStores(agentId: number): P5RecAgent | undefined {
  for (const data of p5LoadedRecordings.values()) {
    const a = data.agents.find(ag => ag.id === agentId);
    if (a) return a;
  }
  for (const data of p4LoadedRecordings.values()) {
    const a = data.agents.find(ag => ag.id === agentId);
    if (a) return a;
  }
  return undefined;
}

function findAgentAcrossLayers(agentId: number): P5RecAgent[] {
  const results: P5RecAgent[] = [];
  for (const [key, data] of p5LoadedRecordings) {
    if (!p5EnabledDatasets.has(key)) continue;
    const a = data.agents.find(ag => ag.id === agentId);
    if (a) results.push(a);
  }
  for (const [key, data] of p4LoadedRecordings) {
    if (!p5EnabledDatasets.has(key)) continue;
    const a = data.agents.find(ag => ag.id === agentId);
    if (a) results.push(a);
  }
  return results;
}

function showRecordingOnMap(filename: string): void {
  const data = p5LoadedRecordings.get(filename) ?? p4LoadedRecordings.get(filename);
  if (!data) return;
  if (p5LoadedRecordings.has(filename)) p5ActiveRecording = filename;
  else p4ActiveRecording = filename;

  p5EnabledDatasets.add(filename);

  const merged = getMergedRecordingData(
    p4LoadedRecordings.has(filename) ? p4LoadedRecordings : p5LoadedRecordings);

  const allArchetypes = [...new Set(merged.agents.map(a => a.archetype))];
  p5RecordingFilterArchetypes = new Set(allArchetypes);

  buildRecordingOverlays(merged, p5RecordingFilterArchetypes);
  buildRecordingFilterChips(merged);
  const panel = p4LoadedRecordings.has(filename) ? 'p4' as const : 'p5' as const;
  buildDatasetList(p4LoadedRecordings.has(filename) ? p4LoadedRecordings : p5LoadedRecordings, panel);

  // Reflect the currently selected datasets in the Emotion Mix / Needs modules
  if (panel === 'p4') renderP4RecordingSummary(merged.agents);
  else renderP5RecordingSummary(merged.agents);

  const overlayId = panel === 'p4' ? '#p4-playback-overlays' : '#p5-playback-overlays';
  const overlays = $(overlayId) as HTMLElement | null;
  if (overlays) overlays.style.display = 'flex';

  if (merged.agents.length > 0 && map) {
    const allCoords = merged.agents.flatMap(a => a.positions);
    if (allCoords.length > 0) {
      const lons = allCoords.map(c => c[0]);
      const lats = allCoords.map(c => c[1]);
      const bounds: [[number, number], [number, number]] = [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ];
      (map as unknown as { fitBounds: (b: [[number, number], [number, number]], o: object) => void })
        .fitBounds(bounds, { padding: 60, maxZoom: 17, duration: 800 });
    }
  }

  if (!p5TrailClickBound) {
    p5TrailClickBound = true;
    map?.on('click', 'recording-trails-layer', (e: MapboxMapEvent) => {
      if (!map) return;
      (e.originalEvent as Event).stopPropagation();
      if (amenityPopup) amenityPopup.remove();
      if (trailPopup) trailPopup.remove();
      const feats = map.queryRenderedFeatures([e.point.x, e.point.y] as [number, number], { layers: ['recording-trails-layer'] });
      const feat = feats[0];
      if (!feat) return;
      const agentId = feat.properties['agentId'] as number | undefined;
      if (agentId == null) return;
      const agentsForId = findAgentAcrossLayers(agentId);
      if (!agentsForId.length) return;
      const currentStore = p5LoadedRecordings.size > 0 ? p5LoadedRecordings : p4LoadedRecordings;
      const currentMerged = getMergedRecordingData(currentStore);
      populateP5FromRecording(agentsForId, currentMerged);

      const agent = agentsForId[0];
      const clickLng = (e.lngLat as unknown as { lng: number; lat: number }).lng;
      const clickLat = (e.lngLat as unknown as { lng: number; lat: number }).lat;
      let closestIdx = 0, closestDist = Infinity;
      agent.positions.forEach((pos, i) => {
        const d = (pos[0] - clickLng) ** 2 + (pos[1] - clickLat) ** 2;
        if (d < closestDist) { closestDist = d; closestIdx = i; }
      });
      const step = closestIdx + 1;

      const allEvents = agentsForId.flatMap(a => a.streamEvents ?? []);
      let stepEvent: (typeof allEvents)[number] | undefined;
      if (allEvents.length) {
        stepEvent = allEvents.filter(ev => ev.step === step).pop();
        if (!stepEvent) {
          let bestDist = Infinity;
          for (const ev of allEvents) {
            const d = Math.abs(ev.step - step);
            if (d < bestDist) { bestDist = d; stepEvent = ev; }
          }
        }
      }
      const cog = agent.cognitionHistory?.[closestIdx];
      const needsAtStep = agent.needsHistory?.[closestIdx];
      const satReason = agent.satisfactionHistory?.[closestIdx] || '';
      const moodStr = cog?.mood ?? 'neutral';
      const moodColor = getMoodColor(moodStr);
      const fmtPct = (v: number | null | undefined) => v == null ? '—' : `${Math.round(v * 100)}%`;
      const needsHtml = needsAtStep
        ? Object.entries(needsAtStep).map(([k, v]) => `
            <div class="trail-need-row">
              <span class="trail-need-label">${escapeHtml(k)}</span>
              <div class="trail-need-bar"><div class="trail-need-fill" style="width:${Math.round((v as number) * 100)}%;background:${(v as number) < 0.3 ? 'var(--danger)' : (v as number) < 0.6 ? 'var(--warning)' : 'var(--success)'}"></div></div>
              <span class="trail-need-val">${fmtPct(v as number)}</span>
            </div>`).join('')
        : '<span style="color:var(--text-muted);font-size:11px">not recorded</span>';
      const satHtml = satReason
        ? `<div class="trail-popup-section-title">Satisfaction</div>
           <div class="trail-popup-desc">${escapeHtml(satReason)}</div>`
        : '';
      const datasetSession = (feat.properties['datasetSession'] as string) ?? '';
      const datasetChip = datasetSession
        ? `<span class="dataset-chip">${escapeHtml(datasetSession)}</span>`
        : '';
      const popupHtml = `<div class="trail-popup-inner">
        <div class="trail-popup-header">
          <span class="step-badge">#${step}</span>
          <span class="mood-dot" style="background:${moodColor}"></span>
          <span class="mood-label" style="color:${moodColor}">${escapeHtml(moodStr)}</span>
          <span class="topic-chip">${escapeHtml(stepEvent?.topic ?? '')}</span>
          ${datasetChip}
        </div>
        <div class="trail-popup-desc">${escapeHtml(stepEvent?.description ?? '—')}</div>
        <div class="trail-popup-section-title">Cognition</div>
        <div class="trail-cognition-row">
          <span>Curiosity</span><span>${fmtPct(cog?.curiosity)}</span>
          <span>Fatigue</span><span>${fmtPct(cog?.fatigue)}</span>
        </div>
        <div class="trail-popup-section-title">Needs</div>
        ${needsHtml}
        ${satHtml}
      </div>`;
      if (recTrailPopup) recTrailPopup.remove();
      recTrailPopup = new mapboxgl.Popup({ className: 'trail-popup', closeButton: true, maxWidth: '320px' })
        .setLngLat(agent.positions[closestIdx] as [number, number])
        .setHTML(popupHtml)
        .addTo(map as unknown as Parameters<MapboxPopup['addTo']>[0]) as MapboxPopup;

      const thoughtEls = document.querySelectorAll('#p5-det-thoughts .thought');
      thoughtEls.forEach(el => el.classList.remove('highlighted'));
      if (stepEvent) {
        for (const el of thoughtEls) {
          if (el.textContent?.includes(stepEvent.description)) {
            el.classList.add('highlighted');
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            break;
          }
        }
      }
    });
  }

  const status = $('#p5-playback-status') as HTMLElement | null;
  const dsCount = p5EnabledDatasets.size;
  if (status) status.textContent = `${dsCount} dataset${dsCount !== 1 ? 's' : ''} · ${merged.agents.length} agents · ${merged.totalSteps} steps`;
}

function buildDatasetList(store: Map<string, P5RecordingData>, panel: 'p4' | 'p5'): void {
  const container = $(`#${panel}-dataset-list`) as HTMLElement | null;
  if (!container) return;
  container.innerHTML = '';
  if (store.size === 0) return;
  const colors = ['#64d2ff', '#ff9f0a', '#30d158', '#ff375f', '#bf5af2', '#ffd60a'];
  let ci = 0;
  for (const [key, data] of store) {
    const enabled = p5EnabledDatasets.has(key);
    const color = colors[ci++ % colors.length];
    const row = document.createElement('label');
    row.className = 'dataset-row';
    row.style.opacity = enabled ? '1' : '0.45';
    row.innerHTML = `
      <input type="checkbox" ${enabled ? 'checked' : ''} />
      <span class="dataset-dot" style="background:${color}"></span>
      <span class="dataset-name">${escapeHtml(data.session)}</span>
      <span class="dataset-meta">${data.agents.length} agent${data.agents.length !== 1 ? 's' : ''} · ${data.totalSteps} steps</span>
      <button class="dataset-remove" title="Remove">×</button>`;
    const cb = row.querySelector('input') as HTMLInputElement;
    cb.addEventListener('change', () => {
      if (cb.checked) p5EnabledDatasets.add(key);
      else p5EnabledDatasets.delete(key);
      refreshMergedOverlays(store, panel);
    });
    const removeBtn = row.querySelector('.dataset-remove') as HTMLButtonElement;
    removeBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      store.delete(key);
      p5EnabledDatasets.delete(key);
      refreshMergedOverlays(store, panel);
    });
    container.appendChild(row);
  }
}

function refreshMergedOverlays(store: Map<string, P5RecordingData>, panel: 'p4' | 'p5' = 'p5'): void {
  const merged = getMergedRecordingData(store);
  const allArchetypes = [...new Set(merged.agents.map(a => a.archetype))];
  p5RecordingFilterArchetypes = new Set(allArchetypes);
  buildRecordingOverlays(merged, p5RecordingFilterArchetypes);
  buildRecordingFilterChips(merged);
  buildDatasetList(store, panel);
  // Keep Emotion Mix / Needs in sync with the currently checked datasets
  if (panel === 'p4') renderP4RecordingSummary(merged.agents);
  else renderP5RecordingSummary(merged.agents);
  const statusSel = panel === 'p4' ? '#p4-playback-status' : '#p5-playback-status';
  const status = $(statusSel) as HTMLElement | null;
  const dsCount = p5EnabledDatasets.size;
  if (status) status.textContent = `${dsCount} dataset${dsCount !== 1 ? 's' : ''} · ${merged.agents.length} agents · ${merged.totalSteps} steps`;
}

function getRecToggleState(layer: 'heatmap' | 'trails' | 'decision-pts'): boolean {
  const prefix = state.currentPanel === 4 ? 'p4' : 'p5';
  const el = $(`#${prefix}-rec-rl-${layer}`) as HTMLInputElement | null;
  return el?.checked !== false;
}

function buildRecordingOverlays(data: P5RecordingData, filterSet: Set<string>): void {
  const filtered = data.agents.filter(a => filterSet.has(a.archetype));

  // Trails
  const trailFeatures = filtered
    .filter(a => a.positions.length >= 2)
    .map(a => ({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: a.positions },
      properties: { archetype: a.archetype, agentId: a.id, datasetSession: (a as unknown as Record<string, unknown>)._datasetSession ?? '' },
    }));
  map?.getSource('recording-trails')?.setData({ type: 'FeatureCollection', features: trailFeatures });
  map?.setLayoutProperty('recording-trails-layer', 'visibility', getRecToggleState('trails') ? 'visible' : 'none');

  // Heatmap — bin positions to a ~11m grid and weight cells by log(dwell count) so
  // genuine hotspots stand out instead of every visited street reading equally hot.
  const cells = new Map<string, { lon: number; lat: number; count: number }>();
  filtered.forEach(a =>
    a.positions.forEach(pos => {
      const key = `${pos[0].toFixed(4)},${pos[1].toFixed(4)}`;
      const cell = cells.get(key);
      if (cell) cell.count++;
      else cells.set(key, { lon: pos[0], lat: pos[1], count: 1 });
    })
  );
  let maxCount = 0;
  cells.forEach(c => { if (c.count > maxCount) maxCount = c.count; });
  const logMax = Math.log1p(maxCount);
  const heatFeatures = maxCount === 0 ? [] : Array.from(cells.values()).map(c => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [c.lon, c.lat] },
    properties: { w: Math.log1p(c.count) / logMax },
  }));
  map?.getSource('recording-heatmap')?.setData({ type: 'FeatureCollection', features: heatFeatures });
  map?.setLayoutProperty('recording-heatmap-layer', 'visibility', getRecToggleState('heatmap') ? 'visible' : 'none');

  // Decision points — bearing changes > 45°
  const decFeatures: object[] = [];
  filtered.forEach(a => {
    for (let i = 1; i < a.positions.length - 1; i++) {
      const b1 = calcBearing(a.positions[i - 1], a.positions[i]);
      const b2 = calcBearing(a.positions[i], a.positions[i + 1]);
      let diff = Math.abs(b2 - b1);
      if (diff > 180) diff = 360 - diff;
      if (diff > 45) {
        decFeatures.push({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: a.positions[i] },
          properties: { archetype: a.archetype },
        });
      }
    }
  });
  map?.getSource('recording-decision-pts')?.setData({ type: 'FeatureCollection', features: decFeatures });
  map?.setLayoutProperty('recording-decision-pts-layer', 'visibility', getRecToggleState('decision-pts') ? 'visible' : 'none');
}

function buildRecordingFilterChips(data: P5RecordingData): void {
  const filterId = state.currentPanel === 4 ? '#p4-playback-filters' : '#p5-playback-filters';
  const container = $(filterId) as HTMLElement | null;
  if (!container) return;
  const archetypeColorMap: Record<string, string> = {
    resident: '#30d158', commuter: '#0a84ff', tourist: '#ff9f0a', student: '#ff375f',
  };
  const archetypes = [...new Set(data.agents.map(a => a.archetype))];
  container.innerHTML = '';
  archetypes.forEach(arch => {
    const count = data.agents.filter(a => a.archetype === arch).length;
    const color = archetypeColorMap[arch] ?? '#aaa';
    const chip = document.createElement('button');
    chip.className = 'chip';
    chip.dataset['arch'] = arch;
    chip.style.cssText = `border-left: 3px solid ${color}; padding-left: 5px; font-size: 11px;`;
    chip.textContent = `${arch} ${count}`;
    chip.addEventListener('click', () => {
      const active = chip.dataset['active'] !== 'false';
      chip.dataset['active'] = active ? 'false' : 'true';
      chip.style.opacity = active ? '0.35' : '1';
      if (active) p5RecordingFilterArchetypes.delete(arch);
      else p5RecordingFilterArchetypes.add(arch);
      if (p5ActiveRecording) {
        const d = p5LoadedRecordings.get(p5ActiveRecording);
        if (d) buildRecordingOverlays(d, p5RecordingFilterArchetypes);
      }
    });
    container.appendChild(chip);
  });
}

/* =====================================================================
   PANEL 5 — Multi Agent
   ===================================================================== */
const archetypeColors: Record<string, string> = {
  resident: '#30d158', commuter: '#0a84ff',
  tourist: '#ff9f0a',  student: '#ff375f',
};
interface P5RecAgent {
  id: number; archetype: string; positions: [number, number][];
  start: [number, number] | null; target: [number, number] | null;
  moodHistory?: string[];
  cognitionHistory?: { mood: string; curiosity: number; fatigue: number }[];
  needsHistory?: Record<string, number>[];
  streamEvents?: StreamEvent[];
  satisfactionHistory?: string[];
}
interface P5RecordingData { session: string; totalSteps: number; agents: P5RecAgent[]; }

async function importRecordingFile(file: File, panel: 'p4' | 'p5'): Promise<void> {
  const statusSel = panel === 'p4' ? '#p4-playback-status' : '#p5-playback-status';
  const status = $(statusSel) as HTMLElement | null;
  if (status) status.textContent = 'Uploading…';
  try {
    const base = panel === 'p4' ? api.lab : api.map;
    const form = new FormData();
    form.append('file', file);
    const resp = await fetch(`${base}/api/recording/upload`, { method: 'POST', body: form });
    if (!resp.ok) { if (status) status.textContent = `Server error ${resp.status} — restart the server`; return; }
    const raw = await resp.json() as Record<string, unknown>;
    if (raw.error) { if (status) status.textContent = `Error: ${raw.error}`; return; }
    const agents = (raw.agents ?? []) as P5RecAgent[];
    if (!agents.length) { if (status) status.textContent = 'No agents found in file.'; return; }
    const data: P5RecordingData = {
      session: (raw.session as string) ?? file.name.replace(/\.parquet$/i, ''),
      totalSteps: (raw.total_steps as number) ?? 0,
      agents,
    };
    const key = `import:${file.name}`;
    if (panel === 'p4') {
      p4LoadedRecordings.set(key, data);
      p4ActiveRecording = key;
    } else {
      p5LoadedRecordings.set(key, data);
      p5ActiveRecording = key;
    }
    showRecordingOnMap(key);

    if (panel === 'p4' && agents.length > 0) {
      populateP4FromRecording(agents[0]);
      renderP4RecordingSummary(getMergedRecordingData(p4LoadedRecordings).agents);
    }
    if (panel === 'p5' && agents.length > 0) {
      populateP5FromRecording([agents[0]], data);
      renderP5RecordingSummary(getMergedRecordingData(p5LoadedRecordings).agents);
    }
    if (status) status.textContent = `Imported: ${data.agents.length} agent(s), ${data.totalSteps} steps`;
  } catch (e) {
    if (status) status.textContent = `Import failed: ${e instanceof Error ? e.message : e}`;
  }
}

function populateP4FromRecording(agent: P5RecAgent): void {
  streamTab = 'all';
  $('#p4-stream-tabs')?.querySelectorAll('button').forEach(b => {
    b.setAttribute('aria-selected', b.getAttribute('data-v') === 'all' ? 'true' : 'false');
  });

  if (agent.moodHistory?.length) {
    state.singleAgent.moodHistory = agent.moodHistory;
    renderEmotionPie('#p4-emotion-svg', '#p4-emotion-legend', agent.moodHistory);
  }
  if (agent.needsHistory?.length) {
    const lastNeeds = agent.needsHistory[agent.needsHistory.length - 1];
    renderNeedsBar($('#p4-needs') as HTMLElement, lastNeeds);
  }
  if (agent.streamEvents?.length) {
    lastStreamEvents = agent.streamEvents;
    renderThoughts(lastStreamEvents);
    renderStreamSummary('all', lastStreamEvents);
  }
  const moodLast = agent.moodHistory?.[agent.moodHistory.length - 1] ?? '—';
  ($('#p4-mood') as HTMLElement).textContent = moodLast;

  // Trail line on map
  if (agent.positions.length >= 2) {
    map?.getSource('trail')?.setData({
      type: 'FeatureCollection',
      features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: agent.positions }, properties: {} }],
    });
  }

  // Clickable step dots on map
  const dotFeatures = agent.positions.map((pos, i) => {
    const cog = agent.cognitionHistory?.[i];
    const mood = cog?.mood ?? agent.moodHistory?.[i] ?? 'neutral';
    const needs = agent.needsHistory?.[i];
    const streamEv = agent.streamEvents?.find(e => e.step === i + 1);
    const desc = streamEv?.description
      || (needs ? `${mood} · hunger ${(needs.hunger ?? 0).toFixed(2)}, energy ${(needs.energy ?? 0).toFixed(2)}` : mood);
    return {
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: pos },
      properties: {
        step: i + 1,
        topic: streamEv?.topic ?? 'step',
        description: desc,
        mood,
        curiosity: cog?.curiosity ?? null,
        fatigue: cog?.fatigue ?? null,
        needs: needs ? JSON.stringify(needs) : null,
      },
    };
  });
  map?.getSource('trail-dots')?.setData({ type: 'FeatureCollection', features: dotFeatures });

  // Fit map to recording bounds
  if (agent.positions.length > 0 && map) {
    const lons = agent.positions.map(c => c[0]);
    const lats = agent.positions.map(c => c[1]);
    (map as unknown as { fitBounds: (b: [[number, number], [number, number]], o: object) => void })
      .fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
        { padding: 60, maxZoom: 17, duration: 800 });
  }
}

function populateP5FromRecording(agents: P5RecAgent[], data: P5RecordingData): void {
  const agent = agents[0];
  if (!agent) return;
  const detPanel = $('#p5-agent-detail') as HTMLElement | null;
  if (!detPanel) return;
  detPanel.style.display = '';
  const nameEl = $('#p5-det-name') as HTMLElement | null;
  if (nameEl) nameEl.textContent = agents.length > 1 ? `Agent ${agent.id} (avg of ${agents.length} layers)` : `Agent ${agent.id}`;
  const tagEl = $('#p5-det-tags') as HTMLElement | null;
  if (tagEl) tagEl.innerHTML = `<span class="chip">${agent.archetype}</span>`;

  const allMoods = agents.flatMap(a => a.moodHistory ?? []);
  if (allMoods.length) {
    const moodEl = $('#p5-det-mood') as HTMLElement | null;
    if (moodEl) moodEl.textContent = allMoods[allMoods.length - 1];
    renderEmotionPie('#p5-emotion-svg', '#p5-emotion-legend', allMoods);
  }

  const allLastNeeds = agents
    .map(a => a.needsHistory?.length ? a.needsHistory[a.needsHistory.length - 1] : null)
    .filter((n): n is Record<string, number> => n !== null);
  if (allLastNeeds.length) {
    const avg: Record<string, number> = {};
    for (const n of allLastNeeds) {
      for (const [k, v] of Object.entries(n)) {
        avg[k] = (avg[k] ?? 0) + v;
      }
    }
    for (const k of Object.keys(avg)) avg[k] /= allLastNeeds.length;
    const needsHost = $('#p5-det-needs') as HTMLElement | null;
    if (needsHost) renderNeedsBar(needsHost, avg);
  }

  const allEvents = agents.flatMap(a => a.streamEvents ?? []);
  if (allEvents.length) {
    allEvents.sort((a, b) => a.step - b.step);
    const host = $('#p5-det-thoughts') as HTMLElement | null;
    if (host) renderThoughtsInto(host, allEvents, 'all', allEvents, false);
  }
}

let p5Bound = false;
let p5StepTimer: number | null = null;
let p5Stepping = false;
let recordPollTimer: number | null = null;
let recordingBase = ''; // tracks which server owns the active recording session
const p5AgentHistories = new Map<number, { pos: [number, number]; step: number }[]>();
let p5CurrentStep = 0;
let p5ExpandedDetailId: string | null = null;
let p5LastStreamEvents: StreamEvent[] = [];
let p5ResultsMode = false;
let p5ResultsSummaries: Record<string, string | null> = {};
const p5LoadedRecordings = new Map<string, P5RecordingData>();
let p5ActiveRecording: string | null = null;
let p5HomeMarker: MapboxMarker | null = null;
let p5OfficeMarker: MapboxMarker | null = null;
let p5RecordingFilterArchetypes = new Set<string>(['resident', 'commuter', 'tourist', 'student']);

async function selectAgentDetail(agentId: number, hintArch?: string): Promise<void> {
  p5SelectedAgent = agentId;
  const grid = $('#p5-agent-grid') as HTMLElement | null;
  const det = $('#p5-agent-detail') as HTMLElement | null;
  if (grid) grid.style.display = 'none';
  if (det) det.style.display = 'flex';
  document.querySelectorAll<HTMLElement>('.agent-mini').forEach(el =>
    el.classList.toggle('selected', Number(el.dataset['id']) === agentId));

  const [mem, streamData, percData] = await Promise.all([
    api.m<Record<string, unknown>>(`/api/agent/${agentId}/memory`).catch(() => null),
    api.m<{ events: StreamEvent[] }>(`/api/agent/${agentId}/stream?n=100`).catch(() => null),
    api.m<{ image_url?: string; perception?: Record<string, unknown>; closest_distance_km?: number | null }>(
      `/api/agent/${agentId}/perception-text`).catch(() => null),
  ]);

  const profile = ((mem?.agent_profile as Record<string, unknown>) ?? {});
  const arch = String(profile['archetype'] ?? hintArch ?? 'unknown');
  const nameEl = $('#p5-det-name') as HTMLElement | null;
  const tagsEl = $('#p5-det-tags') as HTMLElement | null;
  if (nameEl) nameEl.textContent = `Agent ${agentId} · ${arch}`;
  if (tagsEl) tagsEl.innerHTML = buildProfileTagHTML({
    pace: String(profile['pace'] ?? ''),
    curiosity: String(profile['curiosity'] ?? ''),
    social: String(profile['social'] ?? ''),
    archetype: arch,
  });

  const cog = ((mem?.cognition_state as Record<string, unknown>) ?? {});
  const needs = ((mem?.needs as Record<string, number>) ?? {});
  const moodEl = $('#p5-det-mood') as HTMLElement | null;
  const curEl  = $('#p5-det-curiosity') as HTMLElement | null;
  const fatEl  = $('#p5-det-fatigue') as HTMLElement | null;
  if (moodEl) moodEl.textContent = String(cog['mood'] ?? '—');
  if (curEl) curEl.textContent = cog['curiosity'] != null ? `${Math.round(Number(cog['curiosity']) * 100)}%` : '—';
  if (fatEl) fatEl.textContent = cog['fatigue'] != null ? `${Math.round(Number(cog['fatigue']) * 100)}%` : '—';
  renderNeedsBar($('#p5-det-needs') as HTMLElement, needs);

  // Seed initial history from memory position so trail/fly-to works before any steps
  if (!p5AgentHistories.get(agentId)?.length) {
    const pos = mem?.position as { lon?: number; lat?: number } | undefined;
    if (pos?.lon != null && pos?.lat != null) {
      p5AgentHistories.set(agentId, [{ pos: [pos.lon as number, pos.lat as number], step: p5CurrentStep }]);
    }
  }

  const allEvents = streamData?.events ?? [];
  p5LastStreamEvents = allEvents;

  // Emotion pie — build mood history from cognition events (same as P4)
  const moodHist = allEvents
    .filter(e => e.topic === 'cognition')
    .map(e => {
      const meta = e.metadata as Record<string, unknown>;
      const cog = meta?.cognition as Record<string, unknown>;
      return String(cog?.mood ?? meta?.mood ?? e.description?.match(/mood[:\s]+(\w+)/i)?.[1] ?? '');
    })
    .filter(Boolean);
  if (moodHist.length === 0 && cog['mood']) moodHist.push(String(cog['mood']));
  renderEmotionPie('#p5-emotion-svg', '#p5-emotion-legend', moodHist);

  // Perception — same card as P4, using DuckDB data via multi-agent server
  renderSinglePerception(percData ?? {}, {
    hostSel: '#p5-perception',
    chipSel: '#p5-perc-mode-chip',
    archetype: arch,
    apiBase: api.map,
  });

  // Thought stream and summary — same rendering as P4
  void renderTimePhaseBanner('#p5-time-phase-stats');
  renderStreamSummary(p5DetTab, allEvents, '#p5-det-stream-summary');
  renderP5DetThoughts(allEvents, p5DetTab);

  // Home / work markers — all archetypes now have a home; non-residents also have work
  p5HomeMarker?.remove(); p5HomeMarker = null;
  p5OfficeMarker?.remove(); p5OfficeMarker = null;
  const homeLoc = mem?.home as Record<string, unknown> | undefined;
  const workLoc = mem?.work as Record<string, unknown> | undefined;
  if (map) {
    if (homeLoc?.lon != null && homeLoc?.lat != null) {
      const homeLabel = arch === 'tourist' ? 'Hotel' : 'Home';
      const popup = new (mapboxgl.Popup as unknown as new (o: object) => MapboxPopup)({ offset: 25, closeButton: false })
        .setHTML(`<div style="font-size:13px;font-weight:600;">${homeLabel}</div>`);
      p5HomeMarker = new (mapboxgl.Marker as unknown as new (o: object) => MapboxMarker)({ color: '#30d158', scale: 0.9 })
        .setLngLat([homeLoc.lon as number, homeLoc.lat as number])
        .setPopup(popup)
        .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
    }
    if (workLoc?.lon != null && workLoc?.lat != null) {
      const workLabel = arch === 'student' ? 'Campus' : arch === 'tourist' ? 'Attraction' : 'Office';
      const popup = new (mapboxgl.Popup as unknown as new (o: object) => MapboxPopup)({ offset: 25, closeButton: false })
        .setHTML(`<div style="font-size:13px;font-weight:600;">${workLabel}</div>`);
      p5OfficeMarker = new (mapboxgl.Marker as unknown as new (o: object) => MapboxMarker)({ color: '#0a84ff', scale: 0.9 })
        .setLngLat([workLoc.lon as number, workLoc.lat as number])
        .setPopup(popup)
        .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
    }
  }

  renderP5AgentTrail(agentId);
}

function renderP5DetThoughts(allEvents: StreamEvent[], tab: string): void {
  const host = $('#p5-det-thoughts') as HTMLElement | null;
  if (!host) return;
  const filtered = tab === 'all' ? allEvents : allEvents.filter(e => e.topic === tab);
  renderThoughtsInto(host, filtered, tab, allEvents, true);
  // Override click handlers to use this agent's position history
  const reversed = filtered.slice().reverse().filter(ev => !(ev.topic === 'perception' && (ev.metadata as Record<string, unknown>)?.source === 'visual_satisfaction'));
  host.querySelectorAll<HTMLElement>('.thought').forEach((div, i) => {
    const ev = reversed[i];
    if (ev && p5SelectedAgent !== null) {
      div.onclick = () => flyToP5AgentThought(p5SelectedAgent!, ev);
    }
  });
}

function clearAgentDetail(): void {
  p5SelectedAgent = null;
  const grid = $('#p5-agent-grid') as HTMLElement | null;
  const det = $('#p5-agent-detail') as HTMLElement | null;
  if (grid) grid.style.display = '';
  if (det) det.style.display = 'none';
  document.querySelectorAll('.agent-mini.selected').forEach(el => el.classList.remove('selected'));
  thoughtMarker?.remove();
  p5HomeMarker?.remove(); p5HomeMarker = null;
  p5OfficeMarker?.remove(); p5OfficeMarker = null;
  map?.getSource('trail')?.setData({ type: 'FeatureCollection', features: [] });
  map?.getSource('trail-dots')?.setData({ type: 'FeatureCollection', features: [] });
}

function updateP5DetailOverlay(sourceCard?: HTMLElement): void {
  if (!p5ExpandedDetailId) return;
  const overlay = $('#p5-detail-overlay') as HTMLElement;
  if (!overlay) return;
  const body = $('#p5-detail-body') as HTMLElement;
  if (!body) return;
  const card = sourceCard || document.querySelector<HTMLElement>(`#panel-5 [data-detail-id="${p5ExpandedDetailId}"]`);
  if (!card) return;
  body.innerHTML = '';
  Array.from(card.children).forEach((child) => {
    const el = child as HTMLElement;
    if (el.tagName === 'H4') return;
    if (el.classList.contains('expand-card-btn')) return;
    body.appendChild(child.cloneNode(true));
  });
  if (p5ExpandedDetailId === 'p5-emotion-cognition') {
    const svgSrc = document.querySelector<SVGElement>('#p5-emotion-svg');
    const svgDest = body.querySelector<SVGElement>('svg');
    if (svgSrc && svgDest) svgDest.innerHTML = svgSrc.innerHTML;
  }
  if (p5ExpandedDetailId === 'p5-thoughts') {
    const tabBar = body.querySelector('.stream-tab-bar');
    if (tabBar) {
      tabBar.querySelectorAll('button').forEach((btn) => {
        btn.addEventListener('click', () => {
          tabBar.querySelectorAll('button').forEach((b) => b.setAttribute('aria-selected', 'false'));
          btn.setAttribute('aria-selected', 'true');
          p5DetTab = btn.getAttribute('data-v') || 'all';
          renderStreamSummary(p5DetTab, p5LastStreamEvents, '#p5-det-stream-summary');
          renderP5DetThoughts(p5LastStreamEvents, p5DetTab);
          const srcThoughts = $('#p5-det-thoughts') as HTMLElement;
          const srcSummary = $('#p5-det-stream-summary') as HTMLElement;
          const dstThoughts = body.querySelector('#p5-det-thoughts') as HTMLElement;
          const dstSummary = body.querySelector('.stream-summary') as HTMLElement;
          if (srcThoughts && dstThoughts) dstThoughts.innerHTML = srcThoughts.innerHTML;
          if (srcSummary && dstSummary) dstSummary.innerHTML = srcSummary.innerHTML;
        });
      });
    }
  }
}

function panel5Enter(): void {
  clearAgentDetail();
  syncPanel5UI();
  bindPanel5();
  refreshSpawnPins();
  void refreshAgentList();
}

function syncPanel5UI(): void {
  const m = state.multiAgent;
  ($('#p5-count') as HTMLInputElement).value = String(m.count);
  ($('#p5-count-label') as HTMLElement).textContent = String(m.count);
  $$('#p5-spawn-mode button').forEach(b =>
    b.setAttribute('aria-selected', b.getAttribute('data-v') === m.spawnMode ? 'true' : 'false'));
  $$('#p5-pin-mode button').forEach(b =>
    b.setAttribute('aria-selected', b.getAttribute('data-v') === m.pinMode ? 'true' : 'false'));
  (['resident', 'commuter', 'tourist', 'student'] as const).forEach(k => {
    const v = m.archetypeMix[k] ?? 0.25;
    ($(`#p5-mix-${k}`) as HTMLInputElement).value = String(v);
    ($(`#p5-mix-${k}-v`) as HTMLElement).textContent = `${Math.round(v * 100)}%`;
  });
  ($('#p5-speed') as HTMLInputElement).value = String(m.speed);
  ($('#p5-speed-label') as HTMLElement).textContent = `${m.speed.toFixed(2)}×`;
  updateSpawnHint();
}

function bindPanel5(): void {
  if (p5Bound) return; p5Bound = true;
  // Agent detail panel: tab bar + back button
  $$('#p5-det-tabs button').forEach((btn) => {
    btn.addEventListener('click', () => {
      p5DetTab = btn.getAttribute('data-v') ?? 'all';
      $$('#p5-det-tabs button').forEach(b => b.removeAttribute('aria-selected'));
      btn.setAttribute('aria-selected', 'true');
      if (p5SelectedAgent !== null) void selectAgentDetail(p5SelectedAgent);
    });
  });
  $('#p5-det-back')?.addEventListener('click', clearAgentDetail);
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

  // Pin mode (home / work) toggle
  $$('#p5-pin-mode button').forEach((b) => b.addEventListener('click', () => {
    state.multiAgent.pinMode = b.getAttribute('data-v') as 'home' | 'work';
    saveState();
    $$('#p5-pin-mode button').forEach((x) =>
      x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
    updateSpawnHint();
  }));

  (['resident', 'commuter', 'tourist', 'student'] as const).forEach((k) => {
    const inp = $(`#p5-mix-${k}`) as HTMLInputElement;
    const lbl = $(`#p5-mix-${k}-v`) as HTMLElement;
    inp.addEventListener('input', (e) => {
      const v = +(e.target as HTMLInputElement).value;
      state.multiAgent.archetypeMix[k] = v;
      lbl.textContent = `${Math.round(v * 100)}%`;
      saveState();
      updateSpawnHint();
    });
  });
  $('#p5-spawn')!.addEventListener('click', doMultiSpawn);
  $('#p5-clear-pins')?.addEventListener('click', () => {
    state.multiAgent.spawnPoints = [];
    state.multiAgent.homePoints = [];
    state.multiAgent.workPoints = [];
    saveState(); refreshSpawnPins(); updateSpawnHint();
    toast('Pins cleared', 'success');
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
  $('#p5-rec-start')!.addEventListener('click', () => startRecording('p5'));
  $('#p5-rec-stop')!.addEventListener('click', stopRecording);
  $('#p5-results')!.addEventListener('click', toggleP5ResultsMode);

  // P5 rec panel tab switching
  $('#p5-rec-tabs')?.querySelectorAll<HTMLButtonElement>('button').forEach(btn => {
    btn.addEventListener('click', () => {
      const v = btn.dataset.v;
      $('#p5-rec-tabs')?.querySelectorAll('button').forEach(b => b.setAttribute('aria-selected', 'false'));
      btn.setAttribute('aria-selected', 'true');
      const recTab    = $('#p5-rec-tab-record')  as HTMLElement | null;
      const replayTab = $('#p5-rec-tab-replay')  as HTMLElement | null;
      if (recTab)    recTab.style.display    = v === 'record' ? '' : 'none';
      if (replayTab) replayTab.style.display = v === 'replay' ? '' : 'none';
      if (v === 'replay') { /* noop — user imports file */ }
    });
  });

  // P5 file import
  $('#p5-import-btn')?.addEventListener('click', () => {
    ($('#p5-import-file') as HTMLInputElement)?.click();
  });
  $('#p5-import-file')?.addEventListener('change', (e) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (file) void importRecordingFile(file, 'p5');
    (e.target as HTMLInputElement).value = '';
  });

  // P5 results legend checkboxes
  $('#p5-rl-heatmap')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('results-heatmap-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });
  $('#p5-rl-trails')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('p5-trails-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });
  $('#p5-rl-decision-pts')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('p5-decision-pts-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });

  // Recording playback layer toggles
  $('#p5-rec-rl-heatmap')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('recording-heatmap-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });
  $('#p5-rec-rl-trails')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('recording-trails-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });
  $('#p5-rec-rl-decision-pts')?.addEventListener('change', (e) => {
    map?.setLayoutProperty('recording-decision-pts-layer', 'visibility',
      (e.target as HTMLInputElement).checked ? 'visible' : 'none');
  });

  // Expand metric cards into floating overlay (same as panel 4)
  document.querySelectorAll<HTMLElement>('#panel-5 .expandable-card .expand-card-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const card = btn.closest<HTMLElement>('[data-detail-id]');
      if (!card) return;
      p5ExpandedDetailId = card.dataset['detailId'] || null;
      const overlay = $('#p5-detail-overlay') as HTMLElement;
      const title = $('#p5-detail-title') as HTMLElement;
      if (!overlay || !title) return;
      const h4Text = card.querySelector('h4')?.firstChild?.textContent?.trim() || 'Detail';
      title.textContent = h4Text;
      overlay.classList.remove('hidden');
      updateP5DetailOverlay(card);
    });
  });
  $('#p5-detail-close')?.addEventListener('click', () => {
    ($('#p5-detail-overlay') as HTMLElement)?.classList.add('hidden');
    p5ExpandedDetailId = null;
  });
  ($('#p5-detail-overlay') as HTMLElement)?.addEventListener('click', (e) => {
    if ((e.target as HTMLElement).id === 'p5-detail-overlay') {
      ($('#p5-detail-overlay') as HTMLElement)?.classList.add('hidden');
      p5ExpandedDetailId = null;
    }
  });
}

function updateSpawnHint(): void {
  const mode = state.multiAgent.spawnMode;
  const m = state.multiAgent;
  const hints: Record<MultiAgentState['spawnMode'], string> = {
    random:    'Random respawn on the walk network.',
    click:     'Click anywhere on the map to drop spawn pins.',
    poi:       'Spawn agents at random amenities (mix-weighted).',
    home_work: 'Click → home pin · Shift+click → work pin.',
  };
  ($('#p5-spawn-hint') as HTMLElement).textContent = hints[mode];
  ($('#p5-mix-panel') as HTMLElement).style.display = (mode === 'poi') ? '' : 'none';

  const hwPanel = $('#p5-homework-panel') as HTMLElement;
  if (mode === 'home_work') {
    hwPanel.style.display = '';
    $$('#p5-pin-mode button').forEach((b) =>
      b.setAttribute('aria-selected', b.getAttribute('data-v') === m.pinMode ? 'true' : 'false'));
    const maxHome = Math.round(m.count * (m.archetypeMix.resident ?? 0));
    const maxWork = Math.round(m.count * (m.archetypeMix.commuter ?? 0));
    ($('#p5-pin-status') as HTMLElement).textContent =
      `Home: ${m.homePoints.length}/${maxHome} · Work: ${m.workPoints.length}/${maxWork}`;
  } else {
    hwPanel.style.display = 'none';
  }
}

function onPanel5MapClick(e: MapboxMapEvent): void {
  if (state.currentPanel !== 5) return;
  const m = state.multiAgent;
  if (m.spawnMode === 'click') {
    m.spawnPoints.push({ lon: e.lngLat.lng, lat: e.lngLat.lat });
    refreshSpawnPins(); saveState();
  } else if (m.spawnMode === 'home_work') {
    const maxHome = Math.round(m.count * (m.archetypeMix.resident ?? 0));
    const maxWork = Math.round(m.count * (m.archetypeMix.commuter ?? 0));
    // Shift+click always places a work pin regardless of the toggle button
    const isWork = !!(e.originalEvent as MouseEvent)?.shiftKey || m.pinMode === 'work';
    if (!isWork) {
      if (m.homePoints.length >= maxHome) {
        toast(`Max home pins (${maxHome}) reached. Increase Resident mix or agent count.`, 'warning');
        return;
      }
      m.homePoints.push({ lon: e.lngLat.lng, lat: e.lngLat.lat });
    } else {
      if (m.workPoints.length >= maxWork) {
        toast(`Max work pins (${maxWork}) reached. Increase Commuter mix or agent count.`, 'warning');
        return;
      }
      m.workPoints.push({ lon: e.lngLat.lng, lat: e.lngLat.lat });
    }
    refreshSpawnPins(); saveState();
    updateSpawnHint();
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

async function seedHistoriesAndFly(): Promise<void> {
  try {
    const data = await api.m<{
      features: { geometry: { coordinates: [number, number] }; properties: { id: number } }[];
    }>('/api/agents');
    if (!data.features.length) return;
    for (const f of data.features) {
      const id = f.properties.id;
      const pos = f.geometry.coordinates as [number, number];
      if (!p5AgentHistories.has(id)) {
        p5AgentHistories.set(id, [{ pos, step: p5CurrentStep }]);
      }
    }
    const lons = data.features.map(f => f.geometry.coordinates[0]);
    const lats = data.features.map(f => f.geometry.coordinates[1]);
    const cLon = lons.reduce((a, b) => a + b) / lons.length;
    const cLat = lats.reduce((a, b) => a + b) / lats.length;
    map?.flyTo({ center: [cLon, cLat], zoom: 16, duration: 1200 });
  } catch { /* ignore */ }
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
      const maxHome = Math.round(m.count * (m.archetypeMix.resident ?? 0));
      const maxWork = Math.round(m.count * (m.archetypeMix.commuter ?? 0));
      if (m.homePoints.length > maxHome) {
        toast(`Too many home pins (${m.homePoints.length} > ${maxHome}). Remove some or increase Resident mix.`, 'warning'); return;
      }
      if (m.workPoints.length > maxWork) {
        toast(`Too many work pins (${m.workPoints.length} > ${maxWork}). Remove some or increase Commuter mix.`, 'warning'); return;
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
    p5AgentHistories.clear();
    p5CurrentStep = 0;
    clearAgentDetail();
    ($('#p5-step') as HTMLButtonElement).disabled = false;
    ($('#p5-play') as HTMLButtonElement).disabled = false;
    await refreshAgentList();
    await seedHistoriesAndFly();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    toast(`Spawn failed: ${msg}`, 'danger');
    ($('#p5-spawn-status') as HTMLElement).textContent = msg;
  }
}

async function refreshAgentList(): Promise<void> {
  try {
    const data = await api.m<{
      features: {
        geometry: { coordinates: [number, number] };
        properties: { id: number; nearby_count: number; archetype: string };
      }[];
    }>('/api/agents');

    // Enrich every feature with bearing (0 = pointing north until movement is recorded)
    const enriched = {
      ...data,
      features: data.features.map(f => {
        const hist = p5AgentHistories.get(f.properties.id) ?? [];
        const bearing = hist.length >= 2
          ? calcBearing(hist[hist.length - 2].pos, hist[hist.length - 1].pos)
          : 0;
        return { ...f, properties: { ...f.properties, bearing } };
      }),
    };
    map?.getSource('agents')?.setData(enriched);
    ($('#p5-agent-count') as HTMLElement).textContent = String(data.features.length);
    const hasAgents = data.features.length > 0;
    ($('#p5-play') as HTMLButtonElement).disabled = !hasAgents || state.multiAgent.playing;
    ($('#p5-step') as HTMLButtonElement).disabled = !hasAgents;
    ($('#p5-results') as HTMLButtonElement).disabled = !hasAgents;

    const grid = $('#p5-agent-grid') as HTMLElement;
    grid.innerHTML = '';
    data.features.forEach((f) => {
      const p = f.properties;
      const div = document.createElement('div');
      div.className = 'agent-mini' + (p.id === p5SelectedAgent ? ' selected' : '');
      div.dataset['id'] = String(p.id);
      div.dataset['arch'] = p.archetype || '';
      div.innerHTML = `
        <span class="id">#${p.id}</span>
        <div class="arch">${escapeHtml(p.archetype || 'unknown')}</div>`;
      div.addEventListener('click', () => void selectAgentDetail(p.id, p.archetype || undefined));
      grid.appendChild(div);
    });
    const stats = await api.m<{ total_calls?: number }>('/api/llm/stats').catch(() => ({ total_calls: 0 }));
    ($('#p5-llm-stats') as HTMLElement).textContent = `${stats.total_calls || 0} calls`;
    ($('#p4-llm-stats') as HTMLElement).textContent = `${stats.total_calls || 0} calls`;
  } catch { /* okay */ }
}

async function stepMulti(): Promise<void> {
  if (p5Stepping) return;
  p5Stepping = true;
  try {
    const result = await api.postJSON<{
      step: number;
      time_of_day?: string;
      agents: { id: number; lon: number; lat: number; nearby_count: number; needs?: Record<string, number> }[];
      llm_stats: { total_calls?: number };
    }>(api.map, '/api/step_continuous', {});
    p5CurrentStep = result.step;
    for (const a of result.agents) {
      const hist = p5AgentHistories.get(a.id) ?? [];
      hist.push({ pos: [a.lon, a.lat], step: result.step });
      if (hist.length > 500) hist.shift();
      p5AgentHistories.set(a.id, hist);
    }
    ($('#p5-agent-count') as HTMLElement).textContent = String(result.agents.length);
    ($('#p5-step-count') as HTMLElement).textContent = `Step ${result.step}`;
    ($('#p5-time-of-day') as HTMLElement).textContent = result.time_of_day ?? "—";
    ($('#p5-llm-stats') as HTMLElement).textContent = `${result.llm_stats?.total_calls ?? 0} calls`;
    ($('#p4-llm-stats') as HTMLElement).textContent = `${result.llm_stats?.total_calls ?? 0} calls`;
    await refreshAgentList();
    if (p5SelectedAgent !== null) {
      renderP5AgentTrail(p5SelectedAgent);
      // Light update: needs bar from step result (no extra API calls during play)
      const agentData = result.agents.find(a => a.id === p5SelectedAgent);
      if (agentData?.needs) {
        const needsEl = $('#p5-det-needs') as HTMLElement | null;
        if (needsEl) renderNeedsBar(needsEl, agentData.needs);
      }
    }
  } catch (e) {
    console.warn('Multi-agent step failed:', e);
    const msg = e instanceof Error ? e.message : String(e);
    ($('#p5-spawn-status') as HTMLElement).textContent = `Step error: ${msg}`;
    if (state.multiAgent.playing) pauseMultiPlay();
  }
  finally { p5Stepping = false; }
}
function p5StepDelay(): number {
  return Math.max(50, Math.round(1000 / (state.multiAgent.speed || 1)));
}
async function runMultiLoop(): Promise<void> {
  if (!state.multiAgent.playing) return;
  await stepMulti();
  if (state.multiAgent.playing) {
    p5StepTimer = window.setTimeout(runMultiLoop, p5StepDelay());
  }
}
function startMultiPlay(): void {
  state.multiAgent.playing = true;
  ($('#p5-play') as HTMLButtonElement).disabled = true;
  ($('#p5-pause') as HTMLButtonElement).disabled = false;
  if (p5StepTimer !== null) clearTimeout(p5StepTimer);
  p5StepTimer = window.setTimeout(runMultiLoop, 0);
}
function pauseMultiPlay(): void {
  state.multiAgent.playing = false;
  ($('#p5-play') as HTMLButtonElement).disabled = false;
  ($('#p5-pause') as HTMLButtonElement).disabled = true;
  if (p5StepTimer !== null) { clearTimeout(p5StepTimer); p5StepTimer = null; }
  p5Stepping = false;
  // Refresh full agent detail (stream + perception) now that we've paused
  if (p5SelectedAgent !== null) void selectAgentDetail(p5SelectedAgent);
}

function renderP5AgentTrail(agentId: number): void {
  const hist = p5AgentHistories.get(agentId) ?? [];
  const coords = hist.map(e => e.pos);
  map?.getSource('trail')?.setData({
    type: 'FeatureCollection',
    features: coords.length >= 2
      ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} }]
      : [],
  });
  map?.getSource('trail-dots')?.setData({
    type: 'FeatureCollection',
    features: hist.map(e => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: e.pos },
      properties: { step: e.step, topic: 'position', description: `Step ${e.step}` },
    })),
  });
}

function flyToP5AgentThought(agentId: number, ev: StreamEvent): void {
  if (!map) return;
  const hist = p5AgentHistories.get(agentId) ?? [];
  if (!hist.length) return;
  const entry = hist.find(e => e.step === ev.step)
    ?? hist.reduce((best, e) => Math.abs(e.step - ev.step) < Math.abs(best.step - ev.step) ? e : best, hist[0]);
  if (!entry) return;
  (map as unknown as { flyTo(opts: object): void }).flyTo({ center: entry.pos, zoom: 17, duration: 800 });
  thoughtMarker?.remove();
  const popup = new mapboxgl.Popup({ closeButton: true, maxWidth: '280px' })
    .setHTML(`<div class="thought-popup"><b>${escapeHtml(ev.topic)} · step ${ev.step}</b>${escapeHtml(ev.description)}</div>`);
  thoughtMarker = new mapboxgl.Marker({ color: '#5e5ce6', scale: 0.8 })
    .setLngLat(entry.pos)
    .setPopup(popup)
    .addTo(map as unknown as Parameters<MapboxMarker['addTo']>[0]) as MapboxMarker;
  (thoughtMarker as unknown as { togglePopup(): void }).togglePopup();
}

function setRecordingUIState(active: boolean, statusText: string, fileUrl?: string): void {
  for (const p of ['p4', 'p5']) {
    const startBtn = $(`#${p}-rec-start`) as HTMLButtonElement | null;
    const stopBtn  = $(`#${p}-rec-stop`)  as HTMLButtonElement | null;
    if (startBtn) startBtn.disabled = active;
    if (stopBtn)  stopBtn.disabled  = !active;
    const status = $(`#${p}-rec-status`) as HTMLElement | null;
    if (!status) continue;
    status.textContent = statusText;
    if (!active && fileUrl) {
      const a = document.createElement('a');
      a.href = fileUrl; a.target = '_blank'; a.textContent = 'download';
      a.style.cssText = 'color:var(--accent-3); margin-left:8px;';
      status.appendChild(a);
    }
  }
}

function _onBeforeUnload(e: BeforeUnloadEvent): void {
  e.preventDefault();
}

async function startRecording(panel: 'p4' | 'p5' = 'p5'): Promise<void> {
  const name = ($(`#${panel}-rec-name`) as HTMLInputElement | null)?.value || '';
  // P4 (single-agent lab) runs on api.lab; P5 (multi-agent sim) runs on api.map
  const base = panel === 'p4' ? api.lab : api.map;
  try {
    const q = new URLSearchParams({ include_thoughts: 'true', include_perception: 'true' });
    if (name) q.set('session_name', name);
    const res = await api.postJSON<{ session_id: string; session_name?: string }>(
      base, `/api/recording/start?${q.toString()}`, {});
    state.recordingSession = res.session_id;
    state.recordingBase = base;
    saveState();
    recordingBase = base;
    setRecordingUIState(true, `Recording · ${res.session_name || res.session_id}`);
    window.addEventListener('beforeunload', _onBeforeUnload);
    if (recordPollTimer !== null) clearInterval(recordPollTimer);
    recordPollTimer = window.setInterval(async () => {
      const s = await api._fetch(recordingBase, '/api/recording/status')
        .catch(() => null) as { steps_recorded?: number; total_records?: number } | null;
      if (s) setRecordingUIState(true, `Recording · ${s.steps_recorded || 0} steps · ${s.total_records || 0} records`);
    }, 2000);
  } catch (e) { toast(`Recording start failed: ${e instanceof Error ? e.message : e}`, 'danger'); }
}
async function stopRecording(): Promise<void> {
  try {
    const base = recordingBase || api.map;
    const res = await api.postJSON<{ file_name?: string; total_records?: number; message?: string }>(
      base, '/api/recording/stop', {});
    recordingBase = '';
    state.recordingSession = null;
    state.recordingBase = null;
    saveState();
    window.removeEventListener('beforeunload', _onBeforeUnload);
    if (recordPollTimer !== null) { clearInterval(recordPollTimer); recordPollTimer = null; }
    const fileUrl = res.file_name
      ? `${base}/api/recording/download/${res.file_name.split('/').map(encodeURIComponent).join('/')}`
      : undefined;
    const msg = res.file_name
      ? `Saved ${res.file_name} (${res.total_records} records)`
      : (res.message || 'Stopped.');
    setRecordingUIState(false, msg, fileUrl);
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

  // Settings drawer tabs
  $$('#s-tabs button').forEach((b) => {
    b.addEventListener('click', () => {
      $$('#s-tabs button').forEach((x) => x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
      const tab = b.getAttribute('data-v');
      $$('.s-tab-content').forEach((c) =>
        c.setAttribute('data-active', c.getAttribute('data-tab') === tab ? 'true' : 'false'));
    });
  });

  // VLM analysis card — init eagerly so compare button works from first interaction
  _initAnalyseCard();

  // LLM engine (lives in the settings LLM tab — single source of truth)
  const llmEngine = buildLLMEngine('drawer', state.llm, api.map);
  llmEngine.render();
  llmEngine.bindApply('#p4-llm-model', '#p4-llm-apply', '#p4-llm-current');
  ($('#p4-llm-current') as HTMLElement).textContent = state.llm.providerId;
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

  // Nav-aid: per-archetype tabs + save
  let activeNavArch = 'resident';
  function applyNavArchDisplay(arch: string): void {
    const cfg = navArchConfigs[arch] ?? { nav_mode: 'both', gps_dist: 120, compass_dist: 60 };
    ($('#p4-navmode') as HTMLSelectElement).value = cfg.nav_mode;
    applyNavThresholdVisibility(cfg.nav_mode);
    const gpsVal = $('#p4-gps-val') as HTMLElement | null;
    const cmpVal = $('#p4-compass-val') as HTMLElement | null;
    if (gpsVal) gpsVal.textContent = String(cfg.gps_dist);
    if (cmpVal) cmpVal.textContent = String(cfg.compass_dist);
    state.singleAgent.navGpsDist    = cfg.gps_dist;
    state.singleAgent.navCompassDist = cfg.compass_dist;
  }
  $$('#s-nav-arch-tabs button').forEach((b) => b.addEventListener('click', () => {
    $$('#s-nav-arch-tabs button').forEach((x) =>
      x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
    activeNavArch = b.getAttribute('data-v') ?? 'resident';
    applyNavArchDisplay(activeNavArch);
  }));
  applyNavArchDisplay(activeNavArch);

  ($('#s-nav-save') as HTMLButtonElement | null)?.addEventListener('click', async () => {
    const statusEl = $('#s-nav-save-status') as HTMLElement | null;
    const navMode  = ($('#p4-navmode') as HTMLSelectElement).value;
    const gpsDist  = parseInt(($('#p4-gps-val') as HTMLElement).textContent ?? '120', 10);
    const cmpDist  = parseInt(($('#p4-compass-val') as HTMLElement).textContent ?? '60', 10);
    navArchConfigs[activeNavArch] = { nav_mode: navMode, gps_dist: gpsDist, compass_dist: cmpDist };
    archetypeNavMap[activeNavArch] = navMode;
    try {
      await api.postJSON(api.map, '/api/config/archetype-nav', {
        archetype: activeNavArch, nav_mode: navMode, gps_dist: gpsDist, compass_dist: cmpDist,
      });
      if (statusEl) { statusEl.textContent = 'Saved ✓'; setTimeout(() => { statusEl.textContent = ''; }, 2000); }
    } catch {
      if (statusEl) { statusEl.textContent = 'Error saving'; }
    }
  });
  (['buildings', 'buildings3d', 'walk', 'amenities', 'streetview'] as const).forEach((k) => {
    const cb = $(`#s-layer-${k}`) as HTMLInputElement;
    if (!cb) return;
    cb.checked = !!state.layers[k];
    cb.addEventListener('change', () => {
      state.layers[k] = cb.checked; saveState();
      applyLayerVisibility();
      if (k === 'buildings3d' && map) {
        map.easeTo({ pitch: cb.checked ? 45 : 0, duration: 600 });
      }
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
      const s = await api.m<{ total_calls?: number; total_input_tokens?: number; total_output_tokens?: number }>(
        '/api/llm/stats');
      toast(`LLM: ${s.total_calls || 0} calls · ${s.total_input_tokens || 0}↓ / ${s.total_output_tokens || 0}↑ tokens`, '');
    } catch (e) {
      toast(`Stats unavailable: ${e instanceof Error ? e.message : e}`, 'warning');
    }
  });
  $$('.pill-nav .dot').forEach((d) => d.addEventListener('click', () => {
    activatePanel(+d.getAttribute('data-panel')! as PanelId);
  }));
  // Pill prev/next arrows
  function navAdjacent(dir: -1 | 1): void {
    const panels: PanelId[] = [3, 4, 5];
    const idx = panels.indexOf(state.currentPanel as PanelId);
    if (idx === -1) return;
    const next = idx + dir;
    if (next < 0 || next >= panels.length) return;
    activatePanel(panels[next]);
  }
  $('#panel-prev-btn')!.addEventListener('click', () => navAdjacent(-1));
  $('#panel-next-btn')!.addEventListener('click', () => navAdjacent(1));
  // Map toggle button
  $('#map-toggle')!.addEventListener('click', () => {
    state.mapMode = !state.mapMode;
    document.body.classList.toggle('map-mode', state.mapMode);
    if (state.mapMode) {
      state.layers.streetview = false;
      applyLayerVisibility();
      document.body.classList.remove('panel-active', 'dim-map', 'p3-card-view');
    } else {
      ($('#p1-sv-popover') as HTMLElement).style.display = 'none';
      if (state.currentPanel >= 3) activatePanel(state.currentPanel as PanelId);
    }
    saveState();
    refreshNavDots();
    updateZoneFab();
  });
  // Start button — dismisses intro, goes to Personality
  $('#start-btn')!.addEventListener('click', () => {
    const intro = $('#intro') as HTMLElement;
    const video = $('#intro-video') as HTMLVideoElement | null;
    if (video) { video.pause(); video.src = ''; } // release media resource
    intro.classList.add('dismissed');
    document.body.classList.remove('intro-active');
    setTimeout(() => {
      intro.style.display = 'none';
      introRenderers.forEach((r) => { r.active = false; r.renderer.dispose(); });
      introRenderers = [];
    }, 520);
    state.mapMode = false;
    document.body.classList.remove('map-mode');
    ($('#p1-sv-popover') as HTMLElement).style.display = 'none';
    activatePanel(3);
  });
  $('#p1-popover-expand')?.addEventListener('click', () => {
    if (Object.keys(_lastClickedProps).length) _openDetailModal(_lastClickedProps);
  });
  $('#sv-detail-close')?.addEventListener('click',
    () => ($('#sv-detail-modal') as HTMLElement)?.classList.add('hidden'));
  document.addEventListener('keydown', (e) => {
    // Ctrl+Shift+R — restart wizard
    if (e.key === 'R' && e.ctrlKey && e.shiftKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      $('#s-restart')!.click();
      return;
    }
    // 'M' toggles map mode
    if ((e.key === 'm' || e.key === 'M') && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;
      $('#map-toggle')!.click();
      return;
    }
    // 1→Personality, 2→Single, 3→Multi
    const remap: Record<string, number> = { '1': 3, '2': 4, '3': 5 };
    const panel = remap[e.key];
    if (panel && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;
      activatePanel(panel as PanelId);
    }
    // Left/right arrow keys navigate steps (disabled during intro)
    if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;
      if (document.body.classList.contains('intro-active')) return;
      navAdjacent(e.key === 'ArrowLeft' ? -1 : 1);
    }
  });
  // Perception popup expand button — event delegation (works for dynamically created Mapbox popups)
  document.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.popup-sv-expand') as HTMLElement | null;
    if (btn && Object.keys(_lastClickedProps).length) _openDetailModal(_lastClickedProps);
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
  document.body.classList.add('intro-active', 'dim-map');
  // Restore map-mode body classes from persisted state (e.g. page reload while in map mode).
  // Also call panel4Enter/panel5Enter so bindPanel4/bindPanel5 runs and attaches all listeners;
  // without this the panels are visually present but fully non-interactive after a reload.
  if (state.mapMode) {
    document.body.classList.add('map-mode');
    if (state.currentPanel === 4) void panel4Enter();
    else if (state.currentPanel === 5) panel5Enter();
  }
  applyAppleSelects();
  bindGlobalControls();
  panel1Enter(); // bind #btn-benchmark + compare modal regardless of map mode
  refreshNavDots();
  $$('#s-perception button').forEach((x) =>
    x.setAttribute('aria-selected', x.getAttribute('data-v') === state.perceptionMode ? 'true' : 'false'));
  $$('#s-theme button').forEach((x) =>
    x.setAttribute('aria-selected', x.getAttribute('data-v') === state.theme ? 'true' : 'false'));
  initMap();
  initGeocoder();
  loadExternalSources();
  initIntro();
  tryRecoverRecording();
}

async function tryRecoverRecording(): Promise<void> {
  if (!state.recordingSession || !state.recordingBase) return;
  const base = state.recordingBase;
  try {
    const s = await api._fetch(base, '/api/recording/status')
      .catch(() => null) as { is_recording?: boolean; steps_recorded?: number; total_records?: number } | null;
    if (s && s.is_recording) {
      recordingBase = base;
      setRecordingUIState(true, `Recording · ${s.steps_recorded || 0} steps · ${s.total_records || 0} records`);
      window.addEventListener('beforeunload', _onBeforeUnload);
      if (recordPollTimer !== null) clearInterval(recordPollTimer);
      recordPollTimer = window.setInterval(async () => {
        const st = await api._fetch(recordingBase, '/api/recording/status')
          .catch(() => null) as { steps_recorded?: number; total_records?: number } | null;
        if (st) setRecordingUIState(true, `Recording · ${st.steps_recorded || 0} steps · ${st.total_records || 0} records`);
      }, 2000);
      toast('Reconnected to active recording session', 'success');
    } else {
      state.recordingSession = null;
      state.recordingBase = null;
      saveState();
      const rec = await api._fetch(base, '/api/recording/recover')
        .catch(() => null) as { recovered?: boolean; files?: string[]; message?: string } | null;
      if (rec && rec.recovered && rec.files?.length) {
        toast(`Recovered ${rec.files.length} recording(s) from interrupted session`, 'warning');
      }
    }
  } catch {
    state.recordingSession = null;
    state.recordingBase = null;
    saveState();
  }
}

export { boot };
