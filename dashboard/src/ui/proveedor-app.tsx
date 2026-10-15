"use client";

/**
 * Proveedor de aplicación: tema (light/dark), sesión de token,
 * identidad multi-tenant y workspace activo.
 *
 * Persiste tema, token y workspace en localStorage; hidrata sin
 * flash de tema aplicando data-theme en el <html> al montar. El
 * workspace activo se inyecta como override en todas las llamadas
 * a la API — cambiarlo recrea el cliente y re-carga las páginas.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  crearAuth,
  crearClienteIdentidad,
  crearClienteNous,
} from "@/infrastructure/nous-api";
import type {
  PuertoIdentidad,
  PuertoMemoria,
} from "@/application/puertos";
import type { Identidad } from "@/domain/modelos";

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
  /** Puerto de identidad multi-tenant (null sin sesión). */
  idn: PuertoIdentidad | null;
  /** Identidad resuelta del llamante (null sin cargar/sesión). */
  yo: Identidad | null;
  /** Slug del workspace activo (null = el default del token). */
  workspace: string | null;
  /** Cambia el workspace activo y lo persiste. */
  fijarWorkspace: (slug: string | null) => void;
  /** Re-lee la identidad desde la API (tras invitar, crear ws…). */
  recargarIdentidad: () => void;
}

const Contexto = createContext<ContextoApp | null>(null);

/** Hook de acceso al contexto global de la app. */
export function usarApp(): ContextoApp {
  const ctx = useContext(Contexto);
  if (!ctx) throw new Error("usarApp debe usarse dentro de ProveedorApp");
  return ctx;
}

/** Proveedor raíz de tema, sesión e identidad. */
export function ProveedorApp({ children }: { children: React.ReactNode }) {
  const [tema, setTema] = useState<Tema>("dark");
  const [token, setToken] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [yo, setYo] = useState<Identidad | null>(null);

  useEffect(() => {
    const temaGuardado =
      (localStorage.getItem("nous.tema") as Tema | null) ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light");
    setTema(temaGuardado);
    setToken(localStorage.getItem("nous.token"));
    setWorkspace(localStorage.getItem("nous.workspace"));
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
    setYo(null);
    setWorkspace(null);
    localStorage.removeItem("nous.token");
    localStorage.removeItem("nous.workspace");
  }, []);
  const fijarWorkspace = useCallback((slug: string | null) => {
    setWorkspace(slug);
    if (slug) localStorage.setItem("nous.workspace", slug);
    else localStorage.removeItem("nous.workspace");
  }, []);

  const identidadApi: PuertoIdentidad | null = useMemo(
    () => (token ? crearClienteIdentidad(token) : null),
    [token],
  );

  const recargarIdentidad = useCallback(() => {
    if (!identidadApi) return;
    identidadApi
      .identidad()
      .then(setYo)
      .catch(() => setYo(null));
  }, [identidadApi]);

  // Resuelve identidad al entrar con sesión; si el workspace
  // persistido ya no existe (expulsado), cae al default del token.
  useEffect(() => {
    if (!identidadApi) {
      setYo(null);
      return;
    }
    let vivo = true;
    identidadApi
      .identidad()
      .then((idn) => {
        if (!vivo) return;
        setYo(idn);
        const slugs = idn.workspaces.map((w) => w.slug);
        setWorkspace((actual) => {
          if (actual && !slugs.includes(actual)) {
            localStorage.removeItem("nous.workspace");
            return null;
          }
          return actual;
        });
      })
      .catch(() => {
        if (vivo) setYo(null);
      });
    return () => {
      vivo = false;
    };
  }, [identidadApi]);

  // El cliente se recrea al cambiar el workspace: las páginas que
  // dependen de `memoria` en sus efectos re-cargan solas.
  const memoria = useMemo(
    () => (token ? crearClienteNous(token, workspace) : null),
    [token, workspace],
  );
  void crearAuth; // el adaptador de auth se usa en la página de login

  return (
    <Contexto.Provider
      value={{
        tema,
        alternarTema,
        token,
        fijarToken,
        cerrarSesion,
        memoria,
        idn: identidadApi,
        yo,
        workspace,
        fijarWorkspace,
        recargarIdentidad,
      }}
    >
      {children}
    </Contexto.Provider>
  );
}
