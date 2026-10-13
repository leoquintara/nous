"use client";

/**
 * Proveedor de aplicación: tema (light/dark) y sesión de token.
 *
 * Persiste tema y token en localStorage; hidrata sin flash de tema
 * aplicando data-theme en el <html> al montar.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { crearAuth, crearClienteNous } from "@/infrastructure/nous-api";
import type { PuertoMemoria } from "@/application/puertos";

type Tema = "light" | "dark";

interface ContextoApp {
  /** Tema visual activo. */
  tema: Tema;
  /** Cambia el tema y lo persiste. */
  alternarTema: () => void;
  /** Token Bearer vigente o null si no hay sesión. */
  token: string | null;
  /** Guarda el token de sesión validado. */
  fijarToken: (token: string) => void;
  /** Cierra la sesión actual. */
  cerrarSesion: () => void;
  /** Cliente de memoria autenticado (null sin sesión). */
  memoria: PuertoMemoria | null;
}

const Contexto = createContext<ContextoApp | null>(null);

/** Hook de acceso al contexto global de la app. */
export function usarApp(): ContextoApp {
  const ctx = useContext(Contexto);
  if (!ctx) throw new Error("usarApp debe usarse dentro de ProveedorApp");
  return ctx;
}

/** Proveedor raíz de tema y sesión. */
export function ProveedorApp({ children }: { children: React.ReactNode }) {
  const [tema, setTema] = useState<Tema>("dark");
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    const temaGuardado =
      (localStorage.getItem("nous.tema") as Tema | null) ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light");
    setTema(temaGuardado);
    setToken(localStorage.getItem("nous.token"));
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = tema;
    localStorage.setItem("nous.tema", tema);
  }, [tema]);

  const alternarTema = useCallback(
    () => setTema((t) => (t === "dark" ? "light" : "dark")),
    [],
  );
  const fijarToken = useCallback((t: string) => {
    setToken(t);
    localStorage.setItem("nous.token", t);
  }, []);
  const cerrarSesion = useCallback(() => {
    setToken(null);
    localStorage.removeItem("nous.token");
  }, []);

  const memoria = token ? crearClienteNous(token) : null;
  void crearAuth; // el adaptador de auth se usa en la página de login

  return (
    <Contexto.Provider
      value={{ tema, alternarTema, token, fijarToken, cerrarSesion, memoria }}
    >
      {children}
    </Contexto.Provider>
  );
}
