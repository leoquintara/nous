/**
 * Tests de la geometría pura del río temporal 4D.
 *
 * Se validan el mapeo tiempo→X, carriles de ramas/fuentes, puntos
 * de bifurcación, curvas fantasma con ancla a objetivos y ticks.
 */

import { describe, expect, it } from "vitest";

import {
  anclaDeObjetivo,
  bezierCubica,
  carrilDeRama,
  carrilFantasma,
  carrilDeFuente,
  construirFantasmas,
  ctxDe,
  desfaseAfluente,
  desfaseCauce,
  forkDe,
  posEnRio,
  RIO,
  smoothstep,
  ticksDeFecha,
  xEnTiempo,
  type EventoRio,
} from "./geometria";
import type { Objetivo } from "@/domain/modelos";

const DIA = 86_400;
const T_MIN = 1_700_000_000;
const T_MAX = T_MIN + 90 * DIA;
const ctx = ctxDe(T_MIN, T_MAX);

/** Fabrica un evento mínimo del río. */
function ev(parcial: Partial<EventoRio> = {}): EventoRio {
  return {
    id: parcial.id ?? "e",
    ts: parcial.ts ?? T_MIN,
    dimension: parcial.dimension ?? "cognitiva",
    fuente: parcial.fuente ?? "test",
    amplitud: parcial.amplitud ?? 0.5,
    rama: parcial.rama ?? "main",
  };
}

/** Fabrica un objetivo activo mínimo. */
function obj(parcial: Partial<Objetivo> = {}): Objetivo {
  return {
    id: parcial.id ?? "o1",
    titulo: parcial.titulo ?? "meta",
    descripcion: null,
    dimension: parcial.dimension ?? null,
    fecha_objetivo: parcial.fecha_objetivo ?? T_MAX + 30 * DIA,
    estado: parcial.estado ?? "activo",
    rama: "main",
    evento_cierre_id: null,
  };
}

describe("xEnTiempo", () => {
  it("tMin mapea al extremo izquierdo y tMax al derecho", () => {
    expect(xEnTiempo(T_MIN, ctx)).toBe(-RIO.largo / 2);
    expect(xEnTiempo(T_MAX, ctx)).toBe(RIO.largo / 2);
  });

  it("el centro temporal mapea a X=0", () => {
    expect(xEnTiempo(T_MIN + ctx.span / 2, ctx)).toBeCloseTo(0, 6);
  });
});

describe("smoothstep", () => {
  it("respeta los bordes 0 y 1", () => {
    expect(smoothstep(-1)).toBe(0);
    expect(smoothstep(0)).toBe(0);
    expect(smoothstep(1)).toBe(1);
    expect(smoothstep(2)).toBe(1);
  });

  it("en el centro vale 0.5", () => {
    expect(smoothstep(0.5)).toBeCloseTo(0.5, 6);
  });
});

describe("carriles", () => {
  it("main siempre es el cauce central", () => {
    expect(carrilDeRama("main", ["main", "x"])).toBe(0);
    expect(carrilDeRama("main", ["x", "main"])).toBe(0);
  });

  it("las ramas alternan carriles simétricos ±", () => {
    const ramas = ["main", "alfa", "beta", "gamma"];
    expect(carrilDeRama("alfa", ramas)).toBe(1);
    expect(carrilDeRama("beta", ramas)).toBe(-1);
    expect(carrilDeRama("gamma", ramas)).toBe(2);
  });

  it("fuente desconocida cae al primer carril", () => {
    expect(carrilDeFuente("nueva", ["a", "b"])).toBe(1);
    expect(carrilDeFuente("b", ["a", "b"])).toBe(2);
  });
});

describe("forkDe y desfaseCauce", () => {
  const eventos = [
    ev({ rama: "main", ts: T_MIN }),
    ev({ rama: "espejo", ts: T_MIN + 30 * DIA }),
    ev({ rama: "espejo", ts: T_MIN + 60 * DIA }),
  ];

  it("la bifurcación ocurre en el primer evento de la rama", () => {
    expect(forkDe("espejo", eventos)).toBe(T_MIN + 30 * DIA);
    expect(forkDe("main", eventos)).toBe(T_MIN);
    expect(forkDe("inexistente", eventos)).toBeNull();
  });

  it("el desfase es 0 antes del fork y alcanza el carril después", () => {
    const fork = T_MIN + 30 * DIA;
    expect(desfaseCauce(2, fork - 1, fork, ctx).z).toBe(0);
    const despues = desfaseCauce(2, fork + ctx.span, fork, ctx);
    expect(despues.z).toBeCloseTo(2 * RIO.separacionRama, 6);
    // A mitad de la rampa el desfase va a medio camino.
    const medio = desfaseCauce(2, fork + ctx.span * 0.02, fork, ctx);
    expect(medio.z).toBeGreaterThan(0);
    expect(medio.z).toBeLessThan(2 * RIO.separacionRama);
  });

  it("main nunca se desfasa aunque tenga fork", () => {
    expect(desfaseCauce(0, T_MAX, T_MIN, ctx).z).toBe(0);
  });
});

