import { render } from 'preact';
import { App } from './components/App';
import { boot } from './main_legacy';

render(<App />, document.getElementById('root')!);
boot();
