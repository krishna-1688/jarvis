import { useEffect, useRef } from 'react';

/**
 * Jarvis's presence: a shader-rendered plasma core whose colour, energy
 * and turbulence glide between voice states.
 *
 * Cost control: measured at 166% of one core when it animated
 * continuously. Now it renders at 60% resolution (the glow hides it),
 * animates only while Jarvis is doing something, and once idle has
 * settled it stops drawing entirely — a still frame costs nothing. Any
 * state change restarts it.
 */
const RENDER_SCALE = 0.6;
const SETTLE_MS = 1500;

const VERT = `
attribute vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }
`;

const FRAG = `
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform vec3 uA;
uniform vec3 uB;
uniform float uEnergy;
uniform float uSwirl;
uniform float uFlash;
uniform float uRings;

float hash(vec3 p) {
  return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453);
}
float noise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
    mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y),
    f.z);
}
float fbm(vec3 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + 11.7; a *= 0.5; }
  return v;
}

void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
  float t = uTime;
  float r = length(uv);
  float ang = atan(uv.y, uv.x);

  float R = 0.30;
  float wob = fbm(vec3(cos(ang) * 1.6, sin(ang) * 1.6, t * (0.25 + 0.6 * uSwirl))) - 0.5;
  float Rd = R + wob * (0.035 + 0.09 * uEnergy) + 0.01 * sin(t * 1.4);
  float d = r / Rd;

  vec3 col = vec3(0.0);
  float alpha = 0.0;

  if (d < 1.0) {
    float z = sqrt(1.0 - d * d);
    vec3 p = vec3(uv / Rd, z);
    float s = t * (0.12 + uSwirl * 0.9);
    mat2 rot = mat2(cos(s), -sin(s), sin(s), cos(s));
    p.xy = rot * p.xy;
    float n  = fbm(p * 2.1 + vec3(0.0, 0.0, t * 0.22));
    float n2 = fbm(p * 4.3 - vec3(t * 0.18) + n * 1.5);
    vec3 base = mix(uA, uB, smoothstep(0.28, 0.78, n));
    float fres = pow(1.0 - z, 2.2);
    float veins = smoothstep(0.52, 0.62, n2) * (0.35 + uEnergy);
    col = base * (0.42 + 0.85 * n2) * (0.5 + 0.5 * z);
    col += uB * veins * 0.9;
    col += mix(uA, uB, 0.6) * fres * 1.25;
    col *= 0.8 + 0.45 * uEnergy;
    alpha = 1.0;
  }

  // halo
  float outside = max(d - 1.0, 0.0);
  float halo = exp(-5.5 * outside) * (0.28 + 0.45 * uEnergy);
  vec3 haloCol = mix(uA, uB, 0.55) * halo;
  // emitted rings (speaking / wake)
  float ring = pow(max(sin(34.0 * (r - t * 0.11)), 0.0), 6.0) * exp(-7.0 * outside) * step(1.0, d);
  haloCol += uB * ring * uRings * 0.55;
  float edgeFlash = uFlash * exp(-14.0 * abs(d - 1.0));
  haloCol += vec3(1.0) * edgeFlash;

  col += haloCol * step(1.0, d);
  col += vec3(1.0) * uFlash * 0.35 * step(d, 1.0);
  alpha = max(alpha, clamp(max(max(haloCol.r, haloCol.g), haloCol.b), 0.0, 1.0));
  gl_FragColor = vec4(col, alpha);
}
`;

/* state -> [colorA, colorB, energy, swirl, rings] */
const PALETTES = {
  idle:         [[0.05, 0.32, 0.72], [0.35, 0.86, 1.00], 0.14, 0.10, 0.0],
  wake:         [[0.35, 0.75, 1.00], [0.90, 0.98, 1.00], 0.85, 0.45, 1.0],
  listening:    [[0.95, 0.42, 0.10], [1.00, 0.82, 0.42], 0.62, 0.35, 0.3],
  transcribing: [[0.95, 0.42, 0.10], [1.00, 0.82, 0.42], 0.45, 0.80, 0.0],
  thinking:     [[0.36, 0.18, 0.95], [0.82, 0.52, 1.00], 0.38, 1.10, 0.0],
  speaking:     [[0.04, 0.55, 0.78], [0.55, 1.00, 0.92], 0.55, 0.30, 1.0],
  error:        [[0.85, 0.10, 0.22], [1.00, 0.50, 0.45], 0.50, 0.60, 0.0],
};

