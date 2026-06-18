# 08 — Frontend

The frontend is a **Preact** single-page application built with **Vite**, served on port 8091. UI state lives in **Zustand** stores; the map is **Mapbox GL JS**; 3D character previews use **Three.js**. It talks to the FastAPI backend exclusively over REST.

> **Architecture note — hybrid (migration in progress).** The app is mid-migration from a single 6,600-line imperative module to a Preact component tree. Both run together today: `main.tsx` renders the Preact `<App/>` *and* calls `boot()` from the legacy module. The Preact components render the chrome (panels, modals, toasts) and read/write the Zustand stores; the legacy `main_legacy.ts` still owns Mapbox setup, the play loops, and much of the API wiring. New work goes into components + stores; the legacy module is being peeled away.

**Key files:**
- `Frontend/index.html` — HTML shell; loads `/src/main.tsx` as an ES module
- `Frontend/src/main.tsx` — entry point: `render(<App/>)` + `boot()`
- `Frontend/src/components/` — Preact components (App, Topbar, panels, modals, …)
- `Frontend/src/stores/` — four Zustand stores (config, sim, map, ui)
- `Frontend/src/api/client.ts` — REST client (`api.m()` / `api.l()` / `api.postJSON()`)
- `Frontend/src/constants/` — archetypes, LLM providers, perception schema
- `Frontend/src/utils/` — agentIcon, format, geo, moodColors
- `Frontend/src/types.ts` — shared TypeScript types
- `Frontend/src/main_legacy.ts` — legacy imperative bootstrap (~6,600 lines), still active via `boot()`
- `Frontend/vite.config.ts` — Vite build + dev-server config

---

## Build Pipeline

Vite (Rollup under the hood) bundles and serves the app. Preact is aliased in for React via `preact/compat`.

```
index.html → /src/main.tsx (ESM)
        │
        │  vite dev:  on-demand ESM, HMR
        │  vite build: tsc --noEmit (typecheck) → Rollup bundle → Frontend/dist/
        ▼
Frontend/dist/  (index.html + hashed assets: index-*.js, three-*.js, vendor-*.js)
```

```bash
npm install          # one-time: preact, zustand, three, vite, typescript
npm run dev          # vite dev server on :8091 (HMR), proxies /api → :8000
npm run build        # tsc --noEmit && vite build → dist/
npm run preview      # serve the production build on :8091
```

`package.json` scripts:
```json
{
  "scripts": {
    "dev":     "vite",
    "build":   "tsc --noEmit && vite build",
    "preview": "vite preview --port 8091",
    "legacy-dev":   "esbuild src/main_legacy.ts --bundle ... --watch",
    "legacy-build": "esbuild src/main_legacy.ts --bundle ... --minify"
  }
}
```

The `legacy-*` esbuild scripts are retained only to build the standalone legacy bundle; the live app is built by Vite.

### Vite Config Highlights

```ts
// vite.config.ts
export default defineConfig({
  plugins: [react()],                 // @vitejs/plugin-react (works with preact/compat)
  server: {
    port: 8091, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  build: {
    outDir: 'dist', sourcemap: true,
    rollupOptions: { output: { manualChunks: {
      three:  ['three'],
      vendor: ['preact', 'preact/compat', 'zustand'],
    }}},
  },
  resolve: { alias: {                 // React → Preact
    'react': 'preact/compat', 'react-dom': 'preact/compat',
    'react/jsx-runtime': 'preact/jsx-runtime',
  }},
});
```

The dev-server `/api` proxy means the frontend can call `/api/...` directly in development without CORS friction.

---

## Libraries

| Library | Version | Loaded via | Purpose |
|---------|---------|-----------|---------|
| **Preact** | ^10.25 | npm bundle | UI components (React-compatible via `preact/compat`) |
| **Zustand** | ^5.0 | npm bundle | State management (4 stores) |
| **Three.js** | ^0.161 | npm bundle (own chunk) | 3D character models in Panel 3 |
| **Mapbox GL JS** | v3.3.0 | CDN `<script>` | 2D map + agent visualization |
| **Mapbox GL Draw** | v1.4.3 | CDN `<script>` | Zone drawing tool (map mode) |
| **Vite** | ^5.3 | dev dependency | Dev server + Rollup production build |
| **TypeScript** | ^5.3 | dev dependency | Type checking (`tsc --noEmit` gate on build) |

