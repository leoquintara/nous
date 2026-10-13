/**
 * Tipos del grafo cerebral 3D — port del plugin DSH Memory a TypeScript.
 *
 * El grafo visualiza eventos Nous como neuronas: la dimensión define el
 * color, la amplitud el tamaño, la recencia el estado (brillo y pulso).
 */

import type { Dimension, Evento } from "@/domain/modelos";

/** Estado de "tejido" de un nodo según su recencia y relevancia. */
export type EstadoNodo =
  | "fresh"
  | "working"
  | "consolidated"
  | "decayed"
  | "dead";

/** Nodo del grafo (un evento de memoria). */
export interface Nodo {
  id: string;
  /** Texto corto mostrado como etiqueta flotante. */
  etiqueta: string;
  /** Dimensión 4D del evento (fuente de color). */
  dimension: Dimension;
  estado: EstadoNodo;
  amplitud: number;
  /** Consolidación por evocación — alimenta el brillo persistente. */
  refuerzos: number;
  timestamp_unix: number;
  /** Fijado por arrastre del usuario. */
  pinned: boolean;
  /** Grado (nº de aristas) — engorda el nodo. */
  deg: number;
  /** Orden de importancia para el nivel de detalle. */
  rank: number;
}

/** Arista entre dos nodos. */
export interface Arista {
  a: number;
  b: number;
  /** "link" = racha temporal, "tag" = misma dimensión, "core" = hub. */
  kind: "link" | "tag" | "core";
}

/** Punto espacial mutable que la física actualiza por frame. */
export interface Punto {
  x: number;
  y: number;
  z: number;
}

/** Datos listos para renderizar. */
export interface DatosCerebro {
  status: "cargando" | "vacio" | "ready";
  nodes: Nodo[];
  pts: Punto[];
  edges: Arista[];
  /** id de evento → índice de nodo. */
  idToIdx: Map<string, number>;
  /** Vecinos por índice de nodo (para spotlight). */
  nbrs: Map<number, Set<number>>;
}

/** Preferencias visuales ajustables en vivo. */
export interface PrefsCerebro {
  physics: boolean;
  membrane: boolean;
  ambient: boolean;
  deepBrain: boolean;
  flow: boolean;
  lod: boolean;
  rotate: number;
  nodeSize: number;
  edgeWidth: number;
  selGlow: number;
  physRep: number;
  physAtt: number;
  physGrav: number;
  query: string;
}

export const PREFS_DEFAULT: PrefsCerebro = {
  physics: true,
  membrane: true,
  ambient: true,
  deepBrain: true,
  flow: true,
  lod: true,
  rotate: 1,
  nodeSize: 1,
  edgeWidth: 1,
  selGlow: 1,
  physRep: 1,
  physAtt: 1,
  physGrav: 1,
  query: "",
};

/** Evento de "pensamiento" externo (p. ej. una consulta recién hecha). */
export interface EventoPensamiento {
  kind: "recall" | "store" | "search" | "session" | "inject";
  q: string;
  ids: string[];
  ts: number;
}

const COLORES_DIMENSION: Record<Dimension, string> = {
  cognitiva: "#5b9ef4",
  somatica: "#e05a8d",
  conductual: "#2dd4bf",
};

const ESTADOS_ATENUACION: Record<EstadoNodo, number> = {
  fresh: 1.25,
  working: 1.0,
  consolidated: 0.9,
  decayed: 0.55,
  dead: 0.3,
};

