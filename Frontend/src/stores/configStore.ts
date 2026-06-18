import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { UABMState, PanelStatus } from '../types';

interface ConfigState {
  currentPanel: number;
  panelStatus: Record<number, PanelStatus>;
  mapMode: boolean;
  mapStyle: 'dark' | 'light';
  theme: 'dark' | 'light';
  mapboxToken: string;
  mapServerUrl: string;
  labServerUrl: string;
  perceptionMode: string;
  layers: UABMState['layers'];
  zone: UABMState['zone'];
  vlm: UABMState['vlm'];
  llm: UABMState['llm'];
  archetypes: UABMState['archetypes'];
  selectedArchetype: string | null;
  singleAgentConfig: Pick<UABMState['singleAgent'], 'archetype' | 'start' | 'target' | 'navMode' | 'navGpsDist' | 'navCompassDist'>;
  multiAgentConfig: Pick<UABMState['multiAgent'], 'count' | 'spawnMode' | 'pinMode' | 'spawnPoints' | 'homePoints' | 'workPoints' | 'archetypeMix'>;

  setCurrentPanel: (n: number) => void;
  setPanelStatus: (panel: number, status: PanelStatus) => void;
  setMapMode: (v: boolean) => void;
  setTheme: (t: 'dark' | 'light') => void;
  setMapStyle: (s: 'dark' | 'light') => void;
  setMapboxToken: (t: string) => void;
  setMapServerUrl: (u: string) => void;
  setLabServerUrl: (u: string) => void;
  setPerceptionMode: (m: string) => void;
  setLayer: (k: keyof UABMState['layers'], v: boolean) => void;
  setZone: (z: UABMState['zone']) => void;
  setVlm: (v: Partial<UABMState['vlm']>) => void;
  setLlm: (l: UABMState['llm']) => void;
  setArchetypes: (a: UABMState['archetypes']) => void;
  setSelectedArchetype: (a: string | null) => void;
  setSingleAgentConfig: (c: Partial<ConfigState['singleAgentConfig']>) => void;
  setMultiAgentConfig: (c: Partial<ConfigState['multiAgentConfig']>) => void;
}

export const useConfigStore = create<ConfigState>()(
  persist(
    (set) => ({
      currentPanel: 3,
      panelStatus: { 3: 'active', 4: 'locked', 5: 'locked' },
      mapMode: false,
      mapStyle: 'dark',
      theme: 'dark',
      mapboxToken: '',
      mapServerUrl: 'http://127.0.0.1:8000',
      labServerUrl: 'http://127.0.0.1:8100',
      perceptionMode: 'both',
      layers: { buildings: true, walk: true, amenities: false, streetview: true },
      zone: { bbox: null, spacing: 200 },
      vlm: { provider: 'qwen25vl-3b', hfModel: '', enabledFields: null, customPrompt: {}, customFields: [], fieldStructures: {} },
      llm: { mode: 'local', providerId: 'ollama', model: '' },
      archetypes: null,
      selectedArchetype: null,
      singleAgentConfig: {
        archetype: 'resident', start: null, target: null,
        navMode: 'both', navGpsDist: 120, navCompassDist: 60,
      },
      multiAgentConfig: {
        count: 15, spawnMode: 'random', pinMode: 'home',
        spawnPoints: [], homePoints: [], workPoints: [],
        archetypeMix: { resident: 0.25, commuter: 0.25, tourist: 0.25, student: 0.25 },
      },

      setCurrentPanel: (n) => set({ currentPanel: n }),
      setPanelStatus: (panel, status) =>
        set((s) => ({ panelStatus: { ...s.panelStatus, [panel]: status } })),
      setMapMode: (v) => set({ mapMode: v }),
      setTheme: (t) => set({ theme: t }),
      setMapStyle: (s) => set({ mapStyle: s }),
      setMapboxToken: (t) => set({ mapboxToken: t }),
      setMapServerUrl: (u) => set({ mapServerUrl: u }),
      setLabServerUrl: (u) => set({ labServerUrl: u }),
      setPerceptionMode: (m) => set({ perceptionMode: m }),
      setLayer: (k, v) => set((s) => ({ layers: { ...s.layers, [k]: v } })),
      setZone: (z) => set({ zone: z }),
      setVlm: (v) => set((s) => ({ vlm: { ...s.vlm, ...v } })),
      setLlm: (l) => set({ llm: l }),
      setArchetypes: (a) => set({ archetypes: a }),
      setSelectedArchetype: (a) => set({ selectedArchetype: a }),
      setSingleAgentConfig: (c) =>
        set((s) => ({ singleAgentConfig: { ...s.singleAgentConfig, ...c } })),
      setMultiAgentConfig: (c) =>
        set((s) => ({ multiAgentConfig: { ...s.multiAgentConfig, ...c } })),
    }),
    { name: 'uabm:state:v3' },
  ),
);