Mapbox GL JS + Draw are global CDN scripts (not bundled); access goes through typed refs held in `mapStore`.

---

## State Management — Four Zustand Stores

State is split across four `create()` stores in `src/stores/`. Components subscribe with the store hooks; the legacy module reads/writes the same stores via `useXStore.getState()`.

### `configStore` — persisted user configuration

Persisted to `localStorage` via Zustand's `persist` middleware under the key **`uabm:state:v3`**.

```ts
{
  currentPanel: 3,                       // active panel (3 = default)
  panelStatus: { 3: 'active', 4: 'locked', 5: 'locked' },
  mapMode: false,                        // map-mode slots (data acquisition) visible?
  theme: 'dark', mapStyle: 'dark',
  mapboxToken: '', mapServerUrl, labServerUrl,
  perceptionMode: 'both',
  layers: { buildings, walk, amenities, streetview },
  zone: { bbox, spacing },
  vlm:  { provider: 'qwen25vl-3b', hfModel, enabledFields, customPrompt, ... },
  llm:  { mode: 'local', providerId: 'ollama', model },
  archetypes, selectedArchetype,
  singleAgentConfig: { archetype, start, target, navMode, navGpsDist, navCompassDist },
  multiAgentConfig:  { count, spawnMode, pinMode, spawnPoints, homePoints,
                       workPoints, archetypeMix },
}
```

### `simStore` — live simulation runtime (not persisted)

Uses the `subscribeWithSelector` middleware so the legacy loop can subscribe to fine-grained changes. Holds single-agent playback state (`agentId`, `playing`, `speed`, `moodHistory`, `positionHistory`, `stepLog`), multi-agent playback state (`multiPlaying`, `multiCurrentStep`, `agentHistories`, `p5SelectedAgent`), and recording handles (`p4ActiveRecording`, `p5ActiveRecording`). Timer handles are stored as `{ current }` ref objects so Zustand never re-proxies them.

### `mapStore` — non-serialisable Mapbox refs

Mapbox `Map`, `Draw`, `Marker`, and `Popup` objects contain internal state that cannot be proxied by Zustand, so they are stored as stable `{ current }` wrapper objects and mutated in place (`mapRef`, `mapDrawRef`, marker refs, `amenityPopupRef`). Only `mapReady` is a plain reactive boolean.

### `uiStore` — ephemeral UI state

Active modal (`overture | vlm-compare | analyse | sv-detail | llm-compare | p3-create`), map pick mode (`start | target`), the Panel 4 / Panel 5 thought-stream tabs, and the toast queue.

---

## API Client

`src/api/client.ts` exposes a single `api` object that targets two backends:

```ts
api.m<T>(path, opts?)        // map/simulation server  (default :8000)
api.l<T>(path, opts?)        // agent-lab server        (default :8100, optional)
api.postJSON<T>(base, path, body)
```

`_fetch()` parses FastAPI error bodies (both `detail` strings and validation-error arrays) into readable messages, and auto-decodes JSON vs. text by `content-type`. Base URLs come from `apiConfig`, set at boot from persisted `configStore` values — so the user can repoint the backend without rebuilding.

---

## Component Tree

`App.tsx` composes the whole UI; the map sits behind everything and panels/modals overlay it.

```
<App>
  <MapCanvas/>            ← Mapbox GL map (full-screen, behind panels)
  <Topbar/>              ← logo · panel nav · theme · settings
  <MapSlotLeft/>         ← map-mode: Street View extraction + Analyse Images
  <MapSlotRight/>        ← map-mode: Overture zone download + external Data Sources
  <Panel3/>             ← Personality / Archetype editor (default)
  <Panel4/>             ← Single Agent lab
  <Panel5/>             ← Multi-Agent simulation
  <Intro/> <PillNav/> <MapToggleButton/> <MapAnnotation/>
  <SettingsDrawer/> <Toast/> <ZoneFab/>
  ── modals ──
  <OvertureModal/> <VLMCompareModal/> <AnalyseModal/>
  <StreetViewDetailModal/> <LLMCompareModal/> <CreateArchetypeModal/>
</App>
```

