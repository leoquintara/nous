"use client";

/**
 * Canje de invitación — página pública (sin sesión).
 *
 * El invitado pega su token ns_inv_* (o llega con ?token=), elige
 * su handle, y recibe su key ns_u_* una sola vez: al aceptarla
 * queda como token de sesión y entra directo al dashboard.
 */

import { Key, UserPlus } from "iconoir-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { useApp } from "@/ui/proveedor-app";
import { Logo } from "@/ui/logo";
import { crearClienteInvitacion } from "@/infrastructure/nous-api";

/** Formulario de canje (envuelto en Suspense por useSearchParams). */
function FormularioCanje() {
  const { fijarToken } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  const [token, setToken] = useState(params.get("token") ?? "");
  const [handle, setHandle] = useState("");
  const [nombre, setNombre] = useState("");
  const [key, setKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  /** Envía el canje al endpoint público. */
  async function canjear(ev: React.FormEvent) {
    ev.preventDefault();
    setError(null);
    setCargando(true);
    try {
      const r = await crearClienteInvitacion().canjear({
        token: token.trim(),
        handle: handle.trim(),
        nombre: nombre.trim(),
      });
      setKey(r.key);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Invitación inválida, vencida o ya usada",
      );
    } finally {
      setCargando(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="vidrio-fuerte flex w-full max-w-sm flex-col gap-5 p-8">
        <div>
          <h1 className="text-2xl font-semibold">
            <Logo />
          </h1>
          <p className="texto-suave text-sm">
            {key ? "Bienvenido al equipo" : "Únete a un workspace"}
          </p>
        </div>

        {key ? (
          <>
            <p className="texto-suave text-xs">
              Esta es tu llave de acceso — <strong>guárdala</strong>, no se
              vuelve a mostrar.
            </p>
            <code
              className="vidrio block break-all rounded-xl p-3 font-mono text-xs"
              style={{ color: "var(--primary)" }}
            >
              {key}
            </code>
            <button
              type="button"
              onClick={() => {
                fijarToken(key);
                router.replace("/");
              }}
              className="rounded-xl px-4 py-2.5 font-semibold text-white"
              style={{ background: "var(--primary)" }}
            >
              Entrar con esta llave
            </button>
          </>
        ) : (
          <form onSubmit={canjear} className="flex flex-col gap-4">
            <label className="flex flex-col gap-1 text-sm">
              <span className="texto-suave flex items-center gap-1">
                <Key width={14} height={14} /> Token de invitación
              </span>
              <input
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="ns_inv_..."
                required
                className="vidrio rounded-xl px-3 py-2 font-mono text-xs outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="texto-suave">Tu handle (minúsculas)</span>
              <input
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                placeholder="ana.lopez"
                pattern="[a-z0-9][a-z0-9_.\-]{0,63}"
                title="Minúsculas, números, punto, guion y guion bajo"
                required
                className="vidrio rounded-xl px-3 py-2 outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="texto-suave">Tu nombre</span>
              <input
                value={nombre}
                onChange={(e) => setNombre(e.target.value)}
                placeholder="Ana López"
                required
                className="vidrio rounded-xl px-3 py-2 outline-none"
              />
            </label>

            {error && (
              <p
                role="alert"
                className="text-sm"
                style={{ color: "var(--danger)" }}
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={cargando}
              className="flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 font-semibold text-white disabled:opacity-50"
              style={{ background: "var(--primary)" }}
            >
              <UserPlus width={16} height={16} />
              {cargando ? "Canjeando…" : "Canjear invitación"}
            </button>
          </form>
        )}

        <Link
          href="/login"
          className="texto-suave text-center text-xs underline-offset-2 hover:underline"
        >
          Ya tengo llave — ir a login
        </Link>
      </div>
    </main>
  );
}

/** Página pública de canje de invitación. */
export default function PaginaUnirse() {
  return (
    <Suspense>
      <FormularioCanje />
    </Suspense>
  );
}
