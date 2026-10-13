/**
 * Tests de la conversión de eventos a grafo cerebral (tipos.ts).
 *
 * eventosAGrafo es determinista salvo el jitter de posición inicial:
 * se validan estructura, estados por edad, aristas y ranking.
 */

import { describe, expect, it } from "vitest";

import type { Evento } from "@/domain/modelos";

import { REGLAS_DEFAULT } from "./reglas";
import { colorNodo, eventosAGrafo } from "./tipos";

const AHORA = 1_800_000_000;
const DIA = 86_400;

/** Fabrica un evento de prueba con overrides puntuales. */
function evento(parcial: Partial<Evento> = {}): Evento {
  return {
    id: parcial.id ?? crypto.randomUUID(),
    timestamp_unix: parcial.timestamp_unix ?? AHORA,
    dimension: parcial.dimension ?? "cognitiva",
    fuente: parcial.fuente ?? "test",
    contenido: parcial.contenido ?? "nota",
    amplitud: parcial.amplitud ?? 0.5,
    frecuencia: null,
    refuerzos: parcial.refuerzos ?? 0,
  };
}

describe("colorNodo", () => {
  it("devuelve un hex válido por dimensión", () => {
    const nodo = eventosAGrafo([evento()], AHORA).nodes[0];
    expect(colorNodo(nodo)).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("un nodo viejo queda más oscuro que uno fresh de la misma dimensión", () => {
    const base = evento({ timestamp_unix: AHORA });
    const viejo = evento({ timestamp_unix: AHORA - 90 * DIA });
    // eventosAGrafo ordena ascendente: el más viejo va primero.
    const [dead, fresh] = eventosAGrafo([base, viejo], AHORA).nodes;
    expect(dead.estado).toBe("decayed");
    const canal = (hex: string) => parseInt(hex.slice(1, 3), 16);
    expect(canal(colorNodo(fresh))).toBeGreaterThan(canal(colorNodo(dead)));
  });
});

describe("colorNodo por modo", () => {
  const g = () =>
    eventosAGrafo(
      [
        evento({
          id: "a",
          fuente: "signal",
          timestamp_unix: AHORA - 1 * DIA,
          refuerzos: 5,
        }),
        evento({ id: "b", fuente: "cli", timestamp_unix: AHORA }),
      ],
      AHORA,
    );

  it("modo estado usa la paleta de tejido", () => {
    const d = g();
    expect(colorNodo(d.nodes[0], "estado", d)).toBe("#ff5a66");
    expect(colorNodo(d.nodes[1], "estado", d)).toBe("#4ade80");
  });

  it("modo fuente asigna color estable por procedencia", () => {
    const d = g();
    const c1 = colorNodo(d.nodes[0], "fuente", d);
    const c2 = colorNodo(d.nodes[1], "fuente", d);
    expect(c1).not.toBe(c2);
    expect(colorNodo(d.nodes[0], "fuente", d)).toBe(c1);
  });

  it("modo reglas aplica la primera regla que coincide", () => {
    const d = g();
    // refuerzos 5 → regla "important" (#ff5a66).
    expect(
      colorNodo(d.nodes[0], "reglas", d, { rules: REGLAS_DEFAULT }),
    ).toBe("#ff5a66");
  });

  it("una sobreescritura en prefs.colors gana al color del modo", () => {
    const d = g();
    expect(
      colorNodo(d.nodes[0], "estado", d, {
        colors: { "estado:consolidated": "#123456" },
      }),
    ).toBe("#123456");
  });
});

describe("eventosAGrafo", () => {
  it("lista vacía produce status vacio sin estructuras", () => {
    const g = eventosAGrafo([]);
    expect(g.status).toBe("vacio");
    expect(g.nodes).toHaveLength(0);
    expect(g.edges).toHaveLength(0);
  });

  it("ordena cronológicamente aunque la entrada venga desordenada", () => {
    const tarde = evento({ id: "t", timestamp_unix: AHORA });
    const pronto = evento({ id: "p", timestamp_unix: AHORA - DIA });
    const g = eventosAGrafo([tarde, pronto], AHORA);
    expect(g.nodes[0].id).toBe("p");
    expect(g.idToIdx.get("t")).toBe(1);
  });

  it("asigna estado por edad: fresh, working, decayed", () => {
    const g = eventosAGrafo(
      [
        evento({ timestamp_unix: AHORA - 1 * DIA }),
        evento({ timestamp_unix: AHORA - 5 * DIA }),
        evento({ timestamp_unix: AHORA - 40 * DIA }),
      ],
      AHORA,
    );
    // Orden cronológico: el más viejo (40d) primero.
    expect(g.nodes[0].estado).toBe("decayed");
    expect(g.nodes[1].estado).toBe("working");
    expect(g.nodes[2].estado).toBe("fresh");
  });

  it("evento evocado 3+ veces queda consolidated aunque sea viejo", () => {
    const g = eventosAGrafo(
      [
        evento({
          timestamp_unix: AHORA - 90 * DIA,
          amplitud: 0.1,
          refuerzos: 5,
        }),
      ],
      AHORA,
    );
    expect(g.nodes[0].estado).toBe("consolidated");
    expect(g.nodes[0].refuerzos).toBe(5);
  });

  it("evento de 30+ días con amplitud alta queda consolidated", () => {
    const g = eventosAGrafo(
      [evento({ timestamp_unix: AHORA - 20 * DIA, amplitud: 0.8 })],
      AHORA,
    );
    expect(g.nodes[0].estado).toBe("consolidated");
  });

  it("crea aristas link entre consecutivos y tag por dimensión", () => {
    const g = eventosAGrafo(
      [
        evento({ dimension: "cognitiva", timestamp_unix: AHORA - 3 * DIA }),
        evento({ dimension: "somatica", timestamp_unix: AHORA - 2 * DIA }),
        evento({ dimension: "cognitiva", timestamp_unix: AHORA - 1 * DIA }),
      ],
      AHORA,
    );
    const links = g.edges.filter((e) => e.kind === "link");
    const tags = g.edges.filter((e) => e.kind === "tag");
    expect(links).toHaveLength(2);
    // Solo las dos cognitivas comparten dimensión.
    expect(tags).toHaveLength(1);
    expect(tags[0]).toMatchObject({ a: 0, b: 2 });
  });

  it("un nodo viejo, débil y nunca evocado queda dead", () => {
    const g = eventosAGrafo(
      [
        evento({
          timestamp_unix: AHORA - 60 * DIA,
          amplitud: 0.1,
          refuerzos: 0,
        }),
      ],
      AHORA,
    );
    expect(g.nodes[0].estado).toBe("dead");
  });

  it("fuentes cuenta nodos por procedencia ordenada de mayor a menor", () => {
    const g = eventosAGrafo(
      [
        evento({ fuente: "cli", timestamp_unix: AHORA - 3 * DIA }),
        evento({ fuente: "signal", timestamp_unix: AHORA - 2 * DIA }),
        evento({ fuente: "cli", timestamp_unix: AHORA - 1 * DIA }),
      ],
      AHORA,
    );
    expect(g.fuentes).toEqual([
      { id: "cli", count: 2 },
      { id: "signal", count: 1 },
    ]);
  });

  it("stateCounts suma un nodo por cada estado presente", () => {
    const g = eventosAGrafo(
      [
        evento({ timestamp_unix: AHORA - 1 * DIA }),
        evento({ timestamp_unix: AHORA - 40 * DIA }),
      ],
      AHORA,
    );
    expect(g.stateCounts.fresh).toBe(1);
    expect(g.stateCounts.decayed).toBe(1);
    expect(g.stateCounts.consolidated).toBe(0);
  });

  it("un pin persistido restaura la posición exacta y marca el nodo", () => {
    const g = eventosAGrafo(
      [evento({ id: "fijado" }), evento({ id: "libre" })],
      AHORA,
      { fijado: [11, 22, 33] },
    );
    expect(g.pts[0]).toEqual({ x: 11, y: 22, z: 33 });
    expect(g.nodes[0].pinned).toBe(true);
    expect(g.nodes[1].pinned).toBe(false);
    expect(g.pts[1]).not.toEqual({ x: 11, y: 22, z: 33 });
  });

  it("deg cuenta aristas y nbrs registra vecinos simétricos", () => {
    const g = eventosAGrafo(
      [
        evento({ timestamp_unix: AHORA - 2 * DIA }),
        evento({ timestamp_unix: AHORA - 1 * DIA }),
      ],
      AHORA,
    );
    expect(g.nodes[0].deg).toBeGreaterThan(0);
    expect(g.nbrs.get(0)?.has(1)).toBe(true);
    expect(g.nbrs.get(1)?.has(0)).toBe(true);
  });
});
