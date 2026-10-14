/**
 * Adaptador HTTP de la API REST de Nous.
 *
 * Implementa PuertoMemoria y PuertoAuth contra los endpoints reales;
 * inyecta el Bearer token configurado por el operador.
 */

import type {
  PuertoAuth,
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
  NuevoProveedorIA,
  Objetivo,
  PrediccionIA,
  Preferencias,
  ProveedorIA,
  Proyeccion,
  PruebaProveedor,
  Relacion,
  ResultadoBusqueda,
  TagConteo,
} from "@/domain/modelos";

const BASE_URL = process.env.NEXT_PUBLIC_NOUS_API ?? "http://127.0.0.1:8720";

/** Ejecuta una petición autenticada y devuelve el JSON o lanza. */
async function peticion<T>(
  ruta: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE_URL}${ruta}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`Nous API ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as T;
}

/** Crea el adaptador de memoria ligado a un token concreto. */
export function crearClienteNous(token: string): PuertoMemoria {
  return {
    listarEventos: (desde, hasta, limite = 500, rama) =>
      peticion<Evento[]>(
        `/api/eventos?desde_unix=${desde}&hasta_unix=${hasta}&limite=${limite}` +
          (rama ? `&rama=${encodeURIComponent(rama)}` : "") +
          "&repartir=true",
        token,
      ),
    listarRelaciones: (limite = 2000) =>
      peticion<Relacion[]>(`/api/relaciones?limite=${limite}`, token),
    listarTags: (limite = 200) =>
      peticion<TagConteo[]>(`/api/tags?limite=${limite}`, token),
    listarFuentes: () => peticion<FuenteInfo[]>(`/api/fuentes`, token),
    crearFuente: (fuente: NuevaFuente) =>
      peticion<FuenteInfo>(`/api/fuentes`, token, {
        method: "POST",
        body: JSON.stringify(fuente),
      }),
    estadoFuente: async (nombre, estado) => {
      await peticion<{ nombre: string }>(
        `/api/fuentes/${encodeURIComponent(nombre)}`,
        token,
        { method: "PATCH", body: JSON.stringify({ estado }) },
      );
    },
    eliminarFuente: async (nombre) => {
      await peticion<{ nombre: string }>(
        `/api/fuentes/${encodeURIComponent(nombre)}`,
        token,
        { method: "DELETE" },
      );
    },
    ramificarFuente: (nombre, rama, namespace = "") =>
      peticion<FuenteInfo>(
        `/api/fuentes/${encodeURIComponent(nombre)}/ramificar`,
        token,
        { method: "POST", body: JSON.stringify({ rama, namespace }) },
      ),
    buscar: (consulta, topN = 10) =>
      peticion<ResultadoBusqueda[]>(
        `/api/buscar?q=${encodeURIComponent(consulta)}&top_n=${topN}`,
        token,
      ),
    registrarEvento: (evento: NuevoEvento) =>
      peticion(`/api/eventos`, token, {
        method: "POST",
        body: JSON.stringify({ fuente: "dashboard", ...evento }),
      }),
    proyectar: (horizonte, historial) =>
      peticion<Proyeccion>(
        `/api/proyeccion?horizonte_dias=${horizonte}&dias_historial=${historial}`,
        token,
        { method: "POST" },
      ),
    proyeccionActual: () =>
      peticion<Proyeccion>(`/api/proyeccion/actual`, token),
    listarObjetivos: (estado = "activo", rama = "main") =>
      peticion<Objetivo[]>(
        `/api/objetivos?estado=${encodeURIComponent(estado)}&rama=${encodeURIComponent(rama)}`,
        token,
      ),
    crearObjetivo: (objetivo: NuevoObjetivo) =>
      peticion<Objetivo>(`/api/objetivos`, token, {
        method: "POST",
        body: JSON.stringify(objetivo),
      }),
    actualizarObjetivo: (id, parche: ParcheObjetivo) =>
      peticion<Objetivo>(
        `/api/objetivos/${encodeURIComponent(id)}`,
        token,
        { method: "PATCH", body: JSON.stringify(parche) },
      ),
    cerrarObjetivo: (id, estado, resolucion) =>
      peticion<Objetivo>(
        `/api/objetivos/${encodeURIComponent(id)}/cerrar`,
        token,
        {
          method: "POST",
          body: JSON.stringify({ estado, resolucion }),
        },
      ),
    benchmarks: (nombre, limite = 50) => {
      const params = new URLSearchParams({ limite: String(limite) });
      if (nombre) params.set("nombre", nombre);
      return peticion<BenchmarkRun[]>(`/api/benchmarks?${params}`, token);
    },
    actividad: (limite = 50) =>
      peticion<EventoActividad[]>(
        `/api/actividad?limite=${limite}`,
        token,
      ),
    leerPreferencias: (clave) =>
      peticion<Preferencias>(
        `/api/preferencias/${encodeURIComponent(clave)}`,
        token,
      ),
    guardarPreferencias: async (clave, valor) => {
      await peticion<{ guardado: boolean }>(
        `/api/preferencias/${encodeURIComponent(clave)}`,
        token,
        { method: "PUT", body: JSON.stringify(valor) },
      );
    },
    listarProveedoresIA: () =>
      peticion<ProveedorIA[]>(`/api/ia/proveedores`, token),
    guardarProveedorIA: (id, datos: NuevoProveedorIA) =>
      peticion<ProveedorIA>(
        `/api/ia/proveedores/${encodeURIComponent(id)}`,
        token,
        { method: "PUT", body: JSON.stringify(datos) },
      ),
    eliminarProveedorIA: async (id) => {
      await peticion<{ eliminado: boolean }>(
        `/api/ia/proveedores/${encodeURIComponent(id)}`,
        token,
        { method: "DELETE" },
      );
    },
    probarProveedorIA: (id) =>
      peticion<PruebaProveedor>(
        `/api/ia/proveedores/${encodeURIComponent(id)}/probar`,
        token,
        { method: "POST" },
      ),
    predecirIA: (entrada) =>
      peticion<PrediccionIA>(`/api/ia/predecir`, token, {
        method: "POST",
        body: JSON.stringify(entrada),
      }),
  };
}

/** Crea el adaptador de autenticación. */
export function crearAuth(): PuertoAuth {
  return {
    login: async (usuario, password) => {
      const res = await fetch(`${BASE_URL}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usuario, password }),
      });
      if (!res.ok) throw new Error(`Login ${res.status}`);
      return ((await res.json()) as { token: string }).token;
    },
    verificarToken: async (token) => {
      const res = await fetch(`${BASE_URL}/api/eventos?limite=1`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return res.ok;
    },
  };
}
