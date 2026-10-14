"use client";

/**
 * Panel de configuración del río temporal (/universo).
 *
 * Toggles de capas (afluentes, ramas, fantasmas, arcos, etiquetas,
 * polvo, faros), sliders de intensidad y color por dimensión. Todo
 * se aplica en caliente vía `vistaRef` y se persiste en PostgreSQL
 * bajo la clave `universo`.
 */

import type { Dimension } from "@/domain/modelos";
import { COLORES_DIMENSION } from "@/ui/brain/tipos";
import type { IdEscenario } from "./geometria";
import type { PrefsUniverso } from "./prefs";

const DIMENSIONES: Dimension[] = ["cognitiva", "somatica", "conductual"];

/** Escenarios de futuro togglables, con nombre legible. */
const ESCENARIOS: { id: IdEscenario; nombre: string }[] = [
  { id: "continuidad", nombre: "Continuidad (tu patrón sigue)" },
  { id: "riesgo", nombre: "Declive (señales de desgaste)" },
  { id: "florecer", nombre: "Florecer (hacia tus metas)" },
];

/** Fila etiqueta + control. */
function Fila({
  etiqueta,
  children,
}: {
  etiqueta: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex items-center justify-between gap-3 text-[0.72rem]">
      <span className="texto-suave">{etiqueta}</span>
      <span className="flex items-center gap-2">{children}</span>
    </label>
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

/** Slider compacto con etiqueta de valor. */
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

/**
 * Panel de ajustes del universo: capas visibles, cantidad de
 * afluentes, intensidad de fantasmas, separación, bloom y colores
 * por dimensión.
 */
export function PanelVistaUniverso({
  prefs,
  setPref,
}: {
  prefs: PrefsUniverso;
  setPref: <K extends keyof PrefsUniverso>(
    clave: K,
    valor: PrefsUniverso[K],
  ) => void;
}) {
  return (
    <div className="sin-scrollbar flex w-72 flex-col gap-2.5 overflow-y-auto p-3">
      <p className="texto-suave text-[0.62rem] font-semibold tracking-wide uppercase">
        Capas del río
      </p>
      <Fila etiqueta="Afluentes (fuentes)">
        <Interruptor
          activo={prefs.afluentes}
          alCambiar={(x) => setPref("afluentes", x)}
        />
      </Fila>
      <Fila etiqueta="Cantidad de afluentes">
        <Deslizador
          valor={prefs.maxAfluentes}
          max={14}
          paso={1}
          alCambiar={(x) => setPref("maxAfluentes", Math.round(x))}
        />
      </Fila>
      <Fila etiqueta="Separación de afluentes">
        <Deslizador
          valor={prefs.separacion}
          min={0.2}
          max={2.5}
          alCambiar={(x) => setPref("separacion", x)}
        />
      </Fila>
      <Fila etiqueta="Ramificaciones reales">
        <Interruptor
          activo={prefs.ramas}
          alCambiar={(x) => setPref("ramas", x)}
        />
      </Fila>
      <Fila etiqueta="Arcos de relación">
        <Interruptor
          activo={prefs.arcos}
          alCambiar={(x) => setPref("arcos", x)}
        />
      </Fila>
      <Fila etiqueta="Faros de objetivos">
        <Interruptor
          activo={prefs.objetivos}
          alCambiar={(x) => setPref("objetivos", x)}
        />
      </Fila>
      <Fila etiqueta="Etiquetas">
        <Interruptor
          activo={prefs.etiquetas}
          alCambiar={(x) => setPref("etiquetas", x)}
        />
      </Fila>
      <Fila etiqueta="Polvo ambiental">
        <Interruptor
          activo={prefs.polvo}
          alCambiar={(x) => setPref("polvo", x)}
        />
      </Fila>

      <p className="texto-suave mt-1 border-t border-(--linea-vidrio) pt-2 text-[0.62rem] font-semibold tracking-wide uppercase">
        Escenarios de futuro
      </p>
      {ESCENARIOS.map((e) => (
        <Fila key={e.id} etiqueta={e.nombre}>
          <Interruptor
            activo={prefs.canales.includes(e.id)}
            alCambiar={(x) =>
              setPref(
                "canales",
                x
                  ? [...prefs.canales, e.id]
                  : prefs.canales.filter((q) => q !== e.id),
              )
            }
          />
        </Fila>
      ))}
      <Fila etiqueta="Intensidad fantasma">
        <Deslizador
          valor={prefs.intensidadFantasma}
          max={2}
          alCambiar={(x) => setPref("intensidadFantasma", x)}
        />
      </Fila>

      <p className="texto-suave mt-1 border-t border-(--linea-vidrio) pt-2 text-[0.62rem] font-semibold tracking-wide uppercase">
        Atmósfera
      </p>
      <Fila etiqueta="Bloom">
        <Deslizador
          valor={prefs.bloom}
          max={2}
          alCambiar={(x) => setPref("bloom", x)}
        />
      </Fila>
      {DIMENSIONES.map((d) => (
        <Fila key={d} etiqueta={`Color ${d}`}>
          <input
            type="color"
            aria-label={`Color de ${d}`}
            value={prefs.colores[d] ?? COLORES_DIMENSION[d]}
            onChange={(e) =>
              setPref("colores", { ...prefs.colores, [d]: e.target.value })
            }
            className="h-5 w-8 cursor-pointer rounded border-0 bg-transparent"
          />
          {prefs.colores[d] && (
            <button
              type="button"
              onClick={() => {
                const siguiente = { ...prefs.colores };
                delete siguiente[d];
                setPref("colores", siguiente);
              }}
              aria-label={`Restablecer color de ${d}`}
              className="texto-suave text-[0.62rem] hover:text-(--ink)"
            >
              reset
            </button>
          )}
        </Fila>
      ))}
    </div>
  );
}
