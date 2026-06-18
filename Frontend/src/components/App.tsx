import { MapCanvas } from './MapCanvas';
import { Topbar } from './Topbar';
import { MapSlotLeft } from './MapSlotLeft';
import { MapSlotRight } from './MapSlotRight';
import { Panel3 } from './Panel3';
import { Panel4 } from './Panel4';
import { Panel5 } from './Panel5';
import { Intro } from './Intro';
import { PillNav } from './PillNav';
import { MapToggleButton } from './MapToggleButton';
import { MapAnnotation } from './MapAnnotation';
import { SettingsDrawer } from './SettingsDrawer';
import { Toast } from './Toast';
import { ZoneFab } from './ZoneFab';
import { OvertureModal } from './modals/OvertureModal';
import { VLMCompareModal } from './modals/VLMCompareModal';
import { AnalyseModal } from './modals/AnalyseModal';
import { StreetViewDetailModal } from './modals/StreetViewDetailModal';
import { LLMCompareModal } from './modals/LLMCompareModal';
import { CreateArchetypeModal } from './modals/CreateArchetypeModal';

export function App() {
  return (
    <>
      <div id="app">
        <MapCanvas />
        <Topbar />
        <MapSlotLeft />
        <MapSlotRight />
        <Panel3 />
        <Panel4 />
        <Panel5 />
        <Intro />
        <PillNav />
        <MapToggleButton />
        <MapAnnotation />
        <SettingsDrawer />
        <Toast />
        <ZoneFab />
      </div>
      <OvertureModal />
      <VLMCompareModal />
      <AnalyseModal />
      <StreetViewDetailModal />
      <LLMCompareModal />
      <CreateArchetypeModal />
    </>
  );
}
