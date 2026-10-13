"use client";

/**
 * Proyección temporal: genera una proyección dimensional (persistida en
 * el backend) y muestra la distribución probable y las trayectorias.
 */

import { motion } from "motion/react";
import { Timer } from "iconoir-react";
import { useEffect, useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type { Proyeccion } from "@/domain/modelos";

export default function PaginaProyeccion() {
  const { memoria } = usarApp();
  const [proy, setProy] = useState<Proyeccion | null>(null);
  const [horizonte, setHorizonte] = useState(30);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    memoria
      ?.proyeccionActual()
      .then((p) => {
        if ("dimension_probable" in p) setProy(p);
      })
      .catch(() => {});
  }, [memoria]);

  /** Genera una proyección nueva con el horizonte elegido. */
  async function generar() {
    if (!memoria) return;
    setCargando(true);
    setError(null);
    try {
      setProy(await memoria.proyectar(horizonte, 30));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al proyectar");
    } finally {
      setCargando(false);
    }
  }

  return (
    <Shell>
      <section className="mx-auto flex max-w-3xl flex-col gap-5">
        <div className="vidrio-fuerte flex flex-wrap items-end gap-4 p-5">
          <label className="flex flex-col gap-1 text-sm">
            <span className="texto-suave">Horizonte (días)</span>
            <input
              type="number"
              min={1}
              max={90}
              value={horizonte}
              onChange={(e) => setHorizonte(Number(e.target.value))}
              className="vidrio w-28 rounded-xl px-3 py-2 outline-none"
            />
          </label>
          <button
            onClick={generar}
            disabled={cargando}
            className="flex items-center gap-2 rounded-xl px-5 py-2.5 font-semibold text-white disabled:opacity-50"
            style={{ background: "var(--primary)" }}
          >
            <Timer width={16} height={16} />
            {cargando ? "Proyectando…" : "Proyectar"}
          </button>
        </div>

        {error && (
          <p role="alert" className="text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}

        {proy && (
          <div className="vidrio flex flex-col gap-4 p-5">
            <header className="flex items-baseline justify-between">
              <h2 className="font-semibold">
                Dimensión probable:{" "}
                <span style={{ color: "var(--primary)" }}>
                  {proy.dimension_probable}
                </span>
              </h2>
              <span className="texto-suave text-sm">
                confianza {(proy.probabilidad * 100).toFixed(0)}% ·{" "}
                {proy.eventos_analizados ?? "—"} eventos
              </span>
            </header>
            <ul className="flex flex-col gap-2">
              {Object.entries(proy.distribucion).map(([dim, p], i) => (
                <li key={dim} className="flex items-center gap-3 text-sm">
                  <span className="w-24 capitalize">{dim}</span>
                  <div
                    className="vidrio h-2.5 flex-1 overflow-hidden rounded-full"
                    role="progressbar"
                    aria-valuenow={Math.round(p * 100)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`Probabilidad ${dim}`}
                  >
                    <motion.div
                      className="h-full rounded-full"
                      style={{ background: `var(--dim-${dim}, var(--primary))` }}
                      initial={{ width: 0 }}
                      animate={{ width: `${p * 100}%` }}
                      transition={{ delay: i * 0.08 }}
                    />
                  </div>
                  <span className="texto-suave w-10 text-right">
                    {(p * 100).toFixed(0)}%
                  </span>
                </li>
              ))}
            </ul>
            {proy.trayectorias && proy.trayectorias.length > 0 && (
              <ul className="texto-suave mt-1 flex flex-col gap-1 text-xs">
                {proy.trayectorias.map((t, i) => (
                  <li key={i}>
                    {t.descripcion} — {t.eventos} eventos
                    {t.activa ? " (activa)" : ""}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </Shell>
  );
}
