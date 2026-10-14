"use client";

/**
 * Panel de análisis IA reutilizable: muestra la respuesta del motor
 * predictivo — qué proveedor/modelo respondió, si vino vía fallback,
 * su latencia, el razonamiento y los escenarios con probabilidad.
 */

import { motion } from "motion/react";
import { Cpu, Xmark } from "iconoir-react";

import type { PrediccionIA } from "@/domain/modelos";

export function PanelIA({
  prediccion,
  alCerrar,
  titulo = "Análisis del modelo",
}: {
  prediccion: PrediccionIA;
  alCerrar?: () => void;
  titulo?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="vidrio-fuerte flex max-h-96 flex-col gap-3 overflow-y-auto rounded-2xl p-4"
    >
      {prediccion.ok ? (
        <>
          <header className="flex items-start justify-between gap-2">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold">
              <Cpu width={15} height={15} style={{ color: "var(--primary)" }} />
              {titulo}
            </h2>
            <span className="texto-suave flex items-center gap-1 text-[0.65rem]">
              {prediccion.proveedor} · {prediccion.modelo}
              {prediccion.latencia_ms != null &&
                ` · ${prediccion.latencia_ms}ms`}
              {prediccion.fallback && (
                <span
                  className="rounded-full px-1.5 py-0.5 text-[0.6rem]"
                  style={{
                    background: "var(--halo)",
                    color: "var(--primary)",
                  }}
                >
                  vía fallback
                </span>
              )}
              {alCerrar && (
                <button
                  type="button"
                  onClick={alCerrar}
                  aria-label="Cerrar análisis"
                  className="texto-suave ml-1 rounded p-0.5 hover:text-(--ink)"
                >
                  <Xmark width={12} height={12} />
                </button>
              )}
            </span>
          </header>
          {prediccion.razonamiento && (
            <p className="texto-suave text-xs italic">
              {prediccion.razonamiento}
            </p>
          )}
          <ul className="flex flex-col gap-1.5">
            {(prediccion.escenarios ?? []).map((e, i) => (
              <li key={i} className="vidrio flex flex-col gap-1 rounded-xl p-2.5">
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="font-medium">{e.nombre}</span>
                  <span
                    className="shrink-0 tabular-nums"
                    style={{ color: "var(--primary)" }}
                  >
                    {(e.probabilidad * 100).toFixed(0)}%
                  </span>
                </div>
                <div
                  className="vidrio h-1 overflow-hidden rounded-full"
                  role="progressbar"
                  aria-valuenow={Math.round(e.probabilidad * 100)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Probabilidad ${e.nombre}`}
                >
                  <motion.div
                    className="h-full rounded-full"
                    style={{ background: "var(--primary)" }}
                    initial={{ width: 0 }}
                    animate={{ width: `${e.probabilidad * 100}%` }}
                    transition={{ delay: i * 0.06 }}
                  />
                </div>
                {e.descripcion && (
                  <p className="texto-suave text-[0.65rem]">{e.descripcion}</p>
                )}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="texto-suave flex items-center gap-2 text-xs">
          <Cpu width={14} height={14} />
          El motor predictivo no respondió: {prediccion.motivo}
        </p>
      )}
    </motion.div>
  );
}
