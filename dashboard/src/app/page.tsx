"use client";

/**
 * Vista principal: cerebro 3D vivo con los eventos recientes.
 *
 * Port de la experiencia completa del plugin DSH Memory: grafo con
 * física y pensamientos, panel de ajustes, filtro de procedencia,
 * modos de color semánticos, historial con replay y visor de detalle.
 * Las preferencias y los pins se persisten en PostgreSQL.
 */

import { Cpu, Settings } from "iconoir-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type { EventoActividad, PrediccionIA } from "@/domain/modelos";
import { PanelIA } from "@/ui/panel-ia";
import type { CerebroHandle } from "@/ui/brain/CerebroCanvas";
import {
  CursorTemporal,
  PanelControl,
  PanelFuentes,
  TarjetaVidrio,
} from "@/ui/brain/controles";
import { HistorialActividad } from "@/ui/brain/historial";
import { REGLAS_DEFAULT } from "@/ui/brain/reglas";
import {
  eventosAGrafo,
  PINS_MAX,
  type DatosCerebro,
  type PosicionPin,
} from "@/ui/brain/tipos";
import { usePrefsCerebro } from "@/ui/brain/usar-prefs-cerebro";
import {
  VisorNodo,
  type MemoriaConectada,
} from "@/ui/brain/visor-nodo";

const CerebroCanvas = dynamic(
  () =>
    import("@/ui/brain/CerebroCanvas").then((m) => ({
      default: m.CerebroCanvas,
    })),
  { ssr: false },
);

const DIA = 86_400;
const INTERVALO_ACTIVIDAD_MS = 4000;

