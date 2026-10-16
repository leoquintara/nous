"use client";

/**
 * Catálogo de fuentes: gestión completa de procedencias registradas.
 * Los eventos de una fuente permanecen aunque el registro se archive o
 * se elimine — la fuente es metadata simbólica sobre la línea de universo.
 */

import { motion } from "motion/react";
import {
  Archive,
  Check,
  Database,
  GitFork,
  Plus,
  Refresh,
  Trash,
  Xmark,
} from "iconoir-react";
import { useCallback, useEffect, useState } from "react";

import { useApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type { FuenteInfo } from "@/domain/modelos";

/** Tipos de procedencia soportados por el catálogo. */
const TIPOS = ["manual", "obsidian", "gbrain", "memlord", "agente", "import"];

/** Fila del catálogo con sus acciones de ciclo de vida. */
function FilaFuente({
  fuente,
  alArchivar,
  alRamificar,
  alEliminar,
}: {
  fuente: FuenteInfo;
  alArchivar: (f: FuenteInfo) => void;
  alRamificar: (f: FuenteInfo, rama: string) => void;
  alEliminar: (f: FuenteInfo) => void;
}) {
  const [pidiendoRama, setPidiendoRama] = useState(false);
  const [rama, setRama] = useState("");
  const archivada = fuente.estado === "archivada";
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={`vidrio p-4 ${archivada ? "opacity-60" : ""}`}
    >
      <div className="flex items-center gap-3">
        <Database width={18} height={18} style={{ color: "var(--primary)" }} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">
            {fuente.nombre}
            {archivada && (
              <span className="texto-suave ml-2 text-[0.65rem] font-normal italic">
                archivada — sus eventos siguen en la línea
              </span>
            )}
          </p>
          <p className="texto-suave mt-0.5 text-xs">
            {fuente.tipo}
            {fuente.namespace && fuente.namespace !== fuente.nombre
              ? ` · ${fuente.namespace}`
              : ""}
            {fuente.rama_origen !== "main" && ` · rama ${fuente.rama_origen}`}
          </p>
        </div>
        <span className="texto-suave text-xs tabular-nums">
          {fuente.eventos} eventos
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => alArchivar(fuente)}
            title={archivada ? "Reactivar fuente" : "Archivar (los eventos permanecen)"}
            aria-label={`${archivada ? "Reactivar" : "Archivar"} ${fuente.nombre}`}
            className="texto-suave rounded-lg p-1.5 transition-transform duration-200 hover:scale-110 hover:text-(--ink)"
          >
            <Archive width={15} height={15} />
          </button>
          <button
            type="button"
            onClick={() => setPidiendoRama((p) => !p)}
            title="Ramificar en línea de universo paralela"
            aria-label={`Ramificar ${fuente.nombre}`}
            className="texto-suave rounded-lg p-1.5 transition-transform duration-200 hover:scale-110 hover:text-(--ink)"
          >
            <GitFork width={15} height={15} />
          </button>
          <button
            type="button"
            onClick={() => alEliminar(fuente)}
            title="Eliminar registro (los eventos permanecen)"
            aria-label={`Eliminar ${fuente.nombre}`}
            className="texto-suave rounded-lg p-1.5 transition-transform duration-200 hover:scale-110 hover:text-(--danger)"
          >
            <Trash width={15} height={15} />
          </button>
        </div>
      </div>
      {pidiendoRama && (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (rama.trim()) alRamificar(fuente, rama.trim());
            setPidiendoRama(false);
            setRama("");
          }}
        >
          <input
            value={rama}
            onChange={(e) => setRama(e.target.value)}
            placeholder="nombre de la rama paralela…"
            aria-label="Nombre de la rama"
            className="vidrio min-w-0 flex-1 rounded-lg px-2.5 py-1.5 text-xs outline-none"
            autoFocus
          />
          <button
            type="submit"
            disabled={!rama.trim()}
            className="vidrio rounded-lg px-3 py-1 text-xs hover:brightness-110 disabled:opacity-40"
          >
            ramificar
          </button>
        </form>
      )}
    </motion.li>
  );
}

