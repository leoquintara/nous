"use client";

/**
 * Shell de la app: nav glass con logo, enlaces, toggle de tema y logout.
 * Requiere sesión — sin token redirige a /login.
 */

import {
  Activity,
  Brain,
  Compress,
  Cpu,
  Database,
  Expand,
  HalfMoon,
  LogOut,
  Planet,
  Refresh,
  Search,
  SunLight,
  Timer,
  Upload,
  Community,
} from "iconoir-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { usarApp } from "@/ui/proveedor-app";

const ENLACES = [
  { href: "/", etiqueta: "Cerebro", Icono: Brain },
  { href: "/universo", etiqueta: "Timeline", Icono: Planet },
  { href: "/memoria", etiqueta: "Memoria", Icono: Search },
  { href: "/fuentes", etiqueta: "Fuentes", Icono: Database },
  { href: "/proyeccion", etiqueta: "Proyección", Icono: Timer },
  { href: "/benchmarks", etiqueta: "Benchmarks", Icono: Refresh },
  { href: "/modelos", etiqueta: "Modelos", Icono: Cpu },
  { href: "/trazas", etiqueta: "Logs", Icono: Activity },
  { href: "/importar", etiqueta: "Importar", Icono: Upload },
  { href: "/equipo", etiqueta: "Equipo", Icono: Community },
] as const;

/** Estructura autenticada con navegación liquid glass. */
export function Shell({
  children,
  fluid = false,
}: {
  children: React.ReactNode;
  /** Sin contenedor max-w: el contenido ocupa todo el ancho. */
  fluid?: boolean;
}) {
  const { tema, alternarTema, token, cerrarSesion, yo, workspace, fijarWorkspace } =
    usarApp();
  const pathname = usePathname();
  const router = useRouter();
  const [pantalla, setPantalla] = useState(false);

  useEffect(() => {
    const alCambiar = () => setPantalla(document.fullscreenElement != null);
    document.addEventListener("fullscreenchange", alCambiar);
    return () => document.removeEventListener("fullscreenchange", alCambiar);
  }, []);

  useEffect(() => {
    if (token === null && typeof window !== "undefined") {
      const t = setTimeout(() => {
        if (!localStorage.getItem("nous.token")) router.replace("/login");
      }, 50);
      return () => clearTimeout(t);
    }
  }, [token, router]);

  return (
    <div className="flex min-h-dvh flex-col">
      <header
        className="vidrio-fuerte sticky top-3 z-20 mx-4 mt-3 flex items-center gap-4 px-5 py-3"
        style={{
          backdropFilter: "blur(10px) saturate(1.8)",
          WebkitBackdropFilter: "blur(10px) saturate(1.8)",
        }}
      >
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <Brain width={22} height={22} style={{ color: "var(--primary)" }} />
          Nous
        </Link>
        <nav className="flex flex-1 items-center gap-1" aria-label="Principal">
          {ENLACES.map(({ href, etiqueta, Icono }) => (
            <Link
              key={href}
              href={href}
              aria-current={pathname === href ? "page" : undefined}
              className={`flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-sm transition ${
                pathname === href ? "font-semibold" : "texto-suave"
              }`}
              style={
                pathname === href
                  ? { background: "var(--halo)", color: "var(--primary)" }
                  : undefined
              }
            >
              <Icono width={15} height={15} />
              {etiqueta}
            </Link>
          ))}
        </nav>
        {yo && yo.workspaces.length > 0 && (
          <select
            aria-label="Workspace activo"
            title={`Workspace · ${yo.workspace_activo.rol}`}
            value={workspace ?? yo.workspace_activo.slug}
            onChange={(e) =>
              fijarWorkspace(
                e.target.value === yo.workspace_activo.slug
                  ? null
                  : e.target.value,
              )
            }
            className="vidrio max-w-36 rounded-xl px-2 py-1.5 text-xs outline-none"
          >
            {yo.workspaces.map((w) => (
              <option key={w.id} value={w.slug}>
                {w.slug}
                {w.rol !== "owner" ? ` · ${w.rol}` : ""}
              </option>
            ))}
          </select>
        )}
        <button
          onClick={() => {
            if (document.fullscreenElement) {
              void document.exitFullscreen();
            } else {
              void document.documentElement.requestFullscreen();
            }
          }}
          aria-label={pantalla ? "Salir de pantalla completa" : "Pantalla completa"}
          title={pantalla ? "Salir de pantalla completa" : "Pantalla completa"}
          className="vidrio texto-suave rounded-xl p-2"
        >
          {pantalla ? (
            <Compress width={16} height={16} />
          ) : (
            <Expand width={16} height={16} />
          )}
        </button>
        <button
          onClick={alternarTema}
          aria-label={tema === "dark" ? "Tema claro" : "Tema oscuro"}
          className="vidrio rounded-xl p-2"
        >
          {tema === "dark" ? (
            <SunLight width={16} height={16} />
          ) : (
            <HalfMoon width={16} height={16} />
          )}
        </button>
        <button
          onClick={() => {
            cerrarSesion();
            router.replace("/login");
          }}
          aria-label="Cerrar sesión"
          className="vidrio texto-suave rounded-xl p-2"
        >
          <LogOut width={16} height={16} />
        </button>
      </header>
      <div
        className={
          fluid
            ? "flex w-full flex-1 flex-col px-4 pt-4 pb-4"
            : "mx-auto w-full max-w-7xl flex-1 p-4"
        }
      >
        {children}
      </div>
    </div>
  );
}