/** Color de un nodo: dimensión atenuada por su estado de tejido. */
export function colorNodo(n: Nodo): string {
  const base = COLORES_DIMENSION[n.dimension];
  const f = ESTADOS_ATENUACION[n.estado];
  const hex = parseInt(base.slice(1), 16);
  const r = Math.min(255, Math.round(((hex >> 16) & 255) * f));
  const g = Math.min(255, Math.round(((hex >> 8) & 255) * f));
  const b = Math.min(255, Math.round((hex & 255) * f));
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

const DIA = 86_400;

/** Deriva el estado de tejido de un evento por edad, amplitud y refuerzos. */
function estadoDe(e: Evento, ahora: number): EstadoNodo {
  const edad = ahora - e.timestamp_unix;
  if (edad < 2 * DIA) return "fresh";
  // Reconsolidación: una huella evocada ≥3 veces resiste el olvido.
  if (e.refuerzos >= 3) return "consolidated";
  if (edad < 7 * DIA) return "working";
  if (edad < 30 * DIA) return e.amplitud >= 0.6 ? "consolidated" : "decayed";
  return "decayed";
}

/** Convierte eventos Nous en nodos, posiciones y aristas del grafo. */
export function eventosAGrafo(eventos: Evento[], ahora?: number): DatosCerebro {
  const t0 = ahora ?? Math.floor(Date.now() / 1000);
  if (eventos.length === 0) {
    return {
      status: "vacio",
      nodes: [],
      pts: [],
      edges: [],
      idToIdx: new Map(),
      nbrs: new Map(),
    };
  }

  const ordenados = [...eventos].sort(
    (a, b) => a.timestamp_unix - b.timestamp_unix,
  );
  const nodes: Nodo[] = ordenados.map((e, i) => ({
    id: e.id,
    etiqueta: e.contenido.slice(0, 40),
    dimension: e.dimension,
    estado: estadoDe(e, t0),
    amplitud: e.amplitud,
    refuerzos: e.refuerzos ?? 0,
    timestamp_unix: e.timestamp_unix,
    pinned: false,
    deg: 0,
    rank: i,
  }));

  // Posición inicial: hélice temporal — el tiempo sube en Y, la
  // dimensión reparte el azimut, la amplitud acerca al núcleo.
  const SECTOR: Record<Dimension, number> = {
    cognitiva: 0,
    somatica: (Math.PI * 2) / 3,
    conductual: (Math.PI * 4) / 3,
  };
  const tMin = ordenados[0].timestamp_unix;
  const tMax = ordenados[ordenados.length - 1].timestamp_unix;
  const span = Math.max(1, tMax - tMin);
  const pts: Punto[] = ordenados.map((e, i) => {
    const ft = (e.timestamp_unix - tMin) / span;
    const ang = SECTOR[e.dimension] + i * 0.35;
    const radio = 60 + (1 - e.amplitud) * 260;
    return {
      x: Math.cos(ang) * radio + (Math.random() - 0.5) * 40,
      y: (ft - 0.5) * 320 + (Math.random() - 0.5) * 30,
      z: Math.sin(ang) * radio + (Math.random() - 0.5) * 40,
    };
  });

  // Aristas: rachas temporales (consecutivos) + lazos por dimensión.
  const edges: Arista[] = [];
  const nbrs = new Map<number, Set<number>>();
  const agregar = (a: number, b: number, kind: Arista["kind"]) => {
    edges.push({ a, b, kind });
    nbrs.get(a)?.add(b) ?? nbrs.set(a, new Set([b]));
    nbrs.get(b)?.add(a) ?? nbrs.set(b, new Set([a]));
  };
  for (let i = 0; i < ordenados.length; i++) nbrs.set(i, new Set());
  for (let i = 1; i < ordenados.length; i++) {
    agregar(i - 1, i, "link");
  }
  const porDim = new Map<Dimension, number[]>();
  ordenados.forEach((e, i) => {
    const l = porDim.get(e.dimension) ?? [];
    l.push(i);
    porDim.set(e.dimension, l);
  });
  for (const lista of porDim.values()) {
    for (let i = 1; i < lista.length; i++) {
      agregar(lista[i - 1], lista[i], "tag");
    }
  }
  for (const e of edges) {
    nodes[e.a].deg++;
    nodes[e.b].deg++;
  }
  // rank por grado+amplitud para el LOD.
  const ranking = [...nodes].sort(
    (a, b) => b.deg + b.amplitud - (a.deg + a.amplitud),
  );
  ranking.forEach((n, pos) => {
    n.rank = pos;
  });

  return {
    status: "ready",
    nodes,
    pts,
    edges,
    idToIdx: new Map(nodes.map((n, i) => [n.id, i])),
    nbrs,
  };
}
