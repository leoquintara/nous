"use client";

/**
 * Canvas del cerebro 3D — port a TypeScript del ThreeGraphCanvas del
 * plugin DSH Memory, adaptado al modelo de datos de Nous.
 *
 * Renderiza eventos como neuronas instanciadas dentro de una membrana
 * cerebral procedural, con física en vivo, pulsos sinápticos, polvo
 * neuronal ambiental, cascadas de selección, etiquetas anatómicas y
 * "pensamientos" que recorren arcos desde el tálamo a las memorias.
 */

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

import { construirMembrana } from "./geometria";
import type { Membrana } from "./geometria";
import { pasoFisica, TEMBLOR } from "./fisica";
import {
  colorNodo,
  PREFS_DEFAULT,
} from "./tipos";
import type {
  Arista,
  DatosCerebro,
  EventoPensamiento,
  ModoColor,
  PrefsCerebro,
} from "./tipos";

/** Referencia imperativa para inyectar pensamientos desde afuera. */
export interface CerebroHandle {
  /** Dispara un pensamiento: semilla en el tallo → arcos a los nodos. */
  pensar: (ev: EventoPensamiento) => void;
}

interface Props {
  datos: DatosCerebro;
  seleccion: { index: number } | null;
  alSeleccionar: (sel: { index: number } | null) => void;
  prefs?: Partial<PrefsCerebro>;
  /** Modo de color activo del grafo (dimensión/estado/fuente/reglas). */
  modoColor?: ModoColor;
  /** Fuentes ocultas: sus nodos no se dibujan ni se pueden elegir. */
  ocultas?: Set<string>;
  /** Avisa cuando el usuario fija o suelta un nodo por arrastre. */
  alCambiarPin?: (
    eventoId: string,
    pos: [number, number, number] | null,
  ) => void;
  onPensarRef?: React.MutableRefObject<CerebroHandle | null>;
}

const COLORES_KIND: Record<string, number> = {
  inject: 0xa8c7ff,
  search: 0x7dd3fc,
  session: 0x86efac,
  store: 0xf59e0b,
  recall: 0xc084fc,
};

/** Regiones anatómicas proyectadas como etiquetas flotantes. */
function regiones(mem: Membrana): [string, number, number, number][] {
  return [
    ["FRONTAL", 0, mem.y * 0.58, -mem.z * 0.88],
    ["PARIETAL", 0, mem.y * 0.78, mem.z * 0.08],
    ["OCCIPITAL", 0, mem.y * 0.22, mem.z * 1.0],
    ["TEMPORAL", -mem.x * 0.88, -mem.y * 0.3, -mem.z * 0.02],
    ["TEMPORAL", mem.x * 0.88, -mem.y * 0.3, -mem.z * 0.02],
    ["CEREBELO", 0, -mem.y * 0.8, mem.z * 0.6],
    ["TRONCO", 0, -mem.y * 1.0, mem.z * 0.3],
  ];
}

