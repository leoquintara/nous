"use client";

/**
 * Configuración de modelos de IA externos.
 *
 * Local-first por defecto: Nous funciona completo sin proveedor. Si el
 * dev conecta un endpoint OpenAI-compatible (OpenAI, Groq, OpenRouter,
 * Ollama, LM Studio…), el motor predictivo pasa de heurística local a
 * escenarios generados por LLM. La API key se cifra con pgcrypto en
 * PostgreSQL y nunca vuelve a viajar en claro — la UI solo ve máscaras.
 */

import {
  CheckCircle,
  Cpu,
  Eye,
  EyeClosed,
  Play,
  Plus,
  Trash,
  WarningTriangle,
  XmarkCircle,
} from "iconoir-react";
import { useCallback, useEffect, useState } from "react";

import { usarApp } from "@/ui/proveedor-app";
import { Shell } from "@/ui/shell";
import type {
  NuevoProveedorIA,
  ProveedorIA,
  PruebaProveedor,
} from "@/domain/modelos";

/** Presets de endpoints OpenAI-compatible comunes. */
const PRESETS = [
  { nombre: "OpenAI", url: "https://api.openai.com/v1" },
  { nombre: "Groq", url: "https://api.groq.com/openai/v1" },
  { nombre: "OpenRouter", url: "https://openrouter.ai/api/v1" },
  { nombre: "Ollama", url: "http://localhost:11434/v1" },
  { nombre: "LM Studio", url: "http://localhost:1234/v1" },
] as const;

const FORM_VACIO = {
  id: "",
  nombre: "",
  base_url: "https://api.openai.com/v1",
  modelo: "",
  api_key: "",
  habilitado: true,
  incluir_contenido: false,
};

