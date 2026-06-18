import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import type { UABMState } from '../types';

interface StepLogEntry {
  pos: [number, number];
  step: number;
  topic: string;
  description: string;
  mood: string;
  curiosity: number | null;
  fatigue: number | null;
  needs: Record<string, number> | null;
}

interface P5AgentHistory { pos: [number, number] }

interface SimState {
  // Single-agent runtime
  agentId: number | null;
  playing: boolean;
  speed: number;
  moodHistory: string[];
  positionHistory: [number, number][];
  stepLog: StepLogEntry[];
  resultsMode: boolean;
  stepping: boolean;
  stepTimerRef: { current: ReturnType<typeof setTimeout> | null };

  // Multi-agent runtime
  multiPlaying: boolean;
  multiSpeed: number;
  multiCurrentStep: number;
  agentHistories: Map<number, P5AgentHistory[]>;
  p5ResultsMode: boolean;
  p5SelectedAgent: number | null;
  p5StepTimerRef: { current: ReturnType<typeof setInterval> | null };

  // Recording
  p4ActiveRecording: string | null;
  p5ActiveRecording: string | null;
  recordPollTimerRef: { current: ReturnType<typeof setInterval> | null };

  // Actions
  setAgentId: (id: number | null) => void;
  setPlaying: (v: boolean) => void;
  setSpeed: (v: number) => void;
  appendMood: (mood: string) => void;
  appendPosition: (pos: [number, number]) => void;
  appendStepLog: (entry: StepLogEntry) => void;
  clearStepLog: () => void;
  setResultsMode: (v: boolean) => void;
  setStepping: (v: boolean) => void;
  setMultiPlaying: (v: boolean) => void;
  setMultiSpeed: (v: number) => void;
  setMultiCurrentStep: (n: number) => void;
  setAgentHistories: (h: Map<number, P5AgentHistory[]>) => void;
  setP5ResultsMode: (v: boolean) => void;
  setP5SelectedAgent: (id: number | null) => void;
  setP4ActiveRecording: (s: string | null) => void;
  setP5ActiveRecording: (s: string | null) => void;
  resetSingleAgent: () => void;
}

export const useSimStore = create<SimState>()(
  subscribeWithSelector((set) => ({
    agentId: null,
    playing: false,
    speed: 1.0,
    moodHistory: [],
    positionHistory: [],
    stepLog: [],
    resultsMode: false,
    stepping: false,
    stepTimerRef: { current: null },

    multiPlaying: false,
    multiSpeed: 1.0,
    multiCurrentStep: 0,
    agentHistories: new Map(),
    p5ResultsMode: false,
    p5SelectedAgent: null,
    p5StepTimerRef: { current: null },

    p4ActiveRecording: null,
    p5ActiveRecording: null,
    recordPollTimerRef: { current: null },

    setAgentId: (id) => set({ agentId: id }),
    setPlaying: (v) => set({ playing: v }),
    setSpeed: (v) => set({ speed: v }),
    appendMood: (mood) => set((s) => ({ moodHistory: [...s.moodHistory, mood] })),
    appendPosition: (pos) => set((s) => ({ positionHistory: [...s.positionHistory, pos] })),
    appendStepLog: (entry) => set((s) => ({ stepLog: [...s.stepLog, entry] })),
    clearStepLog: () => set({ stepLog: [], moodHistory: [], positionHistory: [] }),
    setResultsMode: (v) => set({ resultsMode: v }),
    setStepping: (v) => set({ stepping: v }),
    setMultiPlaying: (v) => set({ multiPlaying: v }),
    setMultiSpeed: (v) => set({ multiSpeed: v }),
    setMultiCurrentStep: (n) => set({ multiCurrentStep: n }),
    setAgentHistories: (h) => set({ agentHistories: h }),
    setP5ResultsMode: (v) => set({ p5ResultsMode: v }),
    setP5SelectedAgent: (id) => set({ p5SelectedAgent: id }),
    setP4ActiveRecording: (s) => set({ p4ActiveRecording: s }),
    setP5ActiveRecording: (s) => set({ p5ActiveRecording: s }),
    resetSingleAgent: () => set({
      agentId: null, playing: false, moodHistory: [],
      positionHistory: [], stepLog: [], resultsMode: false,
    }),
  })),
);

// Re-export type for use in other files
export type { StepLogEntry, P5AgentHistory };

// Sync from legacy UABMState — called by main_legacy during Phase 1
export function syncLegacyToSimStore(s: UABMState): void {
  const store = useSimStore.getState();
  store.setSpeed(s.singleAgent.speed ?? 1.0);
  store.setMultiSpeed(s.multiAgent.speed ?? 1.0);
}
