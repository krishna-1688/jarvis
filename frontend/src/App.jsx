import { useMemo } from 'react';
import Console from './windows/Console.jsx';
import Widget from './windows/Widget.jsx';
import Scratch from './windows/Scratch.jsx';

export default function App() {
  const mode = useMemo(() => new URLSearchParams(window.location.search).get('window') || 'console', []);
  if (mode === 'widget') return <Widget />;
  if (mode === 'scratch') return <Scratch />;
  return <Console />;
}
