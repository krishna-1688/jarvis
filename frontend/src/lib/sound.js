/**
 * Three sounds only (Section 1.6): a soft mechanical click on wake, a low
 * blip on command success, a dull double-tick on error. Synthesized with
 * WebAudio rather than sourced audio files — no external assets, no
 * licensing to track, and the hardware-instrument aesthetic reads as
 * clean synthesized clicks anyway.
 */

const VOLUME = 0.2;
const MUTE_KEY = 'jarvis-muted';

let audioCtx = null;
let muted = typeof localStorage !== 'undefined' && localStorage.getItem(MUTE_KEY) === 'true';

function getCtx() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  return audioCtx;
}

function playTone(freq, duration, { type = 'sine', gain = VOLUME, filterFreq = null } = {}) {
  if (muted) return;
  const ctx = getCtx();
  const osc = ctx.createOscillator();
  osc.type = type;
  osc.frequency.value = freq;

  const env = ctx.createGain();
  env.gain.setValueAtTime(0, ctx.currentTime);
  env.gain.linearRampToValueAtTime(gain, ctx.currentTime + 0.005);
  env.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);

  let node = osc;
  if (filterFreq) {
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = filterFreq;
    node.connect(filter);
    node = filter;
  }
  node.connect(env);
  env.connect(ctx.destination);

  osc.start();
  osc.stop(ctx.currentTime + duration + 0.02);
}

export function playWakeClick() {
  playTone(1200, 0.03, { type: 'square', gain: VOLUME * 0.6 });
}

export function playSuccessBlip() {
  playTone(420, 0.12, { type: 'sine', gain: VOLUME });
}

export function playErrorTick() {
  playTone(160, 0.05, { type: 'sine', gain: VOLUME, filterFreq: 800 });
  setTimeout(() => playTone(150, 0.05, { type: 'sine', gain: VOLUME, filterFreq: 800 }), 90);
}

/** V.2a boot sequence: one low mechanical "thunk" — a fast downward
 * pitch sweep on a filtered low oscillator, not a beep. Plays once per
 * app launch. */
export function playBootClunk() {
  if (muted) return;
  const ctx = getCtx();

  const osc = ctx.createOscillator();
  osc.type = 'triangle';
  osc.frequency.setValueAtTime(110, ctx.currentTime);
  osc.frequency.exponentialRampToValueAtTime(45, ctx.currentTime + 0.18);

  const env = ctx.createGain();
  env.gain.setValueAtTime(0, ctx.currentTime);
  env.gain.linearRampToValueAtTime(VOLUME * 1.1, ctx.currentTime + 0.008);
  env.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.22);

  const filter = ctx.createBiquadFilter();
  filter.type = 'lowpass';
  filter.frequency.value = 500;

  osc.connect(filter);
  filter.connect(env);
  env.connect(ctx.destination);

  osc.start();
  osc.stop(ctx.currentTime + 0.25);
}

export function isMuted() {
  return muted;
}

export function setMuted(value) {
  muted = value;
  if (typeof localStorage !== 'undefined') localStorage.setItem(MUTE_KEY, String(value));
}

export function toggleMuted() {
  setMuted(!muted);
  return muted;
}
