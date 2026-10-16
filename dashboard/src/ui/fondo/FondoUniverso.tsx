"use client";

import { useEffect, useRef } from "react";

import { useApp } from "@/ui/proveedor-app";
import {
  branchCurve,
  campoEstrellas,
  cursorParaProgreso,
  cursorT,
  eventosSobreLinea,
  proyeccionesDesdeCursor,
  ramasDesdeEventos,
  worldlinePoint,
  type Rama,
} from "./matematicas";

const DIM_OSCURO = ["#4aa4fa", "#e05aa8", "#00d6d4"] as const;
const DIM_CLARO = ["#198bf8", "#c5157a", "#00a896"] as const;
const SEED = 20261015;

/**
 * Fondo vivo del login: la línea de universo 4D con eventos, ramas
 * fantasma y proyecciones. En la landing el cursor del observador
 * viaja con el scroll; aquí sigue al puntero — mover el ratón en
 * horizontal ES viajar en el tiempo. Sin puntero, deriva suave.
 */
export function FondoUniverso() {
  const ref = useRef<HTMLCanvasElement>(null);
  const { tema } = useApp();

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    const oscuro = tema !== "light";
    const dimColores = oscuro ? DIM_OSCURO : DIM_CLARO;
    const colorLinea = oscuro
      ? "rgba(74,164,250,0.55)"
      : "rgba(25,139,248,0.45)";
    const colorRama = oscuro
      ? ["rgba(45,227,225,0.35)", "rgba(45,227,225,0)"]
      : ["rgba(0,168,150,0.3)", "rgba(0,168,150,0)"];
    const colorProyeccion = oscuro
      ? ["rgba(168,168,216,0.5)", "rgba(168,168,216,0)"]
      : ["rgba(69,69,122,0.4)", "rgba(69,69,122,0)"];
    const colorEstrella = oscuro ? "168,168,216" : "69,69,122";
    const haloCursor = oscuro
      ? "rgba(74,164,250,0.5)"
      : "rgba(25,139,248,0.35)";

    let w = 0;
    let h = 0;
    let raf = 0;
    let progreso = 0.5;
    let objetivo = 0.5;
    let punteroVisto = false;

    const eventos = eventosSobreLinea(90, SEED);
    const ramas: Rama[] = ramasDesdeEventos(eventos, SEED);
    const estrellas = campoEstrellas(140, SEED);

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const alMover = (ev: PointerEvent) => {
      punteroVisto = true;
      objetivo = w > 0 ? ev.clientX / w : 0.5;
    };

    const dibujarLinea = (pts: { x: number; y: number }[]) => {
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length; i++) {
        const p = pts[i - 1];
        const c = pts[i];
        ctx.quadraticCurveTo(p.x, p.y, (p.x + c.x) / 2, (p.y + c.y) / 2);
      }
      ctx.stroke();
    };

    const dibujarBezier = (
      curva: [
        { x: number; y: number },
        { x: number; y: number },
        { x: number; y: number },
        { x: number; y: number },
      ],
    ) => {
      ctx.beginPath();
      ctx.moveTo(curva[0].x, curva[0].y);
      ctx.bezierCurveTo(
        curva[1].x, curva[1].y,
        curva[2].x, curva[2].y,
        curva[3].x, curva[3].y,
      );
      ctx.stroke();
    };

    const frame = (time: number) => {
      if (!punteroVisto) {
        // deriva autónoma: vaivén lento alrededor del centro
        objetivo = 0.5 + Math.sin(time * 0.00012) * 0.3;
      }
      progreso += (objetivo - progreso) * 0.07;
      ctx.clearRect(0, 0, w, h);

      for (const s of estrellas) {
        const tw = 0.25 + 0.2 * Math.sin(time * 0.0006 + s.x * 40);
        ctx.fillStyle = `rgba(${colorEstrella},${tw * 0.5})`;
        ctx.fillRect(s.x * w, s.y * h, 1.2, 1.2);
      }

      ctx.setLineDash([4, 6]);
      for (const rama of ramas) {
        const [p0, p1, p2, p3] = branchCurve(rama, w, h);
        const grad = ctx.createLinearGradient(p0.x, p0.y, p3.x, p3.y);
        grad.addColorStop(0, colorRama[0]);
        grad.addColorStop(1, colorRama[1]);
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.2;
        dibujarBezier([p0, p1, p2, p3]);
      }
      ctx.setLineDash([]);

      const pts = Array.from({ length: 60 }, (_, i) =>
        worldlinePoint(i / 59, w, h),
      );
      ctx.strokeStyle = colorLinea;
      ctx.lineWidth = 1.6;
      ctx.shadowColor = haloCursor;
      ctx.shadowBlur = 12;
      dibujarLinea(pts);
      ctx.shadowBlur = 0;

      const tCursor = cursorT(progreso);
      for (const ev of eventos) {
        const p = worldlinePoint(ev.t, w, h);
        const pasado = ev.t <= tCursor;
        const r = 1 + ev.amplitud * 3.2;
        ctx.globalAlpha = pasado ? 0.9 : 0.28;
        ctx.fillStyle = dimColores[ev.dimension];
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      ctx.setLineDash([2, 7]);
      for (const curva of proyeccionesDesdeCursor(progreso, 6, w, h)) {
        const grad = ctx.createLinearGradient(
          curva[0].x, curva[0].y, curva[3].x, curva[3].y,
        );
        grad.addColorStop(0, colorProyeccion[0]);
        grad.addColorStop(1, colorProyeccion[1]);
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1;
        dibujarBezier(curva);
      }
      ctx.setLineDash([]);

      const cur = cursorParaProgreso(progreso, w, h);
      const pulso = 1 + Math.sin(time * 0.003) * 0.25;
      const halo = ctx.createRadialGradient(
        cur.x, cur.y, 0, cur.x, cur.y, 42 * pulso,
      );
      halo.addColorStop(0, oscuro ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.9)");
      halo.addColorStop(0.25, haloCursor);
      halo.addColorStop(1, "rgba(25,139,248,0)");
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(cur.x, cur.y, 42 * pulso, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = oscuro ? "#ffffff" : "#101050";
      ctx.beginPath();
      ctx.arc(cur.x, cur.y, 4, 0, Math.PI * 2);
      ctx.fill();

      if (!reduced) raf = requestAnimationFrame(frame);
    };

    resize();
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", alMover, { passive: true });
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", alMover);
    };
  }, [tema]);

  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0"
    />
  );
}
