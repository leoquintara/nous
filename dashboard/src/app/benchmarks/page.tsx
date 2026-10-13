"use client";

/**
 * Benchmarks: lista las corridas persistidas con sus métricas clave.
 */

import { Refresh } from "iconoir-react";
import { useEffect, useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type { BenchmarkRun } from "@/domain/modelos";

export default function PaginaBenchmarks() {
  const { memoria } = usarApp();
  const [corridas, setCorridas] = useState<BenchmarkRun[]>([]);
  const [error, setError] = useState<string | null>(null);

  /** Carga las corridas de benchmark desde la API. */
  async function cargar() {
    if (!memoria) return;
    try {
      setCorridas(await memoria.benchmarks());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar");
    }
  }

  useEffect(() => {
    void cargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memoria]);

  return (
    <Shell>
      <section className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Benchmarks</h1>
          <button
            onClick={cargar}
            className="vidrio flex items-center gap-2 rounded-xl px-3 py-1.5 text-sm"
          >
            <Refresh width={14} height={14} /> Actualizar
          </button>
        </div>
        {error && (
          <p role="alert" className="text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
        <div className="vidrio overflow-x-auto p-2">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="texto-suave text-xs uppercase">
                <th className="px-3 py-2">Nombre</th>
                <th className="px-3 py-2">Escenario</th>
                <th className="px-3 py-2">Commit</th>
                <th className="px-3 py-2">Fecha</th>
                <th className="px-3 py-2">Métricas</th>
              </tr>
            </thead>
            <tbody>
              {corridas.map((c) => (
                <tr key={c.id} className="borde-linea border-t">
                  <td className="px-3 py-2 font-medium">{c.nombre}</td>
                  <td className="texto-suave px-3 py-2">
                    {c.escenario ?? "—"}
                  </td>
                  <td className="texto-suave px-3 py-2 font-mono text-xs">
                    {c.git_commit?.slice(0, 7) ?? "—"}
                  </td>
                  <td className="texto-suave px-3 py-2">
                    {new Date(c.creado_en).toLocaleString("es")}
                  </td>
                  <td className="texto-suave px-3 py-2 font-mono text-xs">
                    {Object.entries(c.metricas)
                      .slice(0, 4)
                      .map(
                        ([k, v]) =>
                          `${k}=${
                            typeof v === "object" && v !== null
                              ? JSON.stringify(v).slice(0, 40)
                              : String(v).slice(0, 24)
                          }`,
                      )
                      .join("  ")}
                  </td>
                </tr>
              ))}
              {corridas.length === 0 && !error && (
                <tr>
                  <td colSpan={5} className="texto-suave px-3 py-8 text-center">
                    Sin corridas. Ejecuta{" "}
                    <code>python -m scripts.benchmark_search</code> en el
                    servidor.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </Shell>
  );
}
