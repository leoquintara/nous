"use client";

/**
 * /universo — el río temporal 4D: mapa de la línea de universo.
 *
 * El cauce central es la rama "main" con los eventos como orbes;
 * las ramas hijas se bifurcan en el instante de su primer evento y
 * las fuentes llegan como afluentes convergiendo en el presente.
 * Tras el "ahora" divergen los cauces-fantasma (futuros probables
 * por dimensión) que convergen hacia los objetivos del usuario —
 * administrables aquí mismo (se persisten en PostgreSQL).
 */

import { motion } from "motion/react";
import {
  Check,
  Cpu,
  TriangleFlag,
  NavArrowDown,
  NavArrowUp,
  Planet,
  Plus,
  Settings,
  X,
} from "iconoir-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type {
  Dimension,
  Evento,
  FuenteInfo,
  Objetivo,
  PrediccionIA,
  Trayectoria,
} from "@/domain/modelos";
import { PanelIA } from "@/ui/panel-ia";
import { CursorTemporal } from "@/ui/brain/controles";
import {
  construirEscenarios,
  ctxDe,
  statsDimension,
  RIO,
  type CtxTemporal,
  type Escenario,
  type EventoRio,
  type StatsDimension,
} from "@/ui/universo/geometria";
import { PanelVistaUniverso } from "@/ui/universo/controles";
import { usePrefsUniverso } from "@/ui/universo/usar-prefs-universo";
import type { DatosRio } from "@/ui/universo/UniversoCanvas";
import { colorEscenarioHex } from "@/ui/universo/materiales";
import { COLORES_DIMENSION } from "@/ui/brain/tipos";

const UniversoCanvas = dynamic(
  () =>
    import("@/ui/universo/UniversoCanvas").then((m) => ({
      default: m.UniversoCanvas,
    })),
  { ssr: false },
);

const DIA = 86_400;
const LIMITE_HISTORIAL = 4000;
const DIMENSIONES: Dimension[] = ["cognitiva", "somatica", "conductual"];

