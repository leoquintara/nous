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
    tags: parcial.tags ?? [],
    rama: parcial.rama ?? "main",
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

  it("las relaciones persistidas crean aristas link entre extremos", () => {
    const e1 = evento({
      id: "e1",
      dimension: "conductual",
      timestamp_unix: AHORA - 3 * DIA,
    });
    const e2 = evento({
      id: "e2",
      dimension: "somatica",
      timestamp_unix: AHORA - 2 * DIA,
    });
    const e3 = evento({
      id: "e3",
      dimension: "conductual",
      timestamp_unix: AHORA - 1 * DIA,
    });
    const g = eventosAGrafo([e1, e2, e3], AHORA, undefined, [
      { origen_id: "e1", destino_id: "e3", tipo: "explicita", peso: 1 },
    ]);
    // e1 y e3 ya están unidos por la cadena tag de conductual; la
    // relación no duplica la arista pero debe existir el par (0,2).
    const par = g.edges.some(
      (e) => (e.a === 0 && e.b === 2) || (e.a === 2 && e.b === 0),
    );
    expect(par).toBe(true);
    expect(g.nbrs.get(0)?.has(2)).toBe(true);
  });

  it("una relación entre no-adyacentes añade la arista que faltaba", () => {
    const ids = ["n1", "n2", "n3", "n4"];
    const g = eventosAGrafo(
      ids.map((id, i) =>
        evento({
          id,
          dimension: "cognitiva",
          timestamp_unix: AHORA - (4 - i) * DIA,
        }),
      ),
      AHORA,
      undefined,
      [{ origen_id: "n1", destino_id: "n4", tipo: "explicita", peso: 1 }],
    );
    expect(
      g.edges.some(
        (e) => (e.a === 0 && e.b === 3) || (e.a === 3 && e.b === 0),
      ),
    ).toBe(true);
  });

  it("relaciones con ids fuera del grafo se ignoran", () => {
    const g = eventosAGrafo([evento({ id: "x" })], AHORA, undefined, [
      { origen_id: "x", destino_id: "fantasma", tipo: "semantica", peso: 0.8 },
    ]);
    expect(g.edges.length).toBe(0);
  });

  it("dos eventos con tag compartido quedan unidos por arista tag", () => {
    const g = eventosAGrafo(
      [
        evento({ id: "t1", dimension: "cognitiva", tags: ["trabajo"], timestamp_unix: AHORA - 3 * DIA }),
        evento({ id: "t2", dimension: "somatica", timestamp_unix: AHORA - 2 * DIA }),
        evento({ id: "t3", dimension: "somatica", tags: ["trabajo"], timestamp_unix: AHORA - 1 * DIA }),
      ],
      AHORA,
    );
    // t1 y t3 no comparten dimensión ni son consecutivos en cadena
    // tag; sin el vínculo por etiqueta no habría arista (0,2).
    const par02 = g.edges.some(
      (e) => (e.a === 0 && e.b === 2) || (e.a === 2 && e.b === 0),
    );
    expect(par02).toBe(true);
  });

  it("eventos sin tags comunes no crean arista extra", () => {
    const g = eventosAGrafo(
      [
        evento({ dimension: "cognitiva", tags: ["a"], timestamp_unix: AHORA - 3 * DIA }),
        evento({ dimension: "somatica", tags: ["b"], timestamp_unix: AHORA - 2 * DIA }),
        evento({ dimension: "conductual", tags: ["c"], timestamp_unix: AHORA - 1 * DIA }),
      ],
      AHORA,
    );
    // Solo la cadena temporal: 2 aristas link, 0 tag.
    expect(g.edges.filter((e) => e.kind === "tag")).toHaveLength(0);
  });

  it("vinculos registra el vecino, tipo y peso de cada relación", () => {
    const g = eventosAGrafo(
      [
        evento({ id: "v1", timestamp_unix: AHORA - 2 * DIA }),
        evento({ id: "v2", timestamp_unix: AHORA - 1 * DIA }),
      ],
      AHORA,
      undefined,
      [{ origen_id: "v1", destino_id: "v2", tipo: "explicita", peso: 1 }],
    );
    expect(g.vinculos.get(0)?.[0]).toMatchObject({
      idx: 1,
      tipo: "explicita",
      peso: 1,
    });
    expect(g.vinculos.get(1)?.[0]).toMatchObject({ idx: 0 });
  });

  it("ramas lista las líneas de universo presentes", () => {
    const g = eventosAGrafo(
      [
        evento({ rama: "main", timestamp_unix: AHORA - 2 * DIA }),
        evento({ rama: "espejo", timestamp_unix: AHORA - 1 * DIA }),
      ],
      AHORA,
    );
    expect(g.ramas).toContain("main");
    expect(g.ramas).toContain("espejo");
  });
});