### Panel 3 — Personality / Archetype Editor (default)

- Archetype info panel + editor (Resident / Commuter / Tourist / Student)
- **Three.js canvas**: loads the GLB character model for the selected archetype
- Profile + daily-plan phase editor; create-archetype flow via `CreateArchetypeModal`

### Panel 4 — Single Agent Lab

- Place start pin (green) + target pin (orange) on the map; archetype + nav-mode selectors
- Agent detail: Emotion Mix (mood pie + curiosity/fatigue), Needs bars (hunger/energy/social/comfort), **Thought Stream** tabs (`mobility` / `amenity_visit` / `perception` / `all`)
- "What The Agent Sees" perception card (Street View thumbnail + scene + archetype perspective)
- Record Agent (session) controls; playback play/pause/step + speed

### Panel 5 — Multi-Agent Simulation

- Spawn controls: agent count, spawn mode (random / click / poi / home_work), archetype-mix sliders
- Agent grid; click a card to expand into the single-agent detail view (`p5SelectedAgent`)
- Step counter + LLM-calls chip; trails / heatmap / decision-point overlays
- Record Agents (session) controls
- Stream tabs add a `cognition` tab vs. Panel 4

### Map-Mode Slots (formerly "Panel 1")

Toggled by `mapMode`, the left/right slots host the **data-acquisition** workflow:
- **MapSlotLeft** — Street View extraction (zone draw + spacing → `/api/streetview/download`) and Analyse Images (VLM → `/api/streetview/analyze`)
- **MapSlotRight** — Overture zone download and external Data Sources (plugins)

The heavier comparison flows are modals: `OvertureModal`, `VLMCompareModal`, `AnalyseModal`, `StreetViewDetailModal`, `LLMCompareModal`.

---

## Map Layers

Mapbox GL JS renders these layers over the basemap (managed by the legacy module):

| Layer ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `buildings` | fill-extrusion | `/api/buildings` | 3D building footprints |
| `walk-network` | line | `/api/walk_network` | Street edges |
| `amenities` | circle | `/api/amenities` | POI dots |
| `agents-glow` | circle | live GeoJSON | Agent halo (archetype colour) |
| `agents-icon` | symbol | live GeoJSON | Agent dot with archetype icon |
| `trails` | line | accumulated positions | Movement history |
| `heatmap` | heatmap | accumulated positions | Density heatmap |
| `decision-points` | circle | step results | LLM decision locations |

Archetype colour coding (also in `src/constants/archetypes.ts` / `utils/agentIcon.ts`):

| Archetype | Colour |
|-----------|--------|
| Resident | `#4ade80` (green) |
| Commuter | `#60a5fa` (blue) |
| Tourist | `#f97316` (orange) |
| Student | `#a78bfa` (purple) |

---

## Simulation Play Loop

The play loop lives in the legacy module and drives the stores. It polls `/api/step_continuous`, pushes agent positions to the Mapbox source, accumulates trails into `simStore.agentHistories`, and updates the step/LLM chips:

```ts
// conceptual — driven from main_legacy via the stores
const result = await api.m('/api/step_continuous', { method: 'POST' });
agentsSource.setData(buildGeoJSON(result.agents));   // map update
useSimStore.getState().setMultiCurrentStep(result.step);
for (const a of result.agents) { /* append [a.lon, a.lat] to agentHistories */ }
```

Playback speed comes from `simStore.speed` (single) / `multiSpeed` (multi); the interval/timeout handle is kept in the store's ref objects so play/pause toggles are race-free.

---

## External References

| Resource | URL |
|----------|-----|
| Preact | https://preactjs.com/ |
| Zustand | https://github.com/pmndrs/zustand |
| Vite | https://vitejs.dev/ |
| Mapbox GL JS documentation | https://docs.mapbox.com/mapbox-gl-js/ |
| Mapbox GL Draw | https://github.com/mapbox/mapbox-gl-draw |
| Three.js documentation | https://threejs.org/docs/ |
| Three.js GLTFLoader | https://threejs.org/docs/#examples/en/loaders/GLTFLoader |

---

**Next:** [`09_benchmarks.md`](09_benchmarks.md) — how the system's quality is measured with five evaluation notebooks.
