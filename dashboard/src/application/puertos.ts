/**
 * Puertos del dashboard — contratos que la capa de infraestructura
 * implementa y que la UI consume. Ningún componente toca fetch directo.
 */

import type {
  BenchmarkRun,
  CandidatoPredictivo,
  Dimension,
  Evento,
  EventoActividad,
  FuenteInfo,
  HistorialImportacion,
  Identidad,
  Llamada,
  LlamadasStats,
  Llave,
  Miembro,
  NuevoProveedorIA,
  Objetivo,
  ParametrosImportacion,
  PrediccionIA,
  Preferencias,
  ProveedorIA,
  Proyeccion,
  PruebaProveedor,
  Relacion,
  ResultadoBusqueda,
  ResultadoCanje,
  ResultadoImportacion,
  RolWorkspace,
  TagConteo,
  Workspace,
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
  /** Visibilidad — por defecto personal (privada). */
  scope?: "personal" | "workspace";
}

/** Petición de creación de fuente administrable. */
export interface NuevaFuente {
  nombre: string;
  tipo?: string;
  namespace?: string;
  rama_origen?: string;
  metadata?: Record<string, unknown>;
}

/** Petición de creación de objetivo sobre la línea de universo. */
export interface NuevoObjetivo {
  titulo: string;
  fecha_objetivo: number;
  dimension?: Dimension | null;
  descripcion?: string | null;
  rama?: string;
}

/** Cambios parciales sobre un objetivo existente. */
export interface ParcheObjetivo {
  titulo?: string;
  descripcion?: string | null;
  fecha_objetivo?: number;
  dimension?: Dimension | null;
  estado?: Objetivo["estado"];
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
  /** Lista los objetivos del usuario (metas que anclan el futuro). */
  listarObjetivos(estado?: string, rama?: string): Promise<Objetivo[]>;
  /** Fija una meta en la línea de universo. */
  crearObjetivo(objetivo: NuevoObjetivo): Promise<Objetivo>;
  /** Aplica cambios parciales a un objetivo existente. */
  actualizarObjetivo(id: string, parche: ParcheObjetivo): Promise<Objetivo>;
  /** Cierra un objetivo (cumplido/abandonado) con resolución opcional. */
  cerrarObjetivo(id: string, estado: "cumplido" | "abandonado", resolucion?: string): Promise<Objetivo>;
  /** Lista corridas de benchmark persistidas. */
  benchmarks(nombre?: string, limite?: number): Promise<BenchmarkRun[]>;
  /** Actividad cerebral reciente (store/recall) para el grafo vivo. */
  actividad(limite?: number): Promise<EventoActividad[]>;
  /** Lee un documento de preferencias persistido en PostgreSQL. */
  leerPreferencias(clave: string): Promise<Preferencias>;
  /** Persiste un documento de preferencias en PostgreSQL. */
  guardarPreferencias(clave: string, valor: Preferencias): Promise<void>;
  /** Lista los proveedores de IA configurados (keys enmascaradas). */
  listarProveedoresIA(): Promise<ProveedorIA[]>;
  /** Crea o actualiza un proveedor de IA. */
  guardarProveedorIA(id: string, datos: NuevoProveedorIA): Promise<ProveedorIA>;
  /** Elimina un proveedor de IA y su key cifrada. */
  eliminarProveedorIA(id: string): Promise<void>;
  /** Prueba conectividad con el endpoint del proveedor. */
  probarProveedorIA(id: string): Promise<PruebaProveedor>;
  /** Pide escenarios de futuro al motor predictivo (degrada a ok=false). */
  predecirIA(entrada: {
    origen_unix?: number;
    horizonte_dias?: number;
    candidatos?: CandidatoPredictivo[];
    proveedor_id?: string;
  }): Promise<PrediccionIA>;
  /** Lista llamadas registradas a Nous (trazabilidad). */
  listarLlamadas(limite?: number, ruta?: string): Promise<Llamada[]>;
  /** Agregados del registro: por ruta, por origen y pings. */
  statsLlamadas(): Promise<LlamadasStats>;
  /** Registra un ping explícito de conectividad. */
  ping(): Promise<{ status: string; ts: number }>;
  /** Migra memoria externa (memlord, gbrain, obsidian…) a Nous. */
  importarMemoria(
    params: ParametrosImportacion,
  ): Promise<ResultadoImportacion>;
  /** Últimas migraciones ejecutadas (máx. 15). */
  historialImportaciones(): Promise<HistorialImportacion[]>;
}

/** Puerto de autenticación del dashboard. */
export interface PuertoAuth {
  /** Intercambia usuario/contraseña por el token de servicio. */
  login(usuario: string, password: string): Promise<string>;
  /** Verifica que un token Bearer es válido contra la API. */
  verificarToken(token: string): Promise<boolean>;
}

/** Puerto de identidad multi-tenant: workspaces, equipo y llaves. */
export interface PuertoIdentidad {
  /** Quién es el llamante y a qué workspaces pertenece. */
  identidad(): Promise<Identidad>;
  /** Crea un workspace; el llamante queda como owner. */
  crearWorkspace(slug: string, nombre: string): Promise<Workspace>;
  /** Roster del workspace (miembros con rol). */
  listarMiembros(ref: string): Promise<Miembro[]>;
  /** Cambia el rol de un miembro — solo owners. */
  cambiarRol(ref: string, usuarioId: string, rol: RolWorkspace): Promise<void>;
  /** Expulsa un miembro — solo owners; sus memorias permanecen. */
  quitarMiembro(ref: string, usuarioId: string): Promise<void>;
  /** Emite un token de invitación (una sola vez visible). */
  invitar(ref: string, rol: "member" | "viewer", dias?: number): Promise<{ token: string }>;
  /** Llaves del llamante (sin secretos). */
  listarLlaves(): Promise<Llave[]>;
  /** Emite una key propia — en claro una sola vez. */
  crearLlave(nombre?: string, workspaceDefaultId?: string): Promise<{ key: string }>;
  /** Revoca una key propia por UUID o prefijo. */
  revocarLlave(ref: string): Promise<void>;
}

/** Puerto público del canje de invitación (sin Bearer). */
export interface PuertoInvitacion {
  /** Canjea un token ns_inv_* creando usuario + membresía + key. */
  canjear(entrada: {
    token: string;
    handle: string;
    nombre: string;
    email?: string;
  }): Promise<ResultadoCanje>;
}
