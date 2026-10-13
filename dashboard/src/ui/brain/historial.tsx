"use client";

/**
 * Historial de actividad cerebral — port del widget ◷ del plugin DSH
 * Memory. Lista los store/recall recientes y permite re-reproducir la
 * traza sináptica de cualquiera de ellos en el grafo.
 */

import { Clock, Play } from "iconoir-react";
import { useState } from "react";

import type { EventoActividad } from "@/domain/modelos";

const NOMBRE_KIND: Record<string, string> = {
  store: "escritura",
  recall: "evocación",
  search: "búsqueda",
  session: "sesión",
  inject: "inyección",
};

const COLOR_KIND: Record<string, string> = {
  store: "#f59e0b",
  recall: "#c084fc",
  search: "#7dd3fc",
  session: "#86efac",
  inject: "#a8c7ff",
};

function horaRelativa(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86_400)}d`;
}

interface Props {
  eventos: EventoActividad[];
  alReproducir: (ev: EventoActividad) => void;
}

/** Panel flotante con la actividad reciente y botón de replay. */
export function HistorialActividad({ eventos, alReproducir }: Props) {
  const [abierto, setAbierto] = useState(false);
  return (
    <div className="pointer-events-auto flex w-64 flex-col gap-2 p-3">
      <button
        type="button"
        onClick={() => setAbierto((a) => !a)}
        className="flex items-center gap-2 text-left text-[0.65rem] font-semibold tracking-wide uppercase"
        aria-expanded={abierto}
      >
        <Clock width={13} height={13} className="texto-suave" />
        <span className="texto-suave">Actividad</span>
        {eventos.length > 0 && (
          <span
            className="ml-auto rounded-full px-1.5 text-[0.6rem] font-bold text-white"
            style={{ background: "var(--primary)" }}
          >
            {eventos.length}
          </span>
        )}
      </button>
      {abierto && (
        <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto">
          {eventos.length === 0 && (
            <li className="texto-suave px-1 text-[0.7rem]">
              Sin actividad reciente.
            </li>
          )}
          {eventos.map((ev, i) => (
            <li key={`${ev.ts}-${i}`}>
              <button
                type="button"
                onClick={() => alReproducir(ev)}
                title="Reproducir traza sináptica"
                className="group flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-left hover:bg-(--halo)"
              >
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: COLOR_KIND[ev.kind] ?? "#94a3b8" }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[0.7rem] font-medium">
                    {ev.q || "—"}
                  </span>
                  <span className="texto-suave text-[0.6rem]">
                    {NOMBRE_KIND[ev.kind] ?? ev.kind} ·{" "}
                    {ev.ids.length ? `${ev.ids.length} memorias · ` : ""}
                    {horaRelativa(ev.ts)}
                  </span>
                </span>
                <Play
                  width={12}
                  height={12}
                  className="texto-suave shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
