import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { refresh, useSource } from './store.js';

const TONE_HINT = {
  witty: 'Sharp, dry, Iron Man’s Jarvis',
  friendly: 'Warm, upbeat, on your side',
  formal: 'Polite and precise, like a butler',
  'tough-love': 'Direct; calls out procrastination',
  calm: 'Gentle and reassuring',
};
const VOICES = [
  ['en-GB-RyanNeural', 'Ryan · British'],
  ['en-IN-PrabhatNeural', 'Prabhat · Indian'],
  ['en-IN-NeerjaNeural', 'Neerja · Indian'],
  ['en-US-GuyNeural', 'Guy · American'],
  ['en-US-JennyNeural', 'Jenny · American'],
  ['en-GB-SoniaNeural', 'Sonia · British'],
];

const asText = (v) => (Array.isArray(v) ? v.join(', ') : v ?? '');

function Field({ label, hint, children }) {
  return (
    <label className="you-field">
      <span className="x">{label}</span>
      {children}
      {hint && <span className="you-hint">{hint}</span>}
    </label>
  );
}

/** The "You" lens: everything that makes Jarvis personal — name, tone,
 * goals, routine, voice — saved to backend/profile.toml, applied live. */
export default function You() {
  const source = useSource('profile');
  const [draft, setDraft] = useState(null);
  const [state, setState] = useState('idle');

  useEffect(() => {
    if (source.data?.profile && !draft) setDraft(structuredClone(source.data.profile));
  }, [source.data, draft]);

  if (!draft) return <div className="skeleton" style={{ padding: 24 }}><i /><i /></div>;

  const choices = source.data?.choices || { tone: Object.keys(TONE_HINT), reply_length: ['short', 'balanced', 'detailed'] };
  const connected = source.data?.connected || {};
  const set = (sec, key) => (e) => {
    const v = e?.target ? (e.target.type === 'checkbox' ? e.target.checked : e.target.value) : e;
    setDraft((d) => ({ ...d, [sec]: { ...d[sec], [key]: v } }));
    setState('dirty');
  };
  const save = async () => {
    setState('saving');
    try {
      await api.saveProfile(draft);
      await refresh('profile');
      setState('saved');
    } catch {
      setState('error');
    }
  };

  return (
    <div className="you">
      <p className="you-lead">
        Everything here shapes how Jarvis talks to you. Changes apply straight away — no restart.
      </p>

      <div className="x lens-sub">You</div>
      <div className="you-grid">
        <Field label="Name"><input className="field" value={draft.you.name} onChange={set('you', 'name')} placeholder="Alex" /></Field>
        <Field label="Call me" hint="boss, sir, your name — or empty for none">
          <input className="field" value={draft.you.call_me} onChange={set('you', 'call_me')} placeholder="boss" />
        </Field>
      </div>
      <Field label="About you" hint="Background Jarvis keeps in mind: course, year, what you're into">
        <textarea className="field you-area" value={draft.you.about} onChange={set('you', 'about')}
          placeholder="3rd-year CSE student at VIT Chennai, into AI and cricket" />
      </Field>

      <div className="x lens-sub">Style</div>
      <div className="chips">
        {choices.tone.map((t) => (
          <button key={t} type="button" className={`chip-btn ${draft.style.tone === t ? 'is-on' : ''}`} onClick={() => set('style', 'tone')(t)}>{t}</button>
        ))}
      </div>
      <span className="you-hint">{TONE_HINT[draft.style.tone] || ''}</span>
      <div className="chips" style={{ marginTop: 10 }}>
        {choices.reply_length.map((t) => (
          <button key={t} type="button" className={`chip-btn ${draft.style.reply_length === t ? 'is-on' : ''}`} onClick={() => set('style', 'reply_length')(t)}>{t} replies</button>
        ))}
      </div>
      <div className="you-grid">
        <Field label="Language" hint="e.g. English with a little Tamil, Hinglish">
          <input className="field" value={draft.style.language} onChange={set('style', 'language')} />
        </Field>
        <label className="you-toggle">
          <input type="checkbox" checked={!!draft.style.humour} onChange={set('style', 'humour')} />
          <span>Humour</span>
        </label>
      </div>

      <div className="x lens-sub">Goals</div>
      <Field label="Dream"><input className="field" value={draft.goals.dream} onChange={set('goals', 'dream')} placeholder="Build a product used by millions" /></Field>
      <Field label="Ambitions" hint="Comma-separated">
        <input className="field" value={asText(draft.goals.ambitions)} onChange={set('goals', 'ambitions')} placeholder="Product-company placement, CGPA above 9" />
      </Field>
      <Field label="What drives you" hint="Used when you ask for motivation">
        <textarea className="field you-area" value={draft.goals.motivation} onChange={set('goals', 'motivation')} placeholder="Making my parents proud" />
      </Field>

      <div className="x lens-sub">Routine</div>
      <div className="you-grid you-grid--3">
        <Field label="Wake"><input className="field" value={draft.routine.wake_time} onChange={set('routine', 'wake_time')} placeholder="07:00" /></Field>
        <Field label="Sleep"><input className="field" value={draft.routine.sleep_time} onChange={set('routine', 'sleep_time')} placeholder="23:30" /></Field>
        <Field label="Study"><input className="field" value={draft.routine.study_time} onChange={set('routine', 'study_time')} placeholder="20:00-22:00" /></Field>
      </div>
      <Field label="Habits" hint="Comma-separated">
        <input className="field" value={asText(draft.routine.habits)} onChange={set('routine', 'habits')} placeholder="gym at 6pm, revise before bed" />
      </Field>
      <Field label="Favourite things" hint="Comma-separated">
        <input className="field" value={asText(draft.routine.favourites)} onChange={set('routine', 'favourites')} placeholder="AI, cricket, lo-fi" />
      </Field>

      <div className="x lens-sub">Voice</div>
      <div className="you-grid">
        <Field label="Wake reply" hint="{call_me} and {name} are filled in">
          <input className="field" value={draft.voice.wake_reply} onChange={set('voice', 'wake_reply')} />
        </Field>
        <Field label="Voice">
          <select className="field" value={draft.voice.tts_voice} onChange={set('voice', 'tts_voice')}>
            {!VOICES.some(([v]) => v === draft.voice.tts_voice) && <option value={draft.voice.tts_voice}>{draft.voice.tts_voice}</option>}
            {VOICES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </Field>
      </div>

      <div className="x lens-sub">College</div>
      <div className="you-connect">
        {[['vtop', 'VTOP · attendance, marks, exams, timetable'], ['lms', 'LMS · assignments & deadlines'], ['whatsapp', 'WhatsApp · daily brief & reminders']].map(([k, label]) => (
          <div key={k} className="you-connect__row">
            <i className={connected[k] ? 'is-on' : ''} />
            <span>{label}</span>
            <span className="mono">{connected[k] ? 'CONNECTED' : 'NOT SET UP'}</span>
          </div>
        ))}
        {!connected.vtop && <span className="you-hint">Run <span className="mono">python setup.py college</span> to connect your VIT account — your login stays on this laptop.</span>}
      </div>
      <div className="you-grid">
        <Field label="Campus">
          <select className="field" value={draft.college.campus} onChange={set('college', 'campus')}>
            <option value="chennai">VIT Chennai</option>
            <option value="vellore">VIT Vellore (experimental)</option>
          </select>
        </Field>
        <Field label="Admission year" hint="0 = from your registration number">
          <input className="field" type="number" value={draft.college.admission_year} onChange={set('college', 'admission_year')} />
        </Field>
      </div>

      <div className="you-save">
        <span className="you-hint">
          {state === 'saved' ? 'Saved — Jarvis will use this from the next reply.' : state === 'error' ? 'Couldn’t save — is the backend running?' : state === 'dirty' ? 'Unsaved changes' : ''}
        </span>
        <button type="button" className="btn btn--ember" disabled={state === 'saving' || state === 'idle' || state === 'saved'} onClick={save}>
          {state === 'saving' ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}
