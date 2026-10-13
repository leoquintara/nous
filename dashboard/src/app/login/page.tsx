"use client";

/**
 * Página de acceso: admite token de servicio (ns_*) o, si el servidor
 * tiene NOUS_DASHBOARD_USUARIO/PASSWORD configurados, usuario+contraseña
 * que se intercambia por el token vía POST /api/login.
 */

import { Key, User } from "iconoir-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { crearAuth } from "@/infrastructure/nous-api";

export default function PaginaLogin() {
  const { fijarToken } = usarApp();
  const router = useRouter();
  const [modo, setModo] = useState<"token" | "credenciales">("token");
  const [token, setToken] = useState("");
  const [usuario, setUsuario] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  /** Valida el token pegado o intercambia credenciales por él. */
  async function entrar(ev: React.FormEvent) {
    ev.preventDefault();
    setError(null);
    setCargando(true);
    try {
      const auth = crearAuth();
      let t = token.trim();
      if (modo === "credenciales") {
        t = await auth.login(usuario.trim(), password);
      }
      if (!(await auth.verificarToken(t))) {
        throw new Error("Token rechazado por la API");
      }
      fijarToken(t);
      router.replace("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error de acceso");
    } finally {
      setCargando(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <form
        onSubmit={entrar}
        className="vidrio-fuerte flex w-full max-w-sm flex-col gap-5 p-8"
      >
        <div>
          <h1 className="text-2xl font-semibold">Nous</h1>
          <p className="texto-suave text-sm">
            Acceso a tu memoria 4D
          </p>
        </div>

        <div className="flex gap-2" role="tablist" aria-label="Método de acceso">
          {(
            [
              ["token", "Token"],
              ["credenciales", "Usuario"],
            ] as const
          ).map(([m, etiqueta]) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={modo === m}
              onClick={() => setModo(m)}
              className={`vidrio flex-1 rounded-xl px-3 py-2 text-sm transition ${
                modo === m ? "font-semibold" : "texto-suave"
              }`}
            >
              {etiqueta}
            </button>
          ))}
        </div>

        {modo === "token" ? (
          <label className="flex flex-col gap-1 text-sm">
            <span className="texto-suave flex items-center gap-1">
              <Key width={14} height={14} /> Token de servicio (ns_…)
            </span>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="ns_..."
              required
              className="vidrio rounded-xl px-3 py-2 outline-none"
            />
          </label>
        ) : (
          <>
            <label className="flex flex-col gap-1 text-sm">
              <span className="texto-suave flex items-center gap-1">
                <User width={14} height={14} /> Usuario
              </span>
              <input
                value={usuario}
                onChange={(e) => setUsuario(e.target.value)}
                required
                className="vidrio rounded-xl px-3 py-2 outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="texto-suave">Contraseña</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="vidrio rounded-xl px-3 py-2 outline-none"
              />
            </label>
          </>
        )}

        {error && (
          <p role="alert" className="text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={cargando}
          className="rounded-xl px-4 py-2.5 font-semibold text-white transition disabled:opacity-50"
          style={{ background: "var(--primary)" }}
        >
          {cargando ? "Verificando…" : "Entrar"}
        </button>
      </form>
    </main>
  );
}
