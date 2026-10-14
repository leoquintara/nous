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
 * Se usa para anclar objetivos por dimensión, no para los escenarios.
 */
export function carrilFantasma(dimension: Dimension | null): number {
  if (dimension === "cognitiva") return -1;
  if (dimension === "conductual") return 1;
  return 0;
}

/** Identificador de escenario de futuro del río. */
export type IdEscenario = "continuidad" | "riesgo" | "florecer";

/**
 * Escenario de futuro: un cauce-fantasma con nombre y narrativa.
 *
 * Las dimensiones (cognitiva/somática/conductual) son el sustrato
 * neurocientífico que hace posible proyectar — no el futuro mismo.
 * Cada escenario responde "qué pasa si el cauce va por aquí":
 * continuidad (tu patrón sigue igual), riesgo (las señales de
 * desgaste ganan) y florecer (converge hacia tus objetivos).
 *
 * Regla de la metáfora: un objetivo es el punto donde TODOS los
 * cauces-fantasma pretenden llegar — la diferencia entre caminos
 * es si llegan o no. Con meta activa, los tres curvan hacia el
 * ancla: florecer la alcanza, continuidad se acerca sin llegar,
 * declive apunta pero erra y se hunde.
 */
export interface Escenario {
  id: IdEscenario;
  /** Nombre visible del camino. */
  nombre: string;
  /** Qué pasa si el río toma este cauce — con cifras reales. */
  descripcion: string;
  /** Probabilidad heurística 0..1 → opacidad del cauce. */
  probabilidad: number;
  /** Dimensión cuyo patrón sostiene el escenario (null = mixto). */
  sustrato: Dimension | null;
  /** Curva muestreada desde el "ahora" hasta el destino. */
  puntos: Punto3[];
  /** Objetivo al que este cauce pretende llegar, si existe. */
  objetivoAncla: Objetivo | null;
}

