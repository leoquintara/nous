"use client";

/**
 * Equipo: gestión del workspace activo.
 *
 * Workspaces propios, roster con roles (solo owners gestionan),
 * invitaciones canjeables ns_inv_* de un solo uso, y las API keys
 * del usuario — las llaves en claro se muestran una sola vez.
 */

import {
  Check,
  Community,
  Copy,
  Key,
  Plus,
  Trash,
  UserPlus,
} from "iconoir-react";
import { useEffect, useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type { Llave, Miembro, RolWorkspace } from "@/domain/modelos";

/** Badge de rol con color semántico. */
function ChipRol({ rol }: { rol: RolWorkspace }) {
  const fondo =
    rol === "owner"
      ? "var(--primary)"
      : rol === "member"
        ? "var(--dim-cognitiva, var(--primary))"
        : "var(--linea)";
  return (
    <span
      className="rounded-full px-2 py-0.5 text-[0.65rem] font-semibold text-white"
      style={{ background: fondo }}
    >
      {rol}
    </span>
  );
}

/** Secreto mostrado una sola vez con botón de copiar. */
function Secreto({ valor, etiqueta }: { valor: string; etiqueta: string }) {
  const [copiado, setCopiado] = useState(false);
  return (
    <div
      role="status"
      className="vidrio flex items-center gap-2 rounded-xl p-3 text-xs"
    >
      <code className="min-w-0 flex-1 truncate font-mono">{valor}</code>
      <button
        type="button"
        aria-label={etiqueta}
        onClick={() => {
          void navigator.clipboard.writeText(valor).then(() => {
            setCopiado(true);
            setTimeout(() => setCopiado(false), 1500);
          });
        }}
        className="vidrio rounded-lg p-1.5"
      >
        {copiado ? (
          <Check width={14} height={14} style={{ color: "var(--primary)" }} />
        ) : (
          <Copy width={14} height={14} />
        )}
      </button>
      <span className="texto-suave">se muestra una sola vez</span>
    </div>
  );
}

/** Página de gestión de equipo, workspaces y llaves. */
export default function PaginaEquipo() {
  const { yo, workspace, idn, recargarIdentidad } = usarApp();
  const wsRef = workspace ?? yo?.workspace_activo.slug ?? "";
  const wsActivo = yo?.workspaces.find((w) => w.slug === wsRef);
  const soyOwner = wsActivo?.rol === "owner";

  const [miembros, setMiembros] = useState<Miembro[]>([]);
  const [llaves, setLlaves] = useState<Llave[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Formularios
  const [nuevoWs, setNuevoWs] = useState({ slug: "", nombre: "" });
  const [invitacion, setInvitacion] = useState<{
    rol: "member" | "viewer";
    dias: number;
  }>({ rol: "member", dias: 7 });
  const [nombreLlave, setNombreLlave] = useState("");
  const [secreto, setSecreto] = useState<{ tipo: string; valor: string } | null>(
    null,
  );

  function cargar() {
    if (!idn || !wsRef) return;
    idn.listarMiembros(wsRef).then(setMiembros).catch(() => setMiembros([]));
    idn.listarLlaves().then(setLlaves).catch(() => setLlaves([]));
  }

  useEffect(cargar, [idn, wsRef]); // eslint-disable-line react-hooks/exhaustive-deps

  const fallo = (e: unknown) =>
    setError(e instanceof Error ? e.message : "Error inesperado");

  return (
    <Shell>
      <section className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <header className="flex items-center gap-3">
          <Community width={22} height={22} style={{ color: "var(--primary)" }} />
          <div>
            <h1 className="text-lg font-semibold">Equipo</h1>
            <p className="texto-suave text-xs">
              Workspace activo: <strong>{wsRef}</strong>
              {wsActivo && (
                <>
                  {" "}
                  · tu rol es <ChipRol rol={wsActivo.rol} />
                </>
              )}
            </p>
          </div>
        </header>

        {yo?.licencia && !yo.licencia.licenciado && (
          <p
            className="vidrio rounded-xl px-4 py-2.5 text-xs"
            style={{ color: "var(--texto-suave)" }}
          >
            Edición libre: {yo.licencia.usuarios}/
            {yo.licencia.max_usuarios} usuarios ·{" "}
            {yo.licencia.workspaces}/{yo.licencia.max_workspaces}{" "}
            workspaces — configura <code>NOUS_LICENSE_KEY</code> para
            desbloquear el equipo.
          </p>
        )}

        {error && (
          <p role="alert" className="text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
        {secreto && (
          <div className="flex flex-col gap-1">
            <p className="texto-suave text-xs font-semibold">{secreto.tipo}</p>
            <Secreto valor={secreto.valor} etiqueta="Copiar al portapapeles" />
          </div>
        )}

        {/* Workspaces */}
        <section className="vidrio-fuerte flex flex-col gap-3 p-5">
          <h2 className="text-sm font-semibold">Tus workspaces</h2>
          <ul className="flex flex-wrap gap-2">
            {yo?.workspaces.map((w) => (
              <li
                key={w.id}
                className="vidrio flex items-center gap-2 rounded-xl px-3 py-1.5 text-xs"
                style={
                  w.slug === wsRef
                    ? { outline: "1.5px solid var(--primary)" }
                    : undefined
                }
              >
                {w.slug} <ChipRol rol={w.rol} />
              </li>
            ))}
          </ul>
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!idn) return;
              setError(null);
              idn
                .crearWorkspace(nuevoWs.slug.trim(), nuevoWs.nombre.trim())
                .then(() => {
                  setNuevoWs({ slug: "", nombre: "" });
                  recargarIdentidad();
                })
                .catch(fallo);
            }}
          >
            <input
              value={nuevoWs.slug}
              onChange={(e) =>
                setNuevoWs((s) => ({ ...s, slug: e.target.value }))
              }
              placeholder="slug-equipo"
              aria-label="Slug del workspace"
              className="vidrio w-40 rounded-xl px-3 py-1.5 text-xs outline-none"
            />
            <input
              value={nuevoWs.nombre}
              onChange={(e) =>
                setNuevoWs((s) => ({ ...s, nombre: e.target.value }))
              }
              placeholder="Nombre legible"
              aria-label="Nombre del workspace"
              className="vidrio flex-1 rounded-xl px-3 py-1.5 text-xs outline-none"
            />
            <button
              type="submit"
              disabled={!nuevoWs.slug.trim() || !nuevoWs.nombre.trim()}
              className="flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
              style={{ background: "var(--primary)" }}
            >
              <Plus width={13} height={13} /> Crear
            </button>
          </form>
        </section>

        {/* Miembros */}
        <section className="vidrio-fuerte flex flex-col gap-3 p-5">
          <h2 className="text-sm font-semibold">Miembros de {wsRef}</h2>
          <ul className="flex flex-col gap-2">
            {miembros.map((m) => (
              <li
                key={m.usuario_id}
                className="vidrio flex items-center gap-3 rounded-xl px-3 py-2 text-sm"
              >
                <span className="min-w-0 flex-1 truncate">
                  <strong>{m.handle}</strong>
                  {m.nombre ? (
                    <span className="texto-suave"> · {m.nombre}</span>
                  ) : null}
                </span>
                {soyOwner && m.rol !== "owner" ? (
                  <select
                    aria-label={`Rol de ${m.handle}`}
                    value={m.rol}
                    onChange={(e) => {
                      setError(null);
                      idn
                        ?.cambiarRol(
                          wsRef,
                          m.usuario_id,
                          e.target.value as RolWorkspace,
                        )
                        .then(cargar)
                        .catch(fallo);
                    }}
                    className="vidrio rounded-lg px-2 py-1 text-xs outline-none"
                  >
                    <option value="member">member</option>
                    <option value="viewer">viewer</option>
                  </select>
                ) : (
                  <ChipRol rol={m.rol} />
                )}
                {soyOwner && m.usuario_id !== yo?.usuario?.id && (
                  <button
                    type="button"
                    aria-label={`Expulsar a ${m.handle}`}
                    onClick={() => {
                      setError(null);
                      idn
                        ?.quitarMiembro(wsRef, m.usuario_id)
                        .then(cargar)
                        .catch(fallo);
                    }}
                    className="vidrio rounded-lg p-1.5"
                    style={{ color: "var(--danger)" }}
                  >
                    <Trash width={14} height={14} />
                  </button>
                )}
              </li>
            ))}
          </ul>

          {/* Invitación */}
          {soyOwner && (
            <form
              className="flex flex-wrap items-center gap-2 border-t pt-3"
              style={{ borderColor: "var(--linea)" }}
              onSubmit={(e) => {
                e.preventDefault();
                if (!idn) return;
                setError(null);
                idn
                  .invitar(wsRef, invitacion.rol, invitacion.dias)
                  .then((r) =>
                    setSecreto({
                      tipo: `Invitación a ${wsRef} (${invitacion.rol}, ${invitacion.dias}d)`,
                      valor: r.token,
                    }),
                  )
                  .catch(fallo);
              }}
            >
              <UserPlus width={15} height={15} className="texto-suave" />
              <select
                aria-label="Rol del invitado"
                value={invitacion.rol}
                onChange={(e) =>
                  setInvitacion((s) => ({
                    ...s,
                    rol: e.target.value as "member" | "viewer",
                  }))
                }
                className="vidrio rounded-lg px-2 py-1.5 text-xs outline-none"
              >
                <option value="member">member</option>
                <option value="viewer">viewer</option>
              </select>
              <select
                aria-label="Días de validez"
                value={invitacion.dias}
                onChange={(e) =>
                  setInvitacion((s) => ({ ...s, dias: Number(e.target.value) }))
                }
                className="vidrio rounded-lg px-2 py-1.5 text-xs outline-none"
              >
                {[1, 3, 7, 14, 30].map((d) => (
                  <option key={d} value={d}>
                    {d}d
                  </option>
                ))}
              </select>
              <button
                type="submit"
                className="flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold text-white"
                style={{ background: "var(--primary)" }}
              >
                Generar invitación
              </button>
            </form>
          )}
        </section>

        {/* Llaves */}
        <section className="vidrio-fuerte flex flex-col gap-3 p-5">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Key width={14} height={14} /> Tus llaves de API
          </h2>
          <ul className="flex flex-col gap-2">
            {llaves.map((k) => (
              <li
                key={k.id}
                className="vidrio flex items-center gap-3 rounded-xl px-3 py-2 text-xs"
              >
                <code className="font-mono">{k.prefijo}…</code>
                <span className="texto-suave min-w-0 flex-1 truncate">
                  {k.nombre || "sin nombre"}
                  {k.ultimo_uso
                    ? ` · usada ${new Date(k.ultimo_uso).toLocaleDateString("es")}`
                    : " · nunca usada"}
                </span>
                {k.revocada ? (
                  <span className="texto-suave">revocada</span>
                ) : (
                  <button
                    type="button"
                    aria-label={`Revocar llave ${k.prefijo}`}
                    onClick={() => {
                      setError(null);
                      idn
                        ?.revocarLlave(k.id)
                        .then(cargar)
                        .catch(fallo);
                    }}
                    className="vidrio rounded-lg p-1.5"
                    style={{ color: "var(--danger)" }}
                  >
                    <Trash width={13} height={13} />
                  </button>
                )}
              </li>
            ))}
          </ul>
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!idn) return;
              setError(null);
              idn
                .crearLlave(nombreLlave.trim() || undefined)
                .then((r) => {
                  setNombreLlave("");
                  setSecreto({ tipo: "Llave nueva", valor: r.key });
                  cargar();
                })
                .catch(fallo);
            }}
          >
            <input
              value={nombreLlave}
              onChange={(e) => setNombreLlave(e.target.value)}
              placeholder="Etiqueta (p. ej. cursor-laptop)"
              aria-label="Etiqueta de la llave"
              className="vidrio flex-1 rounded-xl px-3 py-1.5 text-xs outline-none"
            />
            <button
              type="submit"
              className="flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold text-white"
              style={{ background: "var(--primary)" }}
            >
              <Plus width={13} height={13} /> Nueva llave
            </button>
          </form>
        </section>
      </section>
    </Shell>
  );
}
