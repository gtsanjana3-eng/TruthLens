import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

/**
 * TruthLens hero: a 3D "forensic eye" rendered with WebGL.
 * – procedural iris shader (animated fibres, rotating data rings, dilating pupil)
 * – eyelids that blink, eye that follows the pointer (idle saccades otherwise)
 * – orbiting scanner rings carrying the five evidence modalities, particle field, scan pulse, bloom glow
 * Falls back to a static CSS eye without WebGL and respects prefers-reduced-motion.
 */

const INK = 0x06101d;
const MODALITIES = ["IMAGE", "VIDEO", "AUDIO", "TEXT", "METADATA"];

const eyeVertex = /* glsl */ `
  varying vec3 vLocal;
  varying vec3 vNormalW;
  varying vec3 vWorld;
  void main() {
    vLocal = normalize(position);
    vec4 w = modelMatrix * vec4(position, 1.0);
    vWorld = w.xyz;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * viewMatrix * w;
  }
`;

const eyeFragment = /* glsl */ `
  uniform float uTime;
  uniform float uPupil;
  uniform float uLid;        // 0 = open, 1 = closed
  uniform vec3 uCenter;      // eye centre in world space (lids stay level while the eyeball rotates)
  varying vec3 vLocal;
  varying vec3 vNormalW;
  varying vec3 vWorld;

  float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
  }
  float fbm(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; } return v; }

  void main() {
    vec3 n = normalize(vLocal);
    float ang = acos(clamp(n.z, -1.0, 1.0));     // angle from the eye's optical axis
    float r = ang / 0.62;                           // 0 at centre, 1 at the iris edge (limbus)
    float theta = atan(n.y, n.x);
    vec3 cyan = vec3(0.13, 0.83, 0.93);
    vec3 gold = vec3(0.88, 0.71, 0.32);
    vec3 col;

    if (r < 1.0) {
      // iris: radial fibres flowing outward + gold collarette near the pupil
      float fib = fbm(vec2(theta * 9.0, r * 2.5 - uTime * 0.25));
      float streaks = pow(fbm(vec2(theta * 34.0, r * 0.8)), 2.0);
      col = mix(vec3(0.01, 0.10, 0.17), cyan * 0.85, fib * 0.85 + streaks * 0.7);
      col = mix(col, gold * 0.75, smoothstep(0.5, 0.0, abs(r - uPupil - 0.1) * 5.0) * 0.6);
      // rotating segmented "data rings" inside the iris
      float ring1 = smoothstep(0.012, 0.0, abs(r - 0.78)) * step(0.5, fract(theta * 6.0 / 6.2832 + uTime * 0.15));
      float ring2 = smoothstep(0.01, 0.0, abs(r - 0.9)) * step(0.7, fract(theta * 24.0 / 6.2832 - uTime * 0.3));
      col += cyan * 1.1 * (ring1 + ring2);
      // limbal ring darkening
      col *= mix(1.0, 0.25, smoothstep(0.82, 1.0, r));
      // pupil with a faint inner glow
      float pupil = smoothstep(uPupil + 0.02, uPupil - 0.02, r);
      col = mix(col, vec3(0.0), pupil);
      col += cyan * 0.2 * smoothstep(0.03, 0.0, abs(r - uPupil)) * (1.0 - pupil);
    } else {
      // sclera: deep navy glass with faint circuit veins
      float veins = smoothstep(0.03, 0.0, abs(fbm(vec2(theta * 3.0, ang * 4.0)) - 0.5)) * smoothstep(1.0, 2.6, r);
      col = vec3(0.03, 0.07, 0.13) + cyan * veins * 0.35;
      col += cyan * 0.15 * smoothstep(1.25, 1.0, r);
    }

    // fresnel rim
    vec3 viewDir = normalize(cameraPosition - vWorld);
    float fres = pow(1.0 - max(dot(viewDir, normalize(vNormalW)), 0.0), 3.0);
    col += cyan * fres * 0.3;

    // eyelids (world-aligned, so they stay level when the eye looks around)
    vec3 d = vWorld - uCenter;
    float open = 1.0 - uLid;
    float lidCurve = open * (1.05 - 0.35 * d.x * d.x);
    float lidDist = abs(d.y) - lidCurve;
    if (lidDist > 0.0) {
      col = vec3(0.015, 0.035, 0.07) + cyan * 0.05 * fbm(d.xy * 6.0);
      col += cyan * 0.9 * smoothstep(0.04, 0.0, lidDist);   // glowing lid edge
    } else {
      col *= mix(1.0, 0.35, smoothstep(-0.18, 0.0, lidDist)); // soft shadow under the lids
    }
    gl_FragColor = vec4(col, 1.0);
  }
`;

