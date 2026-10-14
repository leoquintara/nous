"use client";

/**
 * Migración visual de memoria: trae memorias externas a Nous.
 *
 * Fuentes soportadas: vault Obsidian (directorio .md), export gbrain
 * (.md o JSON/JSONL), export memlord (JSON/JSONL con name/content),
 * y el JSONL nativo de ``nous export``. La ruta es de la máquina donde
 * corre el backend — local-first, nada sale del equipo. Reimportar es
 * seguro: los ids ya existentes se omiten.
 */

import { motion } from "motion/react";
import { CheckCircle, Upload, WarningTriangle } from "iconoir-react";
import { useState } from "react";

import type {
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
    detalle: "Infiere por la forma del origen (dir → obsidian, .jsonl → jsonl…)",
  },
  {
    id: "memlord",
    nombre: "memlord",
    detalle: "Export JSON/JSONL de store_memory: {name, content, memory_type, tags}",
  },
  {
    id: "gbrain",
    nombre: "gbrain",
    detalle: "Directorio de páginas .md o export con {fact|content|text}",
  },
  {
    id: "obsidian",
    nombre: "Obsidian",
    detalle: "Vault .md — frontmatter/tags → tags, [[wikilinks]] → vínculos",
  },
  {
    id: "jsonl",
    nombre: "JSONL nativo",
    detalle: "Formato de `nous export` — portabilidad entre instancias",
  },
  {
    id: "json",
    nombre: "JSON nativo",
    detalle: "Array o documento único en formato Nous",
  },
];

export default function PaginaImportar() {
  const { memoria } = usarApp();
  const [formato, setFormato] = useState<Formato>("memlord");
  const [ruta, setRuta] = useState("");
  const [fuente, setFuente] = useState("");
  const [rama, setRama] = useState("");
  const [ejecutando, setEjecutando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<ResultadoImportacion | null>(null);

  /** Ejecuta la migración contra el backend. */
  async function migrar() {
    if (!memoria || !ruta.trim()) return;
    setEjecutando(true);
    setError(null);
    setResultado(null);
    try {
      const r = await memoria.importarMemoria({
        formato,
        ruta: ruta.trim(),
        fuente: fuente.trim() || undefined,
        rama: rama.trim() || undefined,
      });
      setResultado(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error de migración");
    } finally {
      setEjecutando(false);
    }
  }

  return (
    <Shell>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex max-w-3xl flex-col gap-4"
      >
        <div className="flex items-center gap-3">
          <Upload width={20} height={20} style={{ color: "var(--primary)" }} />
          <div>
            <h1 className="text-lg font-semibold">Migración de memoria</h1>
            <p className="texto-suave text-xs">
              Trae memorias de memlord, gbrain, Obsidian o un export Nous a tu
              línea de universo — sin salir de tu máquina.
            </p>
          </div>
        </div>

        {/* Selector de formato */}
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
          {FORMATOS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFormato(f.id)}
              title={f.detalle}
              className={`vidrio rounded-xl px-3 py-2.5 text-left text-sm transition ${
                formato === f.id ? "ring-2" : "texto-suave"
              }`}
              style={
                formato === f.id
                  ? ({ "--tw-ring-color": "var(--primary)" } as never)
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

        {/* Parámetros de origen e importación */}
        <div className="vidrio-fuerte flex flex-col gap-3 rounded-2xl p-5">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">
              Ruta del origen{" "}
              <span className="texto-suave font-normal">
                (en la máquina donde corre Nous)
              </span>
            </span>
            <input
              type="text"
              value={ruta}
              onChange={(e) => setRuta(e.target.value)}
              placeholder={
                formato === "obsidian" || formato === "gbrain"
                  ? "C:\\Users\\tu\\vault-o-paginas"
                  : "C:\\Users\\tu\\memlord-export.jsonl"
              }
              className="vidrio rounded-xl px-3 py-2 font-mono text-xs outline-none"
            />
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
          <button
            type="button"
            onClick={() => void migrar()}
            disabled={ejecutando || !ruta.trim()}
            className="mt-1 flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white transition disabled:opacity-40"
            style={{ background: "var(--primary)" }}
          >
            <Upload width={15} height={15} />
            {ejecutando ? "Migrando…" : "Ejecutar migración"}
          </button>
        </div>

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

        {/* Resultado */}
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
      </motion.div>
    </Shell>
  );
}
