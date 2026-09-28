import { useMemo } from 'react';
import Orbit from './orbit/Orbit.jsx';
import Widget from './windows/Widget.jsx';

export default function App() {
  const mode = useMemo(() => new URLSearchParams(window.location.search).get('window') || 'console', []);
  if (mode === 'widget') return <Widget />;
  return <Orbit />;
}
