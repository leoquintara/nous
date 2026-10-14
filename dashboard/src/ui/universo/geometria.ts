/**
 * Geometría pura del río temporal 4D (vista /universo).
 *
 * El tiempo fluye en X (pasado → futuro). Cada línea de universo
 * (rama) es un cauce; las ramas hijas se bifurcan en el instante de
 * su primer evento. Las fuentes son afluentes: cintas laterales que
 * convergen en el presente. Tras el "ahora" divergen los cauces-
 * fantasma — futuros probables por dimensión cuya opacidad es la
 * probabilidad, y que convergen hacia los objetivos del usuario.
 *
 * Todo es matemática pura sin Three.js — testeable con Vitest.
 */

import type { Dimension, Objetivo } from "@/domain/modelos";

/** Punto 3D en la escena del río. */
export interface Punto3 {
  x: number;
  y: number;
  z: number;
}

/** Lo que el río necesita saber de cada evento. */
export interface EventoRio {
  id: string;
  /** Timestamp Unix en segundos. */
  ts: number;
  dimension: Dimension;
  fuente: string;
  amplitud: number;
  rama: string;
}

/** Parámetros espaciales del río (unidades de escena). */
export interface ConfigRio {
  /** Unidades de mundo que ocupa el span temporal completo. */
  largo: number;
  /** Separación lateral entre cauces de ramas distintas. */
  separacionRama: number;
  /** Separación lateral entre afluentes de fuentes. */
  separacionFuente: number;
  /** Amplitud del meandro vertical del cauce. */
  meandroY: number;
  /** Amplitud del meandro en profundidad del cauce. */
  meandroZ: number;
  /** Fracción final del span donde el afluente converge al cauce. */
  fraccionConfluencia: number;
}

export const RIO: ConfigRio = {
  largo: 1200,
  separacionRama: 80,
  separacionFuente: 26,
  meandroY: 6,
  meandroZ: 10,
  fraccionConfluencia: 0.08,
};

/** Contexto temporal compartido por todas las funciones del río. */
export interface CtxTemporal {
  tMin: number;
  tMax: number;
  span: number;
  cfg: ConfigRio;
}

/** Construye el contexto temporal a partir de los eventos. */
export function ctxDe(
  tMin: number,
  tMax: number,
  cfg: ConfigRio = RIO,
): CtxTemporal {
  return { tMin, tMax, span: Math.max(1, tMax - tMin), cfg };
}

/** Interpolación suave 0→1 (curva en S, smoothstep clásico). */
export function smoothstep(x: number): number {
  const f = Math.min(1, Math.max(0, x));
  return f * f * (3 - 2 * f);
}

/** Posición X para un instante — el río centrado en el origen. */
export function xEnTiempo(t: number, ctx: CtxTemporal): number {
  return ((t - ctx.tMin) / ctx.span) * ctx.cfg.largo - ctx.cfg.largo / 2;
}

/**
 * Carril lateral de una rama: "main" = 0 (cauce central); el resto
 * alterna carriles ±1, ±2… por orden de aparición — bifurcaciones
 * simétricas a ambos lados del tronco.
 */
export function carrilDeRama(rama: string, ramas: string[]): number {
  if (rama === "main") return 0;
  const otras = ramas.filter((r) => r !== "main");
  const i = Math.max(0, otras.indexOf(rama));
  return i % 2 === 0 ? 1 + i / 2 : -(1 + Math.floor(i / 2));
}

/**
 * Carril de un afluente por fuente: siempre en el mismo lado (z+),
 * ordenados por aparición — el 0 es el más cercano al cauce.
 */
export function carrilDeFuente(fuente: string, fuentes: string[]): number {
  return 1 + Math.max(0, fuentes.indexOf(fuente));
}

/**
 * Punto de bifurcación temporal de una rama hija: el instante de su
 * primer evento. null si la rama no tiene eventos (no bifurca).
 */
export function forkDe(rama: string, eventos: EventoRio[]): number | null {
  let min: number | null = null;
  for (const e of eventos) {
    if (e.rama === rama && (min === null || e.ts < min)) min = e.ts;
  }
  return min;
}

/**
 * Desfase lateral del cauce de una rama en el instante t: crece de
 * 0 a su carril tras el punto de bifurcación (~4% del span).
 */
export function desfaseCauce(
  carril: number,
  t: number,
  tFork: number | null,
  ctx: CtxTemporal,
): { y: number; z: number } {
  if (carril === 0 || tFork === null || t <= tFork) return { y: 0, z: 0 };
  const rampa = Math.max(1, ctx.span * 0.04);
  const f = smoothstep((t - tFork) / rampa);
  return {
    y: carril * ctx.cfg.separacionRama * 0.3 * f,
    z: carril * ctx.cfg.separacionRama * f,
  };
}

/**
 * Punto sobre el río en el instante t para un carril de rama:
 * meandro base compartido + desfase lateral de la bifurcación.
 */
export function posEnRio(
  t: number,
  carril: number,
  tFork: number | null,
  ctx: CtxTemporal,
): Punto3 {
  const x = xEnTiempo(t, ctx);
  const d = desfaseCauce(carril, t, tFork, ctx);
  return {
    x,
    y: Math.sin(x * 0.012) * ctx.cfg.meandroY + d.y,
    z: Math.sin(x * 0.008 + 1.3) * ctx.cfg.meandroZ + d.z,
  };
}

/** Punto sobre una curva cúbica de Bézier (f ∈ [0,1]). */
export function bezierCubica(
  p0: Punto3,
  p1: Punto3,
  p2: Punto3,
  p3: Punto3,
  f: number,
): Punto3 {
  const u = 1 - f;
  const a = u * u * u;
  const b = 3 * u * u * f;
  const c = 3 * u * f * f;
  const d = f * f * f;
  return {
    x: a * p0.x + b * p1.x + c * p2.x + d * p3.x,
    y: a * p0.y + b * p1.y + c * p2.y + d * p3.y,
    z: a * p0.z + b * p1.z + c * p2.z + d * p3.z,
  };
}

