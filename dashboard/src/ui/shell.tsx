"use client";

/**
 * Shell de la app: nav glass con logo, enlaces, toggle de tema y logout.
 * Requiere sesión — sin token redirige a /login.
 */

import {
  Brain,
  Database,
  HalfMoon,
  LogOut,
  Refresh,
  Search,
  SunLight,
  Timer,
} from "iconoir-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { usarApp } from "@/ui/proveedor-app";

const ENLACES = [
  { href: "/", etiqueta: "Cerebro", Icono: Brain },
  { href: "/memoria", etiqueta: "Memoria", Icono: Search },
  { href: "/fuentes", etiqueta: "Fuentes", Icono: Database },
  { href: "/proyeccion", etiqueta: "Proyección", Icono: Timer },
  { href: "/benchmarks", etiqueta: "Benchmarks", Icono: Refresh },
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
  const { tema, alternarTema, token, cerrarSesion } = usarApp();
  const pathname = usePathname();
  const router = useRouter();

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
      <header className="vidrio-fuerte sticky top-3 z-20 mx-4 mt-3 flex items-center gap-4 px-5 py-3">
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
