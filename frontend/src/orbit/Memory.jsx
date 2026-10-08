import { useEffect, useMemo, useState } from 'react';
import { motion } from 'motion/react';
import { api } from '../api.js';
import { useOrbit } from './context.js';
import { useSource } from './store.js';
import { profileView } from './format.js';

const W = 1000;
const H = 620;
const KINDS = {
  course: { label: 'Courses', dot: 'ring', color: '#F3EDE4' },
  exam:   { label: 'Exams',   dot: 'fill', color: '#FF6A2B' },
  person: { label: 'People',  dot: 'fill', color: '#E8C27A' },
  topic:  { label: 'Topics',  dot: 'fill', color: '#9FB8A0' },
  task:   { label: 'Tasks',   dot: 'fill', color: '#6F685E' },
  fact:   { label: 'Facts',   dot: 'ring', color: '#E8C27A' },
};
const kindOf = (k) => KINDS[k] || { label: k, dot: 'fill', color: '#B5AC9F' };

/** Force layout, run once per snapshot (~40 nodes, a few ms). The user sits
 * at the centre; strong links pull tight, everything repels, and more
 * active memories sit closer in. Deterministic, so reopening the lens
 * shows the same map. */
function layout(nodes, edges) {
  const pts = nodes.map((n, i) => {
    const a = (i / nodes.length) * Math.PI * 2;
    const r = 140 + (1 - n.weight) * 180;
    return { ...n, x: W / 2 + Math.cos(a) * r, y: H / 2 + Math.sin(a) * r * 0.72, vx: 0, vy: 0 };
  });
  const byId = Object.fromEntries(pts.map((p) => [p.id, p]));
  for (let step = 0; step < 260; step += 1) {
    const cool = 1 - step / 260;
    for (let i = 0; i < pts.length; i += 1) {
      for (let j = i + 1; j < pts.length; j += 1) {
        const p = pts[i]; const q = pts[j];
        let dx = p.x - q.x; let dy = p.y - q.y;
        const d2 = Math.max(80, dx * dx + dy * dy);
        const f = 2600 / d2;
        const d = Math.sqrt(d2); dx /= d; dy /= d;
        p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
      }
    }
    edges.forEach(({ a, b, w }) => {
      const p = byId[a]; const q = byId[b];
      if (!p || !q) return;
      const dx = q.x - p.x; const dy = q.y - p.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - (70 + (1 - w) * 90)) * 0.012 * (0.4 + w);
      p.vx += (dx / d) * f; p.vy += (dy / d) * f; q.vx -= (dx / d) * f; q.vy -= (dy / d) * f;
    });
    pts.forEach((p) => {
      const dx = W / 2 - p.x; const dy = (H / 2 - p.y) * 1.35;
      const target = 110 + (1 - p.weight) * 200;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - target) * 0.006;
      p.vx += (dx / d) * f; p.vy += (dy / d) * f;
      p.x += p.vx * cool; p.y += p.vy * cool;
      p.vx *= 0.6; p.vy *= 0.6;
      p.x = Math.max(60, Math.min(W - 60, p.x));
      p.y = Math.max(34, Math.min(H - 34, p.y));
    });
  }
  return pts;
}

