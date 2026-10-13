"use client";

/**
 * Controles del cerebro 3D — port de la UI del plugin DSH Memory:
 * panel de ajustes (⚙), panel de fuentes con filtro y tarjeta de
 * estadísticas con ciclo de modo de color, leyenda y refresco.
 */

import {
  Eye,
  EyeClosed,
  NavArrowDown,
  NavArrowUp,
  Palette,
  Plus,
  Refresh,
  Trash,
} from "iconoir-react";
import { useState, type ReactNode } from "react";

import { REGLAS_DEFAULT, type CampoRegla, type OpRegla, type Regla } from "./reglas";
import {
  COLORES_DIMENSION,
  COLORES_ESTADO,
  colorFuente,
  type DatosCerebro,
  type Fuente,
  type ModoColor,
  type PrefsCerebro,
} from "./tipos";

/** Actualiza una preferencia y dispara la persistencia. */
type SetPref = <K extends keyof PrefsCerebro>(
  clave: K,
  valor: PrefsCerebro[K],
) => void;

export const NOMBRE_MODO: Record<ModoColor, string> = {
  dimension: "Dimensión",
  estado: "Estado",
  fuente: "Fuente",
  reglas: "Reglas",
};

const ORDEN_MODOS: ModoColor[] = ["dimension", "estado", "fuente", "reglas"];

/** Fila etiquetada del panel de ajustes. */
function Fila({ etiqueta, children }: { etiqueta: string; children: ReactNode }) {
  return (
    <label className="flex items-center justify-between gap-3 text-xs">
      <span className="texto-suave">{etiqueta}</span>
      <span className="flex items-center gap-2">{children}</span>
    </label>
  );
}

/** Slider compacto de 0..max con etiqueta de valor. */
function Deslizador({
  valor,
  min = 0,
  max = 1,
  paso = 0.05,
  alCambiar,
}: {
  valor: number;
  min?: number;
  max?: number;
  paso?: number;
  alCambiar: (v: number) => void;
}) {
  return (
    <>
      <input
        type="range"
        min={min}
        max={max}
        step={paso}
        value={valor}
        onChange={(e) => alCambiar(Number(e.target.value))}
        className="w-24 accent-(--primary)"
      />
      <span className="texto-suave w-8 text-right tabular-nums">
        {valor.toFixed(2)}
      </span>
    </>
  );
}

