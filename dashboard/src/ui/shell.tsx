"use client";

/**
 * Shell de la app: sidebar vertical glass con logo, enlaces, toggle
 * de tema y logout. Colapsable a solo-iconos (persiste en
 * localStorage). Requiere sesión — sin token redirige a /login.
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
  NavArrowLeft,
  NavArrowRight,
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

import { Logo } from "@/ui/logo";
import { emitirCambioLS, useApp, useLocalStorage } from "@/ui/proveedor-app";

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

const CLAVE_COLAPSO = "nous.sidebar-colapsado";

/** Estructura autenticada con navegación lateral liquid glass. */
export function Shell({
  children,
  fluid = false,
}: {
  children: React.ReactNode;
  /** Sin contenedor max-w: el contenido ocupa todo el ancho. */
  fluid?: boolean;
}) {
  const { tema, alternarTema, token, cerrarSesion, yo, workspace, fijarWorkspace } =
    useApp();
  const pathname = usePathname();
  const router = useRouter();
  const [pantalla, setPantalla] = useState(false);
  // localStorage vía store externo: hidrata como el prerender (false)
  // y luego aplica el valor real — sin mismatch de hidratación.
  const colapsado = useLocalStorage(CLAVE_COLAPSO) === "1";

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

  const alternarColapso = () => {
    localStorage.setItem(CLAVE_COLAPSO, colapsado ? "0" : "1");
    emitirCambioLS();
  };

  return (
    <div className="flex min-h-dvh">
      <aside
        className={`vidrio-fuerte sticky top-3 z-20 m-3 mr-0 flex h-[calc(100dvh-1.5rem)] flex-col gap-1 py-3 transition-[width] duration-200 ${
          colapsado ? "w-16 items-center px-2" : "w-56 px-3"
        }`}
        style={{
          backdropFilter: "blur(10px) saturate(1.8)",
          WebkitBackdropFilter: "blur(10px) saturate(1.8)",
        }}
      >
        <div
          className={`flex items-center py-2 ${
            colapsado ? "flex-col gap-1" : "justify-between px-2"
          }`}
        >
          <Link href="/" className="flex items-center" aria-label="Nous — inicio">
            <Logo compacto={colapsado} />
          </Link>
          <button
            onClick={alternarColapso}
            aria-label={
              colapsado ? "Expandir barra lateral" : "Colapsar barra lateral"
            }
            title={colapsado ? "Expandir" : "Colapsar"}
            className="vidrio texto-suave rounded-xl p-1.5"
          >
            {colapsado ? (
              <NavArrowRight width={15} height={15} />
            ) : (
              <NavArrowLeft width={15} height={15} />
            )}
          </button>
        </div>

        {yo && yo.workspaces.length > 0 &&
          (colapsado ? (
            <button
              onClick={alternarColapso}
              title={`Workspace · ${workspace ?? yo.workspace_activo.slug} (expande para cambiar)`}
              aria-label="Expandir barra para cambiar de workspace"
              className="vidrio mb-1 flex h-9 w-9 items-center justify-center rounded-xl text-xs font-semibold uppercase"
            >
              {(workspace ?? yo.workspace_activo.slug).charAt(0)}
            </button>
          ) : (
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
              className="vidrio mb-1 w-full rounded-xl px-2 py-1.5 text-xs outline-none"
            >
              {yo.workspaces.map((w) => (
                <option key={w.id} value={w.slug}>
                  {w.slug}
                  {w.rol !== "owner" ? ` · ${w.rol}` : ""}
                </option>
              ))}
            </select>
          ))}

        <nav
          className={`mt-2 flex flex-1 flex-col gap-0.5 overflow-y-auto sin-scrollbar ${
            colapsado ? "items-center" : ""
          }`}
          aria-label="Principal"
        >
          {ENLACES.map(({ href, etiqueta, Icono }) => (
            <Link
              key={href}
              href={href}
              aria-current={pathname === href ? "page" : undefined}
              title={colapsado ? etiqueta : undefined}
              className={`flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition ${
                colapsado ? "justify-center px-0 py-2.5 w-10" : ""
              } ${pathname === href ? "font-semibold" : "texto-suave"}`}
              style={
                pathname === href
                  ? { background: "var(--halo)", color: "var(--primary)" }
                  : undefined
              }
            >
              <Icono width={17} height={17} />
              {!colapsado && etiqueta}
            </Link>
          ))}
        </nav>

        <div
          className={`flex border-t pt-2 ${
            colapsado ? "flex-col items-center gap-1.5" : "items-center justify-between"
          }`}
          style={{ borderColor: "var(--linea)" }}
        >
          <button
            onClick={() => {
              if (document.fullscreenElement) {
                void document.exitFullscreen();
              } else {
                void document.documentElement.requestFullscreen();
              }
            }}
            aria-label={
              pantalla ? "Salir de pantalla completa" : "Pantalla completa"
            }
            title={
              pantalla ? "Salir de pantalla completa" : "Pantalla completa"
            }
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
            title={tema === "dark" ? "Tema claro" : "Tema oscuro"}
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
            title="Cerrar sesión"
            className="vidrio texto-suave rounded-xl p-2"
          >
            <LogOut width={16} height={16} />
          </button>
        </div>
      </aside>

      <div
        className={
          fluid
            ? "flex min-w-0 w-full flex-1 flex-col px-4 pt-4 pb-4"
            : "mx-auto w-full min-w-0 max-w-7xl flex-1 p-4"
        }
      >
        {children}
      </div>
    </div>
  );
}
