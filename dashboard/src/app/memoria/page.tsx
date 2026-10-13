"use client";

/**
 * Búsqueda de memoria: consulta en lenguaje natural sobre la búsqueda
 * híbrida 4D; cada resultado muestra scores y su contexto temporal.
 */

import { motion } from "motion/react";
import { Search } from "iconoir-react";
import { useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type { ResultadoBusqueda } from "@/domain/modelos";

export default function PaginaMemoria() {
  const { memoria } = usarApp();
  const [consulta, setConsulta] = useState("");
  const [resultados, setResultados] = useState<ResultadoBusqueda[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Ejecuta la consulta contra la API de Nous. */
  async function buscar(ev: React.FormEvent) {
    ev.preventDefault();
    if (!memoria || !consulta.trim()) return;
    setBuscando(true);
    setError(null);
    try {
      setResultados(await memoria.buscar(consulta.trim(), 10));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error de búsqueda");
    } finally {
      setBuscando(false);
    }
  }

  return (
    <Shell>
      <section className="mx-auto flex max-w-3xl flex-col gap-5">
        <form onSubmit={buscar} className="vidrio-fuerte flex gap-3 p-4">
          <input
            type="search"
            value={consulta}
            onChange={(e) => setConsulta(e.target.value)}
            placeholder="¿Qué recuerdas sobre…?"
            aria-label="Consulta de memoria"
            className="vidrio flex-1 rounded-xl px-4 py-2.5 outline-none"
          />
          <button
            type="submit"
            disabled={buscando || !consulta.trim()}
            className="flex items-center gap-2 rounded-xl px-5 py-2.5 font-semibold text-white disabled:opacity-50"
            style={{ background: "var(--primary)" }}
          >
            <Search width={16} height={16} />
            Buscar
          </button>
        </form>

        {error && (
          <p role="alert" className="text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}

        <ul className="flex flex-col gap-3">
          {resultados.map((r, i) => (
            <motion.li
              key={r.evento.id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.06 }}
              className="vidrio p-4"
            >
              <div className="mb-1 flex items-center justify-between gap-3 text-xs">
                <span
                  className="rounded-full px-2 py-0.5 font-medium"
                  style={{
                    background: `var(--dim-${r.evento.dimension})`,
                    color: "#fff",
                  }}
                >
                  {r.evento.dimension}
                </span>
                <span className="texto-suave">
                  score {r.score_final.toFixed(3)} ·{" "}
                  {new Date(r.evento.timestamp_unix * 1000).toLocaleString("es")}
                </span>
              </div>
              <p className="text-sm">{r.evento.contenido}</p>
              {r.contexto_temporal &&
                (r.contexto_temporal.previos.length > 0 ||
                  r.contexto_temporal.siguientes.length > 0) && (
                  <p className="texto-suave mt-2 border-l-2 pl-2 text-xs" style={{ borderColor: "var(--linea)" }}>
                    Antes:{" "}
                    {r.contexto_temporal.previos
                      .map((e) => e.contenido.slice(0, 30))
                      .join(" · ") || "—"}{" "}
                    | Después:{" "}
                    {r.contexto_temporal.siguientes
                      .map((e) => e.contenido.slice(0, 30))
                      .join(" · ") || "—"}
                  </p>
                )}
            </motion.li>
          ))}
        </ul>
        {!buscando && consulta && resultados.length === 0 && !error && (
          <p className="texto-suave text-center text-sm">
            Sin resultados para «{consulta}».
          </p>
        )}
      </section>
    </Shell>
  );
}
