/**
 * Tests del motor de reglas de color semánticas (reglas.ts).
 *
 * Se validan operadores, campos evaluables, precedencia por orden y
 * el comportamiento de reglas desactivadas y catch-all.
 */

import { describe, expect, it } from "vitest";

import { colorDeReglas, condicionCumple, REGLAS_DEFAULT, type Regla } from "./reglas";
import type { Nodo } from "./tipos";

/** Fabrica un nodo de prueba con overrides puntuales. */
function nodo(parcial: Partial<Nodo> = {}): Nodo {
  return {
    id: "n",
    etiqueta: "",
    contenido: "",
    fuente: "signal",
    dimension: "cognitiva",
    estado: "working",
    amplitud: 0.5,
    refuerzos: 0,
    timestamp_unix: Math.floor(Date.now() / 1000),
    expira_en: null,
    pinned: false,
    deg: 0,
    rank: 0,
    tags: [],
    ...parcial,
  };
}

describe("condicionCumple", () => {
  const n = nodo({ estado: "consolidated", deg: 7, fuente: "mcp" });

  const casos: [Parameters<typeof condicionCumple>[1], boolean][] = [
    [{ field: "estado", op: "eq", value: "consolidated" }, true],
    [{ field: "estado", op: "neq", value: "fresh" }, true],
    [{ field: "deg", op: "gte", value: 5 }, true],
    [{ field: "deg", op: "lt", value: 5 }, false],
    [{ field: "fuente", op: "in", values: ["mcp", "cli"] }, true],
    [{ field: "fuente", op: "in", values: ["cli"] }, false],
    [{ field: "deg", op: "gt", value: 7 }, false],
    [{ field: "deg", op: "lte", value: 7 }, true],
  ];
  it.each(casos)("%j → %s", (cond, esperado) => {
    expect(condicionCumple(n, cond)).toBe(esperado);
  });

  it("edadDias refleja la antigüedad del timestamp", () => {
    const viejo = nodo({ timestamp_unix: 1 });
    expect(
      condicionCumple(viejo, { field: "edadDias", op: "gt", value: 100 }),
    ).toBe(true);
  });
});

describe("colorDeReglas", () => {
  it("la primera regla que coincide gana aunque haya más coincidencias", () => {
    const reglas: Regla[] = [
      {
        id: "a",
        color: "#111111",
        enabled: true,
        tests: [{ field: "estado", op: "eq", value: "fresh" }],
      },
      { id: "b", color: "#222222", enabled: true, tests: [] },
    ];
    expect(colorDeReglas(nodo({ estado: "fresh" }), reglas, "#000000")).toBe(
      "#111111",
    );
  });

  it("una regla desactivada no coincide aunque sus tests se cumplan", () => {
    const reglas: Regla[] = [
      {
        id: "a",
        color: "#111111",
        enabled: false,
        tests: [{ field: "estado", op: "eq", value: "fresh" }],
      },
      { id: "b", color: "#222222", enabled: true, tests: [] },
    ];
    expect(colorDeReglas(nodo({ estado: "fresh" }), reglas, "#000000")).toBe(
      "#222222",
    );
  });

  it("sin coincidencias devuelve el color de reserva", () => {
    const reglas: Regla[] = [
      {
        id: "a",
        color: "#111111",
        enabled: true,
        tests: [{ field: "estado", op: "eq", value: "dead" }],
      },
    ];
    expect(colorDeReglas(nodo({ estado: "fresh" }), reglas, "#abcdef")).toBe(
      "#abcdef",
    );
  });

  it("tests múltiples en una regla funcionan como OR", () => {
    const reglas: Regla[] = [
      {
        id: "a",
        color: "#ff0000",
        enabled: true,
        tests: [
          { field: "refuerzos", op: "gte", value: 3 },
          { field: "deg", op: "gte", value: 5 },
        ],
      },
      { id: "b", color: "#0000ff", enabled: true, tests: [] },
    ];
    expect(colorDeReglas(nodo({ deg: 9, refuerzos: 0 }), reglas, "#000")).toBe(
      "#ff0000",
    );
  });
});

describe("REGLAS_DEFAULT", () => {
  it("cubre los estados de tejido con la paleta de aprendizaje", () => {
    expect(
      colorDeReglas(nodo({ estado: "dead" }), REGLAS_DEFAULT, "#000"),
    ).toBe("#45405a");
    expect(
      colorDeReglas(nodo({ estado: "decayed" }), REGLAS_DEFAULT, "#000"),
    ).toBe("#8b93a1");
    // Un nodo de trabajo con +2 días cae en la regla general.
    const hace10dias = Math.floor(Date.now() / 1000) - 10 * 86_400;
    expect(
      colorDeReglas(
        nodo({ estado: "working", timestamp_unix: hace10dias }),
        REGLAS_DEFAULT,
        "#000",
      ),
    ).toBe("#5b8ef4");
  });
});