/** Página del catálogo de fuentes. */
export default function PaginaFuentes() {
  const { memoria } = useApp();
  const [fuentes, setFuentes] = useState<FuenteInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creando, setCreando] = useState(false);
  const [nueva, setNueva] = useState({ nombre: "", tipo: "manual", namespace: "" });

  /** Recarga el catálogo desde la API. */
  const cargar = useCallback(() => {
    if (!memoria) return;
    memoria
      .listarFuentes()
      .then(setFuentes)
      .catch((e) => setError(e instanceof Error ? e.message : "Error al listar"));
  }, [memoria]);

  useEffect(() => {
    const t = setTimeout(cargar, 0);
    return () => clearTimeout(t);
  }, [cargar]);

  /** Ejecuta una acción del catálogo y recarga al terminar. */
  const accion = (p: Promise<unknown> | undefined) => {
    setError(null);
    p?.then(cargar).catch((e) =>
      setError(e instanceof Error ? e.message : "Error en la acción"),
    );
  };

  return (
    <Shell>
      <section className="mx-auto flex w-full max-w-3xl flex-col gap-5">
        <header className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">Fuentes</h1>
            <p className="texto-suave text-xs">
              Procedencias registradas · eliminar el registro nunca borra
              eventos
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={cargar}
              aria-label="Recargar catálogo"
              className="texto-suave vidrio rounded-xl p-2 transition-transform duration-200 hover:scale-110 hover:text-(--ink)"
            >
              <Refresh width={15} height={15} />
            </button>
            <button
              type="button"
              onClick={() => setCreando((c) => !c)}
              className="flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold text-white"
              style={{ background: "var(--primary)" }}
            >
              {creando ? (
                <Xmark width={13} height={13} />
              ) : (
                <Plus width={13} height={13} />
              )}
              Nueva fuente
            </button>
          </div>
        </header>

        {creando && (
          <motion.form
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            className="vidrio-fuerte flex flex-wrap items-center gap-2 p-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!nueva.nombre.trim()) return;
              accion(
                memoria?.crearFuente({
                  nombre: nueva.nombre.trim(),
                  tipo: nueva.tipo,
                  namespace: nueva.namespace.trim() || undefined,
                }).then(() => {
                  setCreando(false);
                  setNueva({ nombre: "", tipo: "manual", namespace: "" });
                }),
              );
            }}
          >
            <input
              value={nueva.nombre}
              onChange={(e) => setNueva({ ...nueva, nombre: e.target.value })}
              placeholder="nombre"
              aria-label="Nombre de la fuente"
              className="vidrio min-w-40 flex-1 rounded-xl px-3 py-2 text-sm outline-none"
              autoFocus
            />
            <select
              value={nueva.tipo}
              onChange={(e) => setNueva({ ...nueva, tipo: e.target.value })}
              aria-label="Tipo de fuente"
              className="vidrio rounded-xl px-2 py-2 text-sm"
            >
              {TIPOS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              value={nueva.namespace}
              onChange={(e) => setNueva({ ...nueva, namespace: e.target.value })}
              placeholder="namespace (opcional)"
              aria-label="Namespace"
              className="vidrio min-w-32 flex-1 rounded-xl px-3 py-2 text-sm outline-none"
            />
            <button
              type="submit"
              disabled={!nueva.nombre.trim()}
              className="flex items-center gap-1.5 rounded-xl px-4 py-2 text-xs font-semibold text-white disabled:opacity-40"
              style={{ background: "var(--primary)" }}
            >
              <Check width={13} height={13} /> Registrar
            </button>
          </motion.form>
        )}

        {error && (
          <p role="alert" className="text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}

        {fuentes === null ? (
          <p className="texto-suave text-sm">Cargando catálogo…</p>
        ) : fuentes.length === 0 ? (
          <p className="texto-suave vidrio p-6 text-center text-sm">
            Sin fuentes registradas — las fuentes se registran solas al
            recordar con <code>fuente=</code> o desde aquí.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {fuentes.map((f) => (
              <FilaFuente
                key={f.nombre}
                fuente={f}
                alArchivar={(fu) =>
                  accion(
                    memoria?.estadoFuente(
                      fu.nombre,
                      fu.estado === "activa" ? "archivada" : "activa",
                    ),
                  )
                }
                alRamificar={(fu, rama) =>
                  accion(memoria?.ramificarFuente(fu.nombre, rama))
                }
                alEliminar={(fu) => accion(memoria?.eliminarFuente(fu.nombre))}
              />
            ))}
          </ul>
        )}
      </section>
    </Shell>
  );
}
