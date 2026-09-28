import { useState } from 'react';
import { motion } from 'motion/react';
import { useOrbit } from './context.js';
import {
  AssignmentCards, AttendanceBars, AttendanceRings, CgpaCard, ClassList, ExamCards, NextClassCard, TaskList, Timeline,
} from './viz.jsx';
import { cleanCourse, shortCourse } from './format.js';

/** Picks a visual for the structured `data` a command returned, plus the
 * lens that shows the same thing in full. */
function visualFor(data = {}) {
  if (Array.isArray(data.rows) && data.rows[0]?.attended_classes !== undefined) {
    const tightest = [...data.rows].sort((a, b) => (a.percentage ?? 101) - (b.percentage ?? 101))[0];
    return {
      lens: 'attendance', source: 'VTOP',
      node: data.rows.length > 1 ? <AttendanceRings rows={data.rows} /> : <AttendanceBars rows={data.rows} />,
      ask: data.rows.length > 1 && tightest
        ? { label: `Can I skip ${shortCourse(tightest.course_name)} tomorrow?`, text: `can I skip ${cleanCourse(tightest.course_name)} tomorrow` }
        : null,
      legend: data.rows.length > 1,
    };
  }
  if (Array.isArray(data.blocks)) return { lens: 'schedule', node: <Timeline blocks={data.blocks} /> };
  if (Array.isArray(data.classes)) return { lens: 'schedule', node: <ClassList classes={data.classes} day={data.day} /> };
  if (data.next_class) return { lens: 'schedule', node: <NextClassCard cls={data.next_class} /> };
  if (Array.isArray(data.pending)) return { lens: 'assignments', source: 'LMS', node: <AssignmentCards items={data.pending} limit={4} /> };
  if (Array.isArray(data.exams)) return { lens: 'exams', source: 'VTOP', node: <ExamCards exams={data.exams} limit={4} /> };
  if (Array.isArray(data.tasks)) return { lens: 'tasks', node: <TaskList tasks={data.tasks} limit={6} /> };
  if (data.summary?.cgpa !== undefined) return { lens: null, source: 'VTOP', node: <CgpaCard summary={data.summary} /> };
  return null;
}

/** **bold** and `code` inside a line, as React nodes (never raw HTML). */
function inline(line) {
  return line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) return <b key={i}>{part.slice(2, -2)}</b>;
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    return part;
  });
}

/** Plain replies: keep the backend's line structure, render the little
 * markdown models use (bold, code, bullets, headings), and drop status
 * emoji the visuals already express. */
function Prose({ text }) {
  const blocks = [];
  (text || '').split('\n').forEach((raw, i) => {
    const line = raw.replace(/^[\u{1F7E2}\u{1F7E1}\u{1F534}❓✅❌]\s*/u, '');
    const bullet = /^\s*(?:[*\-•]|\d+[.)])\s+(.*)$/.exec(line);
    if (bullet) {
      const last = blocks[blocks.length - 1];
      const item = <li key={i}>{inline(bullet[1])}</li>;
      if (last?.type === 'ul') last.items.push(item); else blocks.push({ type: 'ul', key: i, items: [item] });
    } else if (!line.trim()) {
      blocks.push({ type: 'gap', key: i });
    } else {
      const heading = /^#{1,4}\s+(.*)$/.exec(line);
      blocks.push({ type: heading ? 'h' : 'p', key: i, node: inline(heading ? heading[1] : line) });
    }
  });
  return (
    <div className="prose">
      {blocks.map((b) => {
        if (b.type === 'ul') return <ul key={b.key}>{b.items}</ul>;
        if (b.type === 'gap') return <br key={b.key} />;
        if (b.type === 'h') return <p key={b.key} className="prose__h">{b.node}</p>;
        return <p key={b.key}>{b.node}</p>;
      })}
    </div>
  );
}

const enter = {
  initial: { opacity: 0, y: 14, filter: 'blur(6px)' },
  animate: { opacity: 1, y: 0, filter: 'blur(0px)' },
  transition: { type: 'spring', stiffness: 260, damping: 28 },
};

export default function ReplyCard({ msg, isLatest, expecting }) {
  const { openLens, send } = useOrbit();
  const [showFull, setShowFull] = useState(false);
  const data = msg.data || {};
  const visual = msg.ok !== false ? visualFor(data) : null;
  const confirm = isLatest && expecting && (
    <div className="reply__confirm">
      <button type="button" className="btn btn--primary" onClick={() => send('yes')}>Yes, go ahead</button>
      <button type="button" className="btn" onClick={() => send('no')}>No</button>
    </div>
  );

  if (data.screen) {
    return (
      <motion.article className="reply reply--card" {...enter}>
        <div className="screen-answer">
          {data.thumb && <div className="screen-answer__shot"><img src={`data:image/jpeg;base64,${data.thumb}`} alt="What Jarvis saw" /></div>}
          <div className="reply">
            <Prose text={msg.text} />
            <span className="reply__foot">ONE LOOK · NOT SAVED{data.seconds ? ` · ${data.seconds} S` : ''}</span>
          </div>
        </div>
      </motion.article>
    );
  }

  if (!visual) {
    return (
      <motion.article className={`reply ${msg.ok === false ? 'reply--error' : ''} ${msg.role === 'system' ? 'reply--system' : ''}`} {...enter}>
        <Prose text={msg.text} />
        {confirm}
      </motion.article>
    );
  }

  return (
    <motion.article className="reply reply--card" {...enter}>
      <div className="reply__head">
        <p className="reply__headline">{msg.spoken || msg.text}</p>
        {visual.source && <span className="reply__source">{visual.source}</span>}
      </div>
      {visual.node}
      <div className="reply__tools">
        {visual.ask && <button type="button" className="pill-btn" onClick={() => send(visual.ask.text)}>{visual.ask.label}</button>}
        {visual.lens && <button type="button" className="pill-btn" onClick={() => openLens(visual.lens)}>Open {visual.lens}</button>}
        <button type="button" className="pill-btn" onClick={() => setShowFull((v) => !v)}>{showFull ? 'Hide details' : 'Full text'}</button>
        {visual.legend && <span className="att-legend"><i />75% line</span>}
      </div>
      {showFull && <Prose text={msg.text} />}
      {confirm}
    </motion.article>
  );
}
