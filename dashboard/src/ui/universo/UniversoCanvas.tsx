"use client";

/**
 * Río temporal 4D — el mapa de la línea de universo.
 *
 * El tiempo fluye en X (pasado → futuro). El cauce central es la
 * rama "main"; las ramas hijas se bifurcan en el instante de su
 * primer evento. Las fuentes son afluentes que convergen en el
 * presente. Tras el "ahora" divergen cauces-fantasma (futuros
 * probables por dimensión) que convergen hacia los objetivos.
 * Click en el vacío = viajar el playhead a ese instante; click en
 * orbe = inspeccionar la memoria. OrbitControls para rotar/zoom.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { useEffect, useRef, useState } from "react";

import type { Evento, Objetivo, Relacion } from "@/domain/modelos";
import {
  anclaDeObjetivo,
  carrilDeFuente,
  carrilDeRama,
  desfaseAfluente,
  forkDe,
  posEnRio,
  ticksDeFecha,
  xEnTiempo,
  type CtxTemporal,
  type Escenario,
  type EventoRio,
} from "./geometria";
import {
  colorDeFuente,
  colorDimension,
  colorEscenario,
  materialAtomo,
  materialFlujo,
  materialPuntosSuaves,
} from "./materiales";
import type { PrefsUniverso } from "./prefs";

/** Todo lo que la escena necesita, resuelto por la página. */
export interface DatosRio {
  eventos: Evento[];
  relaciones: Relacion[];
  objetivos: Objetivo[];
  ctx: CtxTemporal;
  /** Segundos de zona de proyección tras tMax. */
  horizonte: number;
  ramas: string[];
  /** Fuentes con eventos, ordenadas por volumen (carriles). */
  fuentes: { id: string; count: number }[];
}

interface Props {
  rio: DatosRio;
  /**
   * Escenarios de futuro proyectados desde el punto enfocado —
   * cambian al mover el cursor sin reconstruir la escena.
   */
  escenarios: Escenario[];
  /** Cursor del observador; null = presente vivo. */
  cursor: number | null;
  /** Apariencia configurable del universo (persistida en DB). */
  vista: PrefsUniverso;
  /** Click en el vacío del río: viaja el playhead a ese instante. */
  alCambiarCursor?: (t: number | null) => void;
  /** Click en un orbe: abre la memoria en el inspector. */
  alSeleccionar?: (evento: Evento) => void;
  /** Click en un afluente: muestra la ficha de la fuente. */
  alSeleccionarFuente?: (fuente: string) => void;
  /** Click en un cauce-fantasma: muestra el escenario del camino. */
  alSeleccionarFantasma?: (escenario: Escenario) => void;
  /** Click en un faro: muestra la ficha del objetivo. */
  alSeleccionarObjetivo?: (objetivo: Objetivo) => void;
}

const RADIO_ORBE = 2.1;

/**
 * Canvas WebGL del río temporal. La escena se construye una vez por
 * ``rio``; el cursor viaja en ref para no reconstruir nada.
 */
