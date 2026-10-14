/**
 * Preferencias de la vista /universo (el río temporal 4D).
 *
 * Se persisten en PostgreSQL bajo la clave `universo` — ver
 * `usar-prefs-universo.ts`. Toda opción aquí se aplica en caliente
 * vía ref, sin reconstruir la escena.
 */

import type { Dimension } from "@/domain/modelos";

/** Apariencia configurable del río temporal. */
export interface PrefsUniverso {
  /** Mostrar las cintas tributarias de las fuentes. */
  afluentes: boolean;
  /** Máximo de afluentes dibujados (0..14, ordenados por volumen). */
  maxAfluentes: number;
  /** Mostrar cauces de ramas reales distintas de "main". */
  ramas: boolean;
  /** Mostrar los cauces-fantasma de futuros probables. */
  fantasmas: boolean;
  /** Dimensiones con cauce-fantasma activo (0..3 canales). */
  fantasmaDims: Dimension[];
  /** Multiplicador de opacidad de los fantasmas (0..2). */
  intensidadFantasma: number;
  /** Mostrar arcos de relación entre orbes. */
  arcos: boolean;
  /** Mostrar etiquetas DOM (ticks, objetivos, escenarios). */
  etiquetas: boolean;
  /** Mostrar el polvo ambiental alrededor del río. */
  polvo: boolean;
  /** Mostrar los faros de objetivos. */
  objetivos: boolean;
  /** Separación lateral de afluentes (0.2..2.5 — escala Z). */
  separacion: number;
  /** Fuerza del bloom del composer (0..2). */
  bloom: number;
  /** Sobrescritura de color por dimensión (hex; {} = paleta base). */
  colores: Partial<Record<Dimension, string>>;
}

export const PREFS_UNIVERSO_DEFAULT: PrefsUniverso = {
  afluentes: true,
  maxAfluentes: 14,
  ramas: true,
  fantasmas: true,
  fantasmaDims: ["cognitiva", "somatica", "conductual"],
  intensidadFantasma: 1,
  arcos: true,
  etiquetas: true,
  polvo: true,
  objetivos: true,
  separacion: 1,
  bloom: 0.75,
  colores: {},
};
