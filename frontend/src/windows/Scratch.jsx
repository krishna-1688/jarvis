import { useState } from 'react';
import SegmentedDisplay from '../components/SegmentedDisplay/SegmentedDisplay.jsx';
import VUMeter from '../components/VUMeter/VUMeter.jsx';
import Teleprinter from '../components/Teleprinter/Teleprinter.jsx';
import AttendanceModule from '../modules/AttendanceModule/AttendanceModule.jsx';
import TasksModule from '../modules/TasksModule/TasksModule.jsx';
import FocusModule from '../modules/FocusModule/FocusModule.jsx';
import ScheduleModule from '../modules/ScheduleModule/ScheduleModule.jsx';
import ExpenseModule from '../modules/ExpenseModule/ExpenseModule.jsx';

const STATES = ['idle', 'wake', 'listening', 'speaking', 'error'];
const SAMPLE_LINES = {
  idle: '',
  wake: '',
  listening: 'what is my attendance in embedded systems',
  speaking: "you're at 67% in Embedded Systems — two more misses and you're under 75.",
  error: 'backend unreachable — retrying in 15s',
};

/** Dev-only scratch page for visually vetting components in isolation. Not part of the built app's normal routes. */
export default function Scratch() {
  const [state, setState] = useState('idle');

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 24, background: 'var(--chassis)', minHeight: '100vh' }}>
      <section style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-dim)', letterSpacing: '0.06em' }}>
          VU METER + TELEPRINTER — state: {state}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {STATES.map((s) => (
            <button
              key={s}
              onClick={() => setState(s)}
              className="mono"
              style={{
                padding: '6px 12px',
                borderRadius: 'var(--radius-control)',
                background: s === state ? 'var(--signal)' : 'var(--inset)',
                color: s === state ? 'var(--on-signal)' : 'var(--ink)',
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              {s}
            </button>
          ))}
        </div>
        <div style={{ maxWidth: 480 }}>
          <VUMeter state={state} />
        </div>
        <Teleprinter text={SAMPLE_LINES[state]} />
      </section>

      <Row size="lg" />
      <Row size="md" />
      <Row size="sm" />

      <section style={{ display: 'flex', gap: 16 }}>
        <div style={{ width: 160, height: 340, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <AttendanceModule w={3} />
        </div>
        <div style={{ width: 420, height: 420, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <AttendanceModule w={5} />
        </div>
      </section>

      <section style={{ display: 'flex', gap: 16 }}>
        <div style={{ width: 260, height: 320, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <TasksModule w={3} />
        </div>
        <div style={{ width: 420, height: 420, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <TasksModule w={5} />
        </div>
      </section>

      <section style={{ display: 'flex', gap: 16 }}>
        <div style={{ width: 260, height: 300, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <FocusModule />
        </div>
        <div style={{ width: 260, height: 300, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <FocusModule mockActive={{ subject: 'DBMS', phase: 'work', remainingSeconds: 754, pomodorosCompleted: 2, plannedMinutes: 120 }} />
        </div>
        <div style={{ width: 260, height: 300, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <FocusModule mockActive={{ subject: 'TOC', phase: 'break', remainingSeconds: 210, pomodorosCompleted: 1, plannedMinutes: 50 }} />
        </div>
      </section>

      <section style={{ display: 'flex', gap: 16 }}>
        <div style={{ width: 260, height: 500, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <ScheduleModule w={3} h={6} />
        </div>
      </section>

      <section style={{ display: 'flex', gap: 16 }}>
        <div style={{ width: 260, height: 320, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <ExpenseModule w={3} />
        </div>
        <div style={{ width: 480, height: 320, background: 'var(--panel)', borderRadius: 6, padding: 8 }}>
          <ExpenseModule w={6} />
        </div>
      </section>
    </div>
  );
}

function Row({ size }) {
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
      <span className="mono" style={{ width: 24, color: 'var(--ink-dim)', fontSize: 11 }}>{size}</span>
      <SegmentedDisplay value="74.2%" size={size} tone="amber" />
      <SegmentedDisplay value="09:41" size={size} tone="amber" />
      <SegmentedDisplay value="FAT-1" size={size} tone="signal" />
      <SegmentedDisplay value="18d 04h" size={size} tone="signal" />
      <SegmentedDisplay value="- - -" size={size} tone="amber" />
      <SegmentedDisplay value="9.12" size={size} tone="safe" />
    </div>
  );
}
