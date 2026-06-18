import { create } from 'zustand';
import type { MapboxMap, MapboxDraw, MapboxMarker, MapboxPopup } from '../types';

// Mapbox objects cannot be Zustand-proxied (they contain non-serialisable internals).
// Store them as stable wrapper objects — Zustand never re-proxies a .current mutation.
interface MapState {
  mapRef:         { current: MapboxMap | null };
  mapDrawRef:     { current: MapboxDraw | null };
  mapReady:       boolean;
  amenityPopupRef:   { current: MapboxPopup | null };
  startMarkerRef:    { current: MapboxMarker | null };
  targetMarkerRef:   { current: MapboxMarker | null };
  agentNavMarkerRef: { current: MapboxMarker | null };

  setMapReady: (v: boolean) => void;
  setMap: (m: MapboxMap) => void;
  setMapDraw: (d: MapboxDraw) => void;
  setStartMarker: (m: MapboxMarker | null) => void;
  setTargetMarker: (m: MapboxMarker | null) => void;
  setAgentNavMarker: (m: MapboxMarker | null) => void;
  setAmenityPopup: (p: MapboxPopup | null) => void;
}

export const useMapStore = create<MapState>()((set, get) => ({
  mapRef:            { current: null },
  mapDrawRef:        { current: null },
  mapReady:          false,
  amenityPopupRef:   { current: null },
  startMarkerRef:    { current: null },
  targetMarkerRef:   { current: null },
  agentNavMarkerRef: { current: null },

  setMapReady: (v) => set({ mapReady: v }),
  setMap:      (m) => { get().mapRef.current = m; },
  setMapDraw:  (d) => { get().mapDrawRef.current = d; },
  setStartMarker:    (m) => { get().startMarkerRef.current = m; },
  setTargetMarker:   (m) => { get().targetMarkerRef.current = m; },
  setAgentNavMarker: (m) => { get().agentNavMarkerRef.current = m; },
  setAmenityPopup:   (p) => { get().amenityPopupRef.current = p; },
}));
