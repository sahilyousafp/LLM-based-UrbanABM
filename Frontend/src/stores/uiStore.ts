import { create } from 'zustand';

type ModalId =
  | 'overture'
  | 'vlm-compare'
  | 'analyse'
  | 'sv-detail'
  | 'llm-compare'
  | 'p3-create'
  | null;

interface Toast {
  id: string;
  msg: string;
  type: 'success' | 'warning' | 'danger' | '';
}

interface UIState {
  activeModal:   ModalId;
  pickMode:      'start' | 'target' | null;
  streamTab:     'mobility' | 'amenity_visit' | 'perception' | 'all';
  p5StreamTab:   'mobility' | 'amenity_visit' | 'perception' | 'cognition' | 'all';
  toasts:        Toast[];

  openModal:    (id: ModalId) => void;
  closeModal:   () => void;
  setPickMode:  (m: UIState['pickMode']) => void;
  setStreamTab: (t: UIState['streamTab']) => void;
  setP5StreamTab: (t: UIState['p5StreamTab']) => void;
  pushToast:    (msg: string, type?: Toast['type']) => void;
  dismissToast: (id: string) => void;
}

let _toastSeq = 0;

export const useUIStore = create<UIState>()((set) => ({
  activeModal:  null,
  pickMode:     null,
  streamTab:    'mobility',
  p5StreamTab:  'mobility',
  toasts:       [],

  openModal:    (id) => set({ activeModal: id }),
  closeModal:   () => set({ activeModal: null }),
  setPickMode:  (m) => set({ pickMode: m }),
  setStreamTab: (t) => set({ streamTab: t }),
  setP5StreamTab: (t) => set({ p5StreamTab: t }),
  pushToast: (msg, type = '') => {
    const id = String(++_toastSeq);
    set((s) => ({ toasts: [...s.toasts, { id, msg, type }] }));
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, 4500);
  },
  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
