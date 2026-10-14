/**
 * Tipos del grafo cerebral 3D — port del plugin DSH Memory a TypeScript.
 *
 * El grafo visualiza eventos Nous como neuronas: el modo de color activo
 * decide si el color sale de la dimensión, el estado de tejido, la
 * fuente o las reglas semánticas; la amplitud da el tamaño y la recencia
 * el pulso.
 */

import type { Dimension, Evento, Relacion } from "@/domain/modelos";
import { colorDeReglas, REGLAS_DEFAULT, type Regla } from "./reglas";

/** Estado de "tejido" de un nodo según su recencia y relevancia. */
export type EstadoNodo =
  | "fresh"
  | "working"
  | "consolidated"
  | "decayed"
  | "dead";

/** Modo de color activo del grafo. */
export type ModoColor = "dimension" | "estado" | "fuente" | "reglas";

/** Modo de etiquetas flotantes. */
export type ModoEtiquetas = "hover" | "hubs" | "all";

/** Nodo del grafo (un evento de memoria). */
export interface Nodo {
  id: string;
  /** Texto corto mostrado como etiqueta flotante. */
  etiqueta: string;
  /** Contenido completo del evento (para el visor de detalle). */
  contenido: string;
  /** Dimensión 4D del evento. */
  dimension: Dimension;
  /** Canal/agente que escribió la huella (eje de procedencia). */
  fuente: string;
  estado: EstadoNodo;
  amplitud: number;
  /** Consolidación por evocación — alimenta el brillo persistente. */
  refuerzos: number;
  timestamp_unix: number;
  /** Caducidad (TTL); null = el recuerdo no vence. */
  expira_en: number | null;
  /** Fijado por arrastre del usuario (persistido). */
  pinned: boolean;
  /** Grado (nº de aristas) — engorda el nodo. */
  deg: number;
  /** Orden de importancia para el nivel de detalle. */
  rank: number;
  /** Etiquetas que enlazan el contenido con otras memorias. */
  tags: string[];
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

/** Fuente de procedencia presente en el grafo (leyenda y filtro). */
export interface Fuente {
  id: string;
  count: number;
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
  /** Fuentes presentes con su conteo, ordenadas por tamaño. */
  fuentes: Fuente[];
  /** Conteo de nodos por estado de tejido (leyenda). */
  stateCounts: Record<EstadoNodo, number>;
  /**
   * Vínculos persistidos por nodo: índice del vecino, tipo y peso.
   * Alimenta el "top de memorias conectadas" del visor de detalle.
   */
  vinculos: Map<number, { idx: number; tipo: string; peso: number }[]>;
  /** Ramas de línea de universo presentes en los datos. */
  ramas: string[];
  /** Unix del evento más antiguo cargado (0 si no hay datos). */
  tMin: number;
  /** Unix del evento más reciente cargado — el "presente" del grafo. */
  tMax: number;
}

/** Posición fijada de un nodo, persistida por el usuario. */
export type PosicionPin = [number, number, number];

/** Preferencias visuales ajustables en vivo, persistidas en PostgreSQL. */
export interface PrefsCerebro {
  physics: boolean;
  membrane: boolean;
  ambient: boolean;
  deepBrain: boolean;
  regions: boolean;
  flow: boolean;
  lod: boolean;
  rotate: number;
  nodeSize: number;
  edgeWidth: number;
  flowSpeed: number;
  memOpacity: number;
  selGlow: number;
  physRep: number;
  physAtt: number;
  physGrav: number;
  query: string;
  labels: ModoEtiquetas;
  /** Modo de color activo del grafo. */
  modoColor: ModoColor;
  /** Sobreescrituras de color por clave del modo activo. */
  colors: Record<string, string>;
  /** Reglas semánticas del modo "reglas" (null = por defecto). */
  rules: Regla[] | null;
  ruleFallback: string;
  /** Fuentes visibles (null = todas). */
  srcFilter: string[] | null;
  /** Máximo de eventos a cargar. */
  srcCap: number;
  /** Rama de línea de universo visible (null = todas). */
  rama: string | null;
  /** Mancha de contaminación de nodos muertos/decaidos en la membrana. */
  contaminacion: boolean;
  /** Posiciones fijadas por arrastre, por id de evento (tope 200). */
  pins: Record<string, PosicionPin>;
}

export const PREFS_DEFAULT: PrefsCerebro = {
  physics: true,
  membrane: true,
  ambient: true,
  deepBrain: true,
  regions: true,
  flow: true,
  lod: true,
  rotate: 1,
  nodeSize: 1,
  edgeWidth: 1,
  flowSpeed: 1,
  memOpacity: 0.75,
  selGlow: 1,
  physRep: 1,
  physAtt: 1,
  physGrav: 1,
  query: "",
  labels: "hover",
  modoColor: "dimension",
  colors: {},
  rules: null,
  ruleFallback: "#5b8ef4",
  srcFilter: null,
  srcCap: 500,
  rama: null,
  contaminacion: true,
  pins: {},
};

/** Máximo de pins persistidos: se descartan los más antiguos. */
export const PINS_MAX = 200;

/** Evento de "pensamiento" externo (p. ej. una consulta recién hecha). */
export interface EventoPensamiento {
  kind: "recall" | "store" | "search" | "session" | "inject";
  q: string;
  ids: string[];
  ts: number;
  /**
   * Replay manual (historial): dibuja la semilla aunque ningún id
   * resuelva — la traza siempre da feedback visual al usuario.
   */
  inmediato?: boolean;
}

export const COLORES_DIMENSION: Record<Dimension, string> = {
  cognitiva: "#5b9ef4",
  somatica: "#e05a8d",
  conductual: "#2dd4bf",
};

/**
 * Paleta del estado de tejido — psicología del aprendizaje:
 * rojo = LTM consolidada, verde = recién codificada (hipocampo),
 * ámbar = memoria de trabajo viva, gris = traza que se desvanece,
 * sepia = sinapsis muerta (candidata a poda).
 */
export const COLORES_ESTADO: Record<EstadoNodo, string> = {
  consolidated: "#ff5a66",
  fresh: "#4ade80",
  working: "#ffb02e",
  decayed: "#8b93a1",
  dead: "#5d4a56",
};

/** Paleta rotativa para las fuentes (procedencia). */
export const COLORES_FUENTE = [
  "#7dd3fc",
  "#c4b5fd",
  "#f9a8d4",
  "#fdba74",
  "#86efac",
  "#fde68a",
];

const ESTADOS_ATENUACION: Record<EstadoNodo, number> = {
  fresh: 1.25,
  working: 1.0,
  consolidated: 0.9,
  decayed: 0.55,
  dead: 0.3,
};

function atenuar(hex: string, f: number): string {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.min(255, Math.round(((n >> 16) & 255) * f));
  const g = Math.min(255, Math.round(((n >> 8) & 255) * f));
  const b = Math.min(255, Math.round((n & 255) * f));
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

/** Color de una fuente por posición en la lista (estable por orden). */
export function colorFuente(fuenteId: string, fuentes: Fuente[]): string {
  const i = fuentes.findIndex((f) => f.id === fuenteId);
  return COLORES_FUENTE[(i < 0 ? 0 : i) % COLORES_FUENTE.length];
}

/** Color de un nodo según el modo activo y las sobreescrituras. */
export function colorNodo(
  n: Nodo,
  modo: ModoColor = "dimension",
  datos?: Pick<DatosCerebro, "fuentes">,
  prefs?: Partial<Pick<PrefsCerebro, "colors" | "rules" | "ruleFallback">>,
): string {
  const clave = modo === "dimension" ? n.dimension
    : modo === "estado" ? `estado:${n.estado}`
    : modo === "fuente" ? `fuente:${n.fuente}`
    : null;
  if (clave && prefs?.colors?.[clave]) return prefs.colors[clave]!;
  switch (modo) {
    case "estado":
      return COLORES_ESTADO[n.estado];
    case "fuente":
      return colorFuente(n.fuente, datos?.fuentes ?? []);
    case "reglas":
      return colorDeReglas(
        n,
        prefs?.rules ?? REGLAS_DEFAULT,
        prefs?.ruleFallback ?? "#5b8ef4",
      );
    default:
      return atenuar(
        COLORES_DIMENSION[n.dimension],
        ESTADOS_ATENUACION[n.estado],
      );
  }
}

const DIA = 86_400;

/**
 * Deriva el estado de tejido de un evento: reconsolidación por
 * refuerzos o amplitud extrema, decaimiento por edad, muerte sináptica
 * para huellas viejas, débiles y nunca evocadas (candidatas a poda).
 */
export function estadoDe(e: Evento, ahora: number): EstadoNodo {
  if (e.expira_en != null && e.expira_en <= ahora) return "dead";
  const edad = ahora - e.timestamp_unix;
  if (e.refuerzos >= 3 || e.amplitud >= 0.9) return "consolidated";
  if (edad < 2 * DIA) return "fresh";
  if (edad < 7 * DIA) return "working";
  if (edad > 45 * DIA && e.refuerzos === 0 && e.amplitud < 0.3) {
    return "dead";
  }
  return edad < 30 * DIA && e.amplitud >= 0.6 ? "consolidated" : "decayed";
}

/**
 * Convierte eventos Nous en nodos, posiciones y aristas del grafo.
 *
 * Las aristas se construyen en tres capas: la cadena temporal
 * (consecutivos), los lazos por dimensión y los vínculos reales
 * persistidos (``relaciones`` — explícitas o inferidas por
 * similitud semántica), que se dibujan como aristas "link".
 */
export function eventosAGrafo(
  eventos: Evento[],
  ahora?: number,
  pins?: Record<string, PosicionPin>,
  relaciones?: Relacion[],
): DatosCerebro {
  const t0 = ahora ?? Math.floor(Date.now() / 1000);
  const vacio: DatosCerebro = {
    status: "vacio",
    nodes: [],
    pts: [],
    edges: [],
    idToIdx: new Map(),
    nbrs: new Map(),
    fuentes: [],
    stateCounts: {
      fresh: 0, working: 0, consolidated: 0, decayed: 0, dead: 0,
    },
    vinculos: new Map(),
    ramas: [],
    tMin: 0,
    tMax: 0,
  };
  if (eventos.length === 0) return vacio;

  const ordenados = [...eventos].sort(
    (a, b) => a.timestamp_unix - b.timestamp_unix,
  );
  const nodes: Nodo[] = ordenados.map((e, i) => ({
    id: e.id,
    etiqueta: e.contenido.slice(0, 40),
    contenido: e.contenido,
    dimension: e.dimension,
    fuente: e.fuente ?? "desconocida",
    estado: estadoDe(e, t0),
    amplitud: e.amplitud,
    refuerzos: e.refuerzos ?? 0,
    timestamp_unix: e.timestamp_unix,
    expira_en: e.expira_en ?? null,
    pinned: pins?.[e.id] !== undefined,
    deg: 0,
    rank: i,
    tags: e.tags ?? [],
  }));

  // Posición inicial: hélice temporal — el tiempo sube en Y, la
  // dimensión reparte el azimut, la amplitud acerca al núcleo. Un pin
  // persistido restaura la posición exacta que dejó el usuario.
  const SECTOR: Record<Dimension, number> = {
    cognitiva: 0,
    somatica: (Math.PI * 2) / 3,
    conductual: (Math.PI * 4) / 3,
  };
  const tMin = ordenados[0].timestamp_unix;
  const tMax = ordenados[ordenados.length - 1].timestamp_unix;
  const span = Math.max(1, tMax - tMin);
  const pts: Punto[] = ordenados.map((e, i) => {
    const pin = pins?.[e.id];
    if (pin) return { x: pin[0], y: pin[1], z: pin[2] };
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
  const pares = new Set<string>();
  const agregar = (a: number, b: number, kind: Arista["kind"]) => {
    const clave = a < b ? `${a}:${b}` : `${b}:${a}`;
    if (pares.has(clave)) return;
    pares.add(clave);
    edges.push({ a, b, kind });
    if (!nbrs.get(a)?.add(b)) nbrs.set(a, new Set([b]));
    if (!nbrs.get(b)?.add(a)) nbrs.set(b, new Set([a]));
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
  // Lazos por tag compartido: memorias que citan la misma etiqueta
  // quedan encadenadas (vínculo semántico estilo Karpathy).
  const porTag = new Map<string, number[]>();
  ordenados.forEach((e, i) => {
    for (const t of e.tags ?? []) {
      const l = porTag.get(t) ?? [];
      l.push(i);
      porTag.set(t, l);
    }
  });
  for (const lista of porTag.values()) {
    for (let i = 1; i < lista.length; i++) {
      agregar(lista[i - 1], lista[i], "tag");
    }
  }
  // Vínculos reales persistidos: explícitos (declarados al registrar)
  // y semánticos (inferidos por similitud al indexarse).
  const vinculos = new Map<number, { idx: number; tipo: string; peso: number }[]>();
  if (relaciones) {
    const idx = new Map(nodes.map((n, i) => [n.id, i]));
    for (const r of relaciones) {
      const a = idx.get(r.origen_id);
      const b = idx.get(r.destino_id);
      if (a !== undefined && b !== undefined) {
        agregar(a, b, "link");
        (vinculos.get(a) ?? vinculos.set(a, []).get(a)!).push({
          idx: b, tipo: r.tipo, peso: r.peso,
        });
        (vinculos.get(b) ?? vinculos.set(b, []).get(b)!).push({
          idx: a, tipo: r.tipo, peso: r.peso,
        });
      }
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

  const conteoFuentes = new Map<string, number>();
  const stateCounts: Record<EstadoNodo, number> = {
    fresh: 0, working: 0, consolidated: 0, decayed: 0, dead: 0,
  };
  for (const n of nodes) {
    conteoFuentes.set(n.fuente, (conteoFuentes.get(n.fuente) ?? 0) + 1);
    stateCounts[n.estado]++;
  }
  const fuentes = [...conteoFuentes.entries()]
    .map(([id, count]) => ({ id, count }))
    .sort((a, b) => b.count - a.count);
  const ramas = [...new Set(ordenados.map((e) => e.rama ?? "main"))];

  return {
    status: "ready",
    nodes,
    pts,
    edges,
    idToIdx: new Map(nodes.map((n, i) => [n.id, i])),
    nbrs,
    fuentes,
    stateCounts,
    vinculos,
    ramas,
    tMin,
    tMax,
  };
}

/**
 * Estado de tejido que tenía (o tendrá) un nodo en el instante `t`:
 * es `estadoDe` evaluado sobre la edad que el evento tenía en ese
 * momento — permite rebobinar el grafo y ver estados ya pasados.
 * Los refuerzos se aproximan con el valor actual (no hay histórico).
 */
export function estadoEn(n: Nodo, t: number): EstadoNodo {
  if (n.expira_en != null && n.expira_en <= t) return "dead";
  const edad = Math.max(0, t - n.timestamp_unix);
  if (n.refuerzos >= 3 || n.amplitud >= 0.9) return "consolidated";
  if (edad < 2 * DIA) return "fresh";
  if (edad < 7 * DIA) return "working";
  if (edad > 45 * DIA && n.refuerzos === 0 && n.amplitud < 0.3) {
    return "dead";
  }
  return edad < 30 * DIA && n.amplitud >= 0.6 ? "consolidated" : "decayed";
}

/**
 * Factor de materialización del nodo en el cursor temporal:
 * 0 antes de nacer, 1 una vez consolidado en la línea, y en medio
 * un smoothstep — la memoria "nace" expandiéndose al scrubbear.
 */
export function factorNacimiento(
  ts: number,
  cursor: number,
  ventana: number,
): number {
  const f = Math.min(1, Math.max(0, (cursor - ts) / Math.max(1, ventana)));
  return f * f * (3 - 2 * f);
}