const ACTIVE = new Set(['wake', 'listening', 'transcribing', 'thinking', 'speaking', 'error']);

function compile(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}

export default function CoreOrb({ state = 'idle', className = '' }) {
  const canvasRef = useRef(null);
  const fallbackRef = useRef(null);
  const stateRef = useRef(state);
  const flashRef = useRef(0);
  const wakeLoopRef = useRef(() => {});

  useEffect(() => {
    if (state === 'wake' && stateRef.current !== 'wake') flashRef.current = 1;
    stateRef.current = state;
    if (fallbackRef.current) fallbackRef.current.dataset.state = state;
    wakeLoopRef.current();
  }, [state]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const gl = canvas?.getContext('webgl', { premultipliedAlpha: true, alpha: true, antialias: false });
    if (!gl) {
      canvas.style.display = 'none';
      fallbackRef.current.style.display = 'block';
      return undefined;
    }

    let program;
    try {
      program = gl.createProgram();
      gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERT));
      gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(program);
      gl.useProgram(program);
    } catch (e) {
      console.warn('[orb] shader failed, using CSS fallback', e);
      canvas.style.display = 'none';
      fallbackRef.current.style.display = 'block';
      return undefined;
    }

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(program, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);

    const u = Object.fromEntries(
      ['uRes', 'uTime', 'uA', 'uB', 'uEnergy', 'uSwirl', 'uFlash', 'uRings'].map((n) => [n, gl.getUniformLocation(program, n)]),
    );

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2) * RENDER_SCALE;
      const { width, height } = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      gl.viewport(0, 0, canvas.width, canvas.height);
    };
    resize();

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const cur = { a: [...PALETTES.idle[0]], b: [...PALETTES.idle[1]], energy: 0.14, swirl: 0.1, rings: 0 };
    let raf = 0;
    let running = false;
    let last = performance.now();
    let clock = 0;
    let idleSince = 0;

    const frame = (now) => {
      if (document.hidden) { running = false; return; }
      const st = stateRef.current;
      const active = ACTIVE.has(st) || flashRef.current > 0.01;
      if (active) idleSince = 0;
      else if (!idleSince) idleSince = now;
      if (!active && now - idleSince > SETTLE_MS) { running = false; return; }
      raf = requestAnimationFrame(frame);

      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const [ta, tb, te, ts, tr] = PALETTES[st] || PALETTES.idle;
      const k = 1 - Math.exp(-dt * 4.5);
      for (let i = 0; i < 3; i += 1) {
        cur.a[i] += (ta[i] - cur.a[i]) * k;
        cur.b[i] += (tb[i] - cur.b[i]) * k;
      }
      // live-feeling energy: jitter while listening/speaking
      const jitter = st === 'listening' || st === 'speaking' ? 0.22 * Math.sin(now / 90) * Math.sin(now / 37) : 0;
      cur.energy += (te + jitter - cur.energy) * k;
      cur.swirl += (ts - cur.swirl) * k;
      cur.rings += (tr - cur.rings) * k;
      flashRef.current *= Math.exp(-dt * 3.2);
      clock += dt * (reduceMotion ? 0.25 : 1) * (0.7 + cur.energy * 0.9);

      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(u.uRes, canvas.width, canvas.height);
      gl.uniform1f(u.uTime, clock);
      gl.uniform3fv(u.uA, cur.a);
      gl.uniform3fv(u.uB, cur.b);
      gl.uniform1f(u.uEnergy, cur.energy);
      gl.uniform1f(u.uSwirl, cur.swirl);
      gl.uniform1f(u.uFlash, flashRef.current);
      gl.uniform1f(u.uRings, cur.rings);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };
    const start = () => {
      if (running) return;
      running = true;
      idleSince = 0;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    };
    wakeLoopRef.current = start;
    const onVisible = () => { if (!document.hidden) start(); };
    document.addEventListener('visibilitychange', onVisible);
    const ro2 = new ResizeObserver(() => { resize(); start(); });
    ro2.observe(canvas);
    start();

    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      ro2.disconnect();
      wakeLoopRef.current = () => {};
      cancelAnimationFrame(raf);
      // Free GPU objects but keep the context alive: StrictMode re-runs
      // this effect on the same canvas, and a lost context can't compile.
      gl.deleteBuffer(buf);
      gl.deleteProgram(program);
    };
  }, []);

  return (
    <div className={`core-orb ${className}`}>
      <canvas ref={canvasRef} className="core-orb__canvas" />
      <div ref={fallbackRef} className="core-orb__fallback" data-state={state} />
    </div>
  );
}
