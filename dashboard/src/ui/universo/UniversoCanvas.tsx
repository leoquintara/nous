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
  type CauceFantasma,
  type CtxTemporal,
  type EventoRio,
} from "./geometria";
import {
  colorDeFuente,
  colorDimension,
  materialAtomo,
  materialPuntosSuaves,
} from "./materiales";

/** Todo lo que la escena necesita, resuelto por la página. */
export interface DatosRio {
  eventos: Evento[];
  relaciones: Relacion[];
  fantasmas: CauceFantasma[];
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
  /** Cursor del observador; null = presente vivo. */
  cursor: number | null;
  /** Click en el vacío del río: viaja el playhead a ese instante. */
  alCambiarCursor?: (t: number | null) => void;
  /** Click en un orbe: abre la memoria en el inspector. */
  alSeleccionar?: (evento: Evento) => void;
}

const RADIO_ORBE = 2.1;

/**
 * Canvas WebGL del río temporal. La escena se construye una vez por
 * ``rio``; el cursor viaja en ref para no reconstruir nada.
 */
export function UniversoCanvas({
  rio,
  cursor,
  alCambiarCursor,
  alSeleccionar,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [glError, setGlError] = useState(false);
  const cursorRef = useRef<number | null>(cursor);
  const selRef = useRef(alSeleccionar);
  const cursorCbRef = useRef(alCambiarCursor);
  useEffect(() => {
    cursorRef.current = cursor;
    selRef.current = alSeleccionar;
    cursorCbRef.current = alCambiarCursor;
  });

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

      // ── Cauces por rama ──────────────────────────────────────
      const matCauce = (color: number, opacidad: number) =>
        new THREE.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: opacidad,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        });
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
        const tubo = new THREE.Mesh(
          new THREE.TubeGeometry(
            new THREE.CatmullRomCurve3(pts),
            160,
            carril === 0 ? 1.4 : 0.8,
            8,
          ),
          matCauce(carril === 0 ? 0x5b9ef4 : colorDeFuente(rama, rio.fuentes), carril === 0 ? 0.34 : 0.26),
        );
        scene.add(tubo);
      }

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
      // Partículas que fluyen por cada afluente hacia la confluencia.
      const SED_POR_AF = 14;
      interface Sed {
        fuente: string;
        fase: number;
        vel: number;
      }
      const seds: Sed[] = [];
      const afGeo = new THREE.BufferGeometry();
      const afPos: number[] = [];
      const afCol: number[] = [];
      const afSize: number[] = [];
      rio.fuentes.slice(0, 14).forEach((f, idx) => {
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
        scene.add(
          new THREE.Mesh(
            new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 90, 0.45, 6),
            matCauce(color, 0.22),
          ),
        );
        tmpC.set(color);
        for (let i = 0; i < SED_POR_AF; i++) {
          seds.push({ fuente: f.id, fase: Math.random(), vel: 0.5 + Math.random() });
          afPos.push(0, 0, 0);
          afCol.push(tmpC.r, tmpC.g, tmpC.b);
          afSize.push(5);
        }
      });
      afGeo.setAttribute("position", new THREE.Float32BufferAttribute(afPos, 3));
      afGeo.setAttribute("color", new THREE.Float32BufferAttribute(afCol, 3));
      afGeo.setAttribute("aSize", new THREE.Float32BufferAttribute(afSize, 1));
      const sedPuntos = new THREE.Points(afGeo, materialPuntosSuaves(H * 0.5));
      sedPuntos.frustumCulled = false;
      scene.add(sedPuntos);

      // ── Cauces-fantasma (futuros probables) ──────────────────
      const caucesF: { mat: THREE.MeshBasicMaterial; fantasma: CauceFantasma; pts: THREE.Vector3[]; puntosEco: THREE.Points }[] = [];
      for (const f of rio.fantasmas) {
        const color = colorDimension(f.dimension);
        const mat = matCauce(color, 0.2);
        const pts = f.puntos.map((p) => new THREE.Vector3(p.x, p.y - 1.2, p.z));
        scene.add(
          new THREE.Mesh(
            new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 70, 0.6, 6),
            mat,
          ),
        );
        // Ecos de eventos futuros: puntos que fluyen por el cauce.
        const geo = new THREE.BufferGeometry();
        const np = 24;
        const arr = new Float32Array(np * 3);
        const cols = new Float32Array(np * 3);
        const szs = new Float32Array(np);
        tmpC.set(color);
        for (let i = 0; i < np; i++) {
          cols[i * 3] = tmpC.r; cols[i * 3 + 1] = tmpC.g; cols[i * 3 + 2] = tmpC.b;
          szs[i] = 4;
        }
        geo.setAttribute("position", new THREE.BufferAttribute(arr, 3));
        geo.setAttribute("color", new THREE.BufferAttribute(cols, 3));
        geo.setAttribute("aSize", new THREE.BufferAttribute(szs, 1));
        const eco = new THREE.Points(geo, materialPuntosSuaves(H * 0.5));
        eco.frustumCulled = false;
        scene.add(eco);
        caucesF.push({ mat, fantasma: f, pts, puntosEco: eco });
      }

      // ── Objetivos: faros pulsantes al final del futuro ───────
      interface Faro {
        ring: THREE.Mesh;
        nucleo: THREE.Mesh;
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
        scene.add(nucleo);
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
        scene.add(ring);
        faros.push({ ring, nucleo, fase: Math.random() * Math.PI * 2 });
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
        scene.add(
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
      polvoGeo.setAttribute("position", new THREE.BufferAttribute(pArr, 3));
      polvoGeo.setAttribute("color", new THREE.BufferAttribute(pCol, 3));
      polvoGeo.setAttribute("aSize", new THREE.BufferAttribute(pSz, 1));
      const polvo = new THREE.Points(polvoGeo, materialPuntosSuaves(H * 0.5));
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
        for (const m of [sedPuntos.material, polvo.material]) {
          (m as THREE.ShaderMaterial).uniforms.uEscala.value = h * 0.5;
        }
        for (const c of caucesF) {
          (c.puntosEco.material as THREE.ShaderMaterial).uniforms.uEscala.value =
            h * 0.5;
        }
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(wrap);

      const v3 = new THREE.Vector3();
      let raf = 0;
      const animar = (tNow: number) => {
        raf = requestAnimationFrame(animar);
        controls.update();

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
          malla.setColorAt(i, tmpC.set(n.color).multiplyScalar(0.35 + 0.65 * nac));
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

        // Cauces-fantasma: se encienden al cruzar el ahora; los ecos
        // fluyen por la curva como sedimentos del futuro.
        for (const c of caucesF) {
          const prob = c.fantasma.probabilidad;
          c.mat.opacity =
            prob * (0.16 + gp * 0.4) + 0.04 * Math.sin(tNow * 0.002);
          const g = c.puntosEco.geometry.getAttribute("position") as THREE.BufferAttribute;
          const npts = g.count;
          for (let i = 0; i < npts; i++) {
            const f = (tNow * 0.00005 + i / npts) % 1;
            const p = c.pts[Math.floor(f * (c.pts.length - 1))];
            g.setXYZ(i, p.x, p.y + 1.2, p.z);
          }
          g.needsUpdate = true;
        }

        // Faros de objetivos: anillo expandiéndose + pulso del núcleo.
        for (const f of faros) {
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
