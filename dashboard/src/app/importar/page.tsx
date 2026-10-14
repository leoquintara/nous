"use client";

/**
 * Migración visual de memoria: trae memorias externas a Nous.
 *
 * Fuentes soportadas: vault Obsidian (directorio .md), export gbrain
 * (.md o JSON/JSONL), export memlord (JSON/JSONL con name/content),
 * y el JSONL nativo de ``nous export``. La ruta es de la máquina donde
 * corre el backend — local-first, nada sale del equipo. Flujo en tres
 * pasos: origen → validar (vista previa sin escribir) → ejecutar.
 * Reimportar es seguro: los ids ya existentes se omiten.
 */

import { motion } from "motion/react";
import {
  CheckCircle,
  Clock,
  Eye,
  Upload,
  WarningTriangle,
} from "iconoir-react";
import { useCallback, useEffect, useState } from "react";

import type {
  HistorialImportacion,
  ParametrosImportacion,
  ResultadoImportacion,
} from "@/domain/modelos";
import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";

type Formato = ParametrosImportacion["formato"];

/** Formatos con su descripción de qué origen esperan. */
const FORMATOS: { id: Formato; nombre: string; detalle: string }[] = [
  {
    id: "auto",
    nombre: "Automático",
    detalle: "Infiere por la forma del origen",
  },
  {
    id: "memlord",
    nombre: "memlord",
    detalle: "Export JSON/JSONL de store_memory",
  },
  {
    id: "gbrain",
    nombre: "gbrain",
    detalle: "Páginas .md o export {fact|content}",
  },
  {
    id: "obsidian",
    nombre: "Obsidian",
    detalle: "Vault .md — wikilinks → vínculos",
  },
  {
    id: "jsonl",
    nombre: "JSONL nativo",
    detalle: "Formato de `nous export`",
  },
  {
    id: "json",
    nombre: "JSON nativo",
    detalle: "Array o documento único Nous",
  },
];

