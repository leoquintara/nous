"use client";

/**
 * Vista principal: cerebro 3D con los eventos de los últimos 90 días
 * más panel lateral de detalle del evento seleccionado.
 */

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import { eventosAGrafo } from "@/ui/brain/tipos";
import type { CerebroHandle } from "@/ui/brain/CerebroCanvas";

const CerebroCanvas = dynamic(
  () =>
    import("@/ui/brain/CerebroCanvas").then((m) => ({
      default: m.CerebroCanvas,
    })),
  { ssr: false },
);

const DIA = 86_400;

export default function PaginaCerebro() {
  const { memoria } = usarApp();
  const [datos, setDatos] = useState<ReturnType<typeof eventosAGrafo> | null>(
    null,
  );
  const [sel, setSel] = useState<{ index: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pensarRef = useRef<CerebroHandle | null>(null);

  useEffect(() => {
    if (!memoria) return;
    const ahora = Math.floor(Date.now() / 1000);
    memoria
      .listarEventos(ahora - 90 * DIA, ahora, 500)
      .then((eventos) => setDatos(eventosAGrafo(eventos)))
      .catch((e: Error) => setError(e.message));
  }, [memoria]);

  const nodoSel = useMemo(
    () => (sel && datos ? datos.nodes[sel.index] : null),
    [sel, datos],
  );

  return (
    <Shell>
      <div className="grid h-[calc(100dvh-7rem)] grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
        <section
          className="vidrio relative overflow-hidden rounded-3xl"
          aria-label="Cerebro de memoria 3D"
        >
          {error && (
            <p className="p-6 text-sm" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}
          {!error && (!datos || datos.status !== "ready") && (
            <div className="texto-suave flex h-full items-center justify-center text-sm">
              {datos?.status === "vacio"
                ? "Sin eventos en los últimos 90 días."
                : "Cargando memoria…"}
            </div>
          )}
          {datos?.status === "ready" && (
            <CerebroCanvas
              datos={datos}
              seleccion={sel}
              alSeleccionar={setSel}
              onPensarRef={pensarRef}
            />
          )}
          <div className="texto-suave pointer-events-none absolute bottom-3 left-4 flex gap-4 text-xs">
            <Leyenda color="var(--dim-cognitiva)" nombre="Cognitiva" />
            <Leyenda color="var(--dim-somatica)" nombre="Somática" />
            <Leyenda color="var(--dim-conductual)" nombre="Conductual" />
          </div>
        </section>

        <aside className="vidrio-fuerte flex flex-col gap-3 overflow-auto p-5">
          <h2 className="text-sm font-semibold tracking-wide uppercase">
            Evento
          </h2>
          {nodoSel ? (
            <>
              <p className="text-sm">{nodoSel.etiqueta}</p>
              <dl className="texto-suave grid grid-cols-2 gap-2 text-xs">
                <dt>Dimensión</dt>
                <dd className="font-medium" style={{ color: "var(--ink)" }}>
                  {nodoSel.dimension}
                </dd>
                <dt>Estado</dt>
                <dd>{nodoSel.estado}</dd>
                <dt>Amplitud</dt>
                <dd>{nodoSel.amplitud.toFixed(2)}</dd>
                <dt>Fecha</dt>
                <dd>
                  {new Date(nodoSel.timestamp_unix * 1000).toLocaleString("es")}
                </dd>
              </dl>
            </>
          ) : (
            <p className="texto-suave text-sm">
              Toca una neurona para ver el evento. Arrastra para fijarla;
              doble clic para soltarla.
            </p>
          )}
        </aside>
      </div>
    </Shell>
  );
}

/** Punto de leyenda de una dimensión. */
function Leyenda({ color, nombre }: { color: string; nombre: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ background: color }}
      />
      {nombre}
    </span>
  );
}
