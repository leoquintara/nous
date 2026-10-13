/**
 * Puertos del dashboard — contratos que la capa de infraestructura
 * implementa y que la UI consume. Ningún componente toca fetch directo.
 */

import type {
  BenchmarkRun,
  Dimension,
  Evento,
  EventoActividad,
  FuenteInfo,
  Preferencias,
  Proyeccion,
  Relacion,
  ResultadoBusqueda,
  TagConteo,
} from "@/domain/modelos";

/** Petición de creación de evento. */
export interface NuevoEvento {
  contenido: string;
  dimension?: Dimension;
  fuente?: string;
  amplitud?: number;
  frecuencia?: number | null;
  tags?: string[];
  rama?: string;
}

/** Petición de creación de fuente administrable. */
export interface NuevaFuente {
  nombre: string;
  tipo?: string;
  namespace?: string;
  rama_origen?: string;
  metadata?: Record<string, unknown>;
}

/** Puerto de acceso a la memoria Nous. */
export interface PuertoMemoria {
  /** Lista eventos del rango temporal en orden cronológico. */
  listarEventos(desdeUnix: number, hastaUnix: number, limite?: number, rama?: string): Promise<Evento[]>;
  /** Lista los vínculos entre eventos (explícitos y semánticos). */
  listarRelaciones(limite?: number): Promise<Relacion[]>;
  /** Lista los tags con su frecuencia de uso. */
  listarTags(limite?: number): Promise<TagConteo[]>;
  /** Lista las fuentes registradas con su conteo de eventos. */
  listarFuentes(): Promise<FuenteInfo[]>;
  /** Registra una fuente nueva (idempotente por nombre). */
  crearFuente(fuente: NuevaFuente): Promise<FuenteInfo>;
  /** Activa o archiva una fuente (los eventos no se tocan). */
  estadoFuente(nombre: string, estado: "activa" | "archivada"): Promise<void>;
  /** Elimina el registro de una fuente; sus eventos permanecen. */
  eliminarFuente(nombre: string): Promise<void>;
  /** Crea una rama de línea de universo derivada de la fuente. */
  ramificarFuente(nombre: string, rama: string, namespace?: string): Promise<FuenteInfo>;
  /** Ejecuta la búsqueda híbrida 4D con contexto temporal. */
  buscar(consulta: string, topN?: number): Promise<ResultadoBusqueda[]>;
  /** Persiste un evento nuevo. */
  registrarEvento(evento: NuevoEvento): Promise<{ id: string; dimension: string }>;
  /** Genera y persiste una proyección dimensional. */
  proyectar(horizonteDias: number, diasHistorial: number): Promise<Proyeccion>;
  /** Devuelve la última proyección persistida sin recalcular. */
  proyeccionActual(): Promise<Proyeccion | { estado: "sin_proyecciones" }>;
  /** Lista corridas de benchmark persistidas. */
  benchmarks(nombre?: string, limite?: number): Promise<BenchmarkRun[]>;
  /** Actividad cerebral reciente (store/recall) para el grafo vivo. */
  actividad(limite?: number): Promise<EventoActividad[]>;
  /** Lee un documento de preferencias persistido en PostgreSQL. */
  leerPreferencias(clave: string): Promise<Preferencias>;
  /** Persiste un documento de preferencias en PostgreSQL. */
  guardarPreferencias(clave: string, valor: Preferencias): Promise<void>;
}

/** Puerto de autenticación del dashboard. */
export interface PuertoAuth {
  /** Intercambia usuario/contraseña por el token de servicio. */
  login(usuario: string, password: string): Promise<string>;
  /** Verifica que un token Bearer es válido contra la API. */
  verificarToken(token: string): Promise<boolean>;
}
