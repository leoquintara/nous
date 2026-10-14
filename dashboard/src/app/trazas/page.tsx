"use client";

/**
 * Trazabilidad: quién llama a Nous, cuándo, con qué latencia.
 *
 * Registro local-first — solo metadata (método, ruta, origen, ms,
 * status), nunca contenido. Los pings/sondas de vida se marcan aparte.
 * El usuario y los agentes de IA se distinguen por `x-nous-origen` o
 * por la familia del User-Agent.
 */

import { motion } from "motion/react";
import { Activity, Refresh } from "iconoir-react";
import { useCallback, useEffect, useState } from "react";

import type { Llamada, LlamadasStats } from "@/domain/modelos";
import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";

/** Color de status: 2xx verde, 3xx azul, 4xx ámbar, 5xx rojo. */
function colorStatus(status: number): string {
  if (status < 300) return "var(--success)";
  if (status < 400) return "var(--primary)";
  if (status < 500) return "var(--warning, #d9a404)";
  return "var(--danger, #c0392b)";
}

/** Formatea ts unix a hora legible corta. */
function hora(ts: number): string {
  return new Date(ts * 1000).toLocaleString("es", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function PaginaTrazas() {
  const { memoria } = usarApp();
  const [llamadas, setLlamadas] = useState<Llamada[]>([]);
  const [stats, setStats] = useState<LlamadasStats | null>(null);
  const [filtro, setFiltro] = useState("");
  const [cargando, setCargando] = useState(false);

  const recargar = useCallback(async () => {
    if (!memoria) return;
    setCargando(true);
    try {
      const [l, s] = await Promise.all([
        memoria.listarLlamadas(300, filtro || undefined),
        memoria.statsLlamadas(),
      ]);
      setLlamadas(l);
      setStats(s);
    } catch {
      /* el error lo muestra la tabla vacía */
    } finally {
      setCargando(false);
    }
  }, [memoria, filtro]);

  useEffect(() => {
    void recargar();
    const t = setInterval(() => void recargar(), 5000);
    return () => clearInterval(t);
  }, [recargar]);

  return (
    <Shell>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-col gap-4"
      >
        <div className="flex items-center gap-3">
          <Activity width={20} height={20} style={{ color: "var(--primary)" }} />
          <div className="flex-1">
            <h1 className="text-lg font-semibold">Trazabilidad</h1>
            <p className="texto-suave text-xs">
              Quién llama a Nous y con qué latencia — solo metadata, nunca
              contenido.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void recargar()}
            disabled={cargando}
            className="vidrio flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-sm"
          >
            <Refresh width={14} height={14} /> Actualizar
          </button>
        </div>

        {/* Tarjetas de agregados */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="vidrio-fuerte rounded-2xl p-4">
            <p className="texto-suave text-xs">Llamadas totales</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">
              {stats?.total ?? "—"}
            </p>
          </div>
          <div className="vidrio-fuerte rounded-2xl p-4">
            <p className="texto-suave text-xs">Pings registrados</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">
              {stats?.pings.total ?? "—"}
            </p>
            {stats?.pings.ultimo && (
              <p className="texto-suave mt-0.5 text-[0.65rem]">
                último {hora(stats.pings.ultimo)}
              </p>
            )}
          </div>
          <div className="vidrio-fuerte col-span-2 rounded-2xl p-4">
            <p className="texto-suave mb-1.5 text-xs">Orígenes</p>
            <div className="flex flex-wrap gap-1.5">
              {stats?.por_origen.slice(0, 6).map((o) => (
                <span
                  key={o.origen}
                  className="vidrio rounded-lg px-2 py-0.5 text-xs"
                >
                  {o.origen} · {o.llamadas}
                </span>
              )) ?? <span className="texto-suave text-xs">—</span>}
            </div>
          </div>
        </div>

        {/* Top rutas con latencia */}
        {stats && stats.por_ruta.length > 0 && (
          <div className="vidrio-fuerte rounded-2xl p-4">
            <p className="texto-suave mb-2 text-xs font-semibold tracking-wide uppercase">
              Rutas más usadas
            </p>
            <div className="flex flex-col gap-1">
              {stats.por_ruta.slice(0, 8).map((r) => (
                <div
                  key={r.ruta}
                  className="flex items-center gap-3 text-xs"
                >
                  <code className="min-w-0 flex-1 truncate">{r.ruta}</code>
                  <span className="texto-suave tabular-nums">
                    {r.llamadas}×
                  </span>
                  <span className="tabular-nums" style={{ color: "var(--primary)" }}>
                    {r.latencia_media}ms
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Filtro + tabla */}
        <input
          type="text"
          value={filtro}
          onChange={(e) => setFiltro(e.target.value)}
          placeholder="Filtrar por ruta exacta (p. ej. /api/eventos)"
          className="vidrio rounded-xl px-3 py-2 text-sm outline-none"
        />
        <div className="vidrio-fuerte overflow-x-auto rounded-2xl">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="texto-suave border-b border-white/10">
                <th className="px-3 py-2 font-medium">Hora</th>
                <th className="px-3 py-2 font-medium">Método</th>
                <th className="px-3 py-2 font-medium">Ruta</th>
                <th className="px-3 py-2 font-medium">Origen</th>
                <th className="px-3 py-2 text-right font-medium">ms</th>
                <th className="px-3 py-2 text-right font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {llamadas.map((l) => (
                <tr key={l.id} className="border-b border-white/5">
                  <td className="texto-suave px-3 py-1.5 whitespace-nowrap tabular-nums">
                    {hora(l.ts)}
                  </td>
                  <td className="px-3 py-1.5 font-mono">{l.metodo}</td>
                  <td className="max-w-56 truncate px-3 py-1.5">
                    {l.es_ping && (
                      <span
                        title="Ping/sonda de vida"
                        style={{ color: "var(--success)" }}
                      >
                        ●{" "}
                      </span>
                    )}
                    {l.ruta}
                  </td>
                  <td className="texto-suave px-3 py-1.5">{l.origen}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {l.latencia_ms}
                  </td>
                  <td
                    className="px-3 py-1.5 text-right font-semibold tabular-nums"
                    style={{ color: colorStatus(l.status) }}
                  >
                    {l.status}
                  </td>
                </tr>
              ))}
              {llamadas.length === 0 && (
                <tr>
                  <td colSpan={6} className="texto-suave px-3 py-6 text-center">
                    Sin llamadas registradas todavía.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </motion.div>
    </Shell>
  );
}
