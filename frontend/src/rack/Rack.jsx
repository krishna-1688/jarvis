import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
// react-grid-layout v2 restructured its default export into a new
// "composable" API; /legacy is the package's own documented drop-in for the
// classic flat-props API (rowHeight, cols, margin, draggableHandle, etc.)
// that the rest of this file is written against.
import { ReactGridLayout as GridLayout } from 'react-grid-layout/legacy';
import RackModule from './RackModule.jsx';
import ModuleTray from './ModuleTray.jsx';
import './Rack.css';

const MARGIN = [12, 12];

function nearestPreset(sizes, w, h) {
  let best = sizes[0];
  let bestDist = Infinity;
  for (const s of sizes) {
    const d = (s.w - w) ** 2 + (s.h - h) ** 2;
    if (d < bestDist) { bestDist = d; best = s; }
  }
  return best;
}

/**
 * A rearrangeable rack of modules (Section 1.4) — the Console's main
 * surface, and (from Task 3.8) the Widget's own thin rack too. Only
 * react-grid-layout's positioning logic is used; every visual is chassis
 * CSS (see Rack.css / RackModule.css / ModuleTray.css) — none of RGL's
 * bundled stylesheet is imported.
 *
 * @param {{ rackId: string, registry: Record<string, import('./moduleRegistry.js').ModuleDef>,
 *            defaultLayout: {i:string,x:number,y:number,w:number,h:number}[],
 *            editMode: boolean, cols?: number }} props
 */
export default function Rack({
  rackId, registry, defaultLayout, editMode, cols = 12, rowHeight = 64,
  collapseRailWhenLocked = false,
}) {
  const containerRef = useRef(null);
  const [width, setWidth] = useState(960);
  const [layout, setLayout] = useState(defaultLayout);
  const [removedIds, setRemovedIds] = useState([]);
  const saveTimer = useRef(null);
  const loadedRef = useRef(false);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useLayoutEffect(() => {
    let cancelled = false;
    (async () => {
      const saved = await window.jarvis?.loadLayout?.(rackId);
      if (!cancelled && saved && Array.isArray(saved.layout)) {
        setLayout(saved.layout);
        setRemovedIds(saved.removedIds || []);
      }
      loadedRef.current = true;
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rackId]);

  const persist = useCallback((nextLayout, nextRemoved) => {
    if (!loadedRef.current) return; // don't clobber a saved file with defaults before the load resolves
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      window.jarvis?.saveLayout?.(rackId, { layout: nextLayout, removedIds: nextRemoved });
    }, 500);
  }, [rackId]);

  const visibleLayout = useMemo(() => layout
    .filter((item) => !removedIds.includes(item.i))
    .map((item) => {
      const def = registry[item.i];
      if (!def) return item;
      const ws = def.allowedSizes.map((s) => s.w);
      const hs = def.allowedSizes.map((s) => s.h);
      return {
        ...item,
        minW: Math.min(...ws), maxW: Math.max(...ws),
        minH: Math.min(...hs), maxH: Math.max(...hs),
      };
    }), [layout, removedIds, registry]);

  const handleLayoutChange = (newLayout) => {
    setLayout((prev) => {
      const merged = prev.map((item) => {
        const found = newLayout.find((n) => n.i === item.i);
        return found ? { ...item, x: found.x, y: found.y, w: found.w, h: found.h } : item;
      });
      persist(merged, removedIds);
      return merged;
    });
  };

  const handleResizeStop = (_layout, _oldItem, newItem) => {
    const def = registry[newItem.i];
    if (!def) return;
    const preset = nearestPreset(def.allowedSizes, newItem.w, newItem.h);
    setLayout((prev) => {
      const merged = prev.map((item) => (
        item.i === newItem.i ? { ...item, w: preset.w, h: preset.h } : item
      ));
      persist(merged, removedIds);
      return merged;
    });
  };

  const handleRemove = (id) => {
    const def = registry[id];
    if (!def || def.removable === false) return;
    const nextRemoved = [...removedIds, id];
    setRemovedIds(nextRemoved);
    persist(layout, nextRemoved);
  };

  const handleAdd = (id) => {
    const def = registry[id];
    if (!def) return;
    const nextRemoved = removedIds.filter((r) => r !== id);
    setRemovedIds(nextRemoved);

    setLayout((prev) => {
      if (prev.some((item) => item.i === id)) {
        persist(prev, nextRemoved);
        return prev; // already has a slot — just un-hiding it via removedIds above
      }
      const preset = def.allowedSizes[0];
      const maxY = prev.reduce((m, item) => Math.max(m, item.y + item.h), 0);
      const next = [...prev, { i: id, x: 0, y: maxY, w: preset.w, h: preset.h }];
      persist(next, nextRemoved);
      return next;
    });
  };

  const handleReset = () => {
    setLayout(defaultLayout);
    setRemovedIds([]);
    persist(defaultLayout, []);
  };

  // Available = registered but not currently visible — covers both an
  // explicit removal AND a module that was never in this layout to begin
  // with (e.g. a new registry entry, or the widget's optional extras).
  const visibleIds = new Set(layout.filter((item) => !removedIds.includes(item.i)).map((item) => item.i));
  const availableModules = Object.values(registry).filter((def) => !visibleIds.has(def.id));

  return (
    <div className={`rack ${editMode ? 'rack--editing' : ''}`}>
      <div className="rack__grid-wrap" ref={containerRef}>
        <GridLayout
          className="rack__grid"
          layout={visibleLayout}
          cols={cols}
          rowHeight={rowHeight}
          width={width}
          margin={MARGIN}
          containerPadding={[0, 0]}
          isDraggable={editMode}
          isResizable={editMode}
          draggableHandle=".rack-module__rail"
          preventCollision={false}
          compactType="vertical"
          resizeHandles={['se']}
          onLayoutChange={handleLayoutChange}
          onResizeStop={handleResizeStop}
        >
          {visibleLayout.map((item) => {
            const def = registry[item.i];
            if (!def) return null;
            return (
              <div key={item.i}>
                <RackModule
                  def={def} w={item.w} h={item.h} editMode={editMode} onRemove={handleRemove}
                  collapseRailWhenLocked={collapseRailWhenLocked}
                />
              </div>
            );
          })}
        </GridLayout>
      </div>
      <ModuleTray visible={editMode} available={availableModules} onAdd={handleAdd} onReset={handleReset} />
    </div>
  );
}
