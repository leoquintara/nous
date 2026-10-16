/**
 * Adaptador HTTP de la API REST de Nous.
 *
 * Implementa PuertoMemoria y PuertoAuth contra los endpoints reales;
 * inyecta el Bearer token configurado por el operador.
 */

import type {
  PuertoAuth,
  PuertoIdentidad,
  PuertoInvitacion,
  PuertoMemoria,
} from "@/application/puertos";
import {
  NuevaFuente,
  NuevoEvento,
  NuevoObjetivo,
  ParcheObjetivo,
} from "@/application/puertos";
import type {
  BenchmarkRun,
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

// El dashboard lo sirve el propio servidor Nous: misma origen siempre.
// Así funciona idéntico por localhost, 127.0.0.1, IP LAN o dominio,
// sin violar el CSP `connect-src 'self'` ni mezclar orígenes.
const BASE_URL =
  process.env.NEXT_PUBLIC_NOUS_API ??
  (typeof window !== "undefined"
    ? window.location.origin
    : "http://127.0.0.1:8720");

/** Fetch con diagnóstico: un TypeError de red se traduce a causa probable. */
async function fetchNous(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new Error(
      `No se pudo contactar a Nous en ${BASE_URL} — ¿está corriendo el servidor?`,
    );
  }
}

/** Añade el override de workspace a una ruta con o sin query. */
function conWorkspace(ruta: string, workspace?: string | null): string {
  if (!workspace) return ruta;
  const sep = ruta.includes("?") ? "&" : "?";
  return `${ruta}${sep}workspace=${encodeURIComponent(workspace)}`;
}

/** Ejecuta una petición autenticada y devuelve el JSON o lanza. */
async function peticion<T>(
  ruta: string,
  token: string,
  init?: RequestInit,
  workspace?: string | null,
): Promise<T> {
  const res = await fetchNous(`${BASE_URL}${conWorkspace(ruta, workspace)}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "x-nous-origen": "dashboard",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`Nous API ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as T;
}

/** Crea el adaptador de memoria ligado a un token y workspace. */
export function crearClienteNous(
  token: string,
  workspace?: string | null,
): PuertoMemoria {
  /** Peticion ligada al token y al workspace activo. */
  const req = <T,>(ruta: string, init?: RequestInit): Promise<T> =>
    peticion<T>(ruta, token, init, workspace);
  return {
    listarEventos: (desde, hasta, limite = 500, rama) =>
      req<Evento[]>(
        `/api/eventos?desde_unix=${desde}&hasta_unix=${hasta}&limite=${limite}` +
          (rama ? `&rama=${encodeURIComponent(rama)}` : "") +
          "&repartir=true",
      ),
    listarRelaciones: (limite = 2000) =>
      req<Relacion[]>(`/api/relaciones?limite=${limite}`),
    listarTags: (limite = 200) =>
      req<TagConteo[]>(`/api/tags?limite=${limite}`),
    listarFuentes: () => req<FuenteInfo[]>(`/api/fuentes`),
    crearFuente: (fuente: NuevaFuente) =>
      req<FuenteInfo>(`/api/fuentes`, {
        method: "POST",
        body: JSON.stringify(fuente),
      }),
    estadoFuente: async (nombre, estado) => {
      await req<{ nombre: string }>(
        `/api/fuentes/${encodeURIComponent(nombre)}`,
        { method: "PATCH", body: JSON.stringify({ estado }) },
      );
    },
    eliminarFuente: async (nombre) => {
      await req<{ nombre: string }>(
        `/api/fuentes/${encodeURIComponent(nombre)}`,
        { method: "DELETE" },
      );
    },
    ramificarFuente: (nombre, rama, namespace = "") =>
      req<FuenteInfo>(
        `/api/fuentes/${encodeURIComponent(nombre)}/ramificar`,
        { method: "POST", body: JSON.stringify({ rama, namespace }) },
      ),
    buscar: (consulta, topN = 10) =>
      req<ResultadoBusqueda[]>(
        `/api/buscar?q=${encodeURIComponent(consulta)}&top_n=${topN}`,
      ),
    registrarEvento: (evento: NuevoEvento) =>
      req(`/api/eventos`, {
        method: "POST",
        body: JSON.stringify({
          fuente: "dashboard",
          ...evento,
          workspace,
        }),
      }),
    proyectar: (horizonte, historial) =>
      req<Proyeccion>(
        `/api/proyeccion?horizonte_dias=${horizonte}&dias_historial=${historial}`,
        { method: "POST" },
      ),
    proyeccionActual: () =>
      req<Proyeccion>(`/api/proyeccion/actual`),
    listarObjetivos: (estado = "activo", rama = "main") =>
      req<Objetivo[]>(
        `/api/objetivos?estado=${encodeURIComponent(estado)}&rama=${encodeURIComponent(rama)}`,
      ),
    crearObjetivo: (objetivo: NuevoObjetivo) =>
      req<Objetivo>(`/api/objetivos`, {
        method: "POST",
        body: JSON.stringify({ ...objetivo, workspace }),
      }),
    actualizarObjetivo: (id, parche: ParcheObjetivo) =>
      req<Objetivo>(
        `/api/objetivos/${encodeURIComponent(id)}`,
        { method: "PATCH", body: JSON.stringify(parche) },
      ),
    cerrarObjetivo: (id, estado, resolucion) =>
      req<Objetivo>(
        `/api/objetivos/${encodeURIComponent(id)}/cerrar`,
        {
          method: "POST",
          body: JSON.stringify({ estado, resolucion }),
        },
      ),
    benchmarks: (nombre, limite = 50) => {
      const params = new URLSearchParams({ limite: String(limite) });
      if (nombre) params.set("nombre", nombre);
      return req<BenchmarkRun[]>(`/api/benchmarks?${params}`);
    },
    actividad: (limite = 50) =>
      req<EventoActividad[]>(
        `/api/actividad?limite=${limite}`,
      ),
    leerPreferencias: (clave) =>
      req<Preferencias>(
        `/api/preferencias/${encodeURIComponent(clave)}`,
      ),
    guardarPreferencias: async (clave, valor) => {
      await req<{ guardado: boolean }>(
        `/api/preferencias/${encodeURIComponent(clave)}`,
        { method: "PUT", body: JSON.stringify(valor) },
      );
    },
    listarProveedoresIA: () =>
      req<ProveedorIA[]>(`/api/ia/proveedores`),
    guardarProveedorIA: (id, datos: NuevoProveedorIA) =>
      req<ProveedorIA>(
        `/api/ia/proveedores/${encodeURIComponent(id)}`,
        { method: "PUT", body: JSON.stringify(datos) },
      ),
    eliminarProveedorIA: async (id) => {
      await req<{ eliminado: boolean }>(
        `/api/ia/proveedores/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      );
    },
    probarProveedorIA: (id) =>
      req<PruebaProveedor>(
        `/api/ia/proveedores/${encodeURIComponent(id)}/probar`,
        { method: "POST" },
      ),
    predecirIA: (entrada) =>
      req<PrediccionIA>(`/api/ia/predecir`, {
        method: "POST",
        body: JSON.stringify(entrada),
      }),
    listarLlamadas: (limite = 200, ruta) =>
      req<Llamada[]>(
        `/api/llamadas?limite=${limite}` +
          (ruta ? `&ruta=${encodeURIComponent(ruta)}` : ""),
      ),
    statsLlamadas: () =>
      req<LlamadasStats>(`/api/llamadas/stats`),
    ping: () =>
      req<{ status: string; ts: number }>(`/api/ping`, {
        method: "POST",
      }),
    importarMemoria: (params) =>
      req<ResultadoImportacion>(`/api/importar`, {
        method: "POST",
        body: JSON.stringify(params),
      }),
    historialImportaciones: () =>
      req<HistorialImportacion[]>(`/api/importar/historial`),
  };
}

/** Crea el adaptador de autenticación. */
export function crearAuth(): PuertoAuth {
  return {
    login: async (usuario, password) => {
      const res = await fetchNous(`${BASE_URL}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usuario, password }),
      });
      if (!res.ok) throw new Error(`Login ${res.status}`);
      return ((await res.json()) as { token: string }).token;
    },
    verificarToken: async (token) => {
      const res = await fetchNous(`${BASE_URL}/api/eventos?limite=1`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return res.ok;
    },
  };
}

