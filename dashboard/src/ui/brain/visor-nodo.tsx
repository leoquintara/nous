"use client";

/**
 * Visor de detalle del nodo seleccionado — port del NodeViewer del
 * plugin DSH Memory. Muestra el contenido completo del evento
 * renderizado como markdown junto a su metadata de procedencia.
 */

import { Xmark } from "iconoir-react";
import type { ReactNode } from "react";

import type { Nodo } from "./tipos";

const NOMBRE_ESTADO: Record<Nodo["estado"], string> = {
  fresh: "Recién codificada",
  working: "Memoria de trabajo",
  consolidated: "Consolidada (LTM)",
  decayed: "Traza que decae",
  dead: "Sinapsis muerta",
};

/** Renderiza markdown básico a nodos React (sin dependencias). */
function renderMarkdown(texto: string): ReactNode[] {
  const lineas = texto.split("\n");
  const salida: ReactNode[] = [];
  let enCodigo = false;
  let codigo: string[] = [];
  let lista: string[] = [];

  const volcarLista = (clave: string) => {
    if (lista.length === 0) return;
    salida.push(
      <ul key={clave} className="my-2 list-disc space-y-1 pl-5">
        {lista.map((l, i) => (
          <li key={i}>{inline(l)}</li>
        ))}
      </ul>,
    );
    lista = [];
  };

  const inline = (l: string): ReactNode[] => {
    const partes = l.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g);
    return partes.map((p, i) => {
      if (p.startsWith("**") && p.endsWith("**")) {
        return <strong key={i}>{p.slice(2, -2)}</strong>;
      }
      if (p.startsWith("*") && p.endsWith("*") && p.length > 2) {
        return <em key={i}>{p.slice(1, -1)}</em>;
      }
      if (p.startsWith("`") && p.endsWith("`") && p.length > 2) {
        return (
          <code
            key={i}
            className="rounded px-1 py-0.5 text-[0.85em]"
            style={{ background: "var(--linea)" }}
          >
            {p.slice(1, -1)}
          </code>
        );
      }
      return p;
    });
  };

  lineas.forEach((linea, idx) => {
    const k = `l${idx}`;
    if (linea.trim().startsWith("```")) {
      if (enCodigo) {
        salida.push(
          <pre
            key={k}
            className="vidrio my-2 overflow-x-auto rounded-xl p-3 text-xs"
          >
            <code>{codigo.join("\n")}</code>
          </pre>,
        );
        codigo = [];
      }
      enCodigo = !enCodigo;
      return;
    }
    if (enCodigo) {
      codigo.push(linea);
      return;
    }
    const h = /^(#{1,3})\s+(.*)/.exec(linea);
    if (h) {
      volcarLista(k);
      const Tag = `h${Math.min(3, h[1].length + 1)}` as "h2" | "h3" | "h4";
      salida.push(
        <Tag key={k} className="mt-3 mb-1 font-semibold first:mt-0">
          {inline(h[2])}
        </Tag>,
      );
      return;
    }
    const li = /^\s*[-•*]\s+(.*)/.exec(linea);
    if (li) {
      lista.push(li[1]);
      return;
    }
    volcarLista(k);
    if (linea.trim() === "") {
      salida.push(<div key={k} className="h-2" />);
      return;
    }
    salida.push(
      <p key={k} className="my-1 leading-relaxed">
        {inline(linea)}
      </p>,
    );
  });
  volcarLista("fin");
  if (enCodigo && codigo.length) {
    salida.push(
      <pre
        key="codigo-final"
        className="vidrio my-2 overflow-x-auto rounded-xl p-3 text-xs"
      >
        <code>{codigo.join("\n")}</code>
      </pre>,
    );
  }
  return salida;
}

interface Props {
  nodo: Nodo;
  alCerrar: () => void;
}

/** Tarjeta flotante con el detalle completo del evento seleccionado. */
export function VisorNodo({ nodo, alCerrar }: Props) {
  return (
    <aside
      className="vidrio-fuerte pointer-events-auto absolute top-3 right-3 bottom-3 z-20 flex w-80 max-w-[85%] flex-col overflow-hidden"
      aria-label="Detalle del evento"
    >
      <header className="flex items-start justify-between gap-2 border-b p-4 pb-3" style={{ borderColor: "var(--linea-vidrio)" }}>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{nodo.etiqueta}</p>
          <p className="texto-suave mt-0.5 text-[0.7rem]">
            {new Date(nodo.timestamp_unix * 1000).toLocaleString("es")}
          </p>
        </div>
        <button
          type="button"
          onClick={alCerrar}
          aria-label="Cerrar detalle"
          className="texto-suave rounded-lg p-1 transition-colors hover:text-(--ink)"
        >
          <Xmark width={18} height={18} />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 text-sm">
        {renderMarkdown(nodo.contenido)}
      </div>

      <footer
        className="texto-suave grid grid-cols-2 gap-x-3 gap-y-1.5 border-t p-4 text-[0.7rem]"
        style={{ borderColor: "var(--linea-vidrio)" }}
      >
        <span>Dimensión</span>
        <span className="text-right font-medium" style={{ color: "var(--ink)" }}>
          {nodo.dimension}
        </span>
        <span>Fuente</span>
        <span className="truncate text-right font-medium" style={{ color: "var(--ink)" }}>
          {nodo.fuente}
        </span>
        <span>Estado</span>
        <span className="text-right">{NOMBRE_ESTADO[nodo.estado]}</span>
        <span>Amplitud</span>
        <span className="text-right">{nodo.amplitud.toFixed(2)}</span>
        <span>Refuerzos</span>
        <span className="text-right">{nodo.refuerzos}</span>
        <span>Conexiones</span>
        <span className="text-right">{nodo.deg}</span>
        {nodo.pinned && (
          <>
            <span>Fijado</span>
            <span className="text-right">doble clic suelta</span>
          </>
        )}
      </footer>
    </aside>
  );
}
