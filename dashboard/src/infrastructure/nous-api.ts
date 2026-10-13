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
import { NuevoEvento } from "@/application/puertos";
import type {
  BenchmarkRun,
  Evento,
  Proyeccion,
  ResultadoBusqueda,
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
    listarEventos: (desde, hasta, limite = 500) =>
      peticion<Evento[]>(
        `/api/eventos?desde_unix=${desde}&hasta_unix=${hasta}&limite=${limite}`,
        token,
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
    benchmarks: (nombre, limite = 50) => {
      const params = new URLSearchParams({ limite: String(limite) });
      if (nombre) params.set("nombre", nombre);
      return peticion<BenchmarkRun[]>(`/api/benchmarks?${params}`, token);
    },
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
