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
  useSyncExternalStore,
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
export function useApp(): ContextoApp {
  const ctx = useContext(Contexto);
  if (!ctx) throw new Error("useApp debe usarse dentro de ProveedorApp");
  return ctx;
}

// localStorage como store externo: useSyncExternalStore hidrata con
// el snapshot de servidor y luego re-lee en cliente — sin mismatch
// de hidratación ni setState en effect. emitirCambio notifica los
// cambios en esta pestaña; el listener "storage" cubre otras.
const suscriptores = new Set<() => void>();

function enCambio(callback: () => void): () => void {
  suscriptores.add(callback);
  window.addEventListener("storage", callback);
  return () => {
    suscriptores.delete(callback);
    window.removeEventListener("storage", callback);
  };
}

function emitirCambio(): void {
  for (const cb of suscriptores) cb();
}

function leerLS(clave: string): string | null {
  return localStorage.getItem(clave);
}

const nulo = () => null;
const temaServidor = () => "light" as Tema; // coincide con :root y el prerender

/** Hook: valor vivo de una clave de localStorage (null en SSR). */
export function useLocalStorage(clave: string): string | null {
  return useSyncExternalStore(enCambio, () => leerLS(clave), nulo);
}

/** Emite cambio de preferencia persistida tras escribir en localStorage. */
export function emitirCambioLS(): void {
  emitirCambio();
}

/** Proveedor raíz de tema, sesión e identidad. */
export function ProveedorApp({ children }: { children: React.ReactNode }) {
  const token = useLocalStorage("nous.token");
  const workspace = useLocalStorage("nous.workspace");
  // Server snapshot "light" = lo que produjo el prerender estático
  // (window undefined). Post-hidratación entra el valor real.
  const tema = useSyncExternalStore(
    enCambio,
    () =>
      (leerLS("nous.tema") as Tema | null) ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"),
    temaServidor,
  );
  const [yo, setYo] = useState<Identidad | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = tema;
    localStorage.setItem("nous.tema", tema);
  }, [tema]);

  const alternarTema = useCallback(() => {
    const siguiente = tema === "dark" ? "light" : "dark";
    localStorage.setItem("nous.tema", siguiente);
    emitirCambio();
  }, [tema]);
  const fijarToken = useCallback((t: string) => {
    localStorage.setItem("nous.token", t);
    emitirCambio();
  }, []);
  const cerrarSesion = useCallback(() => {
    localStorage.removeItem("nous.token");
    localStorage.removeItem("nous.workspace");
    emitirCambio();
  }, []);
  const fijarWorkspace = useCallback((slug: string | null) => {
    if (slug) localStorage.setItem("nous.workspace", slug);
    else localStorage.removeItem("nous.workspace");
    emitirCambio();
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

  // Al cambiar la API (login/logout) la identidad previa queda
  // obsoleta — ajuste de estado durante render (patrón oficial de
  // React para resetear estado derivado cuando cambia una entrada).
  const [apiPrevia, setApiPrevia] = useState(identidadApi);
  if (apiPrevia !== identidadApi) {
    setApiPrevia(identidadApi);
    setYo(null);
  }

  // Resuelve identidad al entrar con sesión; si el workspace
  // persistido ya no existe (expulsado), cae al default del token.
  useEffect(() => {
    if (!identidadApi) return;
    let vivo = true;
    identidadApi
      .identidad()
      .then((idn) => {
        if (!vivo) return;
        setYo(idn);
        const slugs = idn.workspaces.map((w) => w.slug);
        const wsActual = localStorage.getItem("nous.workspace");
        if (wsActual && !slugs.includes(wsActual)) {
          localStorage.removeItem("nous.workspace");
          emitirCambio();
        }
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