export function UniversoCanvas({
  rio,
  escenarios,
  cursor,
  vista,
  alCambiarCursor,
  alSeleccionar,
  alSeleccionarFuente,
  alSeleccionarFantasma,
  alSeleccionarObjetivo,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [glError, setGlError] = useState(false);
  const cursorRef = useRef<number | null>(cursor);
  // Re-montaje aislado del grupo fantasma (no reconstruye la escena).
  const montarRef = useRef<((e: Escenario[]) => void) | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selRef = useRef(alSeleccionar);
  const selFuenteRef = useRef(alSeleccionarFuente);
  const selFantasmaRef = useRef(alSeleccionarFantasma);
  const selObjetivoRef = useRef(alSeleccionarObjetivo);
  const cursorCbRef = useRef(alCambiarCursor);
  const vistaRef = useRef(vista);
  useEffect(() => {
    cursorRef.current = cursor;
    selRef.current = alSeleccionar;
    selFuenteRef.current = alSeleccionarFuente;
    selFantasmaRef.current = alSeleccionarFantasma;
    selObjetivoRef.current = alSeleccionarObjetivo;
    cursorCbRef.current = alCambiarCursor;
    vistaRef.current = vista;
  });

  // Re-proyección de fantasmas al cambiar `escenarios` (cursor o
  // prefs): debounce corto para no tubear geometría por cada tick
  // del slider; solo el grupo fantasma se reconstruye.
  useEffect(() => {
    const montar = montarRef.current;
    if (!montar) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => montar(escenarios), 140);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [escenarios]);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    let limpiar: (() => void) | undefined;
    let cancelado = false;
    void (async () => {
      const N = rio.eventos.length;
      if (N === 0) return;

      const W = wrap.clientWidth || 1;
      const H = wrap.clientHeight || 1;
      const ctx = rio.ctx;

      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(W, H);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      wrap.appendChild(renderer.domElement);
      renderer.domElement.style.display = "block";

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(45, W / H, 1, 6000);
      camera.position.set(0, 190, 560);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.06;
      // El río se lee mejor con el "ahora" a la derecha del encuadre.
      controls.target.set(ctx.cfg.largo * 0.08, 0, 0);
      controls.maxDistance = 2600;

      const composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(scene, camera));
      const bloom = new UnrealBloomPass(
        new THREE.Vector2(W, H),
        0.75,
        0.55,
        0.68,
      );
      composer.addPass(bloom);
      composer.addPass(new OutputPass());

      // Capa DOM de etiquetas proyectadas (ticks, objetivos, hover).
      const capaEtiquetas = document.createElement("div");
      capaEtiquetas.className = "absolute inset-0 overflow-hidden pointer-events-none";
      capaEtiquetas.style.zIndex = "5";
      wrap.appendChild(capaEtiquetas);
      const etiquetas: { el: HTMLSpanElement; pos: THREE.Vector3 }[] = [];
      const anotar = (texto: string, pos: THREE.Vector3) => {
        const el = document.createElement("span");
        el.textContent = texto;
        el.className =
          "absolute text-[0.6rem] leading-none whitespace-nowrap texto-suave bg-(--surface)/60 rounded px-1 py-0.5";
        el.style.transform = "translate(-50%,-100%)";
        capaEtiquetas.appendChild(el);
        etiquetas.push({ el, pos });
        return el;
      };

      const dummy = new THREE.Object3D();
      const tmpC = new THREE.Color();
      const raycaster = new THREE.Raycaster();
      const ndc = new THREE.Vector2();

      // ── Nodos del río (orbe por evento) ──────────────────────
      const eventosRio: EventoRio[] = rio.eventos.map((e) => ({
        id: e.id,
        ts: e.timestamp_unix,
        dimension: e.dimension,
        fuente: e.fuente ?? "desconocida",
        amplitud: e.amplitud,
        rama: e.rama,
      }));
      const forks = new Map<string, number | null>(
        rio.ramas.map((r) => [r, forkDe(r, eventosRio)]),
      );
      const nodos = rio.eventos.map((e) => {
        const carril = carrilDeRama(e.rama, rio.ramas);
        const p = posEnRio(e.timestamp_unix, carril, forks.get(e.rama) ?? null, ctx);
        return {
          ev: e,
          pos: new THREE.Vector3(p.x, p.y, p.z),
          escala: RADIO_ORBE * (0.7 + e.amplitud * 1.1),
          color: colorDimension(e.dimension),
        };
      });

      const malla = new THREE.InstancedMesh(
        new THREE.SphereGeometry(1, 14, 10),
        materialAtomo(),
        N,
      );
      malla.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      malla.frustumCulled = false;
      scene.add(malla);
      for (let i = 0; i < N; i++) {
        malla.setColorAt(i, tmpC.set(nodos[i].color));
        dummy.position.copy(nodos[i].pos);
        dummy.scale.setScalar(nodos[i].escala);
        dummy.updateMatrix();
        malla.setMatrixAt(i, dummy.matrix);
      }

      // ── Cauces por rama — corriente luminosa interna ─────────
      // Todos los materiales de flujo comparten el reloj uT.
      const matsFlujo: THREE.ShaderMaterial[] = [];
      // Capas togglables: cada grupo responde a una opción de `vista`.
      const grupoRamas = new THREE.Group();
      const grupoAfluentes = new THREE.Group();
      const grupoFantasmas = new THREE.Group();
      const grupoArcos = new THREE.Group();
      const grupoFaros = new THREE.Group();
      scene.add(grupoRamas, grupoAfluentes, grupoFantasmas, grupoArcos, grupoFaros);
      for (const rama of rio.ramas) {
        const carril = carrilDeRama(rama, rio.ramas);
        const fork = forks.get(rama) ?? null;
        const ini = carril === 0 ? ctx.tMin : (fork ?? ctx.tMax);
        const pts: THREE.Vector3[] = [];
        const pasos = 140;
        for (let i = 0; i <= pasos; i++) {
          const t = ini + ((ctx.tMax - ini) * i) / pasos;
          const p = posEnRio(t, carril, fork, ctx);
          pts.push(new THREE.Vector3(p.x, p.y - 1.2, p.z));
        }
        const mat = materialFlujo(
          carril === 0 ? 0x5b9ef4 : colorDeFuente(rama, rio.fuentes),
          carril === 0 ? 0.55 : 0.34,
        );
        matsFlujo.push(mat);
        const tubo = new THREE.Mesh(
          new THREE.TubeGeometry(
            new THREE.CatmullRomCurve3(pts),
            160,
            carril === 0 ? 1.4 : 0.8,
            8,
          ),
          mat,
        );
        // El cauce main es estructural; las bifurcaciones se apagan.
        (carril === 0 ? scene : grupoRamas).add(tubo);
      }

      // Corriente viva del cauce principal: partículas viajando
      // pasado → presente dentro del tubo.
      const N_CORRIENTE = 90;
      const corrGeo = new THREE.BufferGeometry();
      const corrPos = new Float32Array(N_CORRIENTE * 3);
      const corrCol = new Float32Array(N_CORRIENTE * 3);
      const corrSz = new Float32Array(N_CORRIENTE);
      const corrFase = new Float32Array(N_CORRIENTE);
      const corrVida = new Float32Array(N_CORRIENTE);
      for (let i = 0; i < N_CORRIENTE; i++) {
        corrVida[i] = Math.random();
        tmpC.setHSL(0.58, 0.7, 0.72 + Math.random() * 0.2);
        corrCol[i * 3] = tmpC.r;
        corrCol[i * 3 + 1] = tmpC.g;
        corrCol[i * 3 + 2] = tmpC.b;
        corrSz[i] = 3 + Math.random() * 4;
        corrFase[i] = Math.random() * Math.PI * 2;
      }
      corrGeo.setAttribute("position", new THREE.BufferAttribute(corrPos, 3));
      corrGeo.setAttribute("color", new THREE.BufferAttribute(corrCol, 3));
      corrGeo.setAttribute("aSize", new THREE.BufferAttribute(corrSz, 1));
      corrGeo.setAttribute("aFase", new THREE.BufferAttribute(corrFase, 1));
      const corriente = new THREE.Points(
        corrGeo,
        materialPuntosSuaves(H * 0.5, 0.4),
      );
      corriente.frustumCulled = false;
      scene.add(corriente);

      // ── Afluentes por fuente ─────────────────────────────────
      const nacimiento = new Map<string, number>();
      for (const e of rio.eventos) {
        const f = e.fuente ?? "desconocida";
        const prev = nacimiento.get(f);
        if (prev === undefined || e.timestamp_unix < prev) {
          nacimiento.set(f, e.timestamp_unix);
        }
      }
      const idsFuentes = rio.fuentes.map((f) => f.id);
      // Afluentes seleccionables: mesh de tubo → nombre de fuente.
      const MAX_AFUENTES = 14;
      const mallasAfluentes: { mesh: THREE.Mesh; fuente: string }[] = [];
      // Partículas que fluyen por cada afluente hacia la confluencia.
      const SED_POR_AF = 14;
      interface Sed {
        fuente: string;
        carril: number;
        fase: number;
        vel: number;
      }
      const seds: Sed[] = [];
      const afGeo = new THREE.BufferGeometry();
      const afPos: number[] = [];
      const afCol: number[] = [];
      const afSize: number[] = [];
      const afFase: number[] = [];
      rio.fuentes.slice(0, MAX_AFUENTES).forEach((f, idx) => {
        const naci = nacimiento.get(f.id);
        if (naci === undefined) return;
        const carril = idx + 1;
        const pts: THREE.Vector3[] = [];
        const pasos = 80;
        for (let i = 0; i <= pasos; i++) {
          const t = naci + ((ctx.tMax - naci) * i) / pasos;
          const p = posEnRio(t, 0, null, ctx);
          p.z += desfaseAfluente(carril, t, naci, ctx);
          p.y -= 3;
          pts.push(new THREE.Vector3(p.x, p.y, p.z));
        }
        const color = colorDeFuente(f.id, rio.fuentes);
        const mat = materialFlujo(color, 0.5);
        matsFlujo.push(mat);
        const mallaAf = new THREE.Mesh(
          new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 90, 0.45, 6),
          mat,
        );
        mallaAf.userData.afluenteIdx = idx;
        grupoAfluentes.add(mallaAf);
        mallasAfluentes.push({ mesh: mallaAf, fuente: f.id });
        anotar(f.id, pts[Math.floor(pts.length * 0.45)]);
        tmpC.set(color);
        for (let i = 0; i < SED_POR_AF; i++) {
          seds.push({ fuente: f.id, carril, fase: Math.random(), vel: 0.5 + Math.random() });
          afPos.push(0, 0, 0);
          afCol.push(tmpC.r, tmpC.g, tmpC.b);
          afSize.push(5);
          afFase.push(Math.random() * Math.PI * 2);
        }
      });
      afGeo.setAttribute("position", new THREE.Float32BufferAttribute(afPos, 3));
      afGeo.setAttribute("color", new THREE.Float32BufferAttribute(afCol, 3));
      afGeo.setAttribute("aSize", new THREE.Float32BufferAttribute(afSize, 1));
      afGeo.setAttribute("aFase", new THREE.Float32BufferAttribute(afFase, 1));
      const sedPuntos = new THREE.Points(afGeo, materialPuntosSuaves(H * 0.5, 0.35));
      sedPuntos.frustumCulled = false;
      grupoAfluentes.add(sedPuntos);

      // ── Cauces-fantasma: escenarios de futuro con corriente ──
      // Reconstruibles sin tocar la escena: al mover el cursor, la
      // proyección se recalcula y este grupo se re-monta en caliente.
      interface EntradaFantasma {
        mesh: THREE.Mesh;
        mat: THREE.ShaderMaterial;
        escenario: Escenario;
      }
      const caucesF: EntradaFantasma[] = [];
      // Subconjunto de `etiquetas` que pertenece a escenarios.
      const etiquetasF: { el: HTMLSpanElement; pos: THREE.Vector3 }[] = [];

      /** (Re)monta los cauces-fantasma: descarta tubos y etiquetas. */
      const montarFantasmas = (escs: Escenario[]) => {
        for (const c of caucesF) {
          grupoFantasmas.remove(c.mesh);
          c.mesh.geometry.dispose();
          c.mat.dispose();
        }
        caucesF.length = 0;
        for (const et of etiquetasF.splice(0)) {
          et.el.remove();
          const i = etiquetas.indexOf(et);
          if (i >= 0) etiquetas.splice(i, 1);
        }
        for (const f of escs) {
          const mat = materialFlujo(colorEscenario(f), 0.8);
          const pts = f.puntos.map(
            (p) => new THREE.Vector3(p.x, p.y - 1.2, p.z),
          );
          const mallaF = new THREE.Mesh(
            new THREE.TubeGeometry(
              new THREE.CatmullRomCurve3(pts),
              70,
              0.6,
              6,
            ),
            mat,
          );
          grupoFantasmas.add(mallaF);
          caucesF.push({ mesh: mallaF, mat, escenario: f });
          anotar(
            `${f.nombre} · ${Math.round(f.probabilidad * 100)}%`,
            pts[Math.floor(pts.length * 0.62)]
              .clone()
              .add(new THREE.Vector3(0, 8, 0)),
          );
          etiquetasF.push(etiquetas[etiquetas.length - 1]);
        }
      };
      montarRef.current = montarFantasmas;
      montarFantasmas(escenarios);

      // ── Objetivos: faros pulsantes al final del futuro ───────
      interface Faro {
        ring: THREE.Mesh;
        nucleo: THREE.Mesh;
        objetivo: Objetivo;
        fase: number;
      }
      const faros: Faro[] = [];
      for (const o of rio.objetivos) {
        if (o.estado !== "activo") continue;
        const a = anclaDeObjetivo(o, ctx);
        const col = o.dimension ? colorDimension(o.dimension) : 0xffd66b;
        const nucleo = new THREE.Mesh(
          new THREE.OctahedronGeometry(4.2),
          new THREE.MeshBasicMaterial({
            color: col,
            transparent: true,
            opacity: 0.95,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
          }),
        );
        nucleo.position.set(a.x, a.y, a.z);
        grupoFaros.add(nucleo);
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(5.5, 6.1, 40),
          new THREE.MeshBasicMaterial({
            color: col,
            transparent: true,
            opacity: 0.5,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            side: THREE.DoubleSide,
          }),
        );
        ring.position.copy(nucleo.position);
        grupoFaros.add(ring);
        faros.push({ ring, nucleo, objetivo: o, fase: Math.random() * Math.PI * 2 });
        anotar(`◆ ${o.titulo}`, nucleo.position.clone().add(new THREE.Vector3(0, 10, 0)));
      }

      // ── Arcos de relación (el tejido sobre el río) ───────────
      const idToIdx = new Map(rio.eventos.map((e, i) => [e.id, i]));
      const arcPts: THREE.Vector3[] = [];
      for (const r of rio.relaciones) {
        const a = idToIdx.get(r.origen_id);
        const b = idToIdx.get(r.destino_id);
        if (a === undefined || b === undefined) continue;
        const curva = new THREE.QuadraticBezierCurve3(
          nodos[a].pos,
          nodos[a].pos
            .clone()
            .add(nodos[b].pos)
            .multiplyScalar(0.5)
            .add(new THREE.Vector3(0, 30 + Math.min(60, Math.abs(a - b) * 0.2), 0)),
          nodos[b].pos,
        );
        const muestras = curva.getPoints(10);
        // Segmentos consecutivos: cada par de puntos = un tramo.
        for (let i = 0; i < muestras.length - 1; i++) {
          arcPts.push(muestras[i], muestras[i + 1]);
        }
      }
      if (arcPts.length) {
        grupoArcos.add(
          new THREE.LineSegments(
            new THREE.BufferGeometry().setFromPoints(arcPts),
            new THREE.LineBasicMaterial({
              color: 0x8ea6d8,
              transparent: true,
              opacity: 0.14,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          ),
        );
      }

      // ── Playhead + línea del ahora ───────────────────────────
      const playhead = new THREE.Mesh(
        new THREE.PlaneGeometry(1.6, 140),
        new THREE.MeshBasicMaterial({
          color: 0x86c5ff,
          transparent: true,
          opacity: 0.5,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          side: THREE.DoubleSide,
        }),
      );
      playhead.visible = false;
      scene.add(playhead);
      const ahora = new THREE.Mesh(
        new THREE.PlaneGeometry(0.8, 140),
        new THREE.MeshBasicMaterial({
          color: 0x4ade80,
          transparent: true,
          opacity: 0.3,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          side: THREE.DoubleSide,
        }),
      );
      ahora.position.x = xEnTiempo(ctx.tMax, ctx);
      scene.add(ahora);
      anotar("ahora", new THREE.Vector3(ahora.position.x, 74, 0));

      // ── Ticks de fecha sobre el cauce ────────────────────────
      for (const tk of ticksDeFecha(ctx, 7)) {
        anotar(tk.etiqueta, new THREE.Vector3(xEnTiempo(tk.t, ctx), -30, 0));
      }

      // ── Polvo ambiental alrededor del río ────────────────────
      const polvoGeo = new THREE.BufferGeometry();
      const NP = 240;
      const pArr = new Float32Array(NP * 3);
      const pCol = new Float32Array(NP * 3);
      const pSz = new Float32Array(NP);
      for (let i = 0; i < NP; i++) {
        pArr[i * 3] = (Math.random() - 0.5) * ctx.cfg.largo * 1.2;
        pArr[i * 3 + 1] = (Math.random() - 0.5) * 160;
        pArr[i * 3 + 2] = (Math.random() - 0.5) * 320;
        tmpC.setHSL(0.6, 0.5, 0.6 + Math.random() * 0.2);
        pCol[i * 3] = tmpC.r; pCol[i * 3 + 1] = tmpC.g; pCol[i * 3 + 2] = tmpC.b;
        pSz[i] = 2 + Math.random() * 3;
      }
      const pFase = new Float32Array(NP);
      for (let i = 0; i < NP; i++) pFase[i] = Math.random() * Math.PI * 2;
      polvoGeo.setAttribute("position", new THREE.BufferAttribute(pArr, 3));
      polvoGeo.setAttribute("color", new THREE.BufferAttribute(pCol, 3));
      polvoGeo.setAttribute("aSize", new THREE.BufferAttribute(pSz, 1));
      polvoGeo.setAttribute("aFase", new THREE.BufferAttribute(pFase, 1));
      const polvo = new THREE.Points(polvoGeo, materialPuntosSuaves(H * 0.5, 1));
      polvo.frustumCulled = false;
      scene.add(polvo);

      // ── Interacción: click orbe → selección; click vacío → viaje
      const planoRio = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
      const hit = new THREE.Vector3();
      let downX = 0;
      let downY = 0;
      const onDown = (e: PointerEvent) => {
        downX = e.clientX;
        downY = e.clientY;
      };
      const onUp = (e: PointerEvent) => {
        // Solo clicks sin arrastre (el arrastre es de la cámara).
        if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) return;
        const r = renderer.domElement.getBoundingClientRect();
        ndc.set(
          ((e.clientX - r.left) / r.width) * 2 - 1,
          -((e.clientY - r.top) / r.height) * 2 + 1,
        );
        raycaster.setFromCamera(ndc, camera);
        const hits = raycaster.intersectObject(malla);
        if (hits.length && hits[0].instanceId != null) {
          selRef.current?.(nodos[hits[0].instanceId].ev);
          return;
        }
        // Faros → fantasmas → afluentes → vacío (viaje del playhead).
        const hitFaro = raycaster.intersectObjects(
          faros.map((f) => f.nucleo),
        );
        if (hitFaro.length) {
          const faro = faros.find((f) => f.nucleo === hitFaro[0].object);
          if (faro) {
            selObjetivoRef.current?.(faro.objetivo);
            return;
          }
        }
        const hitFantasma = raycaster.intersectObjects(
          caucesF.filter((c) => c.mesh.visible).map((c) => c.mesh),
        );
        if (hitFantasma.length) {
          const c = caucesF.find((x) => x.mesh === hitFantasma[0].object);
          if (c) {
            selFantasmaRef.current?.(c.escenario);
            return;
          }
        }
        const hitAfluente = raycaster.intersectObjects(
          mallasAfluentes.filter((m) => m.mesh.visible).map((m) => m.mesh),
        );
        if (hitAfluente.length) {
          const a = mallasAfluentes.find(
            (m) => m.mesh === hitAfluente[0].object,
          );
          if (a) {
            selFuenteRef.current?.(a.fuente);
            return;
          }
        }
        if (raycaster.ray.intersectPlane(planoRio, hit)) {
          const fin = ctx.tMax + rio.horizonte;
          const t =
            ctx.tMin +
            ((hit.x + ctx.cfg.largo / 2) / ctx.cfg.largo) * ctx.span;
          cursorCbRef.current?.(Math.min(fin, Math.max(ctx.tMin, t)));
        }
      };
      renderer.domElement.addEventListener("pointerdown", onDown);
      renderer.domElement.addEventListener("pointerup", onUp);

      const onResize = () => {
        const w = wrap.clientWidth || 1;
        const h = wrap.clientHeight || 1;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
        composer.setSize(w, h);
        for (const m of [
          sedPuntos.material,
          polvo.material,
          corriente.material,
        ]) {
          (m as THREE.ShaderMaterial).uniforms.uEscala.value = h * 0.5;
        }
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(wrap);

      const v3 = new THREE.Vector3();
      // Colores por dimensión resolubles en caliente (vista.colores).
      let coloresKey = "";
      let colDims = new Map<string, number>();
      const afSizeAttr = afGeo.getAttribute("aSize") as THREE.BufferAttribute;
      let ultimoMaxAf = -1;
      let raf = 0;
      const animar = (tNow: number) => {
        raf = requestAnimationFrame(animar);
        controls.update();
        const v = vistaRef.current;

        // ── Vista: capas, intensidades y color ────────────────
        grupoRamas.visible = v.ramas;
        grupoArcos.visible = v.arcos;
        grupoFaros.visible = v.objetivos;
        grupoAfluentes.visible = v.afluentes;
        grupoAfluentes.scale.z = v.separacion;
        polvo.visible = v.polvo;
        bloom.strength = v.bloom;
        capaEtiquetas.style.display = v.etiquetas ? "" : "none";
        const ck = JSON.stringify(v.colores);
        if (ck !== coloresKey) {
          coloresKey = ck;
          colDims = new Map(
            Object.entries(v.colores).map(([d, hex]) => [
              d,
              new THREE.Color(hex).getHex(),
            ]),
          );
        }
        // Cantidad de afluentes: tubos ocultos y seds apagados.
        if (v.maxAfluentes !== ultimoMaxAf) {
          ultimoMaxAf = v.maxAfluentes;
          mallasAfluentes.forEach((m, i) => {
            m.mesh.visible = i < ultimoMaxAf;
          });
          seds.forEach((s, i) =>
            afSizeAttr.setX(i, s.carril <= ultimoMaxAf ? 5 : 0),
          );
          afSizeAttr.needsUpdate = true;
        }

        const tSec = tNow * 0.001;
        for (const m of matsFlujo) m.uniforms.uT.value = tSec;
        for (const m of [
          sedPuntos.material,
          polvo.material,
          corriente.material,
        ]) {
          (m as THREE.ShaderMaterial).uniforms.uT.value = tSec;
        }

        const cur = cursorRef.current;
        const tCur = cur ?? ctx.tMax + rio.horizonte;
        const gp = cur != null
          ? Math.min(1, Math.max(0, (cur - ctx.tMax) / rio.horizonte))
          : 0;

        // Orbes: latido por amplitud + atenuación tras el cursor.
        for (let i = 0; i < N; i++) {
          const n = nodos[i];
          const nac = n.ev.timestamp_unix <= tCur ? 1 : 0.18;
          const latido = 1 + 0.07 * Math.sin(tNow * 0.003 + i * 0.7) * n.ev.amplitud;
          dummy.position.copy(n.pos);
          dummy.scale.setScalar(Math.max(0.0001, n.escala * latido * Math.max(nac, 0.12)));
          dummy.updateMatrix();
          malla.setMatrixAt(i, dummy.matrix);
          malla.setColorAt(
            i,
            tmpC
              .set(colDims.get(n.ev.dimension) ?? n.color)
              .multiplyScalar(0.35 + 0.65 * nac),
          );
        }
        malla.instanceMatrix.needsUpdate = true;
        if (malla.instanceColor) malla.instanceColor.needsUpdate = true;

        // Playhead en el cursor; el "ahora" siempre marca el presente.
        if (cur != null) {
          playhead.visible = true;
          playhead.position.x = xEnTiempo(Math.min(tCur, ctx.tMax + rio.horizonte), ctx);
          (playhead.material as THREE.MeshBasicMaterial).opacity =
            0.4 + 0.15 * Math.sin(tNow * 0.004);
        } else playhead.visible = false;

        // Sedimentos: cada partícula viaja el afluente de su fuente.
        const posAttr = afGeo.getAttribute("position") as THREE.BufferAttribute;
        seds.forEach((s, i) => {
          const naci = nacimiento.get(s.fuente) ?? ctx.tMin;
          s.fase += 0.0009 * s.vel;
          if (s.fase > 1) s.fase = 0;
          const t = naci + (ctx.tMax - naci) * s.fase;
          const carril = carrilDeFuente(s.fuente, idsFuentes);
          const p = posEnRio(t, 0, null, ctx);
          posAttr.setXYZ(
            i,
            p.x,
            p.y - 3,
            p.z + desfaseAfluente(carril, t, naci, ctx),
          );
        });
        posAttr.needsUpdate = true;

        // Corriente del cauce principal: cada partícula viaja el span.
        const corrAttr = corrGeo.getAttribute("position") as THREE.BufferAttribute;
        for (let i = 0; i < N_CORRIENTE; i++) {
          corrVida[i] += 0.0006 + (i % 5) * 0.00012;
          if (corrVida[i] > 1) corrVida[i] = 0;
          const t = ctx.tMin + ctx.span * corrVida[i];
          const p = posEnRio(t, 0, null, ctx);
          corrAttr.setXYZ(i, p.x, p.y - 0.6, p.z);
        }
        corrAttr.needsUpdate = true;

        // Escenarios de futuro: su corriente interna (uT) brilla más
        // al acercarse el playhead al punto donde brotan.
        for (const c of caucesF) {
          const prob = c.escenario.probabilidad;
          const activo = v.fantasmas;
          c.mesh.visible = activo;
          if (!activo) continue;
          c.mat.uniforms.uT.value = tSec;
          c.mat.uniforms.uIntensidad.value =
            (prob * (0.5 + gp * 1.1) + 0.05 * Math.sin(tNow * 0.002)) *
            v.intensidadFantasma;
        }

        // Faros de objetivos: anillo expandiéndose + pulso del núcleo.
        for (const f of faros) {
          const base =
            f.objetivo.dimension != null
              ? (colDims.get(f.objetivo.dimension) ??
                colorDimension(f.objetivo.dimension))
              : 0xffd66b;
          (f.nucleo.material as THREE.MeshBasicMaterial).color.setHex(base);
          (f.ring.material as THREE.MeshBasicMaterial).color.setHex(base);
          const k = (tNow * 0.0012 + f.fase) % 1;
          f.ring.scale.setScalar(1 + k * 1.8);
          (f.ring.material as THREE.MeshBasicMaterial).opacity = 0.55 * (1 - k);
          (f.nucleo.material as THREE.MeshBasicMaterial).opacity =
            0.7 + 0.3 * Math.sin(tNow * 0.005 + f.fase);
          f.nucleo.rotation.y = tNow * 0.001;
        }

        // Etiquetas proyectadas (ocultas tras la cámara quedan fuera).
        for (const et of etiquetas) {
          v3.copy(et.pos).project(camera);
          if (v3.z > 1) {
            et.el.style.display = "none";
          } else {
            et.el.style.display = "block";
            et.el.style.left = `${(v3.x * 0.5 + 0.5) * wrap.clientWidth}px`;
            et.el.style.top = `${(-v3.y * 0.5 + 0.5) * wrap.clientHeight}px`;
          }
        }

        composer.render();
      };
      raf = requestAnimationFrame(animar);

      limpiar = () => {
        cancelAnimationFrame(raf);
        montarRef.current = null;
        ro.disconnect();
        renderer.domElement.removeEventListener("pointerdown", onDown);
        renderer.domElement.removeEventListener("pointerup", onUp);
        controls.dispose();
        renderer.domElement.remove();
        capaEtiquetas.remove();
        renderer.dispose();
        composer.dispose();
        scene.traverse((o) => {
          if (o instanceof THREE.Mesh || o instanceof THREE.Line || o instanceof THREE.Points) {
            o.geometry.dispose();
          }
        });
      };
      if (cancelado) limpiar();
    })().catch((e) => {
      console.error("[UniversoCanvas] fallo en la escena:", e);
      setGlError(true);
    });
    return () => {
      cancelado = true;
      limpiar?.();
    };
  }, [rio]);

  return (
    <div ref={wrapRef} className="absolute inset-0">
      {glError && (
        <div className="texto-suave flex h-full items-center justify-center text-sm">
          WebGL no disponible en este navegador.
        </div>
      )}
    </div>
  );
}