/** Crea el adaptador de identidad multi-tenant. */
export function crearClienteIdentidad(token: string): PuertoIdentidad {
  const req = <T,>(ruta: string, init?: RequestInit): Promise<T> =>
    peticion<T>(ruta, token, init);
  return {
    identidad: () => req<Identidad>(`/api/identidad`),
    crearWorkspace: (slug, nombre) =>
      req<Workspace>(`/api/workspaces`, {
        method: "POST",
        body: JSON.stringify({ slug, nombre }),
      }),
    listarMiembros: (ref) =>
      req<Miembro[]>(`/api/workspaces/${encodeURIComponent(ref)}/miembros`),
    cambiarRol: async (ref, usuarioId, rol: RolWorkspace) => {
      await req<unknown>(
        `/api/workspaces/${encodeURIComponent(ref)}/miembros/${encodeURIComponent(usuarioId)}`,
        { method: "PATCH", body: JSON.stringify({ rol }) },
      );
    },
    quitarMiembro: async (ref, usuarioId) => {
      await req<unknown>(
        `/api/workspaces/${encodeURIComponent(ref)}/miembros/${encodeURIComponent(usuarioId)}`,
        { method: "DELETE" },
      );
    },
    invitar: (ref, rol, dias = 7) =>
      req<{ token: string }>(
        `/api/workspaces/${encodeURIComponent(ref)}/invitaciones`,
        { method: "POST", body: JSON.stringify({ rol, dias }) },
      ),
    listarLlaves: () => req<Llave[]>(`/api/llaves`),
    crearLlave: (nombre, workspaceDefaultId) =>
      req<{ key: string }>(`/api/llaves`, {
        method: "POST",
        body: JSON.stringify({
          nombre: nombre ?? null,
          workspace_default_id: workspaceDefaultId ?? null,
        }),
      }),
    revocarLlave: async (ref) => {
      await req<unknown>(`/api/llaves/${encodeURIComponent(ref)}`, {
        method: "DELETE",
      });
    },
  };
}

/** Canje público de invitación — sin Bearer; el token es la prueba. */
export function crearClienteInvitacion(): PuertoInvitacion {
  return {
    canjear: async (entrada) => {
      const res = await fetchNous(`${BASE_URL}/api/invitaciones/canjear`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entrada),
      });
      if (!res.ok) {
        throw new Error(`Canje ${res.status}: ${await res.text()}`);
      }
      return (await res.json()) as ResultadoCanje;
    },
  };
}
