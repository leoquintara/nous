/**
 * Entidades del dominio Nous — tipos puros, sin dependencias externas.
 *
 * Espejan los dicts JSON que devuelve la API REST del backend Python.
 */

/** Dimensión cognitiva 4D de un evento. */
export type Dimension = "cognitiva" | "somatica" | "conductual";

/** Evento persistido en la línea de universo. */
export interface Evento {
  id: string;
  timestamp_unix: number;
  dimension: Dimension;
  fuente: string | null;
  contenido: string;
  amplitud: number;
  frecuencia: number | null;
  /** Veces que la memoria fue evocada (consolidación por recuerdo). */
  refuerzos: number;
}

/** Contexto temporal adyacente a un resultado de búsqueda. */
export interface ContextoTemporal {
  previos: Evento[];
  siguientes: Evento[];
}

/** Un hit de la búsqueda híbrida 4D. */
export interface ResultadoBusqueda {
  evento: Evento;
  score_final: number;
  score_bm25: number;
  score_vectorial: number;
  contexto_temporal: ContextoTemporal | null;
}

/** Racha dimensional detectada en el historial. */
export interface Trayectoria {
  tipo: string;
  fecha_inicio: number;
  fecha_fin: number;
  descripcion: string;
  eventos: number;
  amplitud_promedio: number;
  confianza: number;
  activa: boolean;
}

/** Proyección persistida o recién generada. */
export interface Proyeccion {
  id: string;
  horizonte_dias: number;
  dimension_probable: string;
  probabilidad: number;
  distribucion: Record<string, number>;
  eventos_analizados?: number;
  trayectorias?: Trayectoria[];
  creado_en?: string;
}

/** Evento de actividad cerebral reciente (store/recall) para el grafo vivo. */
export interface EventoActividad {
  kind: "store" | "recall" | "search" | "session" | "inject";
  /** Texto que originó la actividad (truncado a 80 chars por el backend). */
  q: string;
  /** UUIDs de los eventos implicados. */
  ids: string[];
  /** Timestamp Unix en segundos. */
  ts: number;
}

/** Vínculo entre dos eventos de la línea de universo. */
export interface Relacion {
  origen_id: string;
  destino_id: string;
  /** "explicita" = declarada al registrar; "semantica" = inferida por embedding. */
  tipo: "explicita" | "semantica";
  peso: number;
}

/** Documento de preferencias persistido en PostgreSQL (clave-valor JSON). */
export type Preferencias = Record<string, unknown>;

/** Corrida de benchmark persistida. */
export interface BenchmarkRun {
  id: string;
  nombre: string;
  escenario: string | null;
  parametros: Record<string, unknown>;
  metricas: Record<string, unknown>;
  git_commit: string | null;
  creado_en: string;
}
