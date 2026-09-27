import { useState } from 'react';
import { motion } from 'motion/react';
import { useOrbit } from './context.js';
import {
  AssignmentCards, AttendanceBars, CgpaCard, ClassList, ExamCards, NextClassCard, TaskList, Timeline,
} from './viz.jsx';

/** Picks a visual for the structured `data` a command returned, plus the
 * lens that shows the same thing in full. */
function visualFor(data = {}) {
  if (Array.isArray(data.rows) && data.rows[0]?.attended_classes !== undefined) {
    return { lens: 'attendance', node: <AttendanceBars rows={data.rows} compact limit={data.rows.length > 1 ? 6 : 1} /> };
  }
  if (Array.isArray(data.blocks)) return { lens: 'schedule', node: <Timeline blocks={data.blocks} /> };
  if (Array.isArray(data.classes)) return { lens: 'schedule', node: <ClassList classes={data.classes} day={data.day} /> };
  if (data.next_class) return { lens: 'schedule', node: <NextClassCard cls={data.next_class} /> };
  if (Array.isArray(data.pending)) return { lens: 'assignments', node: <AssignmentCards items={data.pending} limit={4} /> };
  if (Array.isArray(data.exams)) return { lens: 'exams', node: <ExamCards exams={data.exams} limit={4} /> };
  if (Array.isArray(data.tasks)) return { lens: 'tasks', node: <TaskList tasks={data.tasks} limit={6} /> };
  if (data.summary?.cgpa !== undefined) return { lens: null, node: <CgpaCard summary={data.summary} /> };
  return null;
}

/** Plain replies: keep the backend's line structure, drop status emoji
 * the visuals already express. */
function Prose({ text }) {
  return (
    <div className="prose">
      {(text || '').split('\n').map((line, i) => (
        line.trim() ? <p key={i}>{line.replace(/^[\u{1F7E2}\u{1F7E1}\u{1F534}❓✅❌]\s*/u, '')}</p> : <br key={i} />
      ))}
    </div>
  );
}

export default function ReplyCard({ msg, isLatest, expecting }) {
  const { openLens, send } = useOrbit();
  const [showFull, setShowFull] = useState(false);
  const visual = msg.ok !== false ? visualFor(msg.data) : null;
  const headline = visual ? (msg.spoken || msg.text) : msg.text;

  return (
    <motion.article
      className={`reply ${msg.ok === false ? 'reply--error' : ''} ${msg.role === 'system' ? 'reply--system' : ''}`}
      initial={{ opacity: 0, y: 14, filter: 'blur(6px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      transition={{ type: 'spring', stiffness: 260, damping: 28 }}
    >
      <div className="reply__glyph" aria-hidden />
      <div className="reply__body">
        {visual ? <p className="reply__headline">{headline}</p> : <Prose text={headline} />}
        {visual && <div className="reply__visual">{visual.node}</div>}
        {visual && (
          <div className="reply__tools">
            {visual.lens && <button type="button" className="link" onClick={() => openLens(visual.lens)}>Open {visual.lens} →</button>}
            <button type="button" className="link dim" onClick={() => setShowFull((v) => !v)}>{showFull ? 'Hide details' : 'Full text'}</button>
          </div>
        )}
        {visual && showFull && <Prose text={msg.text} />}
        {isLatest && expecting && (
          <div className="reply__confirm">
            <button type="button" className="btn btn--primary" onClick={() => send('yes')}>Yes, go ahead</button>
            <button type="button" className="btn" onClick={() => send('no')}>No</button>
          </div>
        )}
      </div>
    </motion.article>
  );
}