/** Interruptor compacto. */
function Interruptor({
  activo,
  alCambiar,
}: {
  activo: boolean;
  alCambiar: (v: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      checked={activo}
      onChange={(e) => alCambiar(e.target.checked)}
      className="h-4 w-4 accent-(--primary)"
    />
  );
}

const CAMPOS: CampoRegla[] = [
  "estado",
  "edadDias",
  "deg",
  "dimension",
  "fuente",
  "refuerzos",
  "amplitud",
];
const OPS: OpRegla[] = ["eq", "neq", "lt", "lte", "gt", "gte", "has", "in"];

/** Editor de una condición de regla. */
function CondicionEditor({
  cond,
  alCambiar,
  alBorrar,
}: {
  cond: { field: CampoRegla; op: OpRegla; value?: unknown };
  alCambiar: (c: typeof cond) => void;
  alBorrar: () => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <select
        value={cond.field}
        onChange={(e) =>
          alCambiar({ ...cond, field: e.target.value as CampoRegla })
        }
        className="vidrio rounded-lg px-1.5 py-1 text-[0.7rem]"
        aria-label="Campo"
      >
        {CAMPOS.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
      <select
        value={cond.op}
        onChange={(e) => alCambiar({ ...cond, op: e.target.value as OpRegla })}
        className="vidrio rounded-lg px-1.5 py-1 text-[0.7rem]"
        aria-label="Operador"
      >
        {OPS.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      <input
        value={String(cond.value ?? "")}
        onChange={(e) => {
          const v = e.target.value;
          const n = Number(v);
          alCambiar({ ...cond, value: v !== "" && !isNaN(n) ? n : v });
        }}
        className="vidrio w-16 rounded-lg px-1.5 py-1 text-[0.7rem]"
        aria-label="Valor"
      />
      <button
        type="button"
        onClick={alBorrar}
        aria-label="Borrar condición"
        className="texto-suave hover:text-(--danger)"
      >
        <Trash width={13} height={13} />
      </button>
    </div>
  );
}

/** Editor de reglas semánticas con orden, color y activación. */
function EditorReglas({
  reglas,
  alCambiar,
}: {
  reglas: Regla[];
  alCambiar: (r: Regla[]) => void;
}) {
  const mover = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= reglas.length) return;
    const copia = [...reglas];
    [copia[i], copia[j]] = [copia[j], copia[i]];
    alCambiar(copia);
  };
  const poner = (i: number, r: Regla) => {
    const copia = [...reglas];
    copia[i] = r;
    alCambiar(copia);
  };
  return (
    <div className="flex flex-col gap-2">
      {reglas.map((r, i) => (
        <div key={r.id} className="vidrio flex flex-col gap-1.5 rounded-xl p-2">
          <div className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={r.enabled !== false}
              onChange={(e) => poner(i, { ...r, enabled: e.target.checked })}
              className="h-3.5 w-3.5 accent-(--primary)"
              aria-label={`Activar regla ${r.id}`}
            />
            <input
              type="color"
              value={r.color}
              onChange={(e) => poner(i, { ...r, color: e.target.value })}
              className="h-5 w-7 cursor-pointer rounded border-0 bg-transparent"
              aria-label={`Color de ${r.id}`}
            />
            <input
              value={r.id}
              onChange={(e) => poner(i, { ...r, id: e.target.value })}
              className="min-w-0 flex-1 rounded-lg bg-transparent px-1 text-[0.7rem] font-medium"
              aria-label="Nombre de regla"
            />
            <button type="button" onClick={() => mover(i, -1)} aria-label="Subir regla" className="texto-suave">
              <NavArrowUp width={12} height={12} />
            </button>
            <button type="button" onClick={() => mover(i, 1)} aria-label="Bajar regla" className="texto-suave">
              <NavArrowDown width={12} height={12} />
            </button>
            <button
              type="button"
              onClick={() => alCambiar(reglas.filter((_, j) => j !== i))}
              aria-label="Eliminar regla"
              className="texto-suave hover:text-(--danger)"
            >
              <Trash width={13} height={13} />
            </button>
          </div>
          {(r.tests ?? []).map((c, j) => (
            <CondicionEditor
              key={j}
              cond={c}
              alCambiar={(nc) => {
                const tests = [...(r.tests ?? [])];
                tests[j] = nc;
                poner(i, { ...r, tests });
              }}
              alBorrar={() =>
                poner(i, { ...r, tests: (r.tests ?? []).filter((_, k) => k !== j) })
              }
            />
          ))}
          <button
            type="button"
            onClick={() =>
              poner(i, {
                ...r,
                tests: [
                  ...(r.tests ?? []),
                  { field: "estado", op: "eq", value: "fresh" },
                ],
              })
            }
            className="texto-suave self-start text-[0.65rem] hover:text-(--ink)"
          >
            + condición
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() =>
          alCambiar([
            ...reglas,
            {
              id: `regla${reglas.length + 1}`,
              color: "#a78bfa",
              enabled: true,
              tests: [{ field: "fuente", op: "eq", value: "" }],
            },
          ])
        }
        className="vidrio flex items-center justify-center gap-1 rounded-xl py-1.5 text-[0.7rem] hover:brightness-110"
      >
        <Plus width={12} height={12} /> Regla
      </button>
    </div>
  );
}

interface PanelControlProps {
  prefs: PrefsCerebro;
  setPref: SetPref;
  coincidencias: number | null;
  modoColor: ModoColor;
  fuentes: Fuente[];
}

/** Panel ⚙ con todos los ajustes del grafo. */
export function PanelControl({
  prefs,
  setPref,
  coincidencias,
  modoColor,
  fuentes,
}: PanelControlProps) {
  const clavesColor: [string, string][] =
    modoColor === "dimension"
      ? (Object.entries(COLORES_DIMENSION) as [string, string][])
      : modoColor === "estado"
        ? (Object.entries(COLORES_ESTADO) as [string, string][])
        : modoColor === "fuente"
          ? fuentes.map((f) => [`fuente:${f.id}`, colorFuente(f.id, fuentes)])
          : [["fallback", prefs.ruleFallback]];
  return (
    <div className="pointer-events-auto flex max-h-[calc(100%-6rem)] w-72 flex-col gap-3 overflow-y-auto p-4">
      <Fila etiqueta="Buscar en el grafo">
        <input
          type="search"
          value={prefs.query}
          onChange={(e) => setPref("query", e.target.value)}
          placeholder="texto…"
          className="vidrio w-32 rounded-lg px-2 py-1 text-xs outline-none"
        />
      </Fila>
      {coincidencias !== null && (
        <p className="texto-suave -mt-2 text-right text-[0.65rem]">
          {coincidencias} coincidencias
        </p>
      )}
      <Fila etiqueta="Etiquetas">
        <select
          value={prefs.labels}
          onChange={(e) =>
            setPref("labels", e.target.value as PrefsCerebro["labels"])
          }
          className="vidrio rounded-lg px-1.5 py-1 text-xs"
        >
          <option value="hover">al pasar</option>
          <option value="hubs">hubs</option>
          <option value="all">todas</option>
        </select>
      </Fila>
      <Fila etiqueta="Tamaño de nodo">
        <Deslizador valor={prefs.nodeSize} max={3} alCambiar={(v) => setPref("nodeSize", v)} />
      </Fila>
      <Fila etiqueta="Grosor de aristas">
        <Deslizador valor={prefs.edgeWidth} max={3} alCambiar={(v) => setPref("edgeWidth", v)} />
      </Fila>
      <Fila etiqueta="Rotación">
        <Deslizador valor={prefs.rotate} max={3} alCambiar={(v) => setPref("rotate", v)} />
      </Fila>
      <Fila etiqueta="Flujo sináptico">
        <Interruptor activo={prefs.flow} alCambiar={(v) => setPref("flow", v)} />
      </Fila>
      {prefs.flow && (
        <Fila etiqueta="Velocidad de flujo">
          <Deslizador valor={prefs.flowSpeed} max={3} alCambiar={(v) => setPref("flowSpeed", v)} />
        </Fila>
      )}
      <Fila etiqueta="Nivel de detalle">
        <Interruptor activo={prefs.lod} alCambiar={(v) => setPref("lod", v)} />
      </Fila>
      <Fila etiqueta="Membrana">
        <Interruptor activo={prefs.membrane} alCambiar={(v) => setPref("membrane", v)} />
      </Fila>
      {prefs.membrane && (
        <Fila etiqueta="Opacidad membrana">
          <Deslizador valor={prefs.memOpacity} alCambiar={(v) => setPref("memOpacity", v)} />
        </Fila>
      )}
      <Fila etiqueta="Pulso ambiental">
        <Interruptor activo={prefs.ambient} alCambiar={(v) => setPref("ambient", v)} />
      </Fila>
      <Fila etiqueta="Regiones">
        <Interruptor activo={prefs.regions} alCambiar={(v) => setPref("regions", v)} />
      </Fila>
      <Fila etiqueta="Cerebro profundo">
        <Interruptor activo={prefs.deepBrain} alCambiar={(v) => setPref("deepBrain", v)} />
      </Fila>
      <Fila etiqueta="Brillo selección">
        <Deslizador valor={prefs.selGlow} max={2} alCambiar={(v) => setPref("selGlow", v)} />
      </Fila>
      <Fila etiqueta="Física">
        <Interruptor activo={prefs.physics} alCambiar={(v) => setPref("physics", v)} />
      </Fila>
      {prefs.physics && (
        <>
          <Fila etiqueta="Repulsión">
            <Deslizador valor={prefs.physRep} max={3} alCambiar={(v) => setPref("physRep", v)} />
          </Fila>
          <Fila etiqueta="Atracción">
            <Deslizador valor={prefs.physAtt} max={3} alCambiar={(v) => setPref("physAtt", v)} />
          </Fila>
          <Fila etiqueta="Gravedad">
            <Deslizador valor={prefs.physGrav} max={3} alCambiar={(v) => setPref("physGrav", v)} />
          </Fila>
        </>
      )}
      <div className="border-t pt-2" style={{ borderColor: "var(--linea-vidrio)" }}>
        <p className="texto-suave mb-2 text-[0.65rem] font-semibold tracking-wide uppercase">
          Colores — {NOMBRE_MODO[modoColor]}
        </p>
        <div className="flex flex-col gap-1.5">
          {clavesColor.map(([clave, color]) => (
            <Fila key={clave} etiqueta={clave.replace(/^(estado|fuente):/, "")}>
              <input
                type="color"
                value={prefs.colors[clave] ?? color}
                onChange={(e) => {
                  const colors = { ...prefs.colors };
                  if (clave === "fallback") setPref("ruleFallback", e.target.value);
                  else {
                    colors[clave] = e.target.value;
                    setPref("colors", colors);
                  }
                }}
                className="h-5 w-8 cursor-pointer rounded border-0 bg-transparent"
                aria-label={`Color ${clave}`}
              />
            </Fila>
          ))}
        </div>
      </div>
      {modoColor === "reglas" && (
        <div className="border-t pt-2" style={{ borderColor: "var(--linea-vidrio)" }}>
          <div className="mb-2 flex items-center justify-between">
            <p className="texto-suave text-[0.65rem] font-semibold tracking-wide uppercase">
              Reglas semánticas
            </p>
            <button
              type="button"
              onClick={() => setPref("rules", null)}
              className="texto-suave text-[0.65rem] hover:text-(--ink)"
            >
              restablecer
            </button>
          </div>
          <EditorReglas
            reglas={prefs.rules ?? REGLAS_DEFAULT}
            alCambiar={(r) => setPref("rules", r)}
          />
        </div>
      )}
    </div>
  );
}

interface PanelFuentesProps {
  fuentes: Fuente[];
  ocultas: Set<string>;
  alAlternar: (fuente: string) => void;
  cap: number;
  alCambiarCap: (cap: number) => void;
}

/** Panel de procedencia: chips por fuente con conteo y visibilidad. */
export function PanelFuentes({
  fuentes,
  ocultas,
  alAlternar,
  cap,
  alCambiarCap,
}: PanelFuentesProps) {
  if (fuentes.length === 0) return null;
  return (
    <div className="pointer-events-auto flex w-52 flex-col gap-2 p-3">
      <p className="texto-suave text-[0.65rem] font-semibold tracking-wide uppercase">
        Procedencia
      </p>
      <div className="flex flex-col gap-1">
        {fuentes.map((f) => {
          const oculta = ocultas.has(f.id);
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => alAlternar(f.id)}
              title={oculta ? "Mostrar fuente" : "Ocultar fuente"}
              className={`flex items-center gap-2 rounded-xl px-2 py-1.5 text-left text-xs transition-opacity ${oculta ? "opacity-40" : ""}`}
            >
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: colorFuente(f.id, fuentes) }}
              />
              <span className="min-w-0 flex-1 truncate">{f.id}</span>
              <span className="texto-suave tabular-nums">{f.count}</span>
              {oculta ? (
                <EyeClosed width={13} height={13} className="texto-suave" />
              ) : (
                <Eye width={13} height={13} className="texto-suave" />
              )}
            </button>
          );
        })}
      </div>
      <label className="texto-suave flex items-center justify-between gap-2 border-t pt-2 text-[0.65rem]" style={{ borderColor: "var(--linea-vidrio)" }}>
        Tope de eventos
        <input
          type="range"
          min={50}
          max={1000}
          step={50}
          value={cap}
          onChange={(e) => alCambiarCap(Number(e.target.value))}
          className="w-24 accent-(--primary)"
          aria-label="Tope de eventos"
        />
        <span className="w-8 text-right tabular-nums">{cap}</span>
      </label>
    </div>
  );
}

