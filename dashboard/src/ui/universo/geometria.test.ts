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
  construirEscenarios,
  ctxDe,
  desfaseAfluente,
  desfaseCauce,
  forkDe,
  posEnRio,
  RIO,
  smoothstep,
  statsDimension,
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

describe("escenarios de futuro", () => {
  const statsVacias = {
    cognitiva: { total: 0, recientes: 0, amplitudMedia: 0 },
    somatica: { total: 0, recientes: 0, amplitudMedia: 0 },
    conductual: { total: 0, recientes: 0, amplitudMedia: 0 },
  };
  const entradaBase = (parcial = {}) => ({
    distribucion: null,
    stats: statsVacias,
    loopsAbiertos: 0,
    objetivos: [] as Objetivo[],
    horizonte: 30 * DIA,
    ctx,
    muestras: 10,
    ...parcial,
  });

  it("genera los tres escenarios con nombre arrancando en el ahora", () => {
    const es = construirEscenarios(entradaBase());
    expect(es.map((e) => e.id)).toEqual([
      "continuidad",
      "riesgo",
      "florecer",
    ]);
    for (const e of es) {
      expect(e.nombre.length).toBeGreaterThan(0);
      expect(e.descripcion.length).toBeGreaterThan(0);
      expect(e.puntos).toHaveLength(11);
      expect(e.puntos[0].x).toBeCloseTo(xEnTiempo(T_MAX, ctx), 6);
      expect(e.probabilidad).toBeGreaterThan(0);
      expect(e.probabilidad).toBeLessThanOrEqual(0.95);
    }
  });

  it("continuidad toma la probabilidad de la dimensión dominante", () => {
    const es = construirEscenarios(
      entradaBase({ distribucion: { cognitiva: 0.7 } }),
    );
    const cont = es.find((e) => e.id === "continuidad")!;
    expect(cont.probabilidad).toBeCloseTo(0.7, 6);
    expect(cont.sustrato).toBe("cognitiva");
  });

  it("riesgo sube con señales somáticas recientes y loops abiertos", () => {
    const bajo = construirEscenarios(entradaBase());
    const stats = {
      ...statsVacias,
      somatica: { total: 40, recientes: 20, amplitudMedia: 0.6 },
      cognitiva: { total: 10, recientes: 2, amplitudMedia: 0.5 },
    };
    const alto = construirEscenarios(
      entradaBase({ stats, loopsAbiertos: 6 }),
    );
    expect(alto.find((e) => e.id === "riesgo")!.probabilidad).toBeGreaterThan(
      bajo.find((e) => e.id === "riesgo")!.probabilidad,
    );
  });

  it("florecer converge al objetivo activo más próximo", () => {
    const meta = obj({ id: "m1", fecha_objetivo: T_MAX + 20 * DIA });
    const es = construirEscenarios(
      entradaBase({ objetivos: [meta], horizonte: 60 * DIA }),
    );
    const flor = es.find((e) => e.id === "florecer")!;
    expect(flor.objetivoAncla?.id).toBe("m1");
    const ultimo = flor.puntos[flor.puntos.length - 1];
    const ancla = anclaDeObjetivo(meta, ctx);
    expect(ultimo.x).toBeCloseTo(ancla.x, 6);
    expect(ultimo.z).toBeCloseTo(ancla.z, 6);
  });

  it("florecer sin metas queda tenue y sin ancla", () => {
    const es = construirEscenarios(entradaBase());
    const flor = es.find((e) => e.id === "florecer")!;
    expect(flor.objetivoAncla).toBeNull();
    expect(flor.probabilidad).toBeLessThanOrEqual(0.1);
  });

  it("ignora objetivos cumplidos o vencidos como ancla", () => {
    const viejos = [
      obj({ id: "cerrado", estado: "cumplido" }),
      obj({ id: "vencido", fecha_objetivo: T_MAX - DIA }),
    ];
    const es = construirEscenarios(entradaBase({ objetivos: viejos }));
    expect(es.find((e) => e.id === "florecer")!.objetivoAncla).toBeNull();
  });

  it("riesgo se hunde por debajo del cauce; florecer asciende", () => {
    const es = construirEscenarios(entradaBase());
    const fin = (e: (typeof es)[number]) => e.puntos[e.puntos.length - 1];
    expect(fin(es[1]).y).toBeLessThan(0); // riesgo bucea
    expect(fin(es[2]).y).toBeGreaterThan(20); // florecer asciende
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

describe("statsDimension", () => {
  const eventos: EventoRio[] = [
    ev({ id: "a", ts: T_MAX - 2 * DIA, dimension: "cognitiva", amplitud: 0.8 }),
    ev({ id: "b", ts: T_MAX - 10 * DIA, dimension: "cognitiva", amplitud: 0.4 }),
    ev({ id: "c", ts: T_MIN + DIA, dimension: "cognitiva", amplitud: 0.6 }),
    ev({ id: "d", ts: T_MAX - DIA, dimension: "conductual", amplitud: 1 }),
  ];

  it("cuenta totales, recientes y amplitud media por dimensión", () => {
    const s = statsDimension("cognitiva", eventos, T_MAX);
    expect(s.total).toBe(3);
    expect(s.recientes).toBe(2); // solo a y b están dentro de 30d
    expect(s.amplitudMedia).toBeCloseTo(0.6, 5);
  });

  it("devuelve ceros para una dimensión sin eventos", () => {
    const s = statsDimension("somatica", eventos, T_MAX);
    expect(s).toEqual({ total: 0, recientes: 0, amplitudMedia: 0 });
  });

  it("respeta la ventana de días recientes configurada", () => {
    const s = statsDimension("cognitiva", eventos, T_MAX, 5);
    expect(s.recientes).toBe(1); // solo "a" entra en 5 días
  });
});
