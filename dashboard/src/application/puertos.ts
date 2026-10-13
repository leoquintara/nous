/**
 * Puertos del dashboard — contratos que la capa de infraestructura
 * implementa y que la UI consume. Ningún componente toca fetch directo.
 */

import type {
  BenchmarkRun,
  Dimension,
  Evento,
  Proyeccion,
  ResultadoBusqueda,
} from "@/domain/modelos";

/** Petición de creación de evento. */
export interface NuevoEvento {
  contenido: string;
  dimension?: Dimension;
  fuente?: string;
  amplitud?: number;
  frecuencia?: number | null;
}

/** Puerto de acceso a la memoria Nous. */
export interface PuertoMemoria {
  /** Lista eventos del rango temporal en orden cronológico. */
  listarEventos(desdeUnix: number, hastaUnix: number, limite?: number): Promise<Evento[]>;
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
}

/** Puerto de autenticación del dashboard. */
export interface PuertoAuth {
  /** Intercambia usuario/contraseña por el token de servicio. */
  login(usuario: string, password: string): Promise<string>;
  /** Verifica que un token Bearer es válido contra la API. */
  verificarToken(token: string): Promise<boolean>;
}