const corneaFragment = /* glsl */ `
  uniform float uTime;
  varying vec3 vNormalW;
  varying vec3 vWorld;
  void main() {
    vec3 v = normalize(cameraPosition - vWorld);
    vec3 nrm = normalize(vNormalW);
    float fres = pow(1.0 - max(dot(v, nrm), 0.0), 2.5);
    vec3 L = normalize(vec3(-0.6, 0.7, 0.8));
    float spec = pow(max(dot(reflect(-L, nrm), v), 0.0), 60.0);
    gl_FragColor = vec4(vec3(0.6, 0.95, 1.0) * (fres * 0.12 + spec * 0.9), fres * 0.12 + spec * 0.8);
  }
`;

function webglAvailable(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

function dashedCircle(radius: number, color: number, dash: number, gap: number, opacity: number) {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= 256; i++) {
    const a = (i / 256) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a) * radius, Math.sin(a) * radius, 0));
  }
  const g = new THREE.BufferGeometry().setFromPoints(pts);
  const m = new THREE.LineDashedMaterial({ color, dashSize: dash, gapSize: gap, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false });
  const line = new THREE.Line(g, m);
  line.computeLineDistances();
  return line;
}

export default function EyeHero({ className = "", centered = false }: { className?: string; centered?: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const labels = useRef<(HTMLSpanElement | null)[]>([]);
  const [fallback, setFallback] = useState(false);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    if (!webglAvailable()) { setFallback(true); return; }
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    } catch {
      setFallback(true);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(INK, 1);
    renderer.domElement.setAttribute("aria-hidden", "true");
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(INK, 0.06);
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
    camera.position.set(0, 0, 8.2);

    // ---- eyeball, cornea, halo
    const eyeGroup = new THREE.Group();
    scene.add(eyeGroup);
    const eyeUniforms = { uTime: { value: 0 }, uPupil: { value: 0.32 }, uLid: { value: 0 }, uCenter: { value: new THREE.Vector3() } };
    const eyeMat = new THREE.ShaderMaterial({ uniforms: eyeUniforms, vertexShader: eyeVertex, fragmentShader: eyeFragment });
    const eye = new THREE.Mesh(new THREE.SphereGeometry(1.25, 128, 128), eyeMat);
    eyeGroup.add(eye);
    const corneaMat = new THREE.ShaderMaterial({ uniforms: { uTime: eyeUniforms.uTime }, vertexShader: eyeVertex, fragmentShader: corneaFragment, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    const cornea = new THREE.Mesh(new THREE.SphereGeometry(1.31, 96, 96), corneaMat);
    eyeGroup.add(cornea);

    const haloTex = (() => {
      const c = document.createElement("canvas");
      c.width = c.height = 256;
      const g = c.getContext("2d")!;
      const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
      grad.addColorStop(0, "rgba(34,211,238,0.22)");
      grad.addColorStop(0.35, "rgba(34,211,238,0.06)");
      grad.addColorStop(1, "rgba(34,211,238,0)");
      g.fillStyle = grad;
      g.fillRect(0, 0, 256, 256);
      return new THREE.CanvasTexture(c);
    })();
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
    halo.scale.set(6.5, 6.5, 1);
    halo.position.z = -1.2;
    scene.add(halo);

    // ---- scanner rings (each its own tilt and spin)
    const rings = new THREE.Group();
    scene.add(rings);
    const ringSpecs = [
      { r: 1.75, c: 0x22d3ee, dash: 0.18, gap: 0.08, o: 0.9, tilt: [1.25, 0.1], spin: 0.25 },
      { r: 2.05, c: 0xe0b452, dash: 0.05, gap: 0.12, o: 0.8, tilt: [0.35, 0.9], spin: -0.18 },
      { r: 2.4, c: 0x22d3ee, dash: 0.6, gap: 0.25, o: 0.55, tilt: [1.0, -0.6], spin: 0.12 },
      { r: 2.85, c: 0x60a5fa, dash: 0.03, gap: 0.09, o: 0.45, tilt: [0.15, 0.25], spin: -0.08 },
    ];
    const ringObjs = ringSpecs.map((s) => {
      const holder = new THREE.Group();
      holder.rotation.set(s.tilt[0], s.tilt[1], 0);
      const line = dashedCircle(s.r, s.c, s.dash, s.gap, s.o);
      holder.add(line);
      rings.add(holder);
      return { holder, line, spin: s.spin };
    });

    // ---- modality nodes orbiting on the outer tilted ring
    const orbit = new THREE.Group();
    orbit.rotation.set(1.18, 0.22, 0);
    scene.add(orbit);
    const nodeGeo = new THREE.SphereGeometry(0.075, 24, 24);
    const nodes = MODALITIES.map((_, i) => {
      const m = new THREE.Mesh(nodeGeo, new THREE.MeshBasicMaterial({ color: i % 2 ? 0xe0b452 : 0x22d3ee }));
      const a = (i / MODALITIES.length) * Math.PI * 2;
      m.position.set(Math.cos(a) * 3.2, Math.sin(a) * 3.2, 0);
      orbit.add(m);
      return m;
    });
    orbit.add(dashedCircle(3.2, 0x22d3ee, 0.02, 0.06, 0.35));

    // ---- particle field
    const COUNT = 2600;
    const pos = new Float32Array(COUNT * 3);
    const colr = new Float32Array(COUNT * 3);
    const cA = new THREE.Color(0x22d3ee), cB = new THREE.Color(0xe0b452), cC = new THREE.Color(0x60a5fa);
    for (let i = 0; i < COUNT; i++) {
      const radius = 2.2 + Math.pow(Math.random(), 0.6) * 5.5;
      const a = Math.random() * Math.PI * 2;
      const y = (Math.random() - 0.5) * (0.6 + radius * 0.35);
      pos.set([Math.cos(a) * radius, y, Math.sin(a) * radius], i * 3);
      const c = Math.random() < 0.12 ? cB : Math.random() < 0.3 ? cC : cA;
      colr.set([c.r, c.g, c.b], i * 3);
    }
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    pGeo.setAttribute("color", new THREE.BufferAttribute(colr, 3));
    const particles = new THREE.Points(pGeo, new THREE.PointsMaterial({ size: 0.03, vertexColors: true, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false }));
    particles.rotation.x = 0.35;
    scene.add(particles);

    // ---- scan pulse (expanding wire shell) + horizontal scan line
    const pulse = new THREE.Mesh(new THREE.IcosahedronGeometry(1.4, 3), new THREE.MeshBasicMaterial({ color: 0x22d3ee, wireframe: true, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
    scene.add(pulse);
    const scan = new THREE.Mesh(new THREE.PlaneGeometry(4.2, 0.012), new THREE.MeshBasicMaterial({ color: 0x67e8f9, transparent: true, opacity: 0.0, blending: THREE.AdditiveBlending, depthWrite: false }));
    scan.position.z = 1.4;
    scene.add(scan);

    // ---- post-processing bloom
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.55, 0.4, 0.4);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());

    // ---- sizing: eye sits right of centre on wide screens, centred on narrow ones
    let wide = true;
    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      composer.setSize(w, h);
      bloom.setSize(w, h);
      camera.aspect = w / h;
      wide = !centered && window.innerWidth >= 1024 && w / h > 1.25; // side-by-side layout (matches the lg breakpoint)
      // centred stage: fill the screen with the eye, pulling back on portrait phones so the orbit fits
      camera.position.z = wide ? 7.6 : centered ? (w / h >= 1 ? 7.4 : 7.4 + (1 - w / h) * 9) : w / h > 1.4 ? 9.2 : 10.8;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);

    // ---- pointer tracking + idle saccades
    const target = new THREE.Vector2(), look = new THREE.Vector2();
    let lastMove = -10, nextSaccade = 0;
    const onMove = (e: PointerEvent) => {
      const b = el.getBoundingClientRect();
      const cx = b.left + (wide ? b.width * 0.72 : b.width / 2), cy = b.top + b.height * 0.5;
      target.set(THREE.MathUtils.clamp((e.clientX - cx) / (b.width * 0.45), -1, 1), THREE.MathUtils.clamp((e.clientY - cy) / (b.height * 0.55), -1, 1));
      lastMove = clock.elapsedTime;
    };
    window.addEventListener("pointermove", onMove, { passive: true });

    // blink schedule
    let nextBlink = 2.5, blinkStart = -1;
    let visible = true;
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
    io.observe(el);

    const clock = new THREE.Clock();
    const tmp = new THREE.Vector3();
    let raf = 0;
    const frame = () => {
      raf = requestAnimationFrame(frame);
      if (!visible || document.hidden) return;
      const t = clock.getElapsedTime();
      eyeUniforms.uTime.value = t;

      const eyeX = wide ? 2.05 : 0, eyeY = wide ? 0.1 : centered ? 0.1 : 0.2;
      eyeGroup.position.set(eyeX, eyeY + Math.sin(t * 0.8) * 0.06, 0);
      eyeUniforms.uCenter.value.copy(eyeGroup.position);
      halo.position.x = eyeX; halo.position.y = eyeY;
      rings.position.copy(eyeGroup.position); orbit.position.copy(eyeGroup.position);
      pulse.position.copy(eyeGroup.position); particles.position.set(eyeX, eyeY, 0);

      // gaze: pointer, or a random saccade when the pointer is idle
      if (t - lastMove > 2.5 && t > nextSaccade) {
        target.set((Math.random() - 0.5) * 1.4, (Math.random() - 0.5) * 0.9);
        nextSaccade = t + 1.2 + Math.random() * 2.2;
      }
      look.lerp(target, 0.08);
      eyeGroup.rotation.y = look.x * 0.55;
      eyeGroup.rotation.x = look.y * 0.4;

      // pupil breathes, and narrows a little when the gaze moves fast
      const speed = target.distanceTo(look);
      eyeUniforms.uPupil.value = THREE.MathUtils.lerp(eyeUniforms.uPupil.value, 0.27 + Math.sin(t * 1.3) * 0.04 - speed * 0.06, 0.1);

      // blink
      if (t > nextBlink && blinkStart < 0) blinkStart = t;
      if (blinkStart >= 0) {
        const k = (t - blinkStart) / 0.28;
        eyeUniforms.uLid.value = k < 0.5 ? k * 2 : Math.max(0, 2 - k * 2);
        if (k >= 1) { blinkStart = -1; eyeUniforms.uLid.value = 0; nextBlink = t + 3 + Math.random() * 4; }
      }

      ringObjs.forEach((o, i) => { o.line.rotation.z = t * o.spin; o.holder.rotation.y += 0.0008 * (i % 2 ? -1 : 1); });
      orbit.rotation.z = t * 0.12;
      particles.rotation.y = t * 0.035;

      // scan pulse every 4 s
      const pp = (t % 4) / 4;
      pulse.scale.setScalar(1 + pp * 2.2);
      (pulse.material as THREE.MeshBasicMaterial).opacity = (1 - pp) * 0.07;
      const sp = (t % 3.2) / 3.2;
      scan.position.set(eyeX, eyeY + 1.4 - sp * 2.8, 1.4);
      (scan.material as THREE.MeshBasicMaterial).opacity = Math.sin(sp * Math.PI) * 0.5;

      // project modality labels onto their nodes
      const w = el.clientWidth, h = el.clientHeight;
      nodes.forEach((n, i) => {
        const lab = labels.current[i];
        if (!lab) return;
        n.getWorldPosition(tmp);
        const depth = tmp.z;
        tmp.project(camera);
        lab.style.transform = `translate(-50%, -150%) translate(${((tmp.x + 1) / 2) * w}px, ${((1 - tmp.y) / 2) * h}px)`;
        lab.style.opacity = String(THREE.MathUtils.clamp(0.35 + depth * 0.25, 0.15, 1));
      });

      composer.render();
      if (reduced) cancelAnimationFrame(raf); // one still frame only
    };
    frame();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      ro.disconnect();
      io.disconnect();
      scene.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose?.();
        const mat = m.material as THREE.Material | THREE.Material[] | undefined;
        (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => x.dispose());
      });
      haloTex.dispose();
      composer.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [centered]);

  return (
    <div ref={host} className={`eye-hero ${className}`} aria-hidden="true">
      {fallback ? (
        <div className="eye-fallback"><div className="eye-fallback-iris"><div className="eye-fallback-pupil" /></div></div>
      ) : (
        MODALITIES.map((m, i) => (
          <span key={m} ref={(n) => { labels.current[i] = n; }} className="eye-label mono">{m}</span>
        ))
      )}
    </div>
  );
}