export default function Memory() {
  const { send, openLens } = useOrbit();
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(false);
  const [sel, setSel] = useState(null);
  const [detail, setDetail] = useState(null);
  const [off, setOff] = useState(() => new Set());
  const you = profileView(useSource('profile').data).name;

  useEffect(() => {
    api.memoryGraph().then(setGraph).catch(() => setError(true));
  }, []);

  const pts = useMemo(() => (graph ? layout(graph.nodes, graph.edges) : []), [graph]);
  const byId = useMemo(() => Object.fromEntries(pts.map((p) => [p.id, p])), [pts]);

  useEffect(() => {
    if (!pts.length || sel != null) return;
    const first = pts.find((p) => p.kind === 'course') || pts[0];
    setSel(first.id);
  }, [pts, sel]);

  useEffect(() => {
    if (sel == null) return undefined;
    let live = true;
    setDetail(null);
    api.memoryNode(sel).then((d) => { if (live) setDetail(d); }).catch(() => {});
    return () => { live = false; };
  }, [sel]);

  if (error) return <div className="empty" style={{ padding: 24 }}>Couldn't read memory. Is the backend running?</div>;
  if (!graph) return <div className="skeleton" style={{ padding: 24 }}><i /><i /></div>;
  if (!pts.length) return <div className="empty" style={{ padding: 24 }}>Nothing in memory yet. Talk to me and it fills in.</div>;

  const visible = (p) => !off.has(p.kind);
  const near = new Set(graph.edges.filter((e) => e.a === sel || e.b === sel).map((e) => (e.a === sel ? e.b : e.a)));
  const kinds = [...new Set(pts.map((p) => p.kind))];
  const toggle = (k) => setOff((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const node = byId[sel];

  return (
    <div className="mem">
      <div className="mem__stage">
        <div className="mem__filters">
          {kinds.map((k) => {
            const kk = kindOf(k);
            return (
              <button key={k} type="button" className={`mem__filter ${off.has(k) ? 'is-off' : ''}`} onClick={() => toggle(k)}>
                <i style={kk.dot === 'ring' ? { border: `1.5px solid ${kk.color}` } : { background: kk.color }} />{kk.label}
              </button>
            );
          })}
        </div>
        <svg className="mem__svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
          <defs>
            <radialGradient id="mem-kk"><stop offset="0" stopColor="#FFF8EF" /><stop offset=".5" stopColor="#FFC49A" /><stop offset="1" stopColor="#FF6A2B" /></radialGradient>
            <radialGradient id="mem-sel"><stop offset="0" stopColor="rgba(255,106,43,.45)" /><stop offset="1" stopColor="rgba(255,106,43,0)" /></radialGradient>
          </defs>
          <circle cx={W / 2} cy={H / 2} r="300" fill="none" stroke="rgba(243,237,228,.04)" />
          <circle cx={W / 2} cy={H / 2} r="180" fill="none" stroke="rgba(243,237,228,.04)" />
          {pts.filter(visible).map((p) => (
            <line key={`c${p.id}`} x1={W / 2} y1={H / 2} x2={p.x} y2={p.y} stroke="#F3EDE4" strokeWidth={0.5 + p.weight} opacity={0.03 + p.weight * 0.07} />
          ))}
          {graph.edges.map((e) => {
            const p = byId[e.a]; const q = byId[e.b];
            if (!p || !q || !visible(p) || !visible(q)) return null;
            const hot = e.a === sel || e.b === sel;
            return (
              <g key={`${e.a}-${e.b}`}>
                <line className={`mem__edge ${hot ? 'is-hot' : ''}`} x1={p.x} y1={p.y} x2={q.x} y2={q.y}
                  stroke={hot ? '#FF6A2B' : '#F3EDE4'} strokeWidth={hot ? 1.2 + e.w * 3 : 0.6 + e.w * 2}
                  opacity={hot ? 0.5 + e.w * 0.4 : sel != null ? 0.06 + e.w * 0.12 : 0.1 + e.w * 0.25} strokeLinecap="round" />
                {hot && <line className="mem__flow" x1={p.x} y1={p.y} x2={q.x} y2={q.y} strokeWidth="1.2" />}
              </g>
            );
          })}
          <circle cx={W / 2} cy={H / 2} r="40" fill="rgba(255,106,43,.08)" />
          <circle cx={W / 2} cy={H / 2} r="20" fill="url(#mem-kk)" />
          <text x={W / 2} y={H / 2 + 40} textAnchor="middle" style={{ fontFamily: 'var(--font-signal)', fontSize: 11, letterSpacing: 3, fill: '#F3EDE4' }}>{you === 'there' ? 'YOU' : you.toUpperCase().slice(0, 10)}</text>
          {pts.filter(visible).map((p, i) => {
            const kk = kindOf(p.kind);
            const isSel = p.id === sel;
            const r = 5 + p.weight * 12;
            const dim = sel != null && !isSel && !near.has(p.id);
            return (
              <motion.g key={p.id} className={`mem__node ${isSel ? 'is-sel' : ''} ${near.has(p.id) ? 'is-near' : ''} ${dim ? 'is-dim' : ''}`}
                initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.1 + i * 0.015, type: 'spring', stiffness: 220, damping: 22 }}
                style={{ transformOrigin: `${p.x}px ${p.y}px` }}
                onClick={() => setSel(p.id)} role="button" aria-label={p.label}>
                {isSel && <circle cx={p.x} cy={p.y} r={r + 34} fill="url(#mem-sel)" />}
                <circle cx={p.x} cy={p.y} r={r + 8} fill="transparent" />
                {kk.dot === 'ring'
                  ? <circle cx={p.x} cy={p.y} r={r} fill="#0B0A09" stroke={isSel ? '#FF6A2B' : kk.color} strokeWidth={isSel ? 2.5 : 1.5} />
                  : <circle cx={p.x} cy={p.y} r={r * 0.8} fill={kk.color} />}
                {isSel && kk.dot === 'ring' && <circle cx={p.x} cy={p.y} r={r * 0.35} fill="#FF6A2B" />}
                <text x={p.x} y={p.y + r + 16} textAnchor="middle" style={isSel ? { fontSize: 14, fontWeight: 700 } : undefined}>
                  {p.label.length > 22 ? `${p.label.slice(0, 21)}…` : p.label}
                </text>
              </motion.g>
            );
          })}
        </svg>
        <div className="mem__legend">
          <span><i style={{ width: 26, height: 3, borderRadius: 2, background: '#FF6A2B', display: 'inline-block' }} />active path</span>
          <span><i style={{ width: 26, height: 1, background: 'rgba(243,237,228,.4)', display: 'inline-block' }} />link, brighter when used</span>
          <span style={{ flex: 1 }} />
          <span className="mono">{pts.length} MEMORIES · {graph.edges.length} LINKS</span>
        </div>
      </div>

      <aside className="mem__side">
        {node && (
          <>
            <div>
              <div className="x" style={{ color: '#FF8A4C' }}>{kindOf(node.kind).label.replace(/s$/, '')}</div>
              <h4>{node.label}</h4>
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, color: '#B5AC9F' }}>
                <span>Activity</span>
                <span className="mono" style={{ color: '#F3EDE4' }}>{node.weight > 0.66 ? 'STRONG' : node.weight > 0.33 ? 'STEADY' : 'FADING'}</span>
              </div>
              <div className="mem__bar"><i style={{ width: `${Math.max(6, node.weight * 100)}%` }} /></div>
              <div className="dim" style={{ fontSize: 12, marginTop: 8 }}>
                Last mentioned {node.seen}{detail ? ` · in ${detail.events} conversation${detail.events === 1 ? '' : 's'}` : ''}
              </div>
            </div>
            {detail?.links?.length > 0 && (
              <div>
                <div className="x">Connected to</div>
                <div style={{ marginTop: 4 }}>
                  {detail.links.map((l) => (
                    <button key={l.id} type="button" className="mem__link" onClick={() => byId[l.id] && setSel(l.id)}>
                      <i style={kindOf(l.kind).dot === 'ring' ? { border: `1.5px solid ${kindOf(l.kind).color}` } : { background: kindOf(l.kind).color }} />
                      <span>{l.label}</span>
                      <span style={{ flex: 'none', width: 64, height: 3, borderRadius: 2, background: 'rgba(243,237,228,.08)' }}>
                        <span style={{ display: 'block', width: `${Math.max(8, l.w * 100)}%`, height: 3, borderRadius: 2, background: 'rgba(243,237,228,.7)' }} />
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {detail?.recent?.length > 0 && (
              <div>
                <div className="x">Recent moments</div>
                <div style={{ marginTop: 4 }}>
                  {detail.recent.map((m, k) => (
                    <div key={k} className="mem__moment">
                      <span className="mono">{m.ago.toUpperCase()}</span>
                      <span>“{m.text}”</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div style={{ marginTop: 'auto', display: 'flex', gap: 8 }}>
              <button type="button" className="btn btn--primary" style={{ flex: 1 }}
                onClick={() => { openLens('memory'); send(`what do you know about ${node.label}`); }}>
                Ask about {node.label.length > 14 ? 'this' : node.label}
              </button>
              <button type="button" className="btn" onClick={() => { openLens('memory'); send(`forget everything about ${node.label}`); }}>Forget</button>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
