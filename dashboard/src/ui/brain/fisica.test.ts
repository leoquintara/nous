/**
 * Tests del motor de física del cerebro (fisica.ts).
 *
 * pasoFisica muta pts/vel in-place; los casos usan grafos mínimos con
 * prefs controladas para aislar cada fuerza.
 */

import { describe, expect, it } from "vitest";

import { pasoFisica } from "./fisica";
import type { Membrana } from "./geometria";
import {
  eventosAGrafo,
  PREFS_DEFAULT,
  type Nodo,
  type PrefsCerebro,
  type Punto,
} from "./tipos";

const MEM: Membrana = { x: 400, y: 320, z: 400 };

/** Construye nodos mínimos sin pasar por eventosAGrafo. */
function nodo(parcial: Partial<Nodo> = {}): Nodo {
  return {
    id: "n",
    etiqueta: "",
    dimension: "cognitiva",
    estado: "working",
    amplitud: 0.5,
    refuerzos: 0,
    timestamp_unix: 0,
    pinned: false,
    deg: 0,
    rank: 0,
    ...parcial,
  };
}

function prefs(parcial: Partial<PrefsCerebro> = {}): PrefsCerebro {
  return { ...PREFS_DEFAULT, ...parcial };
}

describe("pasoFisica", () => {
  it("con physics=false no toca posiciones ni velocidades", () => {
    const pts: Punto[] = [{ x: 10, y: 20, z: 30 }];
    const vel: Punto[] = [{ x: 1, y: 2, z: 3 }];
    pasoFisica(pts, vel, [nodo()], [], prefs({ physics: false }), MEM);
    expect(pts[0]).toEqual({ x: 10, y: 20, z: 30 });
    expect(vel[0]).toEqual({ x: 1, y: 2, z: 3 });
  });

  it("un nodo pinned queda congelado y sin velocidad", () => {
    const pts: Punto[] = [
      { x: 0, y: 0, z: 0 },
      { x: 100, y: 0, z: 0 },
    ];
    const vel: Punto[] = [
      { x: 5, y: 5, z: 5 },
      { x: 0, y: 0, z: 0 },
    ];
    const nodes = [nodo({ pinned: true }), nodo({ estado: "consolidated" })];
    pasoFisica(pts, vel, nodes, [], prefs({ membrane: false }), MEM);
    expect(pts[0]).toEqual({ x: 0, y: 0, z: 0 });
    expect(vel[0]).toEqual({ x: 0, y: 0, z: 0 });
  });

  it("un resorte atrae dos nodos separados más allá del reposo", () => {
    const pts: Punto[] = [
      { x: 0, y: 160, z: 0 },
      { x: 300, y: 160, z: 0 },
    ];
    const vel: Punto[] = [
      { x: 0, y: 0, z: 0 },
      { x: 0, y: 0, z: 0 },
    ];
    const nodes = [nodo(), nodo()];
    // "core" tiene reposo 115: a 300 de distancia tira hacia el otro.
    const edges = [{ a: 0, b: 1, kind: "core" as const }];
    pasoFisica(pts, vel, nodes, edges, prefs({ membrane: false }), MEM);
    expect(pts[0].x).toBeGreaterThan(0);
    expect(pts[1].x).toBeLessThan(300);
  });

  it("la membrana contiene un nodo que escapa del elipsoide", () => {
    const pts: Punto[] = [{ x: 9999, y: 0, z: 0 }];
    const vel: Punto[] = [{ x: 100, y: 0, z: 0 }];
    pasoFisica(pts, vel, [nodo()], [], prefs({ membrane: true }), MEM);
    const e2 = (pts[0].x / MEM.x) ** 2 + (pts[0].y / MEM.y) ** 2 + (pts[0].z / MEM.z) ** 2;
    expect(e2).toBeLessThanOrEqual(1.0001);
    // Rebote: la velocidad queda invertida y amortiguada.
    expect(vel[0].x).toBeLessThan(0);
  });

  it("la velocidad queda limitada a 12 por frame", () => {
    const pts: Punto[] = [{ x: 0, y: 160, z: 0 }];
    const vel: Punto[] = [{ x: 0, y: 0, z: 0 }];
    // Dos nodos casi superpuestos generan repulsión brutal.
    pts.push({ x: 0.5, y: 160, z: 0 });
    vel.push({ x: 0, y: 0, z: 0 });
    const nodes = [nodo(), nodo()];
    pasoFisica(pts, vel, nodes, [], prefs({ membrane: false }), MEM);
    for (const v of vel) {
      expect(Math.hypot(v.x, v.y, v.z)).toBeLessThanOrEqual(12.0001);
    }
  });
});

describe("integración con eventosAGrafo", () => {
  it("un paso sobre un grafo real no produce NaN", () => {
    const g = eventosAGrafo([
      {
        id: "a", timestamp_unix: 1, dimension: "cognitiva", fuente: "t",
        contenido: "x", amplitud: 0.5, frecuencia: null, refuerzos: 0,
      },
      {
        id: "b", timestamp_unix: 2, dimension: "somatica", fuente: "t",
        contenido: "y", amplitud: 0.5, frecuencia: null, refuerzos: 0,
      },
    ]);
    const vel = g.pts.map(() => ({ x: 0, y: 0, z: 0 }));
    pasoFisica(g.pts, vel, g.nodes, g.edges, prefs(), MEM);
    for (const p of g.pts) {
      expect(Number.isFinite(p.x)).toBe(true);
      expect(Number.isFinite(p.y)).toBe(true);
      expect(Number.isFinite(p.z)).toBe(true);
    }
  });
});
