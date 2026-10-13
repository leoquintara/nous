/**
 * Motor de reglas de color semánticas — port del plugin DSH Memory.
 *
 * En el modo de color "reglas", las reglas ordenadas evalúan cada nodo:
 * la primera que coincide lo pinta y el resto toma el color de reserva.
 * Editables en el panel de control y persistidas en PostgreSQL.
 */

import type { Nodo } from "./tipos";

/** Campos evaluables por una condición de regla. */
export type CampoRegla =
  | "estado"
  | "edadDias"
  | "deg"
  | "dimension"
  | "fuente"
  | "refuerzos"
  | "amplitud";

/** Operadores de comparación soportados. */
export type OpRegla =
  | "eq"
  | "neq"
  | "lt"
  | "lte"
  | "gt"
  | "gte"
  | "has"
  | "in";

/** Una condición dentro de una regla. */
export interface CondicionRegla {
  field: CampoRegla;
  op: OpRegla;
  value?: unknown;
  values?: unknown[];
}

/** Regla de color: tests ordenados, la primera coincidencia gana. */
export interface Regla {
  id: string;
  color: string;
  enabled: boolean;
  tests: CondicionRegla[];
}

const DIA_MS = 86_400_000;

const CAMPOS: Record<CampoRegla, (n: Nodo) => unknown> = {
  estado: (n) => n.estado,
  edadDias: (n) => (Date.now() - n.timestamp_unix * 1000) / DIA_MS,
  deg: (n) => n.deg,
  dimension: (n) => n.dimension,
  fuente: (n) => n.fuente,
  refuerzos: (n) => n.refuerzos,
  amplitud: (n) => n.amplitud,
};

const OPS: Record<OpRegla, (v: unknown, x: unknown) => boolean> = {
  eq: (v, x) => v === x,
  neq: (v, x) => v !== x,
  lt: (v, x) => (v as number) < (x as number),
  lte: (v, x) => (v as number) <= (x as number),
  gt: (v, x) => (v as number) > (x as number),
  gte: (v, x) => (v as number) >= (x as number),
  has: (v, x) => Array.isArray(v) && v.includes(x),
  in: (v, x) => Array.isArray(x) && x.includes(v),
};

/** Indica si una condición se cumple sobre el nodo. */
export function condicionCumple(n: Nodo, c: CondicionRegla): boolean {
  const v = CAMPOS[c.field]?.(n);
  return v !== undefined && OPS[c.op](v, c.value ?? c.values);
}

/**
 * Devuelve el color de la primera regla que coincide; si ninguna, el
 * reserva. Una regla sin tests es catch-all.
 */
export function colorDeReglas(
  n: Nodo,
  reglas: Regla[],
  reserva: string,
): string {
  for (const r of reglas) {
    if (r.enabled === false) continue;
    if (!r.tests?.length || r.tests.some((c) => condicionCumple(n, c))) {
      return r.color;
    }
  }
  return reserva;
}

/**
 * Paleta por defecto — psicología del aprendizaje (igual que en DSH):
 * sepia oscuro = sinapsis muerta (candidata a poda), gris = traza que
 * se desvanece, rojo = LTM consolidada (importante), verde = huella
 * recién codificada, azul = conocimiento general.
 */
export const REGLAS_DEFAULT: Regla[] = [
  {
    id: "dead",
    color: "#45405a",
    enabled: true,
    tests: [{ field: "estado", op: "eq", value: "dead" }],
  },
  {
    id: "decayed",
    color: "#8b93a1",
    enabled: true,
    tests: [{ field: "estado", op: "eq", value: "decayed" }],
  },
  {
    id: "important",
    color: "#ff5a66",
    enabled: true,
    tests: [
      { field: "refuerzos", op: "gte", value: 3 },
      { field: "deg", op: "gte", value: 5 },
      { field: "amplitud", op: "gte", value: 0.85 },
    ],
  },
  {
    id: "fresh",
    color: "#4ade80",
    enabled: true,
    tests: [{ field: "edadDias", op: "lt", value: 2 }],
  },
  { id: "general", color: "#5b8ef4", enabled: true, tests: [] },
];