/** Slugifica el nombre para usarlo como id de proveedor. */
function slugDe(nombre: string): string {
  return nombre
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

export default function PaginaModelos() {
  const { memoria } = usarApp();
  const [proveedores, setProveedores] = useState<ProveedorIA[]>([]);
  const [form, setForm] = useState(FORM_VACIO);
  const [editando, setEditando] = useState<string | null>(null);
  const [verKey, setVerKey] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pruebas, setPruebas] = useState<
    Record<string, PruebaProveedor | "cargando">
  >({});

  const recargar = useCallback(() => {
    memoria
      ?.listarProveedoresIA()
      .then(setProveedores)
      .catch(() => {});
  }, [memoria]);

  useEffect(recargar, [recargar]);

  /** Persiste el formulario (alta o edición del proveedor). */
  async function guardar() {
    if (!memoria) return;
    const id = editando ?? slugDe(form.nombre);
    if (!id) {
      setError("El nombre es obligatorio");
      return;
    }
    setGuardando(true);
    setError(null);
    try {
      const datos: NuevoProveedorIA = {
        nombre: form.nombre,
        base_url: form.base_url,
        modelo: form.modelo,
        habilitado: form.habilitado,
        incluir_contenido: form.incluir_contenido,
      };
      if (form.api_key.trim()) datos.api_key = form.api_key.trim();
      await memoria.guardarProveedorIA(id, datos);
      setForm(FORM_VACIO);
      setEditando(null);
      recargar();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al guardar");
    } finally {
      setGuardando(false);
    }
  }

  /** Prueba la conexión del proveedor contra su endpoint. */
  async function probar(id: string) {
    if (!memoria) return;
    setPruebas((p) => ({ ...p, [id]: "cargando" }));
    try {
      const r = await memoria.probarProveedorIA(id);
      setPruebas((p) => ({ ...p, [id]: r }));
    } catch (e) {
      setPruebas((p) => ({
        ...p,
        [id]: { ok: false, detalle: String(e) },
      }));
    }
  }

  /** Carga un proveedor en el formulario para editarlo. */
  function editar(p: ProveedorIA) {
    setEditando(p.id);
    setForm({
      id: p.id,
      nombre: p.nombre,
      base_url: p.base_url,
      modelo: p.modelo,
      api_key: "",
      habilitado: p.habilitado,
      incluir_contenido: p.incluir_contenido,
    });
  }

  return (
    <Shell>
      <section className="mx-auto flex max-w-3xl flex-col gap-5">
        <header>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Cpu width={20} height={20} style={{ color: "var(--primary)" }} />
            Modelos de IA
          </h1>
          <p className="texto-suave mt-1 text-sm">
            Conecta un endpoint OpenAI-compatible para activar el motor
            predictivo: los escenarios de futuro del río y el análisis de
            proyección pasan de heurística local a tu LLM.
          </p>
        </header>

        {/* Warning de privacidad — obligatorio al salir del local-first. */}
        <div
          className="vidrio flex gap-3 rounded-2xl p-4 text-sm"
          role="note"
          style={{ borderColor: "var(--warning, #e0a04f)" }}
        >
          <WarningTriangle
            width={18}
            height={18}
            className="mt-0.5 shrink-0"
            style={{ color: "var(--warning, #e0a04f)" }}
          />
          <p className="texto-suave">
            Al habilitar un proveedor, un{" "}
            <strong className="text-(--ink)">resumen estadístico</strong>{" "}
            de tu línea temporal sale de tu máquina hacia ese endpoint. El
            contenido de tus memorias solo viaja si activas{" "}
            <em>incluir contenido</em> por proveedor. La API key se cifra
            con pgcrypto en PostgreSQL y nunca se muestra completa.
          </p>
        </div>

        {/* Formulario alta/edición. */}
        <div className="vidrio-fuerte flex flex-col gap-3 rounded-2xl p-5">
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.nombre}
                type="button"
                onClick={() =>
                  setForm((f) => ({
                    ...f,
                    nombre: f.nombre || p.nombre,
                    base_url: p.url,
                  }))
                }
                className="vidrio texto-suave rounded-lg px-2.5 py-1 text-xs hover:text-(--ink)"
              >
                {p.nombre}
              </button>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm">
              <span className="texto-suave">Nombre</span>
              <input
                value={form.nombre}
                onChange={(e) =>
                  setForm((f) => ({ ...f, nombre: e.target.value }))
                }
                placeholder="Mi OpenAI"
                className="vidrio rounded-xl px-3 py-2 outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="texto-suave">Modelo</span>
              <input
                value={form.modelo}
                onChange={(e) =>
                  setForm((f) => ({ ...f, modelo: e.target.value }))
                }
                placeholder="gpt-4o-mini / llama3.1"
                className="vidrio rounded-xl px-3 py-2 outline-none"
              />
            </label>
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="texto-suave">Base URL (OpenAI-compatible)</span>
            <input
              value={form.base_url}
              onChange={(e) =>
                setForm((f) => ({ ...f, base_url: e.target.value }))
              }
              placeholder="https://api.openai.com/v1"
              className="vidrio rounded-xl px-3 py-2 outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="texto-suave">
              API key{" "}
              {editando && (
                <em className="not-italic opacity-70">
                  (vacío = conservar la actual)
                </em>
              )}
            </span>
            <span className="relative">
              <input
                type={verKey ? "text" : "password"}
                value={form.api_key}
                onChange={(e) =>
                  setForm((f) => ({ ...f, api_key: e.target.value }))
                }
                placeholder={
                  editando ? "sk-… (sin cambios)" : "sk-…"
                }
                autoComplete="off"
                className="vidrio w-full rounded-xl px-3 py-2 pr-10 outline-none"
              />
              <button
                type="button"
                onClick={() => setVerKey((v) => !v)}
                aria-label={verKey ? "Ocultar key" : "Mostrar key"}
                className="texto-suave absolute top-1/2 right-3 -translate-y-1/2"
              >
                {verKey ? (
                  <EyeClosed width={15} height={15} />
                ) : (
                  <Eye width={15} height={15} />
                )}
              </button>
            </span>
          </label>
          <div className="flex flex-wrap items-center gap-5 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.habilitado}
                onChange={(e) =>
                  setForm((f) => ({ ...f, habilitado: e.target.checked }))
                }
                className="h-4 w-4 accent-(--primary)"
              />
              Habilitado para el motor predictivo
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.incluir_contenido}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    incluir_contenido: e.target.checked,
                  }))
                }
                className="h-4 w-4 accent-(--primary)"
              />
              Incluir contenido de memorias
            </label>
          </div>
          {form.incluir_contenido && (
            <p
              className="text-xs"
              style={{ color: "var(--warning, #e0a04f)" }}
            >
              Con esta opción, fragmentos recientes de tus memorias viajan
              al endpoint en cada predicción.
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <button
              onClick={guardar}
              disabled={guardando || !form.nombre.trim() || !form.modelo.trim()}
              className="flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
              style={{ background: "var(--primary)" }}
            >
              <Plus width={15} height={15} />
              {guardando
                ? "Guardando…"
                : editando
                  ? "Guardar cambios"
                  : "Agregar proveedor"}
            </button>
            {editando && (
              <button
                onClick={() => {
                  setEditando(null);
                  setForm(FORM_VACIO);
                }}
                className="vidrio texto-suave rounded-xl px-4 py-2.5 text-sm"
              >
                Cancelar
              </button>
            )}
          </div>
        </div>

        {/* Lista de proveedores configurados. */}
        {proveedores.length > 0 && (
          <ul className="flex flex-col gap-3">
            {proveedores.map((p) => {
              const prueba = pruebas[p.id];
              return (
                <li
                  key={p.id}
                  className="vidrio flex flex-col gap-2 rounded-2xl p-4"
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="font-semibold">{p.nombre}</span>
                    <span className="texto-suave font-mono text-xs">
                      {p.modelo}
                    </span>
                    {p.api_key_set ? (
                      <span className="texto-suave font-mono text-xs">
                        {p.api_key_mascara}
                      </span>
                    ) : (
                      <span
                        className="text-xs"
                        style={{ color: "var(--warning, #e0a04f)" }}
                      >
                        sin key — solo endpoints sin auth
                      </span>
                    )}
                    <span
                      className="rounded-full px-2 py-0.5 text-[0.68rem] font-semibold"
                      style={{
                        background: p.habilitado
                          ? "var(--halo)"
                          : "var(--linea-vidrio)",
                        color: p.habilitado ? "var(--primary)" : "var(--ink)",
                      }}
                    >
                      {p.habilitado ? "habilitado" : "deshabilitado"}
                    </span>
                    {p.incluir_contenido && (
                      <span className="texto-suave text-[0.68rem]">
                        + contenido
                      </span>
                    )}
                  </div>
                  <span className="texto-suave font-mono text-[0.68rem] break-all">
                    {p.base_url}
                  </span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => probar(p.id)}
                      disabled={prueba === "cargando"}
                      className="vidrio texto-suave flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs hover:text-(--ink) disabled:opacity-50"
                    >
                      <Play width={12} height={12} />
                      {prueba === "cargando" ? "Probando…" : "Probar"}
                    </button>
                    <button
                      onClick={() => editar(p)}
                      className="vidrio texto-suave rounded-lg px-2.5 py-1 text-xs hover:text-(--ink)"
                    >
                      Editar
                    </button>
                    <button
                      onClick={async () => {
                        await memoria?.eliminarProveedorIA(p.id);
                        recargar();
                      }}
                      aria-label={`Eliminar ${p.nombre}`}
                      className="vidrio texto-suave rounded-lg px-2.5 py-1 text-xs hover:text-(--danger)"
                    >
                      <Trash width={12} height={12} />
                    </button>
                    {prueba && prueba !== "cargando" && (
                      <span
                        className="flex items-center gap-1 text-xs"
                        style={{
                          color: prueba.ok
                            ? "var(--success)"
                            : "var(--danger)",
                        }}
                      >
                        {prueba.ok ? (
                          <CheckCircle width={13} height={13} />
                        ) : (
                          <XmarkCircle width={13} height={13} />
                        )}
                        {prueba.ok
                          ? `Conectado · ${prueba.modelos?.length ?? 0} modelos`
                          : (prueba.detalle ?? "falló")}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </Shell>
  );
}