describe("posEnRio", () => {
  it("respeta el meandro acotado del cauce", () => {
    for (const t of [T_MIN, T_MIN + ctx.span * 0.5, T_MAX]) {
      const p = posEnRio(t, 0, null, ctx);
      expect(Math.abs(p.y)).toBeLessThanOrEqual(RIO.meandroY + 0.01);
      expect(Math.abs(p.z)).toBeLessThanOrEqual(RIO.meandroZ + 0.01);
    }
  });

  it("una rama bifurcada diverge lateralmente tras el fork", () => {
    const fork = T_MIN + 30 * DIA;
    const antes = posEnRio(fork - 1, 1, fork, ctx);
    const despues = posEnRio(T_MAX, 1, fork, ctx);
    expect(antes.z - Math.sin(antes.x * 0.008 + 1.3) * RIO.meandroZ).toBe(0);
    expect(despues.z).toBeGreaterThan(RIO.meandroZ);
  });
});

describe("bezierCubica", () => {
  const p0 = { x: 0, y: 0, z: 0 };
  const p1 = { x: 10, y: 5, z: 0 };
  const p2 = { x: 20, y: 5, z: 0 };
  const p3 = { x: 30, y: 0, z: 0 };

  it("pasa por los extremos exactos", () => {
    expect(bezierCubica(p0, p1, p2, p3, 0)).toEqual(p0);
    expect(bezierCubica(p0, p1, p2, p3, 1)).toEqual(p3);
  });

  it("el punto medio queda dentro de la envolvente", () => {
    const m = bezierCubica(p0, p1, p2, p3, 0.5);
    expect(m.x).toBeCloseTo(15, 6);
    expect(m.y).toBeGreaterThan(0);
    expect(m.y).toBeLessThan(5);
  });
});

describe("fantasmas y objetivos", () => {
  it("genera un cauce por dimensión arrancando en el ahora", () => {
    const fs = construirFantasmas(null, [], 30 * DIA, ctx, 10);
    expect(fs).toHaveLength(3);
    for (const f of fs) {
      expect(f.puntos).toHaveLength(11);
      expect(f.puntos[0].x).toBeCloseTo(xEnTiempo(T_MAX, ctx), 6);
    }
  });

  it("la probabilidad toma la distribución y se acota a [0,1]", () => {
    const fs = construirFantasmas(
      { cognitiva: 0.7, somatica: 1.4 },
      [],
      30 * DIA,
      ctx,
      4,
    );
    expect(fs[0].probabilidad).toBeCloseTo(0.7, 6);
    expect(fs[1].probabilidad).toBe(1);
    expect(fs[2].probabilidad).toBeCloseTo(0.33, 2);
  });

  it("el cauce converge al objetivo más próximo de su dimensión", () => {
    const meta = obj({
      id: "m1",
      dimension: "cognitiva",
      fecha_objetivo: T_MAX + 20 * DIA,
    });
    const fs = construirFantasmas(null, [meta], 60 * DIA, ctx, 8);
    const cogn = fs.find((f) => f.dimension === "cognitiva")!;
    expect(cogn.objetivoAncla?.id).toBe("m1");
    const ultimo = cogn.puntos[cogn.puntos.length - 1];
    const ancla = anclaDeObjetivo(meta, ctx);
    expect(ultimo.x).toBeCloseTo(ancla.x, 6);
    expect(ultimo.z).toBeCloseTo(ancla.z, 6);
  });

  it("ignora objetivos cumplidos, vencidos o de otra dimensión", () => {
    const viejos = [
      obj({ id: "cerrado", estado: "cumplido", dimension: "somatica" }),
      obj({ id: "vencido", fecha_objetivo: T_MAX - DIA, dimension: "somatica" }),
      obj({ id: "ajeno", dimension: "conductual" }),
    ];
    const fs = construirFantasmas(null, viejos, 30 * DIA, ctx, 4);
    const som = fs.find((f) => f.dimension === "somatica")!;
    expect(som.objetivoAncla).toBeNull();
  });

  it("un objetivo transversal ancla cualquier dimensión", () => {
    const fs = construirFantasmas(
      null,
      [obj({ dimension: null })],
      30 * DIA,
      ctx,
      4,
    );
    for (const f of fs) expect(f.objetivoAncla).not.toBeNull();
  });

  it("carrilFantasma diverge por dimensión", () => {
    expect(carrilFantasma("cognitiva")).toBe(-1);
    expect(carrilFantasma("conductual")).toBe(1);
    expect(carrilFantasma("somatica")).toBe(0);
    expect(carrilFantasma(null)).toBe(0);
  });
});

describe("desfaseAfluente", () => {
  const nacimiento = T_MIN + 10 * DIA;

  it("es 0 antes de que nazca la fuente", () => {
    expect(desfaseAfluente(3, nacimiento - 1, nacimiento, ctx)).toBe(0);
  });

  it("mantiene su carril en tramo medio y converge al final", () => {
    const medio = desfaseAfluente(3, nacimiento + ctx.span * 0.4, nacimiento, ctx);
    expect(medio).toBeCloseTo(3 * RIO.separacionFuente, 0);
    const fin = desfaseAfluente(3, T_MAX, nacimiento, ctx);
    expect(fin).toBeCloseTo(0, 6);
  });
});

describe("ticksDeFecha", () => {
  it("devuelve la cantidad pedida cubriendo el span completo", () => {
    const ticks = ticksDeFecha(ctx, 5);
    expect(ticks).toHaveLength(5);
    expect(ticks[0].t).toBe(T_MIN);
    expect(ticks[ticks.length - 1].t).toBe(T_MAX);
    for (const tk of ticks) expect(tk.etiqueta.length).toBeGreaterThan(0);
  });
});