/** Datos de entrada para proyectar los escenarios de futuro. */
export interface EntradaEscenarios {
  /** Distribución recency-weighted por dimensión (del backend). */
  distribucion: Record<string, number> | null;
  /** Soporte por dimensión (statsDimension sobre el historial). */
  stats: Record<Dimension, StatsDimension>;
  /** Compromisos abiertos sin cerrar (alimentan el declive). */
  loopsAbiertos: number;
  /** Metas activas del usuario. */
  objetivos: Objetivo[];
  /** Segundos de proyección tras tMax. */
  horizonte: number;
  ctx: CtxTemporal;
  muestras?: number;
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

/**
 * Proyecta los tres escenarios de futuro tras el "ahora":
 *
 * - **continuidad**: el cauce sigue su curso — prob = share de la
 *   dimensión dominante (recency-weighted).
 * - **riesgo**: señales somáticas recientes + loops abiertos hunden
 *   el cauce — prob heurística = base + share somática reciente +
 *   presión de compromisos.
 * - **florecer**: converge al objetivo activo más próximo — prob =
 *   base + impulso de la dimensión ancla + amplitud media.
 *
 * Heurísticas documentadas: no son inferencia estadística dura,
 * son una lectura honesta de los patrones actuales.
 */
export function construirEscenarios(
  entrada: EntradaEscenarios,
): Escenario[] {
  const {
    distribucion,
    stats,
    loopsAbiertos,
    objetivos,
    horizonte,
    ctx,
  } = entrada;
  const muestras = entrada.muestras ?? 40;
  const clamp = (x: number) => Math.min(0.95, Math.max(0.03, x));
  const dims: Dimension[] = ["cognitiva", "somatica", "conductual"];
  const inicio = posEnRio(ctx.tMax, 0, null, ctx);
  const fin = ctx.tMax + horizonte;
  const xFin = xEnTiempo(fin, ctx);

  const dominante = dims.reduce((a, b) =>
    (distribucion?.[b] ?? 0) > (distribucion?.[a] ?? 0) ? b : a,
  );
  const shareDom = distribucion?.[dominante] ?? 1 / 3;
  const recTot = Math.max(
    1,
    dims.reduce((s, d) => s + stats[d].recientes, 0),
  );
  const shareSom = stats.somatica.recientes / recTot;
  const ancla =
    objetivos
      .filter((o) => o.estado === "activo" && o.fecha_objetivo > ctx.tMax)
      .sort((a, b) => a.fecha_objetivo - b.fecha_objetivo)[0] ?? null;
  const sustratoFlor = ancla?.dimension ?? dominante;
  const impulsoFlor = stats[sustratoFlor].recientes / recTot;

  const probs: Record<IdEscenario, number> = {
    continuidad: clamp(shareDom),
    riesgo: clamp(0.06 + shareSom * 0.55 + Math.min(0.35, loopsAbiertos * 0.06)),
    florecer: ancla
      ? clamp(
          0.18 +
            impulsoFlor * 0.5 +
            stats[sustratoFlor].amplitudMedia * 0.2,
        )
      : 0.08,
  };

  const destinoAncla = ancla ? anclaDeObjetivo(ancla, ctx) : null;
  const descripciones: Record<IdEscenario, string> = {
    continuidad:
      `Si tu patrón sigue igual: "${dominante}" domina tu línea ` +
      `(${pct(shareDom)}) con ${stats[dominante].recientes} eventos ` +
      `recientes sosteniendo el rumbo.` +
      (ancla
        ? ` Apunta a ◆ "${ancla.titulo}" pero no llega — el hábito pesa más que la meta.`
        : ""),
    riesgo:
      `Camino de desgaste: ${stats.somatica.recientes} señales ` +
      `somáticas recientes y ${loopsAbiertos} compromisos abiertos ` +
      `presionan el cauce hacia abajo.` +
      (ancla
        ? ` Apunta a ◆ "${ancla.titulo}" pero erra — las señales actuales no lo sostienen.`
        : ""),
    florecer: ancla
      ? `Converge a ◆ "${ancla.titulo}". Impulso ` +
        `${sustratoFlor}: ${stats[sustratoFlor].recientes} eventos ` +
        `recientes amigan esta meta.`
      : "Sin metas fijadas — crea un objetivo para darle destino a este cauce.",
  };

  /**
   * Destinos naturales de cada escenario. Con meta activa, todos
   * apuntan al ancla: florecer llega (1.0), continuidad se acerca
   * sin llegar (0.45) y declive falla quedándose bajo (0.22).
   */
  const tirarHacia = (p: Punto3, f: number): Punto3 =>
    destinoAncla
      ? {
          x: p.x + (destinoAncla.x - p.x) * f,
          y: p.y + (destinoAncla.y - p.y) * f,
          z: p.z + (destinoAncla.z - p.z) * f,
        }
      : p;

  const destinos: Record<IdEscenario, Punto3> = {
    continuidad: tirarHacia(
      {
        x: xFin,
        y: Math.sin(xFin * 0.012) * ctx.cfg.meandroY + 10,
        z: Math.sin(xFin * 0.008 + 1.3) * ctx.cfg.meandroZ + 16,
      },
      0.45,
    ),
    riesgo: tirarHacia(
      {
        x: xEnTiempo(ctx.tMax + horizonte * 0.85, ctx),
        y: -52,
        z: -ctx.cfg.separacionRama * 0.95,
      },
      0.22,
    ),
    // Florecer es el único que llega exactamente al ancla.
    florecer:
      destinoAncla ?? {
        x: xFin,
        y: 58,
        z: ctx.cfg.separacionRama * 0.95,
      },
  };

  const def: { id: IdEscenario; nombre: string; sustrato: Dimension | null }[] = [
    { id: "continuidad", nombre: "Continuidad", sustrato: dominante },
    { id: "riesgo", nombre: "Declive", sustrato: "somatica" },
    { id: "florecer", nombre: "Florecer", sustrato: sustratoFlor },
  ];

  return def.map(({ id, nombre, sustrato }) => {
    const destino = destinos[id];
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
    return {
      id,
      nombre,
      descripcion: descripciones[id],
      probabilidad: probs[id],
      sustrato,
      puntos,
      // Todos los cauces pretenden llegar al objetivo.
      objetivoAncla: ancla,
    };
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

/** Estadísticas de una dimensión para la tarjeta de escenario. */
export interface StatsDimension {
  /** Eventos totales de la dimensión en la ventana cargada. */
  total: number;
  /** Eventos de los últimos `diasRecientes` — el "impulso" del camino. */
  recientes: number;
  /** Amplitud media 0..1 — intensidad emocional del camino. */
  amplitudMedia: number;
}

/**
 * Resume el soporte de una dimensión en el historial: cuántos
 * eventos la alimentan, cuántos son recientes (impulso) y su
 * amplitud media. Puro — alimenta la tarjeta "qué pasa si el
 * cauce va por este camino".
 */
export function statsDimension(
  dimension: Dimension,
  eventos: EventoRio[],
  ahora: number,
  diasRecientes = 30,
): StatsDimension {
  const corte = ahora - diasRecientes * 86_400;
  let total = 0;
  let recientes = 0;
  let sumaAmp = 0;
  for (const e of eventos) {
    if (e.dimension !== dimension) continue;
    total += 1;
    sumaAmp += e.amplitud;
    if (e.ts >= corte) recientes += 1;
  }
  return {
    total,
    recientes,
    amplitudMedia: total ? sumaAmp / total : 0,
  };
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