const fechaCorta = (t: number) =>
  new Date(t * 1000).toLocaleDateString("es-MX", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

export default function PaginaUniverso() {
  const { memoria } = usarApp();
  const { prefs, setPref } = usePrefsUniverso(memoria);
  const [rio, setRio] = useState<DatosRio | null>(null);
  const [objetivos, setObjetivos] = useState<Objetivo[]>([]);
  const [fuentesInfo, setFuentesInfo] = useState<Map<string, FuenteInfo>>(
    new Map(),
  );
  const [trayectorias, setTrayectorias] = useState<Trayectoria[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [reproduciendo, setReproduciendo] = useState(false);
  const [sel, setSel] = useState<Evento | null>(null);
  const [selFuente, setSelFuente] = useState<string | null>(null);
  const [selFantasma, setSelFantasma] = useState<Escenario | null>(null);
  const [selObjetivo, setSelObjetivo] = useState<Objetivo | null>(null);
  const [panelMetas, setPanelMetas] = useState(true);
  const [panelVista, setPanelVista] = useState(false);
  const datosRef = useRef({ eventos: [] as Evento[], ctx: null as CtxTemporal | null });

  /** Eventos como EventoRio (para stats y proyección de escenarios). */
  const eventosRio = useMemo<EventoRio[]>(
    () =>
      (rio?.eventos ?? []).map((e) => ({
        id: e.id,
        ts: e.timestamp_unix,
        dimension: e.dimension,
        fuente: e.fuente ?? "desconocida",
        amplitud: e.amplitud,
        rama: e.rama,
      })),
    [rio],
  );

  /** Instante desde el que se proyecta el futuro (cursor o ahora). */
  const origenEnfocado = useMemo(() => {
    if (!rio) return null;
    return cursor == null
      ? rio.ctx.tMax
      : Math.min(Math.max(cursor, rio.ctx.tMin), rio.ctx.tMax);
  }, [rio, cursor]);

  /**
   * Escenarios de futuro proyectados desde el punto enfocado:
   * brotan del cursor (o del "ahora" si no hay viaje) y se
   * recalculan con los eventos visibles hasta ese instante —
   * así el usuario explora futuros contrafácticos en cualquier t.
   */
  const escenarios = useMemo(() => {
    if (!rio || origenEnfocado == null) return [];
    const origen = origenEnfocado;
    // Loops abiertos *en el origen*: el cierre cuenta solo si su
    // evento de cierre ya existía en ese instante.
    const tsPorId = new Map(rio.eventos.map((e) => [e.id, e.timestamp_unix]));
    const cerradosEn = new Set(
      rio.relaciones
        .filter(
          (r) =>
            r.tipo === "cierra" &&
            (tsPorId.get(r.destino_id) ?? Infinity) <= origen,
        )
        .map((r) => r.origen_id),
    );
    const loopsAbiertos = rio.eventos.filter(
      (e) =>
        e.tipo === "compromiso" &&
        e.timestamp_unix <= origen &&
        !cerradosEn.has(e.id) &&
        !e.reemplazado_por &&
        !(e.expira_en != null && e.expira_en < origen),
    ).length;
    return construirEscenarios({
      eventos: eventosRio,
      origen,
      loopsAbiertos,
      objetivos,
      horizonte: rio.horizonte,
      ctx: rio.ctx,
      maxEscenarios: prefs.maxEscenarios,
    });
  }, [rio, eventosRio, origenEnfocado, objetivos, prefs.maxEscenarios]);

  /** Ajustes de probabilidad/narrativa devueltos por el LLM. */
  const [ajustesIA, setAjustesIA] = useState<
    Map<string, { probabilidad: number; descripcion: string }>
  >(new Map());
  const [iaActiva, setIaActiva] = useState(false);
  /** Última respuesta completa del motor (para el panel visible). */
  const [analisisIA, setAnalisisIA] = useState<PrediccionIA | null>(null);
  const [cargandoIA, setCargandoIA] = useState(false);

  useEffect(() => {
    memoria
      ?.listarProveedoresIA()
      .then((ps) =>
        setIaActiva(ps.some((p) => p.habilitado && p.api_key_set)),
      )
      .catch(() => {});
  }, [memoria]);

  /**
   * Regla de control de tokens: NUNCA llamar al LLM sin gesto explícito
   * del usuario — ni en efectos ni en debounces. La única vía al motor
   * predictivo es el botón «Analizar con IA». Los ajustes se fusionan
   * por id (o nombre) y el río se re-monta con ellos.
   */
  useEffect(() => {
    // Mover el cursor cambia el instante proyectado: los ajustes y el
    // análisis previos eran para OTRO origen — se descartan sin red.
    setAjustesIA(new Map());
    setAnalisisIA(null);
  }, [origenEnfocado]);

  /** Mapa de ajustes por id/nombre a partir de la respuesta del LLM. */
  function mapaDeAjustes(
    r: PrediccionIA,
  ): Map<string, { probabilidad: number; descripcion: string }> {
    const mapa = new Map<
      string,
      { probabilidad: number; descripcion: string }
    >();
    for (const e of r.escenarios ?? []) {
      if (e.id) mapa.set(e.id, e);
      mapa.set(`nombre:${e.nombre}`, e);
    }
    return mapa;
  }

  /** Análisis explícito desde el punto enfocado: re-pondera y muestra
   *  la respuesta completa (proveedor, latencia, razonamiento). */
  async function analizarIA() {
    if (!memoria || cargandoIA) return;
    setCargandoIA(true);
    try {
      const r = await memoria.predecirIA({
        origen_unix:
          origenEnfocado != null ? Math.floor(origenEnfocado) : undefined,
        horizonte_dias: 30,
        candidatos: escenarios.map((e) => ({
          id: e.id,
          nombre: e.nombre,
          probabilidad: e.probabilidad,
          descripcion: e.descripcion,
        })),
      });
      setAnalisisIA(r);
      if (r.ok) setAjustesIA(mapaDeAjustes(r));
    } catch (e) {
      setAnalisisIA({
        ok: false,
        motivo: e instanceof Error ? e.message : "Error de red",
      });
    } finally {
      setCargandoIA(false);
    }
  }

  /** Escenarios con los ajustes del LLM fusionados (si los hay). */
  const escenariosFinal = useMemo(
    () =>
      escenarios.map((e) => {
        const ajuste =
          ajustesIA.get(e.id) ?? ajustesIA.get(`nombre:${e.nombre}`);
        return ajuste
          ? {
              ...e,
              probabilidad: ajuste.probabilidad,
              descripcion: ajuste.descripcion || e.descripcion,
            }
          : e;
      }),
    [escenarios, ajustesIA],
  );

  /** Reconstruye el río con eventos + relaciones + proyección + metas. */
  const recargar = useCallback(
    async (objs?: Objetivo[]) => {
      if (!memoria) return;
      try {
        const metas = objs ?? (await memoria.listarObjetivos("activo", "main"));
        const [eventos, relaciones, proy, catalogo] = await Promise.all([
          memoria.listarEventos(0, Math.floor(Date.now() / 1000), LIMITE_HISTORIAL, "*"),
          memoria.listarRelaciones(4000),
          memoria.proyeccionActual().catch(() => null),
          memoria.listarFuentes().catch(() => [] as FuenteInfo[]),
        ]);
        setObjetivos(metas);
        setFuentesInfo(new Map(catalogo.map((f) => [f.nombre, f])));
        setTrayectorias(
          proy && "trayectorias" in proy ? (proy.trayectorias ?? []) : [],
        );
        if (eventos.length === 0) {
          setRio(null);
          datosRef.current = { eventos, ctx: null };
          setError(null);
          return;
        }
        const tMin = eventos[0].timestamp_unix;
        const tMax = eventos[eventos.length - 1].timestamp_unix;
        const ctx = ctxDe(tMin, tMax, RIO);
        const horizonte = Math.max(ctx.span * 0.15, 3 * DIA);
        const ramas = [
          "main",
          ...[...new Set(eventos.map((e) => e.rama))].filter(
            (r) => r !== "main",
          ),
        ];
        const conteo = new Map<string, number>();
        for (const e of eventos) {
          const f = e.fuente ?? "desconocida";
          conteo.set(f, (conteo.get(f) ?? 0) + 1);
        }
        const fuentes = [...conteo.entries()]
          .map(([id, count]) => ({ id, count }))
          .sort((a, b) => b.count - a.count);
        setRio({
          eventos,
          relaciones,
          objetivos: metas,
          ctx,
          horizonte,
          ramas,
          fuentes,
        });
        datosRef.current = { eventos, ctx };
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Error al cargar el universo");
      }
    },
    [memoria],
  );

  useEffect(() => {
    const id = setTimeout(() => void recargar(), 0);
    return () => clearTimeout(id);
  }, [recargar]);

  /** Fija una meta nueva y refresca el río (los cauces convergen). */
  const crearMeta = useCallback(
    async (titulo: string, fecha: number) => {
      if (!memoria) return;
      await memoria.crearObjetivo({
        titulo,
        fecha_objetivo: fecha,
        dimension: null,
      });
      await recargar(await memoria.listarObjetivos("activo", "main"));
    },
    [memoria, recargar],
  );

  /** Cierra una meta (cumplido/abandonado) y refresca el río. */
  const cerrarMeta = useCallback(
    async (id: string, estado: "cumplido" | "abandonado") => {
      if (!memoria) return;
      await memoria.cerrarObjetivo(id, estado);
      await recargar(await memoria.listarObjetivos("activo", "main"));
    },
    [memoria, recargar],
  );

  // Recorrido automático tMin → horizonte (misma semántica del cerebro).
  useEffect(() => {
    if (!reproduciendo || !rio) return;
    const fin = rio.ctx.tMax + rio.horizonte;
    const paso = Math.max(1, (fin - rio.ctx.tMin) / 600);
    let t = cursor ?? rio.ctx.tMin;
    const id = setInterval(() => {
      t = Math.min(fin, t + paso * 4);
      setCursor(t);
      if (t >= fin) setReproduciendo(false);
    }, 50);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reproduciendo, rio]);

  return (
    <Shell fluid>
      <section
        className="vidrio relative min-h-0 flex-1 overflow-hidden rounded-3xl"
        aria-label="Río temporal 4D de la línea de universo"
      >
        {error && (
          <p className="p-6 text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
        {!error && !rio && (
          <div className="texto-suave flex h-full items-center justify-center gap-2 text-sm">
            <Planet width={16} height={16} className="animate-spin" />
            Cargando línea de universo…
          </div>
        )}
        {rio && (
          <UniversoCanvas
            rio={rio}
            escenarios={escenariosFinal}
            cursor={cursor}
            vista={prefs}
            alCambiarCursor={setCursor}
            alSeleccionar={(e) => {
              setSel(e);
              setSelFuente(null);
              setSelFantasma(null);
              setSelObjetivo(null);
            }}
            alSeleccionarFuente={(f) => {
              setSelFuente(f);
              setSel(null);
              setSelFantasma(null);
              setSelObjetivo(null);
            }}
            alSeleccionarFantasma={(f) => {
              setSelFantasma(f);
              setSel(null);
              setSelFuente(null);
              setSelObjetivo(null);
            }}
            alSeleccionarObjetivo={(o) => {
              setSelObjetivo(o);
              setSel(null);
              setSelFuente(null);
              setSelFantasma(null);
            }}
          />
        )}

        {/* Encabezado: título + toggle del panel de metas. */}
        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between gap-2 p-3">
          <div className="vidrio-fuerte pointer-events-auto flex items-center gap-2 rounded-full px-3.5 py-1.5 text-xs font-semibold">
            <Planet width={14} height={14} style={{ color: "var(--primary)" }} />
            Línea de universo
            {rio && (
              <span className="texto-suave font-normal">
                {rio.eventos.length} eventos · {rio.ramas.length}{" "}
                {rio.ramas.length === 1 ? "rama" : "ramas"}
              </span>
            )}
          </div>
          <div className="pointer-events-auto flex items-start gap-2">
            {iaActiva && (
              <button
                type="button"
                onClick={() => void analizarIA()}
                disabled={cargandoIA}
                title="Analizar con IA desde el punto enfocado"
                className="vidrio-fuerte texto-suave flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs transition hover:text-(--ink) disabled:opacity-50"
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
            <div className="vidrio-fuerte flex flex-col rounded-2xl">
              <button
                type="button"
                onClick={() => setPanelVista((p) => !p)}
                aria-label="Ajustes del universo"
                aria-expanded={panelVista}
                className="texto-suave self-end rounded-xl p-2 transition-transform duration-200 hover:scale-110 hover:text-(--ink) active:scale-90"
              >
                <Settings
                  width={16}
                  height={16}
                  className={`transition-transform duration-300 ease-out ${panelVista ? "rotate-180" : ""}`}
                />
              </button>
              {panelVista && (
                <PanelVistaUniverso prefs={prefs} setPref={setPref} />
              )}
            </div>
            <div className="vidrio-fuerte flex flex-col rounded-2xl">
              <button
                type="button"
                onClick={() => setPanelMetas((p) => !p)}
                aria-label="Panel de objetivos"
                aria-expanded={panelMetas}
                className="texto-suave flex items-center gap-2 self-end rounded-xl p-2 text-xs transition-transform duration-200 hover:scale-105 hover:text-(--ink) active:scale-90"
              >
                <TriangleFlag width={15} height={15} />
                Objetivos
                {panelMetas ? (
                  <NavArrowUp width={12} height={12} />
                ) : (
                  <NavArrowDown width={12} height={12} />
                )}
              </button>
              {panelMetas && (
                <PanelMetas
                  objetivos={objetivos}
                  alCrear={crearMeta}
                  alCerrar={cerrarMeta}
                />
              )}
            </div>
          </div>
        </div>

        {/* Detalle de la memoria seleccionada. */}
        {sel && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="vidrio-fuerte absolute bottom-16 left-3 z-10 w-72 rounded-2xl p-4"
          >
            <div className="flex items-start justify-between gap-2">
              <span
                className="rounded-full px-2 py-0.5 text-[0.65rem] font-medium"
                style={{
                  color: COLORES_DIMENSION[sel.dimension],
                  background: "var(--surface-vidrio)",
                }}
              >
                {sel.dimension}
              </span>
              <button
                type="button"
                onClick={() => setSel(null)}
                aria-label="Cerrar detalle"
                className="texto-suave rounded p-0.5 hover:text-(--ink)"
              >
                <X width={13} height={13} />
              </button>
            </div>
            <p className="mt-2 text-sm leading-snug">{sel.contenido}</p>
            <p className="texto-suave mt-2 text-[0.65rem]">
              {fechaCorta(sel.timestamp_unix)} · {sel.fuente} · rama {sel.rama}
            </p>
            {sel.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {sel.tags.map((t) => (
                  <span
                    key={t}
                    className="vidrio rounded-full px-1.5 py-0.5 text-[0.6rem]"
                  >
                    #{t}
                  </span>
                ))}
              </div>
            )}
          </motion.div>
        )}

        {/* Ficha del afluente (fuente) seleccionado. */}
        {selFuente && rio && (
          <TarjetaFuente
            fuente={selFuente}
            info={fuentesInfo.get(selFuente)}
            eventos={rio.eventos}
            alCerrar={() => setSelFuente(null)}
          />
        )}

        {/* Escenario del camino fantasma seleccionado. */}
        {selFantasma && rio && (
          <TarjetaEscenario
            escenario={selFantasma}
            stats={
              selFantasma.sustrato
                ? statsDimension(
                    selFantasma.sustrato,
                    eventosRio,
                    selFantasma.origen,
                  )
                : null
            }
            trayectoria={
              trayectorias.find(
                (t) =>
                  t.activa &&
                  selFantasma.sustrato != null &&
                  t.descripcion
                    .toLowerCase()
                    .includes(selFantasma.sustrato),
              ) ?? null
            }
            alCerrar={() => setSelFantasma(null)}
          />
        )}

        {/* Análisis IA desde el punto enfocado. */}
        {analisisIA && (
          <div className="absolute right-3 bottom-3 z-10 w-80">
            <PanelIA
              prediccion={analisisIA}
              alCerrar={() => setAnalisisIA(null)}
              titulo="Análisis desde el punto enfocado"
            />
          </div>
        )}

        {/* Ficha del objetivo (faro) seleccionado. */}
        {selObjetivo && (
          <TarjetaObjetivo
            objetivo={selObjetivo}
            alCerrar={() => setSelObjetivo(null)}
            alCumplir={() => {
              void cerrarMeta(selObjetivo.id, "cumplido");
              setSelObjetivo(null);
            }}
            alAbandonar={() => {
              void cerrarMeta(selObjetivo.id, "abandonado");
              setSelObjetivo(null);
            }}
          />
        )}

        {rio && (
          <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 w-[min(34rem,48%)] -translate-x-1/2">
            <CursorTemporal
              tMin={rio.ctx.tMin}
              tMax={rio.ctx.tMax}
              horizonte={rio.horizonte}
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
      </section>
    </Shell>
  );
}

/**
 * Panel de objetivos: lista las metas activas con su fecha límite y
 * permite fijar una nueva (título + fecha). Las metas son el punto
 * al que TODOS los cauces-fantasma pretenden llegar — se crean sin
 * pedir dimensión: el sustrato lo calcula el motor, no el usuario.
 */
function PanelMetas({
  objetivos,
  alCrear,
  alCerrar,
}: {
  objetivos: Objetivo[];
  alCrear: (titulo: string, fecha: number) => Promise<void>;
  alCerrar: (id: string, estado: "cumplido" | "abandonado") => Promise<void>;
}) {
  const [titulo, setTitulo] = useState("");
  const [fecha, setFecha] = useState("");
  const [creando, setCreando] = useState(false);
  const activos = useMemo(
    () => objetivos.filter((o) => o.estado === "activo"),
    [objetivos],
  );

  /** Envía el formulario si tiene título y fecha válida. */
  const enviar = async () => {
    const ts = new Date(`${fecha}T12:00:00`).getTime() / 1000;
    if (!titulo.trim() || !fecha || !Number.isFinite(ts)) return;
    setCreando(true);
    try {
      await alCrear(titulo.trim(), Math.round(ts));
      setTitulo("");
      setFecha("");
    } finally {
      setCreando(false);
    }
  };

  return (
    <div className="flex w-72 flex-col gap-3 p-3">
      <div className="sin-scrollbar flex max-h-44 flex-col gap-1.5 overflow-y-auto">
        {activos.length === 0 && (
          <p className="texto-suave text-[0.7rem]">
            Sin metas activas — los futuros fantasma fluyen libres.
          </p>
        )}
        {activos.map((o) => (
          <div
            key={o.id}
            className="vidrio flex items-center gap-2 rounded-xl px-2.5 py-1.5"
          >
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full"
              style={{
                background: o.dimension
                  ? COLORES_DIMENSION[o.dimension]
                  : "var(--warning)",
              }}
            />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[0.72rem] font-medium">{o.titulo}</p>
              <p className="texto-suave text-[0.62rem]">
              {fechaCorta(o.fecha_objetivo)}
            </p>
            </div>
            <button
              type="button"
              onClick={() => void alCerrar(o.id, "cumplido")}
              aria-label={`Marcar "${o.titulo}" como cumplido`}
              title="Cumplido"
              className="texto-suave rounded p-1 transition-transform hover:scale-110 hover:text-(--success) active:scale-90"
            >
              <Check width={13} height={13} />
            </button>
            <button
              type="button"
              onClick={() => void alCerrar(o.id, "abandonado")}
              aria-label={`Abandonar "${o.titulo}"`}
              title="Abandonar"
              className="texto-suave rounded p-1 transition-transform hover:scale-110 hover:text-(--danger) active:scale-90"
            >
              <X width={13} height={13} />
            </button>
          </div>
        ))}
      </div>
      <form
        className="flex flex-col gap-1.5 border-t border-(--linea-vidrio) pt-2"
        onSubmit={(e) => {
          e.preventDefault();
          void enviar();
        }}
      >
        <input
          value={titulo}
          onChange={(e) => setTitulo(e.target.value)}
          placeholder="Nueva meta (p. ej. lanzar la beta)"
          aria-label="Título del objetivo"
          className="vidrio rounded-lg px-2 py-1.5 text-[0.72rem] outline-none"
        />
        <div className="flex gap-1.5">
          <input
            type="date"
            value={fecha}
            onChange={(e) => setFecha(e.target.value)}
            aria-label="Fecha límite"
            className="vidrio min-w-0 flex-1 rounded-lg px-2 py-1.5 text-[0.72rem] outline-none"
          />
          <button
            type="submit"
            disabled={creando || !titulo.trim() || !fecha}
            aria-label="Fijar objetivo"
            title="Fijar objetivo"
            className="vidrio rounded-lg px-2.5 py-1.5 transition-transform hover:scale-105 active:scale-90 disabled:opacity-40"
            style={{ color: "var(--primary)" }}
          >
            <Plus width={14} height={14} />
          </button>
        </div>
      </form>
    </div>
  );
}

/** Envoltura común de las fichas emergentes del río. */
function Tarjeta({
  titulo,
  color,
  alCerrar,
  children,
}: {
  titulo: string;
  color: string;
  alCerrar: () => void;
  children: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="vidrio-fuerte absolute bottom-16 left-3 z-10 w-72 rounded-2xl p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <span
          className="rounded-full px-2 py-0.5 text-[0.65rem] font-medium"
          style={{ color, background: "var(--surface-vidrio)" }}
        >
          {titulo}
        </span>
        <button
          type="button"
          onClick={alCerrar}
          aria-label="Cerrar detalle"
          className="texto-suave rounded p-0.5 hover:text-(--ink)"
        >
          <X width={13} height={13} />
        </button>
      </div>
      {children}
    </motion.div>
  );
}

/**
 * Ficha de un afluente: qué fuente alimenta ese tributario, cuántos
 * eventos aporta y su desglose por dimensión.
 */
function TarjetaFuente({
  fuente,
  info,
  eventos,
  alCerrar,
}: {
  fuente: string;
  info: FuenteInfo | undefined;
  eventos: Evento[];
  alCerrar: () => void;
}) {
  const datos = useMemo(() => {
    const propios = eventos.filter((e) => (e.fuente ?? "desconocida") === fuente);
    const dims = new Map<Dimension, number>();
    for (const e of propios) {
      dims.set(e.dimension, (dims.get(e.dimension) ?? 0) + 1);
    }
    return {
      total: propios.length,
      dims,
      primero: propios[0]?.timestamp_unix ?? null,
      ultimo: propios[propios.length - 1]?.timestamp_unix ?? null,
    };
  }, [fuente, eventos]);

  return (
    <Tarjeta titulo={`afluente · ${fuente}`} color="var(--primary)" alCerrar={alCerrar}>
      <p className="mt-2 text-sm leading-snug">
        {datos.total} eventos aportados al cauce
        {info ? ` · tipo ${info.tipo}` : ""}
        {info?.estado === "archivada" ? " · archivada" : ""}
      </p>
      <p className="texto-suave mt-1 text-[0.65rem]">
        {datos.primero ? `${fechaCorta(datos.primero)} → ${fechaCorta(datos.ultimo ?? datos.primero)}` : "sin eventos en la ventana"}
        {info && info.eventos > datos.total
          ? ` · ${info.eventos} totales en DB`
          : ""}
      </p>
      <div className="mt-2 flex flex-wrap gap-1">
        {[...datos.dims.entries()].map(([d, n]) => (
          <span
            key={d}
            className="vidrio rounded-full px-1.5 py-0.5 text-[0.6rem]"
            style={{ color: COLORES_DIMENSION[d] }}
          >
            {d} · {n}
          </span>
        ))}
      </div>
    </Tarjeta>
  );
}

/**
 * Tarjeta de escenario: qué significa que el cauce se vaya por este
 * camino — probabilidad, narrativa generada con cifras reales,
 * soporte de la dimensión sustrato y trayectoria activa.
 */
function TarjetaEscenario({
  escenario,
  stats,
  trayectoria,
  alCerrar,
}: {
  escenario: Escenario;
  stats: { total: number; recientes: number; amplitudMedia: number } | null;
  trayectoria: Trayectoria | null;
  alCerrar: () => void;
}) {
  const hex = colorEscenarioHex(escenario);
  return (
    <Tarjeta
      titulo={`${escenario.nombre} · ${Math.round(escenario.probabilidad * 100)}%`}
      color={hex}
      alCerrar={alCerrar}
    >
      <p className="mt-2 text-sm leading-snug">{escenario.descripcion}</p>
      {stats && escenario.sustrato && (
        <p className="texto-suave mt-2 text-[0.68rem] leading-snug">
          Sustrato {escenario.sustrato}: {stats.recientes} eventos en
          30d ({stats.total} en la ventana) · amplitud media{" "}
          {stats.amplitudMedia.toFixed(2)}
        </p>
      )}
      {trayectoria && (
        <p
          className="mt-2 rounded-lg px-2 py-1.5 text-[0.65rem] leading-snug"
          style={{ background: "var(--surface-vidrio)", color: hex }}
        >
          Trayectoria activa: {trayectoria.descripcion} · confianza{" "}
          {Math.round(trayectoria.confianza * 100)}%
        </p>
      )}
    </Tarjeta>
  );
}

/** Ficha de un faro: el objetivo fijado y acciones de cierre. */
function TarjetaObjetivo({
  objetivo,
  alCerrar,
  alCumplir,
  alAbandonar,
}: {
  objetivo: Objetivo;
  alCerrar: () => void;
  alCumplir: () => void;
  alAbandonar: () => void;
}) {
  return (
    <Tarjeta titulo={`◆ ${objetivo.titulo}`} color="var(--warning)" alCerrar={alCerrar}>
      <p className="texto-suave mt-2 text-[0.68rem]">
        Meta: {fechaCorta(objetivo.fecha_objetivo)} · rama {objetivo.rama}
      </p>
      {objetivo.descripcion && (
        <p className="mt-2 text-sm leading-snug">{objetivo.descripcion}</p>
      )}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={alCumplir}
          className="vidrio flex-1 rounded-lg px-2 py-1.5 text-[0.68rem] transition-transform hover:scale-105 active:scale-95"
          style={{ color: "var(--success)" }}
        >
          Marcar cumplido
        </button>
        <button
          type="button"
          onClick={alAbandonar}
          className="vidrio flex-1 rounded-lg px-2 py-1.5 text-[0.68rem] transition-transform hover:scale-105 active:scale-95"
          style={{ color: "var(--danger)" }}
        >
          Abandonar
        </button>
      </div>
    </Tarjeta>
  );
}