/** Formatea ts unix a fecha corta. */
function fecha(ts: number): string {
  return new Date(ts * 1000).toLocaleString("es", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function PaginaImportar() {
  const { memoria } = usarApp();
  const [formato, setFormato] = useState<Formato>("memlord");
  const [ruta, setRuta] = useState("");
  const [fuente, setFuente] = useState("");
  const [rama, setRama] = useState("");
  const [ejecutando, setEjecutando] = useState<"previa" | "real" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previa, setPrevia] = useState<ResultadoImportacion | null>(null);
  const [resultado, setResultado] = useState<ResultadoImportacion | null>(null);
  const [historial, setHistorial] = useState<HistorialImportacion[]>([]);

  const cargarHistorial = useCallback(() => {
    memoria?.historialImportaciones().then(setHistorial).catch(() => {});
  }, [memoria]);

  useEffect(cargarHistorial, [cargarHistorial]);

  /** Ejecuta la migración o solo la vista previa de lectura. */
  async function ejecutar(soloPrevia: boolean) {
    if (!memoria || !ruta.trim()) return;
    setEjecutando(soloPrevia ? "previa" : "real");
    setError(null);
    try {
      const r = await memoria.importarMemoria({
        formato,
        ruta: ruta.trim(),
        fuente: fuente.trim() || undefined,
        rama: rama.trim() || undefined,
        vista_previa: soloPrevia,
      });
      if (soloPrevia) {
        setPrevia(r);
      } else {
        setResultado(r);
        setPrevia(null);
        cargarHistorial();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error de migración");
    } finally {
      setEjecutando(null);
    }
  }

  const formatoInfo = FORMATOS.find((f) => f.id === formato);

  return (
    <Shell>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex max-w-3xl flex-col gap-5"
      >
        <div className="flex items-center gap-3">
          <Upload width={20} height={20} style={{ color: "var(--primary)" }} />
          <div>
            <h1 className="text-lg font-semibold">Migración de memoria</h1>
            <p className="texto-suave text-xs">
              Trae memorias de memlord, gbrain, Obsidian o un export Nous —
              sin que nada salga de tu máquina.
            </p>
          </div>
        </div>

        {/* Paso 1 — origen */}
        <div className="vidrio-fuerte rounded-2xl p-5">
          <p className="texto-suave mb-3 text-xs font-semibold tracking-wide uppercase">
            1 · Elige el origen
          </p>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
            {FORMATOS.map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => {
                  setFormato(f.id);
                  setPrevia(null);
                }}
                className={`rounded-xl px-3 py-2.5 text-left text-sm transition ${
                  formato === f.id ? "vidrio-fuerte" : "vidrio texto-suave"
                }`}
                style={
                  formato === f.id
                    ? {
                        boxShadow: "inset 0 0 0 1.5px var(--primary)",
                        color: "var(--primary)",
                      }
                    : undefined
                }
              >
                <p className="font-semibold">{f.nombre}</p>
                <p className="texto-suave mt-0.5 text-[0.65rem] leading-tight">
                  {f.detalle}
                </p>
              </button>
            ))}
          </div>
        </div>

        {/* Paso 2 — parámetros */}
        <div className="vidrio-fuerte flex flex-col gap-3 rounded-2xl p-5">
          <p className="texto-suave text-xs font-semibold tracking-wide uppercase">
            2 · Parámetros de conexión e importación
          </p>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">
              Ruta del origen{" "}
              <span className="texto-suave font-normal">
                — en la máquina donde corre Nous
              </span>
            </span>
            <input
              type="text"
              value={ruta}
              onChange={(e) => {
                setRuta(e.target.value);
                setPrevia(null);
              }}
              placeholder={
                formato === "obsidian" || formato === "gbrain"
                  ? "C:\\Users\\tu\\vault-o-paginas"
                  : "C:\\Users\\tu\\memlord-export.jsonl"
              }
              className="vidrio rounded-xl px-3 py-2.5 font-mono text-xs outline-none"
            />
            {formatoInfo && (
              <span className="texto-suave text-[0.68rem]">
                {formatoInfo.detalle}
              </span>
            )}
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">
                Nombre de fuente{" "}
                <span className="texto-suave font-normal">(opcional)</span>
              </span>
              <input
                type="text"
                value={fuente}
                onChange={(e) => setFuente(e.target.value)}
                placeholder={formato}
                className="vidrio rounded-xl px-3 py-2 text-sm outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">
                Rama destino{" "}
                <span className="texto-suave font-normal">(opcional)</span>
              </span>
              <input
                type="text"
                value={rama}
                onChange={(e) => setRama(e.target.value)}
                placeholder="main"
                className="vidrio rounded-xl px-3 py-2 text-sm outline-none"
              />
            </label>
          </div>
          <div className="mt-1 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void ejecutar(true)}
              disabled={ejecutando !== null || !ruta.trim()}
              className="vidrio flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold disabled:opacity-40"
              style={{ color: "var(--primary)" }}
            >
              <Eye width={15} height={15} />
              {ejecutando === "previa" ? "Validando…" : "Validar origen"}
            </button>
            <button
              type="button"
              onClick={() => void ejecutar(false)}
              disabled={ejecutando !== null || !ruta.trim()}
              className="flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white transition disabled:opacity-40"
              style={{ background: "var(--primary)" }}
            >
              <Upload width={15} height={15} />
              {ejecutando === "real" ? "Migrando…" : "Ejecutar migración"}
            </button>
          </div>
        </div>

        {/* Vista previa */}
        {previa && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="vidrio rounded-2xl p-5"
          >
            <p className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <Eye width={16} height={16} style={{ color: "var(--primary)" }} />
              Vista previa — nada se ha escrito todavía
            </p>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {(
                [
                  ["Memorias detectadas nuevas", previa.nuevas ?? 0, "var(--success)"],
                  ["Ya existen (se omitirían)", previa.omitidos, "var(--primary)"],
                  ["Filas con error", previa.errores, "var(--danger, #c0392b)"],
                  ["Vínculos detectados", previa.vinculos, "var(--primary)"],
                ] as const
              ).map(([etiqueta, valor, color]) => (
                <div key={etiqueta} className="vidrio rounded-xl p-3">
                  <p className="texto-suave text-[0.65rem]">{etiqueta}</p>
                  <p
                    className="mt-0.5 text-xl font-semibold tabular-nums"
                    style={{ color }}
                  >
                    {valor}
                  </p>
                </div>
              ))}
            </div>
            <p className="texto-suave mt-3 text-xs">
              Pulsa «Ejecutar migración» para traerlas a tu línea de universo.
            </p>
          </motion.div>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm">
            <WarningTriangle
              width={18}
              height={18}
              className="mt-0.5 shrink-0 text-red-400"
            />
            <p className="break-all">{error}</p>
          </div>
        )}

        {/* Resultado de la ejecución */}
        {resultado && (
          <motion.div
            initial={{ opacity: 0, scale: 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            className="vidrio-fuerte rounded-2xl p-5"
          >
            <div className="mb-3 flex items-center gap-2">
              <CheckCircle
                width={18}
                height={18}
                style={{ color: "var(--success)" }}
              />
              <h2 className="font-semibold">
                Migración completada — fuente{" "}
                <code className="text-sm">{resultado.fuente}</code>
              </h2>
            </div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {(
                [
                  ["Memorias migradas", resultado.importados, "var(--success)"],
                  ["Omitidas (duplicadas)", resultado.omitidos, "var(--primary)"],
                  ["Errores de fila", resultado.errores, "var(--danger, #c0392b)"],
                  ["Vínculos creados", resultado.vinculos, "var(--primary)"],
                ] as const
              ).map(([etiqueta, valor, color]) => (
                <div key={etiqueta} className="vidrio rounded-xl p-3">
                  <p className="texto-suave text-[0.65rem]">{etiqueta}</p>
                  <p
                    className="mt-0.5 text-xl font-semibold tabular-nums"
                    style={{ color }}
                  >
                    {valor}
                  </p>
                </div>
              ))}
            </div>
            <p className="texto-suave mt-3 text-xs">
              Los embeddings se regeneraron con tu modelo local — reimportar el
              mismo origen no duplica nada.
            </p>
          </motion.div>
        )}

        {/* Historial de migraciones */}
        {historial.length > 0 && (
          <div className="vidrio-fuerte rounded-2xl p-5">
            <p className="texto-suave mb-3 flex items-center gap-2 text-xs font-semibold tracking-wide uppercase">
              <Clock width={14} height={14} /> Migraciones recientes
            </p>
            <ul className="flex flex-col gap-1.5">
              {historial.map((h, i) => (
                <li
                  key={`${h.ts}-${i}`}
                  className="vidrio flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded-xl px-3 py-2 text-xs"
                >
                  <span className="texto-suave tabular-nums">
                    {fecha(h.ts)}
                  </span>
                  <span className="font-semibold">{h.fuente}</span>
                  <span className="texto-suave">({h.formato})</span>
                  <span className="ml-auto tabular-nums">
                    <span style={{ color: "var(--success)" }}>
                      +{h.resultado.importados}
                    </span>
                    {" · "}
                    {h.resultado.omitidos} omitidas
                    {" · "}
                    {h.resultado.errores} errores
                    {h.resultado.vinculos > 0 &&
                      ` · ${h.resultado.vinculos} vínculos`}
                  </span>
                  <code className="texto-suave w-full truncate text-[0.65rem]">
                    {h.ruta}
                  </code>
                </li>
              ))}
            </ul>
          </div>
        )}
      </motion.div>
    </Shell>
  );
}
