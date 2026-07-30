import { useEffect, useState } from 'react';
import Rack from '../rack/Rack.jsx';
import { WIDGET_MODULE_REGISTRY, DEFAULT_WIDGET_LAYOUT } from '../rack/widgetRegistry.js';
import './Widget.css';

/**
 * The Widget is its own rack (Section 1.4): 1 row x 6 cols, thin modules
 * only, its own persisted layout file, same Ctrl+E mechanic as the
 * Console. Editing needs more vertical room than the locked 88px shell
 * has, so the main process resizes the actual OS window while editing —
 * see the `widget-edit-mode` IPC handler in electron/main.js.
 */
export default function Widget() {
  const [editMode, setEditMode] = useState(false);

  useEffect(() => {
    const onKeyDown = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'e') {
        e.preventDefault();
        setEditMode((v) => !v);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    window.jarvis?.setWidgetEditMode?.(editMode);
  }, [editMode]);

  return (
    <div className={`widget-shell drag-region ${editMode ? 'is-editing' : ''}`}>
      <Rack
        rackId="widget"
        registry={WIDGET_MODULE_REGISTRY}
        defaultLayout={DEFAULT_WIDGET_LAYOUT}
        editMode={editMode}
        cols={6}
        rowHeight={72}
        collapseRailWhenLocked
      />
    </div>
  );
}