/**
 * Ancla en la escena de un objetivo: posición X en su fecha límite,
 * en el carril fantasma de su dimensión (transversal = centro alto).
 */
export function anclaDeObjetivo(
  objetivo: Pick<Objetivo, "fecha_objetivo" | "dimension">,
  ctx: CtxTemporal,
): Punto3 {
  const x = xEnTiempo(objetivo.fecha_objetivo, ctx);
  const carril = carrilFantasma(objetivo.dimension);
  return {
    x,
    y: Math.sin(x * 0.012) * ctx.cfg.meandroY + carril * 22,
    z: Math.sin(x * 0.008 + 1.3) * ctx.cfg.meandroZ + carril * 34,
  };
}

/**
 * Carril fantasma de una dimensión: cognitiva diverge a -1,
 * conductual a +1, somática hacia arriba (carril 0 con altura).
 */
export function carrilFantasma(dimension: Dimension | null): number {
  if (dimension === "cognitiva") return -1;
  if (dimension === "conductual") return 1;
  return 0;
}

/** Cauce-fantasma: futuro probable de una dimensión. */
export interface CauceFantasma {
  dimension: Dimension;
  /** Peso de la distribución proyectada → opacidad del cauce. */
  probabilidad: number;
  /** Curva muestreada desde el "ahora" hasta el ancla del futuro. */
  puntos: Punto3[];
  /** Ancla al objetivo más próximo de la dimensión, si existe. */
  objetivoAncla: Objetivo | null;
}

/**
 * Construye los cauces-fantasma tras el presente: uno por dimensión,
 * Bézier desde el "ahora" divergiendo a su carril. Si hay objetivos
 * activos de la dimensión, el cauce converge hacia el más próximo;
 * si no, extrapola hasta el horizonte en su carril libre.
 */
export function construirFantasmas(
  distribucion: Record<string, number> | null,
  objetivos: Objetivo[],
  horizonte: number,
  ctx: CtxTemporal,
  muestras = 40,
): CauceFantasma[] {
  const dims: Dimension[] = ["cognitiva", "somatica", "conductual"];
  const inicio = posEnRio(ctx.tMax, 0, null, ctx);
  const fin = ctx.tMax + horizonte;
  return dims.map((dimension) => {
    const prob = Math.max(0, Math.min(1, distribucion?.[dimension] ?? 0.33));
    const ancla =
      objetivos
        .filter(
          (o) =>
            o.estado === "activo" &&
            o.fecha_objetivo > ctx.tMax &&
            (o.dimension === dimension || o.dimension === null),
        )
        .sort((a, b) => a.fecha_objetivo - b.fecha_objetivo)[0] ?? null;
    const carril = carrilFantasma(dimension);
    const destino: Punto3 = ancla
      ? anclaDeObjetivo(ancla, ctx)
      : {
          x: xEnTiempo(fin, ctx),
          y:
            Math.sin(xEnTiempo(fin, ctx) * 0.012) * ctx.cfg.meandroY +
            (dimension === "somatica" ? 46 : carril * 26),
          z:
            Math.sin(xEnTiempo(fin, ctx) * 0.008 + 1.3) *
              ctx.cfg.meandroZ +
            carril * ctx.cfg.separacionRama * 1.15,
        };
    const dx = Math.max(40, destino.x - inicio.x);
    const c1: Punto3 = { x: inicio.x + dx * 0.35, y: inicio.y, z: inicio.z };
    const c2: Punto3 = {
      x: destino.x - dx * 0.35,
      y: destino.y,
      z: destino.z,
    };
    const puntos: Punto3[] = [];
    for (let i = 0; i <= muestras; i++) {
      puntos.push(bezierCubica(inicio, c1, c2, destino, i / muestras));
    }
    return { dimension, probabilidad: prob, puntos, objetivoAncla: ancla };
  });
}

/**
 * Desfase lateral de un afluente (fuente) en el instante t: su
 * carril propio desde que nace, convergiendo al cauce central en la
 * última fracción del span — el tributario se funde en el río.
 */
export function desfaseAfluente(
  carril: number,
  t: number,
  tNacimiento: number,
  ctx: CtxTemporal,
): number {
  if (t <= tNacimiento) return 0;
  const arranque = Math.max(1, ctx.span * 0.03);
  const cauce =
    carril * ctx.cfg.separacionFuente * smoothstep(
      (t - tNacimiento) / arranque,
    );
  const confluencia = ctx.span * ctx.cfg.fraccionConfluencia;
  const faltante = Math.max(1, ctx.tMax - tNacimiento - confluencia);
  const f = smoothstep(
    (t - (tNacimiento + faltante)) / confluencia,
  );
  return cauce * (1 - f);
}

/**
 * Ticks de fecha legibles sobre el río: instantes aproximadamente
 * equiespaciados redondeados al día, con etiqueta corta.
 */
export function ticksDeFecha(
  ctx: CtxTemporal,
  cantidad = 7,
): { t: number; etiqueta: string }[] {
  const n = Math.max(2, cantidad);
  const ticks: { t: number; etiqueta: string }[] = [];
  for (let i = 0; i < n; i++) {
    const t = Math.round(ctx.tMin + (ctx.span * i) / (n - 1));
    const etiqueta = new Date(t * 1000).toLocaleDateString("es-MX", {
      day: "numeric",
      month: "short",
      year: ctx.span > 300 * 86_400 ? "numeric" : undefined,
    });
    ticks.push({ t, etiqueta });
  }
  return ticks;
}