interface TarjetaProps {
  datos: DatosCerebro | null;
  modoColor: ModoColor;
  alCambiarModo: (m: ModoColor) => void;
  alRefrescar: () => void;
  cargando: boolean;
  reglas: Regla[];
}

/** Tarjeta superior: stats, ciclo de color, refresco y leyenda. */
export function TarjetaVidrio({
  datos,
  modoColor,
  alCambiarModo,
  alRefrescar,
  cargando,
  reglas,
}: TarjetaProps) {
  const [plegada, setPlegada] = useState(false);
  const siguiente = ORDEN_MODOS[(ORDEN_MODOS.indexOf(modoColor) + 1) % 4];
  const leyenda: [string, string][] =
    datos == null
      ? []
      : modoColor === "dimension"
        ? (Object.entries(COLORES_DIMENSION) as [string, string][])
        : modoColor === "estado"
          ? (Object.entries(datos.stateCounts) as [string, number][]).map(
              ([e, n]) => [`${e} · ${n}`, COLORES_ESTADO[e as keyof typeof COLORES_ESTADO]],
            )
          : modoColor === "fuente"
            ? datos.fuentes.map((f) => [
                `${f.id} · ${f.count}`,
                colorFuente(f.id, datos.fuentes),
              ])
            : reglas.filter((r) => r.enabled !== false).map((r) => [r.id, r.color]);
  return (
    <div className="pointer-events-auto flex flex-col gap-1 p-3">
      <div className="flex items-center gap-3">
        <span className="text-xs font-semibold tabular-nums">
          {datos?.nodes.length ?? 0} memorias · {datos?.edges.length ?? 0} aristas
        </span>
        <button
          type="button"
          onClick={() => alCambiarModo(siguiente)}
          title={`Color: ${NOMBRE_MODO[modoColor]} → ${NOMBRE_MODO[siguiente]}`}
          className="vidrio flex items-center gap-1.5 rounded-xl px-2.5 py-1 text-[0.7rem] font-medium hover:brightness-110"
        >
          <Palette width={13} height={13} />
          {NOMBRE_MODO[modoColor]}
        </button>
        <button
          type="button"
          onClick={alRefrescar}
          disabled={cargando}
          title="Recargar grafo"
          className="texto-suave rounded-lg p-1 hover:text-(--ink) disabled:opacity-40"
          aria-label="Recargar grafo"
        >
          <Refresh width={15} height={15} className={cargando ? "animate-spin" : ""} />
        </button>
        <button
          type="button"
          onClick={() => setPlegada((p) => !p)}
          aria-label={plegada ? "Expandir" : "Colapsar"}
          className="texto-suave rounded-lg p-1 hover:text-(--ink)"
        >
          {plegada ? <NavArrowDown width={15} height={15} /> : <NavArrowUp width={15} height={15} />}
        </button>
      </div>
      {!plegada && leyenda.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
          {leyenda.map(([nombre, color]) => (
            <span key={nombre} className="texto-suave flex items-center gap-1.5 text-[0.65rem]">
              <span className="h-2 w-2 rounded-full" style={{ background: color }} />
              {nombre}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