export default function PaginaCerebro() {
  const { memoria } = useApp();
  const { prefs, setPref } = usePrefsCerebro(memoria);
  const [datos, setDatos] = useState<DatosCerebro | null>(null);
  const [sel, setSel] = useState<{ index: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);
  const [panelAbierto, setPanelAbierto] = useState(false);
  const [historial, setHistorial] = useState<EventoActividad[]>([]);
  const [iaDisponible, setIaDisponible] = useState(false);
  const [analisisIA, setAnalisisIA] = useState<PrediccionIA | null>(null);
  const [cargandoIA, setCargandoIA] = useState(false);
  // Cursor del observador 4D: null = presente vivo.
  const [cursor, setCursor] = useState<number | null>(null);
  const [reproduciendo, setReproduciendo] = useState(false);
  const pensarRef = useRef<CerebroHandle | null>(null);
  const pinsRef = useRef(prefs.pins);
  const datosRef = useRef(datos);
  useEffect(() => {
    pinsRef.current = prefs.pins;
    datosRef.current = datos;
  });

  /** Recarga los eventos y reconstruye el grafo respetando los pins. */
  const recargar = useCallback((): Promise<void> => {
    if (!memoria) return Promise.resolve();
    const ahora = Math.floor(Date.now() / 1000);
    setCargando(true);
    return Promise.all([
      memoria.listarEventos(
        ahora - 90 * DIA,
        ahora,
        prefs.srcCap,
        prefs.rama ?? undefined,
      ),
      memoria.listarRelaciones(),
    ])
      .then(([eventos, relaciones]) => {
        setDatos(
          eventosAGrafo(eventos, ahora, pinsRef.current, relaciones),
        );
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setCargando(false));
  }, [memoria, prefs.srcCap, prefs.rama]);

  useEffect(() => {
    const id = setTimeout(recargar, 0);
    return () => clearTimeout(id);
  }, [recargar]);

  // ¿Hay un proveedor de IA configurado? El botón solo aparece entonces.
  useEffect(() => {
    memoria
      ?.listarProveedoresIA()
      .then((ps) =>
        setIaDisponible(ps.some((p) => p.habilitado && p.api_key_set)),
      )
      .catch(() => {});
  }, [memoria]);

  /** Analiza el estado actual de la memoria con el LLM configurado. */
  const analizarIA = useCallback(async () => {
    if (!memoria || cargandoIA) return;
    setCargandoIA(true);
    try {
      setAnalisisIA(await memoria.predecirIA({ horizonte_dias: 30 }));
    } catch (e) {
      setAnalisisIA({
        ok: false,
        motivo: e instanceof Error ? e.message : "Error de red",
      });
    } finally {
      setCargandoIA(false);
    }
  }, [memoria, cargandoIA]);

  // Fuentes ocultas: srcFilter nulo = todas visibles.
  const ocultas = useMemo(() => {
    const s = new Set<string>();
    if (prefs.srcFilter && datos) {
      for (const f of datos.fuentes) {
        if (!prefs.srcFilter.includes(f.id)) s.add(f.id);
      }
    }
    return s;
  }, [prefs.srcFilter, datos]);

  /** Alterna la visibilidad de una fuente y lo persiste. */
  const alternarFuente = useCallback(
    (fuente: string) => {
      if (!datos) return;
      const todas = datos.fuentes.map((f) => f.id);
      const visibles = prefs.srcFilter ?? todas;
      const siguiente = visibles.includes(fuente)
        ? visibles.filter((f) => f !== fuente)
        : [...visibles, fuente];
      setPref(
        "srcFilter",
        siguiente.length === todas.length ? null : siguiente,
      );
    },
    [datos, prefs.srcFilter, setPref],
  );

  /** Muestra u oculta todas las fuentes de golpe. */
  const alternarTodas = useCallback(
    (mostrar: boolean) => {
      setPref("srcFilter", mostrar ? null : []);
    },
    [setPref],
  );

  /** Persiste la posición de un nodo fijado/soltado por arrastre. */
  const alCambiarPin = useCallback(
    (eventoId: string, pos: PosicionPin | null) => {
      const pins = { ...pinsRef.current };
      if (pos) {
        pins[eventoId] = pos;
        const ids = Object.keys(pins);
        if (ids.length > PINS_MAX) {
          for (const viejo of ids.slice(0, ids.length - PINS_MAX)) {
            delete pins[viejo];
          }
        }
      } else {
        delete pins[eventoId];
      }
      setPref("pins", pins);
    },
    [setPref],
  );

  // Conteo de coincidencias de la consulta de resaltado.
  const coincidencias = useMemo(() => {
    const q = prefs.query.trim().toLowerCase();
    if (!q || !datos) return null;
    return datos.nodes.filter((n) =>
      n.etiqueta.toLowerCase().includes(q),
    ).length;
  }, [prefs.query, datos]);

  // Polling de actividad: cada evento nuevo dispara un pensamiento en
  // el grafo y entra al historial; un store recarga los datos.
  useEffect(() => {
    if (!memoria) return;
    const vistos = new Set<string>();
    let ultimoTs = 0;
    let vivo = true;

    /** Procesa eventos nuevos: pensamiento + historial + refresh. */
    const procesar = (eventos: EventoActividad[]) => {
      const nuevos = eventos.filter((ev) => {
        const k = `${ev.ts}:${ev.kind}:${ev.q}`;
        // ts tiene resolución de segundos: dos eventos distintos en el
        // mismo segundo solo se distinguen por la clave compuesta.
        if (vistos.has(k)) return false;
        vistos.add(k);
        return true;
      });
      if (nuevos.length === 0) return;
      ultimoTs = Math.max(ultimoTs, ...nuevos.map((e) => e.ts));
      // Poda: las claves más viejas que el feed ya no pueden repetirse.
      if (vistos.size > 500) {
        for (const k of vistos) {
          if (Number(k.split(":")[0]) < ultimoTs - 120) vistos.delete(k);
        }
      }
      setHistorial((h) => [...nuevos.reverse(), ...h].slice(0, 40));
      // Los ids que aún no están en el grafo (store reciente) quedan
      // encolados en el canvas y se animan cuando el recargado llega.
      for (const ev of nuevos) {
        pensarRef.current?.pensar({ ...ev });
      }
      if (nuevos.some((ev) => ev.kind === "store")) recargar();
    };

    memoria
      .actividad(50)
      .then((eventos) => {
        if (!vivo) return;
        ultimoTs = Math.max(0, ...eventos.map((e) => e.ts));
        for (const e of eventos) vistos.add(`${e.ts}:${e.kind}:${e.q}`);
        setHistorial([...eventos].reverse().slice(0, 40));
      })
      .catch(() => undefined);

    const intervalo = setInterval(() => {
      memoria
        .actividad(50)
        .then((eventos) => vivo && procesar(eventos))
        .catch(() => undefined);
    }, INTERVALO_ACTIVIDAD_MS);
    return () => {
      vivo = false;
      clearInterval(intervalo);
    };
  }, [memoria, recargar]);

  const nodoSel = useMemo(
    () => (sel && datos ? datos.nodes[sel.index] : null),
    [sel, datos],
  );

  // Línea de universo: dominio del cursor = [tMin, tMax + horizonte].
  const temporal = useMemo(() => {
    if (!datos || datos.status !== "ready") return null;
    const span = Math.max(1, datos.tMax - datos.tMin);
    return {
      tMin: datos.tMin,
      tMax: datos.tMax,
      horizonte: Math.max(span * 0.15, 3 * DIA),
    };
  }, [datos]);

  // Recorrido automático: nace en tMin si el cursor está en el
  // presente y avanza ~12 pasos/s hasta el borde del horizonte.
  useEffect(() => {
    if (!reproduciendo || !temporal) return;
    const fin = temporal.tMax + temporal.horizonte;
    const paso = Math.max(1, (fin - temporal.tMin) / 600);
    let t = cursor ?? temporal.tMin;
    const id = setInterval(() => {
      t = Math.min(fin, t + paso * 4);
      setCursor(t);
      if (t >= fin) setReproduciendo(false);
    }, 50);
    return () => clearInterval(id);
    // cursor se captura una vez al arrancar el recorrido.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reproduciendo, temporal]);

  /**
   * Las 5 memorias conectadas más relevantes: primero los vínculos
   * persistidos (explícitos > semánticos por peso), luego vecinos
   * estructurales del grafo.
   */
  const conectadas = useMemo<MemoriaConectada[]>(() => {
    if (!sel || !datos) return [];
    const vinculos = datos.vinculos.get(sel.index) ?? [];
    const porPeso = [...vinculos].sort((a, b) => {
      const t = (x: string) => (x === "explicita" ? 1 : 0);
      return t(b.tipo) - t(a.tipo) || b.peso - a.peso;
    });
    const base = porPeso.map((v) => ({ ...v, estructural: false }));
    if (base.length < 5) {
      const ya = new Set(base.map((v) => v.idx));
      for (const vecino of datos.nbrs.get(sel.index) ?? []) {
        if (!ya.has(vecino)) {
          base.push({ idx: vecino, tipo: "", peso: 0, estructural: true });
        }
      }
    }
    return base.slice(0, 5).map((v) => ({
      idx: v.idx,
      etiqueta: datos.nodes[v.idx].etiqueta,
      estado: datos.nodes[v.idx].estado,
      tipo: v.estructural ? null : v.tipo,
      peso: v.peso,
    }));
  }, [sel, datos]);

  /** Ajustes y visor coexisten: el visor se corre a la izquierda. */
  const alternarPanel = () => {
    setPanelAbierto((p) => !p);
  };
  /** Seleccionar un nodo abre su visor sin cerrar los ajustes. */
  const seleccionar = useCallback((s: { index: number } | null) => {
    setSel(s);
  }, []);

  return (
    <Shell fluid>
      <section
        className="vidrio relative min-h-0 flex-1 overflow-hidden rounded-3xl"
        aria-label="Cerebro de memoria 3D"
      >
        {error && (
          <p className="p-6 text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
        {!error && (!datos || datos.status === "cargando") && (
          <div className="texto-suave flex h-full items-center justify-center text-sm">
            Cargando memoria…
          </div>
        )}
        {(datos?.status === "ready" || datos?.status === "vacio") && (
          <CerebroCanvas
            datos={datos}
            seleccion={sel}
            alSeleccionar={seleccionar}
            prefs={prefs}
            modoColor={prefs.modoColor}
            ocultas={ocultas}
            alCambiarPin={alCambiarPin}
            cursor={cursor}
            onPensarRef={pensarRef}
          />
        )}
        {datos?.status === "vacio" && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-end justify-center pb-10">
            <p className="vidrio texto-suave rounded-full px-4 py-1.5 text-xs">
              Memoria vacía — el cerebro espera su primer evento.
            </p>
          </div>
        )}

        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between gap-2 p-3">
          {datos && datos.fuentes.length > 0 ? (
            <div className="vidrio-fuerte pointer-events-auto">
              <PanelFuentes
                fuentes={datos.fuentes}
                ocultas={ocultas}
                alAlternar={alternarFuente}
                alAlternarTodas={alternarTodas}
                cap={prefs.srcCap}
                alCambiarCap={(cap) => setPref("srcCap", cap)}
              />
            </div>
          ) : (
            <div />
          )}
          <div className="flex items-start gap-2">
            {iaDisponible && (
              <button
                type="button"
                onClick={() => void analizarIA()}
                disabled={cargandoIA}
                title="Analizar el estado de la memoria con IA"
                className="vidrio-fuerte texto-suave pointer-events-auto flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs transition hover:text-(--ink) disabled:opacity-50"
              >
                <Cpu
                  width={14}
                  height={14}
                  className={cargandoIA ? "animate-pulse" : ""}
                  style={{ color: "var(--primary)" }}
                />
                {cargandoIA ? "Analizando…" : "Analizar con IA"}
              </button>
            )}
            <div className="vidrio-fuerte pointer-events-auto flex flex-col">
              <button
                type="button"
                onClick={alternarPanel}
                aria-label="Ajustes del grafo"
                aria-expanded={panelAbierto}
                className="texto-suave self-end rounded-lg p-2 transition-transform duration-200 hover:scale-110 active:scale-90 hover:text-(--ink)"
              >
                <Settings
                  width={18}
                  height={18}
                  className={`transition-transform duration-300 ease-out ${panelAbierto ? "rotate-180" : ""}`}
                />
              </button>
              {panelAbierto && (
                <PanelControl
                  prefs={prefs}
                  setPref={setPref}
                  coincidencias={coincidencias}
                  modoColor={prefs.modoColor}
                  fuentes={datos?.fuentes ?? []}
                  ramas={datos?.ramas ?? []}
                />
              )}
            </div>
          </div>
        </div>

        {analisisIA && !panelAbierto && (
          <div className="absolute top-14 right-3 z-10 w-80">
            <PanelIA
              prediccion={analisisIA}
              alCerrar={() => setAnalisisIA(null)}
            />
          </div>
        )}

        <div className="pointer-events-none absolute bottom-3 left-3 z-10">
          <div className="vidrio-fuerte">
            <HistorialActividad
              eventos={historial}
              alReproducir={(ev) =>
                pensarRef.current?.pensar({ ...ev, inmediato: true })
              }
            />
          </div>
        </div>

        {temporal && (
          <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 w-[min(34rem,42%)] -translate-x-1/2">
            <CursorTemporal
              tMin={temporal.tMin}
              tMax={temporal.tMax}
              horizonte={temporal.horizonte}
              cursor={cursor}
              reproduciendo={reproduciendo}
              alCambiarCursor={(t) => {
                setCursor(t);
                if (t != null) setReproduciendo(false);
              }}
              alReproducir={() => setReproduciendo((r) => !r)}
            />
          </div>
        )}

        {!panelAbierto && (
          <div className="pointer-events-none absolute right-3 bottom-3 z-10">
            <div className="vidrio-fuerte pointer-events-auto">
              <TarjetaVidrio
                datos={datos}
                modoColor={prefs.modoColor}
                alCambiarModo={(m) => setPref("modoColor", m)}
                alRefrescar={recargar}
                cargando={cargando}
                reglas={prefs.rules ?? REGLAS_DEFAULT}
              />
            </div>
          </div>
        )}

        {nodoSel && (
          <VisorNodo
            nodo={nodoSel}
            conectadas={conectadas}
            alCerrar={() => setSel(null)}
            alSaltar={(idx) => setSel({ index: idx })}
            desplazado={panelAbierto}
          />
        )}
      </section>
    </Shell>
  );
}
