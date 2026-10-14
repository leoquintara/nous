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
  /** Etiquetas que enlazan contenido (explícitas + #hashtags). */
  tags: string[];
  /** Línea de universo a la que pertenece el evento. */
  rama: string;
  /** Clase epistémica: evento, hecho, creencia, compromiso, preferencia. */
  tipo: string;
  /** Timestamp Unix de caducidad (TTL); null = no expira. */
  expira_en: number | null;
  /** UUID del evento que lo sustituye (supersede); null = vigente. */
  reemplazado_por: string | null;
  /** Contraparte del compromiso (solo tipo="compromiso"). */
  con_quien: string | null;
}

/** Tag agregado con su frecuencia de uso. */
export interface TagConteo {
  tag: string;
  eventos: number;
}

/** Fuente de datos administrable registrada en el backend. */
export interface FuenteInfo {
  nombre: string;
  tipo: string;
  estado: "activa" | "archivada";
  /** Sub-agrupación dentro de la fuente (p. ej. "obsidian/vault-x"). */
  namespace: string;
  /** Rama de la línea de universo donde nació la fuente. */
  rama_origen: string;
  metadata: Record<string, unknown>;
  eventos: number;
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
  /** "explicita"/"semantica" del tejido; "cierra"/"reemplaza" del ciclo de vida. */
  tipo: "explicita" | "semantica" | "cierra" | "reemplaza";
  peso: number;
}

/** Meta del usuario que ancla una proyección futura del río temporal. */
export interface Objetivo {
  id: string;
  titulo: string;
  descripcion: string | null;
  /** Dimensión a la que converge; null = objetivo transversal. */
  dimension: Dimension | null;
  /** Fecha límite (Unix epoch, segundos). */
  fecha_objetivo: number;
  /** Ciclo de vida auditable — nunca se borra en duro salvo petición. */
  estado: "activo" | "cumplido" | "abandonado";
  /** Línea de universo donde vive el objetivo. */
  rama: string;
  /** Evento conductual que registró el cierre (auditoría). */
  evento_cierre_id: string | null;
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

/**
 * Proveedor de IA externo configurado (endpoint OpenAI-compatible).
 * La API key nunca sale del backend — solo su máscara.
 */
export interface ProveedorIA {
  id: string;
  nombre: string;
  base_url: string;
  modelo: string;
  /** Si participa en el motor predictivo. */
  habilitado: boolean;
  /** Opt-in: enviar fragmentos de memorias además de agregados. */
  incluir_contenido: boolean;
  /** Hay key cifrada almacenada. */
  api_key_set: boolean;
  /** Máscara visible de la key (``sk-…wxyz``); null si no hay. */
  api_key_mascara: string | null;
  /** Es el proveedor por defecto del motor predictivo. */
  predeterminado: boolean;
  /** id del proveedor de reserva si este falla. */
  fallback_id: string | null;
}

/** Datos de alta/edición de un proveedor (api_key opcional al editar). */
export interface NuevoProveedorIA {
  nombre: string;
  base_url: string;
  modelo: string;
  api_key?: string;
  habilitado: boolean;
  incluir_contenido: boolean;
  /** Usar como proveedor por defecto (solo puede haber uno). */
  predeterminado?: boolean;
  /** id del proveedor de reserva si el primario falla. */
  fallback_id?: string | null;
}

/** Resultado de la prueba de conexión con un proveedor. */
export interface PruebaProveedor {
  ok: boolean;
  modelos?: string[];
  detalle?: string;
}

/** Escenario de futuro devuelto por el motor predictivo. */
export interface EscenarioIA {
  id: string | null;
  nombre: string;
  probabilidad: number;
  descripcion: string;
  sustrato: Dimension | null;
}

/** Respuesta del motor predictivo (degrada con ok=false + motivo). */
export interface PrediccionIA {
  ok: boolean;
  motivo?: string;
  escenarios?: EscenarioIA[];
  razonamiento?: string;
  proveedor?: string;
  modelo?: string;
  latencia_ms?: number;
  /** True si respondió el proveedor de reserva, no el primario. */
  fallback?: boolean;
}

/** Una llamada registrada a Nous (trazabilidad — solo metadata). */
export interface Llamada {
  id: string;
  ts: number;
  metodo: string;
  ruta: string;
  /** Quién llama: dashboard, agente:cursor, usuario:navegador… */
  origen: string;
  /** User-Agent crudo truncado. */
  agente: string;
  latencia_ms: number;
  status: number;
  es_ping: boolean;
}

/** Agregados del registro de llamadas para el panel de trazabilidad. */
export interface LlamadasStats {
  total: number;
  por_ruta: { ruta: string; llamadas: number; latencia_media: number }[];
  por_origen: { origen: string; llamadas: number }[];
  pings: { total: number; ultimo: number | null };
}

/** Parámetros de una migración de memoria hacia Nous. */
export interface ParametrosImportacion {
  formato: "auto" | "jsonl" | "json" | "obsidian" | "gbrain" | "memlord";
  /** Ruta del archivo/directorio en la máquina del backend. */
  ruta?: string;
  /** Endpoint MCP streamable-HTTP de la instancia (gbrain/memlord). */
  url?: string;
  /** Bearer token de la instancia remota. */
  token?: string;
  fuente?: string;
  rama?: string;
  /** Solo lee y anticipa contadores — no escribe nada. */
  vista_previa?: boolean;
}

/** Contadores devueltos por una migración. */
export interface ResultadoImportacion {
  importados: number;
  omitidos: number;
  errores: number;
  vinculos: number;
  formato: string;
  fuente: string;
  /** True si fue solo lectura — ``nuevas`` anticipa la ejecución real. */
  vista_previa?: boolean;
  /** Filas que entrarían al ejecutar (solo en vista previa). */
  nuevas?: number;
}

/** Entrada del historial de migraciones persistido en el backend. */
export interface HistorialImportacion {
  ts: number;
  formato: string;
  fuente: string;
  ruta: string;
  resultado: {
    importados: number;
    omitidos: number;
    errores: number;
    vinculos: number;
  };
}

/** Candidato heurístico que el LLM re-pondera en /universo. */
export interface CandidatoPredictivo {
  id: string;
  nombre: string;
  probabilidad: number;
  descripcion: string;
}
