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
  float Rd = R + wob * (0.012 + 0.06 * uEnergy) + 0.006 * sin(t * 1.4);
  float d = r / Rd;
  // Soft edge over ~2 px: the canvas renders at 60% scale, and a hard
  // cut-off showed as a jagged rim.
  float aa = 2.2 / (Rd * min(uRes.x, uRes.y));
  float body = 1.0 - smoothstep(1.0 - aa, 1.0, d);

  vec3 col = vec3(0.0);
  float alpha = 0.0;

  if (d < 1.0) {
    float z = sqrt(max(1.0 - d * d, 0.0));
    vec3 p = vec3(uv / Rd, z);
    float s = t * (0.12 + uSwirl * 0.9);
    mat2 rot = mat2(cos(s), -sin(s), sin(s), cos(s));
    p.xy = rot * p.xy;
    // Molten core, not a textured planet: soft low-frequency plasma,
    // thin bright filaments, a white-hot heart and a lit rim.
    float n  = fbm(p * 1.3 + vec3(0.0, 0.0, t * 0.22));
    float n2 = fbm(p * 2.6 - vec3(t * 0.18) + n * 1.2);
    // Brightness comes mostly from depth (z), so the sphere reads as one
    // glowing body; the noise only swirls colour through it.
    float glow = 0.35 + 0.65 * z;
    vec3 base = mix(uA, uB, smoothstep(0.30, 0.80, n * 0.6 + z * 0.5));
    float fres = pow(1.0 - z, 2.6);
    float veins = smoothstep(0.56, 0.63, n2) * (0.25 + uEnergy) * z;
    float heart = pow(z, 3.0);
    col = base * glow * (0.70 + 0.30 * n2);
    col += uB * veins * 0.55;
    col += mix(uA, uB, 0.5) * fres * 1.2;
    col += vec3(1.0, 0.92, 0.82) * heart * (0.35 + 0.45 * uEnergy) * (0.75 + 0.25 * n);
    col *= 0.85 + 0.40 * uEnergy;
    col += vec3(1.0) * uFlash * 0.35;
    col *= body;
    alpha = body;
  }

  // halo — faded to nothing before the canvas edge, so no square shows
  float outside = max(d - 1.0, 0.0);
  float frame = 1.0 - smoothstep(0.40, 0.5, max(abs(uv.x), abs(uv.y)));
  float halo = exp(-5.5 * outside) * (0.28 + 0.45 * uEnergy) * frame;
  vec3 haloCol = mix(uA, uB, 0.55) * halo;
  // emitted rings (speaking / wake)
  float ring = pow(max(sin(34.0 * (r - t * 0.11)), 0.0), 6.0) * exp(-7.0 * outside) * step(1.0, d) * frame;
  haloCol += uB * ring * uRings * 0.55;
  float edgeFlash = uFlash * exp(-14.0 * abs(d - 1.0));
  haloCol += vec3(1.0) * edgeFlash;

  float outer = 1.0 - body;
  col += haloCol * outer;
  alpha = max(alpha, clamp(max(max(haloCol.r, haloCol.g), haloCol.b), 0.0, 1.0) * outer);
  gl_FragColor = vec4(col, alpha);
}
`;

/* state -> [colorA, colorB, energy, swirl, rings]. One ember hue family;
 * moods differ in heat and motion, not in colour — except thinking
 * (brass) and error (deep red). */
const PALETTES = {
  idle:         [[0.55, 0.16, 0.03], [1.00, 0.55, 0.24], 0.14, 0.10, 0.0],
  wake:         [[1.00, 0.45, 0.15], [1.00, 0.93, 0.85], 0.85, 0.45, 1.0],
  listening:    [[1.00, 0.40, 0.12], [1.00, 0.88, 0.72], 0.66, 0.35, 0.3],
  transcribing: [[1.00, 0.40, 0.12], [1.00, 0.88, 0.72], 0.45, 0.80, 0.0],
  thinking:     [[0.60, 0.38, 0.08], [0.93, 0.78, 0.50], 0.40, 1.10, 0.0],
  speaking:     [[0.95, 0.33, 0.08], [1.00, 0.76, 0.52], 0.56, 0.30, 1.0],
  error:        [[0.50, 0.04, 0.04], [0.92, 0.30, 0.24], 0.50, 0.60, 0.0],
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
  const stillRef = useRef(null);
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

    const host = canvas.parentElement;
    const still = stillRef.current;
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2) * RENDER_SCALE;
      const { width, height } = host.getBoundingClientRect();
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
      const settle = !active && now - idleSince > SETTLE_MS;
      if (!settle) raf = requestAnimationFrame(frame);

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

      if (settle) {
        // Measured: a WebGL canvas left on screen keeps Electron's GPU
        // process busy (~25% of a core) even with zero draws. Once idle,
        // swap in a still of this exact frame (read in the same task, while
        // the drawing buffer is intact) and take the canvas off screen.
        running = false;
        try {
          still.src = canvas.toDataURL('image/png');
          still.style.display = 'block';
          canvas.style.display = 'none';
        } catch { /* keep the canvas; it just stays on screen */ }
      }
    };
    const start = () => {
      if (running) return;
      running = true;
      idleSince = 0;
      last = performance.now();
      if (canvas.style.display === 'none') {
        canvas.style.display = 'block';
        still.style.display = 'none';
      }
      raf = requestAnimationFrame(frame);
    };
    wakeLoopRef.current = start;
    const onVisible = () => { if (!document.hidden) start(); };
    document.addEventListener('visibilitychange', onVisible);
    // Watch the wrapper, not the canvas: hiding the canvas must not count
    // as a resize, or settling would restart the loop forever.
    const ro2 = new ResizeObserver(() => { resize(); start(); });
    ro2.observe(host);
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
      <img ref={stillRef} className="core-orb__canvas" alt="" style={{ display: 'none' }} />
      <div ref={fallbackRef} className="core-orb__fallback" data-state={state} />
    </div>
  );
}
