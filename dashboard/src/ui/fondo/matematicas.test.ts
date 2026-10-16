import { describe, expect, it } from "vitest";

import {
  branchCurve,
  campoEstrellas,
  cursorParaProgreso,
  cursorT,
  eventosSobreLinea,
  proyeccionesDesdeCursor,
  ramasDesdeEventos,
  seededRandom,
  worldlinePoint,
} from "./matematicas";

const W = 1440;
const H = 900;

describe("seededRandom", () => {
  it("es determinista para la misma semilla", () => {
    const a = seededRandom(42);
    const b = seededRandom(42);
    for (let i = 0; i < 20; i++) expect(a()).toBe(b());
  });

  it("produce valores en [0, 1)", () => {
    const rng = seededRandom(7);
    for (let i = 0; i < 500; i++) {
      const v = rng();
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });
});

describe("worldlinePoint", () => {
  it("fluye de izquierda a derecha según t", () => {
    const p0 = worldlinePoint(0, W, H);
    const p1 = worldlinePoint(1, W, H);
    expect(p0.x).toBeLessThan(0); // overshoot izquierdo
    expect(p1.x).toBeGreaterThan(W); // overshoot derecho
  });

  it("se mantiene dentro de la banda vertical del viewport", () => {
    for (let t = 0; t <= 1; t += 0.05) {
      const p = worldlinePoint(t, W, H);
      expect(p.y).toBeGreaterThan(H * 0.1);
      expect(p.y).toBeLessThan(H * 0.9);
    }
  });
});

describe("cursorT / cursorParaProgreso", () => {
  it("recorta el progreso a [0, 1]", () => {
    expect(cursorT(-0.5)).toBe(cursorT(0));
    expect(cursorT(1.5)).toBe(cursorT(1));
  });

  it("el cursor nunca sale del viewport", () => {
    for (const p of [0, 0.25, 0.5, 0.75, 1]) {
      const punto = cursorParaProgreso(p, W, H);
      expect(punto.x).toBeGreaterThan(0);
      expect(punto.x).toBeLessThan(W);
    }
  });

  it("avanza monótonamente con el progreso", () => {
    let prev = -Infinity;
    for (let p = 0; p <= 1; p += 0.1) {
      const t = cursorT(p);
      expect(t).toBeGreaterThan(prev);
      prev = t;
    }
  });
});

describe("eventosSobreLinea", () => {
  it("genera la cantidad pedida, determinista", () => {
    const a = eventosSobreLinea(50, 123);
    const b = eventosSobreLinea(50, 123);
    expect(a).toHaveLength(50);
    expect(a).toEqual(b);
  });

  it("los eventos cubren la línea en orden y con dimensiones válidas", () => {
    const eventos = eventosSobreLinea(30, 9);
    for (const ev of eventos) {
      expect(ev.t).toBeGreaterThanOrEqual(0.005);
      expect(ev.t).toBeLessThanOrEqual(0.995);
      expect([0, 1, 2]).toContain(ev.dimension);
      expect(ev.amplitud).toBeGreaterThanOrEqual(0.25);
      expect(ev.amplitud).toBeLessThanOrEqual(1);
    }
  });
});

describe("ramasDesdeEventos", () => {
  it("solo ramifica eventos de amplitud alta y respeta su origen", () => {
    const eventos = eventosSobreLinea(90, 20261015);
    const ramas = ramasDesdeEventos(eventos, 20261015);
    const origenes = new Set(eventos.map((e) => e.t));
    for (const rama of ramas) {
      expect(origenes.has(rama.origen)).toBe(true);
      expect([1, -1]).toContain(rama.direccion);
      expect(rama.alcance).toBeGreaterThanOrEqual(0.3);
      expect(rama.alcance).toBeLessThanOrEqual(1);
    }
  });

  it("es determinista para la misma entrada y semilla", () => {
    const eventos = eventosSobreLinea(40, 55);
    expect(ramasDesdeEventos(eventos, 55)).toEqual(
      ramasDesdeEventos(eventos, 55),
    );
  });
});

describe("branchCurve", () => {
  it("la rama nace exactamente sobre la worldline en su origen", () => {
    const rama = { origen: 0.4, direccion: 1 as const, alcance: 0.6 };
    const [p0] = branchCurve(rama, W, H);
    expect(p0).toEqual(worldlinePoint(0.4, W, H));
  });

  it("la dirección invierte el sentido vertical", () => {
    const base = { origen: 0.5, alcance: 0.5 };
    const arriba = branchCurve({ ...base, direccion: 1 }, W, H);
    const abajo = branchCurve({ ...base, direccion: -1 }, W, H);
    expect(arriba[3].y).toBeGreaterThan(arriba[0].y);
    expect(abajo[3].y).toBeLessThan(abajo[0].y);
  });
});

describe("proyeccionesDesdeCursor", () => {
  it("acota la cantidad a [1, 10]", () => {
    expect(proyeccionesDesdeCursor(0.5, 0, W, H)).toHaveLength(1);
    expect(proyeccionesDesdeCursor(0.5, 99, W, H)).toHaveLength(10);
  });

  it("todas las proyecciones nacen en el cursor", () => {
    const curvas = proyeccionesDesdeCursor(0.6, 6, W, H);
    const origen = cursorParaProgreso(0.6, W, H);
    for (const [p0] of curvas) {
      expect(p0.x).toBeCloseTo(origen.x, 5);
      expect(p0.y).toBeCloseTo(origen.y, 5);
    }
  });

  it("las proyecciones divergen — los extremos no coinciden", () => {
    const curvas = proyeccionesDesdeCursor(0.3, 5, W, H);
    const ys = new Set(curvas.map((c) => c[3].y.toFixed(3)));
    expect(ys.size).toBe(5);
  });
});

describe("campoEstrellas", () => {
  it("devuelve puntos normalizados deterministas", () => {
    const a = campoEstrellas(100, 1);
    const b = campoEstrellas(100, 1);
    expect(a).toEqual(b);
    for (const s of a) {
      expect(s.x).toBeGreaterThanOrEqual(0);
      expect(s.x).toBeLessThan(1);
      expect(s.y).toBeGreaterThanOrEqual(0);
      expect(s.y).toBeLessThan(1);
    }
  });
});
