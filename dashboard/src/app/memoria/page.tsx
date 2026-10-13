"use client";

/**
 * Búsqueda de memoria: consulta en lenguaje natural sobre la búsqueda
 * híbrida 4D; cada resultado muestra scores y su contexto temporal.
 * Bajo el buscador, la "memoria fresca": los eventos activos de los
 * últimos N días — ventana configurable persistida en PostgreSQL.
 */

import { motion } from "motion/react";
import { Search } from "iconoir-react";
import { useCallback, useEffect, useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type { Evento, ResultadoBusqueda } from "@/domain/modelos";

/** Ventanas de frescura ofrecidas (en días). */
const VENTANAS = [1, 7, 14, 30, 90];
/** Clave del documento de preferencias donde vive la ventana. */
const CLAVE_PREFS = "memoria";
const DIA_S = 86400;

/** Lee la ventana persistida, o el default de 7 días. */
function diasDesde(doc: Record<string, unknown>): number {
  const d = Number(doc.dias);
  return VENTANAS.includes(d) ? d : 7;
}

/** Chip de dimensión reutilizado por resultados y memoria fresca. */
function ChipDimension({ dimension }: { dimension: Evento["dimension"] }) {
  return (
    <span
      className="rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ background: `var(--dim-${dimension})`, color: "#fff" }}
    >
      {dimension}
    </span>
  );
}

/** Página de consulta y memoria fresca. */
export default function PaginaMemoria() {
  const { memoria } = usarApp();
  const [consulta, setConsulta] = useState("");
  const [resultados, setResultados] = useState<ResultadoBusqueda[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dias, setDias] = useState<number | null>(null);
  const [frescos, setFrescos] = useState<Evento[]>([]);

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

  /** Carga los eventos de la ventana de frescura elegida. */
  const cargarFrescos = useCallback(
    (ventana: number) => {
      if (!memoria) return;
      const hasta = Math.floor(Date.now() / 1000);
      memoria
        .listarEventos(hasta - ventana * DIA_S, hasta, 80)
        .then((evs) => setFrescos(evs.toReversed()))
        .catch(() => setFrescos([]));
    },
    [memoria],
  );

  // Hidrata la ventana desde PostgreSQL; luego carga la lista.
  useEffect(() => {
    if (!memoria) return;
    let vivo = true;
    memoria
      .leerPreferencias(CLAVE_PREFS)
      .then((doc) => (vivo ? diasDesde(doc) : 7))
      .catch(() => 7)
      .then((d) => {
        if (!vivo) return;
        setDias(d);
        cargarFrescos(d);
      });
    return () => {
      vivo = false;
    };
  }, [memoria, cargarFrescos]);

  /** Cambia la ventana, la persiste en DB y recarga la lista. */
  const cambiarVentana = (d: number) => {
    setDias(d);
    cargarFrescos(d);
    memoria?.guardarPreferencias(CLAVE_PREFS, { dias: d }).catch(() => {
      /* preferencia no persistida — la sesión conserva el valor */
    });
  };

  return (
    <Shell>
      <section className="mx-auto flex w-full max-w-3xl flex-col gap-5">
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

        {consulta && resultados.length > 0 ? (
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
                  <ChipDimension dimension={r.evento.dimension} />
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
        ) : (
          !buscando &&
          consulta &&
          !error && (
            <p className="texto-suave text-center text-sm">
              Sin resultados para «{consulta}».
            </p>
          )
        )}

        <section aria-label="Memoria fresca" className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold">Memoria fresca</h2>
            <div
              className="vidrio flex items-center gap-0.5 rounded-xl p-0.5"
              role="group"
              aria-label="Ventana de frescura en días"
            >
              {VENTANAS.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => cambiarVentana(d)}
                  aria-pressed={dias === d}
                  className={`rounded-lg px-2.5 py-1 text-[0.7rem] transition-colors ${
                    dias === d
                      ? "font-semibold text-white"
                      : "texto-suave hover:text-(--ink)"
                  }`}
                  style={dias === d ? { background: "var(--primary)" } : undefined}
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>
          {frescos.length === 0 ? (
            <p className="texto-suave vidrio p-4 text-center text-xs">
              {dias === null
                ? "Cargando preferencias…"
                : `Sin eventos en los últimos ${dias} día${dias === 1 ? "" : "s"}.`}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {frescos.map((ev, i) => (
                <motion.li
                  key={ev.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: Math.min(i * 0.03, 0.5) }}
                  className="vidrio flex items-start gap-3 p-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm">{ev.contenido}</p>
                    <p className="texto-suave mt-1 flex flex-wrap items-center gap-2 text-[0.65rem]">
                      <ChipDimension dimension={ev.dimension} />
                      <span>
                        {new Date(ev.timestamp_unix * 1000).toLocaleString("es")}
                      </span>
                      {ev.fuente && <span>· {ev.fuente}</span>}
                      {ev.tags.length > 0 && (
                        <span className="texto-suave">
                          {ev.tags.map((t) => `#${t}`).join(" ")}
                        </span>
                      )}
                    </p>
                  </div>
                </motion.li>
              ))}
            </ul>
          )}
        </section>
      </section>
    </Shell>
  );
}
