"use client";

/**
 * Visor de detalle del nodo seleccionado — port del NodeViewer del
 * plugin DSH Memory. Muestra el contenido completo del evento
 * renderizado como markdown, badges de dimensión/estado, sus tags y
 * las memorias conectadas más relevantes como acordeón navegable.
 */

import { NavArrowDown, Xmark } from "iconoir-react";
import { useState, type ReactNode } from "react";

import { COLORES_DIMENSION, COLORES_ESTADO, type Nodo } from "./tipos";

const NOMBRE_ESTADO: Record<Nodo["estado"], string> = {
  fresh: "Recién codificada",
  working: "Memoria de trabajo",
  consolidated: "Consolidada (LTM)",
  decayed: "Traza que decae",
  dead: "Sinapsis muerta",
};

const NOMBRE_TIPO_VINCULO: Record<string, string> = {
  explicita: "declarado",
  semantica: "afinidad",
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

/** Memoria conectada al nodo activo (fila del acordeón). */
export interface MemoriaConectada {
  /** Índice del nodo vecino en el grafo. */
  idx: number;
  etiqueta: string;
  estado: Nodo["estado"];
  /** Tipo de vínculo si viene de `relaciones`; null = lazo estructural. */
  tipo: string | null;
  peso: number;
}

interface Props {
  nodo: Nodo;
  conectadas: MemoriaConectada[];
  alCerrar: () => void;
  /** Salta la selección al nodo vecino pulsado en el acordeón. */
  alSaltar: (idx: number) => void;
  /** Corre el visor a la izquierda cuando el panel de ajustes está abierto. */
  desplazado?: boolean;
}

/** Chip semántico con el color del modo que representa. */
function Badge({ texto, color }: { texto: string; color: string }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.65rem] font-medium"
      style={{
        background: `color-mix(in srgb, ${color} 18%, transparent)`,
        color,
        border: `1px solid color-mix(in srgb, ${color} 40%, transparent)`,
      }}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: color }}
      />
      {texto}
    </span>
  );
}

/** Tarjeta flotante con el detalle completo del evento seleccionado. */
export function VisorNodo({ nodo, conectadas, alCerrar, alSaltar, desplazado }: Props) {
  const [abierto, setAbierto] = useState<number | null>(0);
  return (
    <aside
      className={`vidrio-fuerte pointer-events-auto absolute top-3 bottom-3 z-20 flex w-80 max-w-[85%] flex-col overflow-hidden transition-[right] duration-300 ${
        desplazado ? "right-[19.5rem]" : "right-3"
      }`}
      aria-label="Detalle del evento"
    >
      <header className="flex items-start justify-between gap-2 border-b p-4 pb-3" style={{ borderColor: "var(--linea-vidrio)" }}>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{nodo.etiqueta}</p>
          <p className="texto-suave mt-0.5 text-[0.7rem]">
            {new Date(nodo.timestamp_unix * 1000).toLocaleString("es")}
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <Badge
              texto={nodo.dimension}
              color={COLORES_DIMENSION[nodo.dimension]}
            />
            <Badge
              texto={NOMBRE_ESTADO[nodo.estado]}
              color={COLORES_ESTADO[nodo.estado]}
            />
          </div>
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

      <div className="sin-scrollbar min-h-0 flex-1 overflow-y-auto p-4 text-sm">
        {renderMarkdown(nodo.contenido)}

        {nodo.tags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {nodo.tags.map((tag) => (
              <span
                key={tag}
                className="vidrio rounded-full px-2 py-0.5 text-[0.65rem] font-medium"
                style={{ color: "var(--primary)" }}
              >
                #{tag}
              </span>
            ))}
          </div>
        )}

        {conectadas.length > 0 && (
          <div className="mt-4 border-t pt-2" style={{ borderColor: "var(--linea-vidrio)" }}>
            <p className="texto-suave mb-1.5 text-[0.65rem] font-semibold tracking-wide uppercase">
              Memorias conectadas
            </p>
            <div className="flex flex-col gap-1">
              {conectadas.map((c, i) => {
                const abierta = abierto === i;
                return (
                  <div key={c.idx} className="vidrio rounded-xl">
                    <button
                      type="button"
                      onClick={() => setAbierto(abierta ? null : i)}
                      aria-expanded={abierta}
                      className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
                    >
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ background: COLORES_ESTADO[c.estado] }}
                      />
                      <span className="min-w-0 flex-1 truncate text-[0.7rem] font-medium">
                        {c.etiqueta}
                      </span>
                      {c.tipo && (
                        <span className="texto-suave shrink-0 text-[0.6rem]">
                          {NOMBRE_TIPO_VINCULO[c.tipo] ?? c.tipo}
                        </span>
                      )}
                      <NavArrowDown
                        width={12}
                        height={12}
                        className={`texto-suave shrink-0 transition-transform ${abierta ? "rotate-180" : ""}`}
                      />
                    </button>
                    {abierta && (
                      <div className="px-2.5 pb-2">
                        <button
                          type="button"
                          onClick={() => alSaltar(c.idx)}
                          className="text-[0.65rem] font-medium"
                          style={{ color: "var(--primary)" }}
                        >
                          Ir a la neurona →
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <footer
        className="texto-suave grid grid-cols-2 gap-x-3 gap-y-1.5 border-t p-4 text-[0.7rem]"
        style={{ borderColor: "var(--linea-vidrio)" }}
      >
        <span>Fuente</span>
        <span className="truncate text-right font-medium" style={{ color: "var(--ink)" }}>
          {nodo.fuente}
        </span>
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