/** Renderiza el grafo cerebral con WebGL y animación continua. */
export function CerebroCanvas({
  datos,
  seleccion,
  alSeleccionar,
  prefs,
  modoColor = "dimension",
  ocultas,
  alCambiarPin,
  onPensarRef,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const livePrefs = useRef<PrefsCerebro>({ ...PREFS_DEFAULT, ...prefs });
  const selRef = useRef(seleccion);
  const modoRef = useRef(modoColor);
  const ocultasRef = useRef(ocultas);
  const pinRef = useRef(alCambiarPin);
  /**
   * Pensamientos cuyos ids aún no existen en el grafo (un store llega
   * antes que el recargado). Viven fuera del efecto: cada nueva
   * `datos` recrea la escena y drena los que ya resuelven.
   */
  const pendientesRef = useRef<{ ev: EventoPensamiento; hasta: number }[]>([]);
  useEffect(() => {
    livePrefs.current = { ...PREFS_DEFAULT, ...prefs };
    selRef.current = seleccion;
    modoRef.current = modoColor;
    ocultasRef.current = ocultas;
    pinRef.current = alCambiarPin;
  });
  const [glError, setGlError] = useState(false);

  useEffect(() => {
    if (datos.status !== "ready" || datos.nodes.length === 0) return;
    const wrap = wrapRef.current;
    if (!wrap) return;
    let cancelado = false;

    const { pts, edges, nodes } = datos;
    const N = nodes.length;
    const pulsos = edges
      .filter((e) => e.kind === "link")
      .slice(0, 300)
      .map((e) => ({ a: e.a, b: e.b, t: Math.random(), s: 0.5 + Math.random() * 0.9 }));

    let matchSet: Set<number> | null = null;
    let matchQuery: string | null = null;
    /** Indica si el nodo i coincide con la consulta de resaltado. */
    const coincide = (i: number): boolean | null => {
      const q = livePrefs.current.query.trim().toLowerCase();
      if (q !== matchQuery) {
        matchQuery = q;
        matchSet = q
          ? new Set(
              nodes
                .map((n, j) => (n.etiqueta.toLowerCase().includes(q) ? j : -1))
                .filter((j) => j >= 0),
            )
          : null;
      }
      return matchSet === null ? null : matchSet.has(i);
    };

    const vel = pts.map(() => ({ x: 0, y: 0, z: 0 }));
    const mem: Membrana = { x: 430, y: 430, z: 430 };

    const pct95 = (arr: number[]) => {
      const s = [...arr].sort((a, b) => a - b);
      return s[Math.floor(s.length * 0.95)] || 1;
    };
    mem.x = Math.max(200, pct95(pts.map((p) => Math.abs(p.x))) * 1.18 + 30);
    mem.y = Math.max(170, pct95(pts.map((p) => Math.abs(p.y))) * 1.18 + 30);
    mem.z = Math.max(200, pct95(pts.map((p) => Math.abs(p.z))) * 1.18 + 30);

    let limpiar = () => {};
    /**
     * Despachador estable: mientras la escena async se reconstruye,
     * los pensamientos se encolan en vez de perderse; cuando la
     * escena está viva, `despacharPensar` apunta a la implementación
     * real que resuelve ids contra `datos.idToIdx`.
     */
    let despacharPensar: ((ev: EventoPensamiento) => void) | null = null;
    if (onPensarRef) {
      onPensarRef.current = {
        pensar: (ev: EventoPensamiento) => {
          if (despacharPensar) {
            despacharPensar(ev);
          } else {
            pendientesRef.current.push({
              ev,
              hasta: performance.now() + 8000,
            });
          }
        },
      };
    }
    (async () => {
      const scene = new THREE.Scene();
      // La bóveda craneal siempre es oscura: todos los efectos
      // (traza sináptica, halos, bloom) suman luz con
      // AdditiveBlending — sobre fondo claro serían invisibles.
      scene.background = new THREE.Color(0x0b0b1e);
      const camera = new THREE.PerspectiveCamera(
        55,
        wrap.clientWidth / Math.max(1, wrap.clientHeight),
        1,
        5000,
      );
      camera.position.set(0, 120, 620);
      const renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
      });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(wrap.clientWidth, Math.max(1, wrap.clientHeight));
      renderer.domElement.style.cssText =
        "display:block;cursor:grab;touch-action:none";
      wrap.appendChild(renderer.domElement);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.4;
      controls.minDistance = 80;
      controls.maxDistance = 2200;

      /** Gesto de trackpad: pan con dos dedos orbita la escena. */
      const onWheel = (ev: WheelEvent) => {
        if (ev.ctrlKey) return;
        const esPad =
          ev.deltaMode === 0 &&
          (Math.abs(ev.deltaX) > 0.5 ||
            (ev.deltaY % 1 !== 0 && Math.abs(ev.deltaY) < 60));
        if (!esPad) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        const off = camera.position.clone().sub(controls.target);
        const sph = new THREE.Spherical().setFromVector3(off);
        sph.theta -= ev.deltaX * 0.0032;
        sph.phi = Math.max(
          0.06,
          Math.min(Math.PI - 0.06, sph.phi - ev.deltaY * 0.0032),
        );
        camera.position
          .copy(controls.target)
          .add(new THREE.Vector3().setFromSpherical(sph));
        camera.lookAt(controls.target);
      };

      const composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(scene, camera));
      composer.addPass(
        new UnrealBloomPass(
          new THREE.Vector2(wrap.clientWidth, Math.max(1, wrap.clientHeight)),
          0.55,
          0.35,
          0.72,
        ),
      );
      composer.addPass(new OutputPass());

      const geoNodo = new THREE.SphereGeometry(1, 14, 10);
      const matNodo = new THREE.MeshBasicMaterial({ color: 0xffffff });
      const malla = new THREE.InstancedMesh(geoNodo, matNodo, N);
      malla.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      malla.frustumCulled = false;
      scene.add(malla);
      const dummy = new THREE.Object3D();
      const tmpC = new THREE.Color();
      const blanco = new THREE.Color(0xffffff);
      for (let i = 0; i < N; i++) malla.setColorAt(i, tmpC.set(0xffffff));

      const matHalo = new THREE.MeshBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.2,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });
      const halos = new THREE.InstancedMesh(
        new THREE.SphereGeometry(1, 12, 8),
        matHalo,
        Math.min(N, 256),
      );
      halos.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      halos.frustumCulled = false;
      halos.count = 0;
      scene.add(halos);
      for (let i = 0; i < 256; i++) halos.setColorAt(i, tmpC.set(0xffffff));

      // Aristas por tipo, cada una con su estilo.
      const grupos: Record<"link" | "tag" | "core", Arista[]> = {
        link: [],
        tag: [],
        core: [],
      };
      for (const e of edges)
        grupos[e.kind === "link" ? "link" : e.kind === "core" ? "core" : "tag"].push(e);
      type GrupoLineas = {
        g: THREE.BufferGeometry;
        pos: Float32Array;
        list: Arista[];
        m: THREE.LineBasicMaterial;
      };
      const mkLineas = (
        list: Arista[],
        color: number,
        opacity: number,
        blend?: THREE.Blending,
      ): GrupoLineas => {
        const g = new THREE.BufferGeometry();
        const pos = new Float32Array(list.length * 6);
        g.setAttribute(
          "position",
          new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage),
        );
        const m = new THREE.LineBasicMaterial({
          color,
          transparent: true,
          opacity,
          blending: blend ?? THREE.NormalBlending,
          depthWrite: false,
        });
        const l = new THREE.LineSegments(g, m);
        l.frustumCulled = false;
        scene.add(l);
        return { g, pos, list, m };
      };
      const lineasLink = mkLineas(grupos.link, 0x94c5ff, 0.45, THREE.AdditiveBlending);
      const lineasTag = mkLineas(grupos.tag, 0x94a3b8, 0.13);
      const lineasCore = mkLineas(grupos.core, 0xcbd5e1, 0.09);

      // Spotlight de aristas del nodo seleccionado.
      const hlGeo = new THREE.BufferGeometry();
      const hlPos = new Float32Array(4096 * 6);
      hlGeo.setAttribute(
        "position",
        new THREE.BufferAttribute(hlPos, 3).setUsage(THREE.DynamicDrawUsage),
      );
      const hlLineas = new THREE.LineSegments(
        hlGeo,
        new THREE.LineBasicMaterial({
          color: 0xe8f0ff,
          transparent: true,
          opacity: 0.7,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        }),
      );
      hlLineas.frustumCulled = false;
      scene.add(hlLineas);

      // Pulsos sinápticos viajando por las aristas.
      const NP = pulsos.length;
      const pGeo = new THREE.BufferGeometry();
      const pPos = new Float32Array(NP * 3);
      const pCol = new Float32Array(NP * 3);
      pGeo.setAttribute("position", new THREE.BufferAttribute(pPos, 3).setUsage(THREE.DynamicDrawUsage));
      pGeo.setAttribute("color", new THREE.BufferAttribute(pCol, 3).setUsage(THREE.DynamicDrawUsage));
      const pPuntos = new THREE.Points(
        pGeo,
        new THREE.PointsMaterial({
          size: 6,
          vertexColors: true,
          transparent: true,
          opacity: 0.9,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          sizeAttenuation: true,
        }),
      );
      pPuntos.frustumCulled = false;
      scene.add(pPuntos);

      // Membrana cerebral procedural + estructuras profundas.
      const { grupo: memGrupo, tractos, materiales } = construirMembrana(mem);
      scene.add(memGrupo);

      // Chispas recorriendo los tractos profundos.
      const NT = 22;
      const tractoPulsos = Array.from({ length: NT }, () => ({
        c: tractos[(Math.random() * tractos.length) | 0] ?? tractos[0],
        t: Math.random(),
        s: 0.5 + Math.random(),
      }));
      const tpGeo = new THREE.BufferGeometry();
      const tpPos = new Float32Array(NT * 3);
      const tpCol = new Float32Array(NT * 3);
      tpGeo.setAttribute("position", new THREE.BufferAttribute(tpPos, 3).setUsage(THREE.DynamicDrawUsage));
      tpGeo.setAttribute("color", new THREE.BufferAttribute(tpCol, 3).setUsage(THREE.DynamicDrawUsage));
      const tpPuntos = new THREE.Points(
        tpGeo,
        new THREE.PointsMaterial({
          size: 7,
          vertexColors: true,
          transparent: true,
          opacity: 0.9,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          sizeAttenuation: true,
        }),
      );
      tpPuntos.frustumCulled = false;
      memGrupo.add(tpPuntos);

      // Polvo neuronal ambiental dentro de la membrana.
      const NA = 520;
      const aPos = new Float32Array(NA * 3);
      const aVel = new Float32Array(NA * 3);
      for (let i = 0; i < NA; i++) {
        let x = 0;
        let y = 0;
        let z = 0;
        do {
          x = Math.random() * 2 - 1;
          y = Math.random() * 2 - 1;
          z = Math.random() * 2 - 1;
        } while (x * x + y * y + z * z > 1);
        aPos[i * 3] = x * mem.x * 0.9;
        aPos[i * 3 + 1] = y * mem.y * 0.9;
        aPos[i * 3 + 2] = z * mem.z * 0.9;
        aVel[i * 3] = Math.random() - 0.5;
        aVel[i * 3 + 1] = Math.random() - 0.5;
        aVel[i * 3 + 2] = Math.random() - 0.5;
      }
      const aGeo = new THREE.BufferGeometry();
      aGeo.setAttribute("position", new THREE.BufferAttribute(aPos, 3).setUsage(THREE.DynamicDrawUsage));
      const ambMat = new THREE.PointsMaterial({
        color: 0x7d95c8,
        size: 1.8,
        transparent: true,
        opacity: 0.28,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        sizeAttenuation: true,
      });
      const ambPuntos = new THREE.Points(aGeo, ambMat);
      ambPuntos.frustumCulled = false;
      scene.add(ambPuntos);

      // Contaminación cerebral: las huellas muertas/decaidas destilan
      // motas oscuras que emigran a la membrana — basura mnémica que
      // mancha el tejido. Pref `contaminacion` lo conmuta.
      const focos = nodes
        .map((n, i) => i)
        .filter((i) => nodes[i].estado === "dead" || nodes[i].estado === "decayed");
      const NC = Math.min(focos.length * 5, 400);
      const mPos = new Float32Array(NC * 3);
      const mDesde = new Float32Array(NC * 3);
      const mHacia = new Float32Array(NC * 3);
      const mT = new Float32Array(NC);
      const mVel = new Float32Array(NC);
      for (let m = 0; m < NC; m++) {
        const f = focos[m % focos.length];
        const p = pts[f];
        mDesde[m * 3] = p.x;
        mDesde[m * 3 + 1] = p.y;
        mDesde[m * 3 + 2] = p.z;
        // Punto de llegada: la superficie delipsoidal en la dirección
        // del nodo — la mota se acumula en la membrana que lo encierra.
        const len = Math.max(1e-3, Math.hypot(p.x / mem.x, p.y / mem.y, p.z / mem.z));
        mHacia[m * 3] = (p.x / len) * 0.97;
        mHacia[m * 3 + 1] = (p.y / len) * 0.97;
        mHacia[m * 3 + 2] = (p.z / len) * 0.97;
        mT[m] = Math.random();
        mVel[m] = 0.00016 + Math.random() * 0.0003;
      }
      const mGeo = new THREE.BufferGeometry();
      mGeo.setAttribute("position", new THREE.BufferAttribute(mPos, 3).setUsage(THREE.DynamicDrawUsage));
      const motas = new THREE.Points(
        mGeo,
        new THREE.PointsMaterial({
          color: 0x8e3b52,
          size: 3.4,
          transparent: true,
          opacity: 0.6,
          depthWrite: false,
          sizeAttenuation: true,
        }),
      );
      motas.frustumCulled = false;
      motas.visible = focos.length > 0;
      scene.add(motas);

      // Cascadas de impulso al seleccionar un nodo.
      const NCASC = 64;
      const cascadas: { a: number; b: number; t0: number; dur: number }[] = [];
      const cPos = new Float32Array(NCASC * 3);
      const cCol = new Float32Array(NCASC * 3);
      const cGeo = new THREE.BufferGeometry();
      cGeo.setAttribute("position", new THREE.BufferAttribute(cPos, 3).setUsage(THREE.DynamicDrawUsage));
      cGeo.setAttribute("color", new THREE.BufferAttribute(cCol, 3).setUsage(THREE.DynamicDrawUsage));
      const cPuntos = new THREE.Points(
        cGeo,
        new THREE.PointsMaterial({
          size: 9,
          vertexColors: true,
          transparent: true,
          opacity: 0.95,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          sizeAttenuation: true,
        }),
      );
      cPuntos.frustumCulled = false;
      cPuntos.visible = false;
      scene.add(cPuntos);
      /** Dispara cascadas por todas las aristas del nodo seleccionado. */
      const dispararCascada = (i: number) => {
        let lanzadas = 0;
        for (const e of edges) {
          if (e.a !== i && e.b !== i) continue;
          if (cascadas.length >= NCASC || lanzadas >= 26) break;
          cascadas.push({
            a: i,
            b: e.a === i ? e.b : e.a,
            t0: performance.now(),
            dur: 420 + Math.random() * 380,
          });
          lanzadas++;
        }
      };

      // Anclas anatómicas + el punto del tálamo y la puerta del tallo.
      const corteza = new THREE.Vector3(0, -mem.y * 0.02, mem.z * 0.04);
      const puertaTallo = new THREE.Vector3(0, -mem.y * 0.78, mem.z * 0.24);
      const puntoCorteza = new THREE.Mesh(
        new THREE.SphereGeometry(5, 16, 12),
        new THREE.MeshBasicMaterial({
          color: 0xcfe0ff,
          transparent: true,
          opacity: 0.85,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        }),
      );
      puntoCorteza.position.copy(corteza);
      scene.add(puntoCorteza);

      // Capa DOM de etiquetas anatómicas y de nodos.
      const capaEtiquetas = document.createElement("div");
      capaEtiquetas.style.cssText =
        "position:absolute;inset:0;overflow:hidden;pointer-events:none";
      wrap.appendChild(capaEtiquetas);
      const elsRegion = regiones(mem).map(([nombre]) => {
        const d = document.createElement("div");
        d.style.cssText =
          "position:absolute;transform:translate(-50%,-50%);color:#8ea3cf;font:600 9px system-ui,sans-serif;letter-spacing:2.5px;text-transform:uppercase;opacity:.42;text-shadow:0 0 5px rgba(8,12,20,.9);white-space:nowrap;pointer-events:none";
        d.textContent = nombre;
        capaEtiquetas.appendChild(d);
        return d;
      });
      const poolEtiquetas: HTMLDivElement[] = [];
      /** Obtiene (o crea) la etiqueta DOM del nodo i. */
      const etiqueta = (i: number) => {
        if (!poolEtiquetas[i]) {
          const d = document.createElement("div");
          d.style.cssText =
            "position:absolute;transform:translate(-50%,-100%);color:#e2e8f0;font:10px system-ui,sans-serif;text-shadow:0 0 4px rgba(10,12,18,.9),0 0 8px rgba(10,12,18,.9);white-space:nowrap";
          capaEtiquetas.appendChild(d);
          poolEtiquetas[i] = d;
        }
        return poolEtiquetas[i];
      };

      // Pensamientos: semilla que asciende del tallo y dispara arcos.
      const flashes = new Map<number, { s: number; c: THREE.Color }>();
      interface LineaPensamiento {
        idx: number;
        line: THREE.Line;
        tube: THREE.Mesh;
        curve: THREE.QuadraticBezierCurve3;
        spark: THREE.Mesh;
        trail: THREE.Mesh;
        glow: THREE.Sprite;
        delay: number;
        col: THREE.Color;
        /** true tras la llegada — la chispa ya recogió el color del nodo. */
        fired: boolean;
      }
      interface Pensamiento {
        group: THREE.Group;
        seed: THREE.Mesh;
        lines: LineaPensamiento[];
        lab: HTMLDivElement;
        t0: number;
      }
      const pensamientos: Pensamiento[] = [];
      let texGlow: THREE.CanvasTexture | null = null;
      /** Textura radial compartida de los sprites de glow. */
      const texturaGlow = () => {
        if (texGlow) return texGlow;
        const c = document.createElement("canvas");
        c.width = c.height = 64;
        const g = c.getContext("2d")!;
        const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
        gr.addColorStop(0, "rgba(255,255,255,1)");
        gr.addColorStop(0.35, "rgba(255,255,255,.45)");
        gr.addColorStop(1, "rgba(255,255,255,0)");
        g.fillStyle = gr;
        g.fillRect(0, 0, 64, 64);
        texGlow = new THREE.CanvasTexture(c);
        return texGlow;
      };
      /** Elimina un pensamiento y libera sus recursos WebGL. */
      const matarPensamiento = (th: Pensamiento) => {
        scene.remove(th.group);
        for (const l of th.lines) {
          l.line.geometry.dispose();
          (l.line.material as THREE.Material).dispose();
          l.tube.geometry.dispose();
          (l.tube.material as THREE.Material).dispose();
          l.spark.geometry.dispose();
          (l.spark.material as THREE.Material).dispose();
          l.trail.geometry.dispose();
          (l.trail.material as THREE.Material).dispose();
          l.glow.material.dispose();
        }
        th.seed.geometry.dispose();
        (th.seed.material as THREE.Material).dispose();
        th.lab.parentNode?.removeChild(th.lab);
      };
      /** Spawnea la geometría de un pensamiento ya resuelto. */
      const spawnPensamiento = (ev: EventoPensamiento) => {
        if (pensamientos.length >= 4) matarPensamiento(pensamientos.shift()!);
        const group = new THREE.Group();
        const col = COLORES_KIND[ev.kind] ?? 0xa8c7ff;
        const objetivos = ev.ids
          .map((id) => datos.idToIdx.get(id))
          .filter((i): i is number => i != null)
          .slice(0, 12);
        const lines = objetivos.map((idx) => {
          const to = new THREE.Vector3(pts[idx].x, pts[idx].y, pts[idx].z);
          const mid = corteza.clone().lerp(to, 0.45);
          mid.y += 50 + Math.random() * 60;
          mid.x += (Math.random() - 0.5) * 90;
          const curve = new THREE.QuadraticBezierCurve3(
            corteza.clone(),
            mid,
            to.clone(),
          );
          const tube = new THREE.Mesh(
            new THREE.TubeGeometry(curve, 32, 1.3, 6, false),
            new THREE.MeshBasicMaterial({
              color: col,
              transparent: true,
              opacity: 0,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          );
          group.add(tube);
          const line = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(curve.getPoints(24)),
            new THREE.LineBasicMaterial({
              color: 0xffffff,
              transparent: true,
              opacity: 0,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          );
          group.add(line);
          const spark = new THREE.Mesh(
            new THREE.SphereGeometry(4.4, 10, 8),
            new THREE.MeshBasicMaterial({
              color: 0xffffff,
              transparent: true,
              opacity: 0,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          );
          group.add(spark);
          const trail = new THREE.Mesh(
            new THREE.SphereGeometry(2.4, 8, 6),
            new THREE.MeshBasicMaterial({
              color: col,
              transparent: true,
              opacity: 0,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          );
          group.add(trail);
          const glow = new THREE.Sprite(
            new THREE.SpriteMaterial({
              map: texturaGlow(),
              color: col,
              transparent: true,
              opacity: 0,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          );
          glow.scale.setScalar(26);
          group.add(glow);
          return {
            idx,
            line,
            tube,
            curve,
            spark,
            trail,
            glow,
            delay: Math.random() * 400,
            col: new THREE.Color(
              colorNodo(nodes[idx], modoRef.current, datos, livePrefs.current),
            ),
            fired: false,
          };
        });
        const seed = new THREE.Mesh(
          new THREE.SphereGeometry(7, 16, 12),
          new THREE.MeshBasicMaterial({
            color: objetivos.length ? 0xffffff : 0x9a6b3f,
            transparent: true,
            opacity: 0,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
          }),
        );
        seed.position.copy(puertaTallo);
        group.add(seed);
        const lab = document.createElement("div");
        lab.style.cssText =
          "position:absolute;transform:translate(-50%,-160%);color:#dbeafe;font:11px system-ui,sans-serif;text-shadow:0 0 6px rgba(15,25,50,.95),0 0 10px rgba(15,25,50,.9);white-space:nowrap;pointer-events:none;opacity:0;transition:opacity .4s";
        lab.textContent = `◌ ${ev.q.slice(0, 46)}`;
        capaEtiquetas.appendChild(lab);
        scene.add(group);
        pensamientos.push({ group, seed, lines, lab, t0: performance.now() });
      };
      /**
       * Drena los pensamientos pendientes: los ids que ya existen se
       * animan ahora; los que no esperan a la próxima recarga.
       */
      const drenarPendientes = () => {
        const tNow = performance.now();
        const resto: typeof pendientesRef.current = [];
        for (const p of pendientesRef.current) {
          const ok = p.ev.ids.filter((id) => datos.idToIdx.has(id));
          if (ok.length) spawnPensamiento({ ...p.ev, ids: ok });
          const falta = p.ev.ids.filter((id) => !datos.idToIdx.has(id));
          if (falta.length && tNow < p.hasta) {
            resto.push({ ev: { ...p.ev, ids: falta }, hasta: p.hasta });
          }
        }
        pendientesRef.current = resto;
      };
      /** Crea un pensamiento: lo que resuelve se anima, el resto espera. */
      const pensar = (ev: EventoPensamiento) => {
        const ok = ev.ids.filter((id) => datos.idToIdx.has(id));
        const falta = ev.ids.filter((id) => !datos.idToIdx.has(id));
        if (ok.length || ev.ids.length === 0) {
          spawnPensamiento({ ...ev, ids: ok });
        }
        if (falta.length) {
          pendientesRef.current.push({
            ev: { ...ev, ids: falta },
            hasta: performance.now() + 8000,
          });
        }
      };
      drenarPendientes();
      if (cancelado) return;
      despacharPensar = pensar;

      // Picking, arrastre y fijado de nodos.
      const ray = new THREE.Raycaster();
      const ndc = new THREE.Vector2();
      const planoDrag = new THREE.Plane();
      const hitV = new THREE.Vector3();
      const lodVisible = (i: number) => {
        const P = livePrefs.current;
        if (ocultasRef.current?.has(nodes[i].fuente)) return false;
        if (!P.lod) return true;
        const dist = camera.position.length();
        const corte = Math.max(25, Math.round(N * Math.min(1, (dist - 90) / 900)));
        return (
          nodes[i].rank < corte ||
          i === hover ||
          i === selRef.current?.index ||
          coincide(i) === true
        );
      };
      /** Devuelve el índice del nodo bajo el cursor o -1. */
      const pick = (ev: PointerEvent | MouseEvent) => {
        const r = renderer.domElement.getBoundingClientRect();
        ndc.set(
          ((ev.clientX - r.left) / r.width) * 2 - 1,
          -((ev.clientY - r.top) / r.height) * 2 + 1,
        );
        ray.setFromCamera(ndc, camera);
        for (const h of ray.intersectObject(malla)) {
          const i = h.instanceId;
          if (i != null && lodVisible(i)) return i;
        }
        return -1;
      };
      let drag: { x: number; y: number; moved: boolean; node: number | null } | null =
        null;
      let hover = -1;
      let ultimoSel = -2;
      const onDown = (ev: PointerEvent) => {
        const i = pick(ev);
        drag = { x: ev.clientX, y: ev.clientY, moved: false, node: i >= 0 ? i : null };
        if (drag.node != null) {
          controls.enabled = false;
          const p = pts[drag.node];
          camera.getWorldDirection(hitV);
          planoDrag.setFromNormalAndCoplanarPoint(
            hitV.negate(),
            new THREE.Vector3(p.x, p.y, p.z),
          );
        }
      };
      const onMove = (ev: PointerEvent) => {
        if (drag) {
          if (Math.abs(ev.clientX - drag.x) + Math.abs(ev.clientY - drag.y) > 3)
            drag.moved = true;
          if (drag.node != null && drag.moved) {
            const r = renderer.domElement.getBoundingClientRect();
            ndc.set(
              ((ev.clientX - r.left) / r.width) * 2 - 1,
              -((ev.clientY - r.top) / r.height) * 2 + 1,
            );
            ray.setFromCamera(ndc, camera);
            if (ray.ray.intersectPlane(planoDrag, hitV)) {
              const p = pts[drag.node];
              p.x = hitV.x;
              p.y = hitV.y;
              p.z = hitV.z;
              nodes[drag.node].pinned = true;
            }
            renderer.domElement.style.cursor = "grabbing";
          }
        } else {
          hover = pick(ev);
          renderer.domElement.style.cursor = hover >= 0 ? "pointer" : "grab";
        }
      };
      const onUp = (ev: PointerEvent) => {
        if (drag && !drag.moved) {
          const i = pick(ev);
          alSeleccionar(i >= 0 ? { index: i } : null);
        } else if (drag?.node != null) {
          alSeleccionar({ index: drag.node });
          const p = pts[drag.node];
          pinRef.current?.(nodes[drag.node].id, [p.x, p.y, p.z]);
        }
        drag = null;
        controls.enabled = true;
        renderer.domElement.style.cursor = "grab";
      };
      renderer.domElement.addEventListener("pointerdown", onDown);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      renderer.domElement.addEventListener("dblclick", (ev) => {
        const i = pick(ev);
        if (i >= 0 && nodes[i].pinned) {
          nodes[i].pinned = false;
          pinRef.current?.(nodes[i].id, null);
        }
      });
      renderer.domElement.addEventListener("wheel", onWheel, { passive: false });

      const ro = new ResizeObserver(() => {
        const w = wrap.clientWidth;
        const h = Math.max(1, wrap.clientHeight);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
        composer.setSize(w, h);
      });
      ro.observe(wrap);

      const v3 = new THREE.Vector3();
      const etiquetados = new Set<number>();
      let raf = 0;
      /** Bucle de animación principal: física + render por frame. */
      const frame = () => {
        if (cancelado) return;
        const P = livePrefs.current;
        const tNow = performance.now();
        controls.autoRotate = P.rotate > 0.01 && !drag && hover < 0;
        controls.autoRotateSpeed = 0.35 * P.rotate;
        controls.update();
        pasoFisica(pts, vel, nodes, edges, P, mem);

        const sel = selRef.current?.index;
        const rel = sel != null ? datos.nbrs.get(sel) : null;
        for (const [k, f] of flashes) {
          const s2 = f.s * 0.94;
          if (s2 < 0.02) flashes.delete(k);
          else f.s = s2;
        }
        // Onda lenta que barre la corteza en Y (~6s) — actividad basal viva.
        const yOnda = (((tNow * 0.00016) % 1) * 2 - 1) * mem.y * 1.15;
        let nHalo = 0;
        for (let i = 0; i < N; i++) {
          const p = pts[i];
          const det = lodVisible(i);
          let wr = Math.max(2.2, (5.5 + Math.min(4, nodes[i].deg * 0.35)) * P.nodeSize);
          if (ocultasRef.current?.has(nodes[i].fuente)) wr = 0.0001;
          else if (!det) wr *= 0.4;
          if (P.ambient) {
            const sh = TEMBLOR[nodes[i].estado];
            wr *= 1 + sh[0] * Math.sin(tNow * 0.004 * sh[1] + i * 1.618);
          }
          dummy.position.set(p.x, p.y, p.z);
          dummy.scale.setScalar(wr);
          dummy.updateMatrix();
          malla.setMatrixAt(i, dummy.matrix);
          tmpC.set(colorNodo(nodes[i], modoRef.current, datos, P));
          const m = coincide(i);
          const relacionado = rel ? rel.has(i) : false;
          const ajeno = sel != null && i !== sel && !relacionado;
          const sg = P.selGlow;
          let boost = det ? 0.92 : 0.42;
          // Brillo por consolidación: las memorias muy evocadas lucen.
          boost += Math.min(0.4, nodes[i].refuerzos * 0.05);
          if (P.ambient)
            boost += Math.exp(-(((p.y - yOnda) / 80) ** 2)) * 0.5;
          if (m === false || ajeno) boost *= 0.35;
          if (relacionado || i === hover) boost = 1 + 0.5 * sg;
          if (i === sel) {
            tmpC.lerp(blanco, 0.35);
            boost = 1 + 0.6 * sg;
          }
          const fl = flashes.get(i);
          if (fl) {
            boost += fl.s * 1.6;
            tmpC.lerp(fl.c, Math.min(0.6, fl.s * 0.6));
          }
          malla.setColorAt(i, tmpC.multiplyScalar(boost));
          if (
            (relacionado || (fl && fl.s > 0.15) || nodes[i].refuerzos >= 4) &&
            nHalo < 256
          ) {
            dummy.scale.setScalar(wr * 2.2);
            dummy.updateMatrix();
            halos.setMatrixAt(nHalo, dummy.matrix);
            halos.setColorAt(
              nHalo,
              tmpC.set(
                fl && fl.s > 0.15
                  ? fl.c
                  : colorNodo(nodes[i], modoRef.current, datos, P),
              ),
            );
            nHalo++;
          }
        }
        malla.instanceMatrix.needsUpdate = true;
        if (malla.instanceColor) malla.instanceColor.needsUpdate = true;
        halos.count = nHalo;
        halos.instanceMatrix.needsUpdate = true;
        if (halos.instanceColor) halos.instanceColor.needsUpdate = true;

        for (const grp of [lineasLink, lineasTag, lineasCore]) {
          const cull = grp !== lineasLink && P.lod;
          let o = 0;
          for (const e of grp.list) {
            const a = pts[e.a];
            const b = pts[e.b];
            if (cull && !lodVisible(e.a) && !lodVisible(e.b)) {
              grp.pos[o++] = a.x;
              grp.pos[o++] = a.y;
              grp.pos[o++] = a.z;
              grp.pos[o++] = a.x;
              grp.pos[o++] = a.y;
              grp.pos[o++] = a.z;
              continue;
            }
            grp.pos[o++] = a.x;
            grp.pos[o++] = a.y;
            grp.pos[o++] = a.z;
            grp.pos[o++] = b.x;
            grp.pos[o++] = b.y;
            grp.pos[o++] = b.z;
          }
          grp.g.attributes.position.needsUpdate = true;
        }
        lineasLink.m.opacity = Math.min(0.55, 0.3 * P.edgeWidth);
        lineasTag.m.opacity = Math.min(0.4, 0.13 * P.edgeWidth);
        lineasCore.m.opacity = Math.min(0.3, 0.09 * P.edgeWidth);

        let ho = 0;
        if (sel != null) {
          for (const e of edges) {
            if (e.a !== sel && e.b !== sel) continue;
            if (ho + 6 > hlPos.length) break;
            const a = pts[e.a];
            const b = pts[e.b];
            hlPos[ho++] = a.x;
            hlPos[ho++] = a.y;
            hlPos[ho++] = a.z;
            hlPos[ho++] = b.x;
            hlPos[ho++] = b.y;
            hlPos[ho++] = b.z;
          }
        }
        hlGeo.setDrawRange(0, ho / 3);
        hlGeo.attributes.position.needsUpdate = true;
        hlLineas.material.opacity = Math.min(1, 0.7 * P.selGlow);

        if (P.flow && NP) {
          for (let i = 0; i < NP; i++) {
            const pl = pulsos[i];
            if (!lodVisible(pl.a) || !lodVisible(pl.b)) {
              pPos[i * 3 + 1] = 99999;
              continue;
            }
            pl.t += 0.004 * pl.s;
            if (pl.t > 1) {
              pl.t = 0;
              pl.s = 0.5 + Math.random() * 0.9;
            }
            const a = pts[pl.a];
            const b = pts[pl.b];
            pPos[i * 3] = a.x + (b.x - a.x) * pl.t;
            pPos[i * 3 + 1] = a.y + (b.y - a.y) * pl.t;
            pPos[i * 3 + 2] = a.z + (b.z - a.z) * pl.t;
            const brillo = Math.sin(pl.t * Math.PI) * 1.7;
            tmpC.set(colorNodo(nodes[pl.a], modoRef.current, datos, P));
            pCol[i * 3] = tmpC.r * brillo;
            pCol[i * 3 + 1] = tmpC.g * brillo;
            pCol[i * 3 + 2] = tmpC.b * brillo;
          }
          pGeo.attributes.position.needsUpdate = true;
          pGeo.attributes.color.needsUpdate = true;
          pPuntos.visible = true;
        } else pPuntos.visible = false;

        if (P.ambient) {
          ambPuntos.visible = true;
          for (let i = 0; i < NA; i++) {
            const o = i * 3;
            aVel[o] += (Math.random() - 0.5) * 0.05;
            aVel[o + 1] += (Math.random() - 0.5) * 0.05;
            aVel[o + 2] += (Math.random() - 0.5) * 0.05;
            aVel[o] *= 0.985;
            aVel[o + 1] *= 0.985;
            aVel[o + 2] *= 0.985;
            aPos[o] += aVel[o];
            aPos[o + 1] += aVel[o + 1];
            aPos[o + 2] += aVel[o + 2];
            const q =
              (aPos[o] ** 2) / (mem.x * mem.x * 0.81) +
              (aPos[o + 1] ** 2) / (mem.y * mem.y * 0.81) +
              (aPos[o + 2] ** 2) / (mem.z * mem.z * 0.81);
            if (q > 1) {
              aVel[o] *= -1;
              aVel[o + 1] *= -1;
              aVel[o + 2] *= -1;
            }
          }
          aGeo.attributes.position.needsUpdate = true;
          ambMat.opacity = 0.2 + 0.1 * Math.sin(tNow * 0.0013);
        } else ambPuntos.visible = false;

        if (sel !== ultimoSel) {
          ultimoSel = sel ?? -2;
          if (sel != null) dispararCascada(sel);
        }
        let co = 0;
        for (let i = cascadas.length - 1; i >= 0; i--) {
          const c = cascadas[i];
          const tt = (tNow - c.t0) / c.dur;
          if (tt >= 1) {
            cascadas.splice(i, 1);
            continue;
          }
          const a = pts[c.a];
          const b = pts[c.b];
          cPos[co * 3] = a.x + (b.x - a.x) * tt;
          cPos[co * 3 + 1] = a.y + (b.y - a.y) * tt;
          cPos[co * 3 + 2] = a.z + (b.z - a.z) * tt;
          tmpC.set(colorNodo(nodes[c.b]));
          const g = Math.sin(tt * Math.PI) * 2.4;
          cCol[co * 3] = tmpC.r * g;
          cCol[co * 3 + 1] = tmpC.g * g;
          cCol[co * 3 + 2] = tmpC.b * g;
          co++;
        }
        cGeo.setDrawRange(0, co);
        cGeo.attributes.position.needsUpdate = true;
        cGeo.attributes.color.needsUpdate = true;
        cPuntos.visible = co > 0;

        if (P.ambient && P.deepBrain && tractos.length) {
          for (let i = 0; i < NT; i++) {
            const tp = tractoPulsos[i];
            tp.t += 0.0038 * tp.s;
            if (tp.t > 1) {
              tp.t = 0;
              tp.c = tractos[(Math.random() * tractos.length) | 0];
            }
            const v = tp.c.getPoint(tp.t);
            tpPos[i * 3] = v.x;
            tpPos[i * 3 + 1] = v.y;
            tpPos[i * 3 + 2] = v.z;
            const g = Math.sin(tp.t * Math.PI) * 2;
            tpCol[i * 3] = 0.94 * g;
            tpCol[i * 3 + 1] = 0.74 * g;
            tpCol[i * 3 + 2] = 0.45 * g;
          }
          tpGeo.attributes.position.needsUpdate = true;
          tpGeo.attributes.color.needsUpdate = true;
          tpPuntos.visible = true;
        } else tpPuntos.visible = false;

        for (const m of materiales) m.uniforms.uTime.value = tNow * 0.001;
        memGrupo.visible = P.membrane;
        // Motas de contaminación: destilan del foco a la membrana y
        // vuelven — hollín mnémico en bucle lento.
        motas.visible = P.contaminacion && focos.length > 0;
        if (motas.visible) {
          for (let m = 0; m < NC; m++) {
            mT[m] += mVel[m];
            if (mT[m] > 1) mT[m] = 0;
            const t = mT[m];
            const suav = t * t * (3 - 2 * t);
            mPos[m * 3] = mDesde[m * 3] + (mHacia[m * 3] - mDesde[m * 3]) * suav;
            mPos[m * 3 + 1] =
              mDesde[m * 3 + 1] + (mHacia[m * 3 + 1] - mDesde[m * 3 + 1]) * suav;
            mPos[m * 3 + 2] =
              mDesde[m * 3 + 2] + (mHacia[m * 3 + 2] - mDesde[m * 3 + 2]) * suav;
          }
          mGeo.attributes.position.needsUpdate = true;
        }
        memGrupo.scale.setScalar(1 + 0.015 * Math.sin(tNow * 0.0009));
        // Marcapaso talámico: latido agudo que mantiene viva la escena.
        const latido = Math.pow(Math.max(0, Math.sin(tNow * 0.0052)), 6);
        puntoCorteza.scale.setScalar(1 + 0.55 * latido);
        (puntoCorteza.material as THREE.MeshBasicMaterial).opacity =
          0.45 + 0.55 * latido;

        // Proyección de etiquetas anatómicas al espacio de pantalla.
        const rect = wrap.getBoundingClientRect();
        regiones(mem).forEach(([, x, y, z], i) => {
          v3.set(x, y, z).applyMatrix4(memGrupo.matrixWorld).project(camera);
          const el = elsRegion[i];
          if (v3.z > 1) {
            el.style.opacity = "0";
          } else {
            el.style.opacity = ".42";
            el.style.left = `${(v3.x * 0.5 + 0.5) * rect.width}px`;
            el.style.top = `${(-v3.y * 0.5 + 0.5) * rect.height}px`;
          }
        });
        // Etiquetas de nodo según el modo: hover (selección+cursor),
        // hubs (los de mayor grado) o all (todos los del nivel de
        // detalle, con tope por rendimiento).
        etiquetados.clear();
        const anotar = (i: number) => {
          if (i < 0 || !lodVisible(i)) return;
          v3.set(pts[i].x, pts[i].y, pts[i].z).project(camera);
          if (v3.z > 1) return;
          const el = etiqueta(i);
          el.textContent = nodes[i].etiqueta;
          el.style.left = `${(v3.x * 0.5 + 0.5) * rect.width}px`;
          el.style.top = `${(-v3.y * 0.5 + 0.5) * rect.height}px`;
          el.style.opacity = "1";
          etiquetados.add(i);
        };
        anotar(sel ?? -1);
        anotar(hover);
        if (P.labels === "hubs") {
          let hubs = 0;
          for (let i = 0; i < N && hubs < 12; i++) {
            if (nodes[i].rank < 12) {
              anotar(i);
              hubs++;
            }
          }
        } else if (P.labels === "all") {
          for (let i = 0; i < N && etiquetados.size < 160; i++) {
            anotar(i);
          }
        }
        for (let i = 0; i < poolEtiquetas.length; i++) {
          if (poolEtiquetas[i] && !etiquetados.has(i)) {
            poolEtiquetas[i].style.opacity = "0";
          }
        }

        // Pensamientos en vuelo: la semilla asciende del tallo al
        // tálamo, los arcos salen a buscar la memoria y la chispa
        // regresa "cargada" con el color del nodo encontrado.
        for (let i = pensamientos.length - 1; i >= 0; i--) {
          const th = pensamientos[i];
          const t = tNow - th.t0;
          if (t > 6500) {
            matarPensamiento(th);
            pensamientos.splice(i, 1);
            continue;
          }
          const fade =
            t < 500 ? t / 500
            : t > 5300 ? Math.max(0, 1 - (t - 5300) / 1200)
            : 1;
          (th.seed.material as THREE.MeshBasicMaterial).opacity =
            fade * (0.6 + 0.4 * Math.sin(tNow * 0.01));
          // Ascenso con smoothstep (700ms): los arcos solo disparan
          // cuando el relevo alcanza el tálamo.
          const ASCENSO = 700;
          const ap = Math.min(1, t / ASCENSO);
          th.seed.position.lerpVectors(
            puertaTallo,
            corteza,
            ap * ap * (3 - 2 * ap),
          );
          v3.copy(th.seed.position).project(camera);
          th.lab.style.left = `${(v3.x * 0.5 + 0.5) * rect.width}px`;
          th.lab.style.top = `${(-v3.y * 0.5 + 0.5) * rect.height}px`;
          th.lab.style.opacity = String(fade);
          for (const l of th.lines) {
            const IDA = 1400;
            const VUELTA = 900;
            const tt = t - ASCENSO - l.delay;
            const p = Math.max(0, Math.min(1, tt / IDA));
            // Parpadeo eléctrico — las sinapsis crepitan, no brillan fijas.
            const fl = 0.78 + 0.22 * Math.sin(tNow * 0.021 + l.idx * 1.7);
            (l.tube.material as THREE.MeshBasicMaterial).opacity =
              fade * 0.5 * Math.min(1, p * 3) * fl;
            (l.line.material as THREE.LineBasicMaterial).opacity =
              fade * 0.8 * Math.min(1, p * 3) * fl;
            const sparkMat = l.spark.material as THREE.MeshBasicMaterial;
            const trailMat = l.trail.material as THREE.MeshBasicMaterial;
            if (p < 1) {
              // Ida: el rayo caza la memoria.
              if (p > 0) {
                l.curve.getPoint(p, v3);
                l.spark.position.copy(v3);
                l.glow.position.copy(v3);
                sparkMat.opacity = fade * Math.sin(p * Math.PI);
                l.glow.material.opacity =
                  fade * Math.sin(p * Math.PI) * 0.9 * fl;
                l.curve.getPoint(Math.max(0, p - 0.07), v3);
                l.trail.position.copy(v3);
                trailMat.opacity = fade * Math.sin(p * Math.PI) * 0.55;
              }
            } else {
              // Llegada: el nodo destella teñido con su color y la
              // chispa regresa al tálamo cargando lo que encontró.
              if (!l.fired) {
                l.fired = true;
                flashes.set(l.idx, { s: 1, c: l.col });
                sparkMat.color.copy(l.col);
                l.glow.material.color.copy(l.col);
              }
              const r = Math.max(0, Math.min(1, (tt - IDA) / VUELTA));
              if (r < 1) {
                l.curve.getPoint(1 - r, v3);
                l.spark.position.copy(v3);
                l.glow.position.copy(v3);
                sparkMat.opacity = fade * Math.sin(r * Math.PI) * 0.8;
                l.glow.material.opacity = fade * Math.sin(r * Math.PI) * 0.6;
                l.curve.getPoint(Math.min(1, 1 - r + 0.07), v3);
                l.trail.position.copy(v3);
                trailMat.opacity = fade * Math.sin(r * Math.PI) * 0.4;
              } else {
                sparkMat.opacity = 0;
                l.glow.material.opacity = 0;
                trailMat.opacity = 0;
              }
            }
          }
        }

        composer.render();
        raf = requestAnimationFrame(frame);
      };
      raf = requestAnimationFrame(frame);

      limpiar = () => {
        despacharPensar = null;
        cancelAnimationFrame(raf);
        ro.disconnect();
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        renderer.domElement.remove();
        capaEtiquetas.remove();
        for (const th of pensamientos) matarPensamiento(th);
        renderer.dispose();
        composer.dispose();
        scene.traverse((o) => {
          if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments || o instanceof THREE.Points) {
            o.geometry.dispose();
          }
        });
      };
    })().catch(() => setGlError(true));

    return () => {
      cancelado = true;
      limpiar();
      if (onPensarRef) onPensarRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datos]);

  if (glError)
    return (
      <div className="vidrio texto-suave flex h-full items-center justify-center text-sm">
        WebGL no disponible en este navegador.
      </div>
    );
  return <div ref={wrapRef} className="relative h-full w-full" />;
}
