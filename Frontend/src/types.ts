// Shared types for the UABM frontend.

export type PanelId = 1 | 2 | 3 | 4 | 5;
export type PanelStatus = 'pending' | 'active' | 'done' | 'locked';

export type SpawnMode = 'random' | 'click' | 'poi' | 'home_work';
export type NavMode = 'gps' | 'direction_sense' | 'both' | 'none';
export type PerceptionMode = 'amenities' | 'perception' | 'both' | 'rule_based';
export type LLMMode = 'local' | 'api';
export type Theme = 'dark' | 'light';
export type Archetype = 'resident' | 'commuter' | 'tourist' | 'student' | string;

export interface LngLat { lon: number; lat: number; }

export interface LLMSelection {
  mode: LLMMode;
  providerId: string;
  model: string;
  apiKey: string;
}

export interface SingleAgentState {
  id: number | null;
  archetype: Archetype;
  start: LngLat | null;
  target: LngLat | null;
  navMode: NavMode;
  navGpsDist: number;
  navCompassDist: number;
  moodHistory: string[];
  positionHistory: [number, number][];
  playing: boolean;
}

export interface MultiAgentState {
  count: number;
  spawnMode: SpawnMode;
  spawnPoints: LngLat[];
  homePoints: LngLat[];
  workPoints: LngLat[];
  archetypeMix: Record<string, number>;
  playing: boolean;
  speed: number;
}

export interface PerceptionSubField {
  key: string;
  values: string[];   // empty = free-text; ≥2 = enum shown as tag pills
}

export interface PerceptionFieldSpec {
  key: string;
  label: string;
  prompt: string;
  description?: string;
  fields?: PerceptionSubField[];
}

export interface VLMState {
  provider: string;
  hfModel?: string;
  enabledFields: string[] | null;
  customPrompt: Record<string, string>;
  customFields: PerceptionFieldSpec[];
  fieldStructures: Record<string, PerceptionSubField[]>;
}

export interface VLMCardSpec {
  id: string;
  name: string;
  active?: boolean;
  pros: string;
  cons: string;
  props: Record<string, string>;
}

export interface DailyPlanPhase {
  id?: string;
  time_of_day?: string;
  goal?: string;
  target_types?: string[];
  perception_preferences?: string[];
  perception_avoid?: { field: string; value: string }[];
  max_visits?: number;
  en_route_stops?: unknown[];
}

export interface ArchetypeProfile {
  profile?: {
    interests?: string[];
    pace?: string;
    curiosity?: string;
    social?: string;
    description?: string;
    age?: string | null;
    gender?: string | null;
  };
  daily_plan?: DailyPlanPhase[];
}

export type ArchetypeMap = Record<string, ArchetypeProfile>;

export interface UABMState {
  currentPanel: PanelId;
  panelStatus: Record<number, PanelStatus>;
  mapMode: boolean;
  mapStyle: 'dark' | 'light';
  theme: Theme;
  mapboxToken: string;
  mapServerUrl: string;
  labServerUrl: string;
  perceptionMode: PerceptionMode;
  layers: { buildings: boolean; walk: boolean; amenities: boolean; streetview: boolean };
  zone: { bbox: [number, number, number, number] | null; spacing: number };
  selectedPoint: { lat: number; lon: number; image_url?: string } | null;
  vlm: VLMState;
  llm: LLMSelection;
  archetypes: ArchetypeMap | null;
  selectedArchetype: string | null;
  singleAgent: SingleAgentState;
  multiAgent: MultiAgentState;
  recordingSession: string | null;
  availableProviders?: { id: string; name: string; description?: string }[];
}

export interface StreetViewFeatureProps {
  lat: number;
  lon: number;
  heading?: number;
  image_url?: string;
  model?: string;
  [key: string]: unknown;
}

// Minimal Mapbox typings — we use it via the global from CDN.
declare global {
  interface Window {
    mapboxgl: {
      Map: new (opts: Record<string, unknown>) => MapboxMap;
      Marker: new (opts?: Record<string, unknown>) => MapboxMarker;
      Popup: new (opts?: Record<string, unknown>) => MapboxPopup;
      accessToken: string;
    };
    MapboxDraw: new (opts?: Record<string, unknown>) => MapboxDraw;
    UABM?: Record<string, unknown>;
  }
}

export interface MapboxLngLat { lng: number; lat: number; }
export interface MapboxMap {
  on(event: string, layerOrHandler: string | ((e: MapboxMapEvent) => void), handler?: (e: MapboxMapEvent) => void): void;
  once(event: string, handler: () => void): void;
  setStyle(url: string): void;
  setLayoutProperty(id: string, prop: string, value: unknown): void;
  getLayer(id: string): unknown;
  getSource(id: string): MapboxSource | undefined;
  addSource(id: string, src: Record<string, unknown>): void;
  addLayer(layer: Record<string, unknown>): void;
  addControl(ctrl: unknown): void;
  getCanvas(): HTMLCanvasElement;
  queryRenderedFeatures(
    geometry: [number, number] | [[number, number], [number, number]],
    options?: { layers?: string[]; filter?: unknown[] },
  ): { properties: Record<string, unknown> }[];
  flyTo(options: { center: [number, number]; zoom?: number; duration?: number; pitch?: number; bearing?: number }): void;
  dragPan: { disable(): void; enable(): void };
}
export interface MapboxSource {
  setData(data: unknown): void;
  _data?: { features: { geometry: { coordinates: [number, number] } }[] };
}
export interface MapboxMapEvent {
  lngLat: MapboxLngLat;
  point: { x: number; y: number };
  features?: { properties: Record<string, unknown> }[];
  originalEvent?: MouseEvent;
}
export interface MapboxMarker {
  setLngLat(coords: [number, number]): MapboxMarker;
  addTo(map: MapboxMap): MapboxMarker;
  remove(): void;
}
export interface MapboxPopup {
  setLngLat(coords: [number, number]): MapboxPopup;
  setHTML(html: string): MapboxPopup;
  addTo(map: MapboxMap): MapboxPopup;
  remove(): void;
  on(event: string, handler: () => void): MapboxPopup;
}
export interface MapboxDraw {
  changeMode(mode: string): void;
  getAll(): { features: { geometry: { type: string; coordinates: number[][][] } }[] };
  deleteAll(): void;
}
