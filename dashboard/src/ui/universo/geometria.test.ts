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
  detectarRachas,
  distribucionEn,
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

describe("escenarios de futuro (dinámicos)", () => {
  /** Historial mínimo con eventos en el origen. */
  const base: EventoRio[] = [
    ev({ id: "e1", ts: T_MAX - 20 * DIA, dimension: "cognitiva" }),
    ev({ id: "e2", ts: T_MAX - 10 * DIA, dimension: "cognitiva" }),
    ev({ id: "e3", ts: T_MAX - 5 * DIA, dimension: "cognitiva" }),
    ev({ id: "e4", ts: T_MAX - 2 * DIA, dimension: "somatica" }),
    ev({ id: "e5", ts: T_MAX - DIA, dimension: "conductual" }),
  ];
  const entradaBase = (parcial = {}) => ({
    eventos: base,
    origen: T_MAX,
    loopsAbiertos: 0,
    objetivos: [] as Objetivo[],
    horizonte: 30 * DIA,
    ctx,
    muestras: 10,
    ...parcial,
  });

  it("genera los tres escenarios base arrancando en el origen", () => {
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

  it("continuidad refleja la dimensión dominante en el origen", () => {
    const es = construirEscenarios(entradaBase());
    const cont = es.find((e) => e.id === "continuidad")!;
    expect(cont.sustrato).toBe("cognitiva");
    expect(cont.probabilidad).toBeGreaterThan(0.4);
  });

  it("una racha viva agrega un cauce de persistencia", () => {
    // Cuatro eventos conductuales seguidos CERRANDO la historia —
    // la racha queda viva en el origen (e5 ya era conductual: run=5).
    const eventos = [
      ...base,
      ev({ id: "r1", ts: T_MAX - 80_000, dimension: "conductual" }),
      ev({ id: "r2", ts: T_MAX - 60_000, dimension: "conductual" }),
      ev({ id: "r3", ts: T_MAX - 40_000, dimension: "conductual" }),
      ev({ id: "r4", ts: T_MAX - 20_000, dimension: "conductual" }),
    ];
    const es = construirEscenarios(
      entradaBase({ eventos, maxEscenarios: 5 }),
    );
    expect(es.some((e) => e.id === "racha:conductual")).toBe(true);
  });

  it("un giro de momentum agrega un candidato si otra dimensión acelera", () => {
    // Cognitiva domina el histórico; conductual acelera en los
    // últimos días sin llegar a dominar ni formar racha viva.
    const eventos = [
      ev({ id: "a", ts: T_MAX - 80 * DIA, dimension: "cognitiva" }),
      ev({ id: "b", ts: T_MAX - 70 * DIA, dimension: "cognitiva" }),
      ev({ id: "c", ts: T_MAX - 60 * DIA, dimension: "cognitiva" }),
      ev({ id: "d", ts: T_MAX - 50 * DIA, dimension: "cognitiva" }),
      ev({ id: "g1", ts: T_MAX - 5 * DIA, dimension: "conductual" }),
      ev({ id: "g2", ts: T_MAX - 4 * DIA, dimension: "cognitiva" }),
      ev({ id: "g3", ts: T_MAX - 3 * DIA, dimension: "conductual" }),
      ev({ id: "g4", ts: T_MAX - 2 * DIA, dimension: "cognitiva" }),
      ev({ id: "g5", ts: T_MAX - DIA, dimension: "conductual" }),
      ev({ id: "g6", ts: T_MAX - 12 * 3600, dimension: "cognitiva" }),
    ];
    const es = construirEscenarios(
      entradaBase({ eventos, maxEscenarios: 5 }),
    );
    expect(es.some((e) => e.id === "giro:conductual")).toBe(true);
  });

  it("riesgo sube con señales somáticas recientes y loops abiertos", () => {
    const bajo = construirEscenarios(entradaBase());
    const eventos = [
      ...base,
      ...Array.from({ length: 8 }, (_, i) =>
        ev({ id: `s${i}`, ts: T_MAX - (i + 1) * DIA, dimension: "somatica" }),
      ),
    ];
    const alto = construirEscenarios(
      entradaBase({ eventos, loopsAbiertos: 6 }),
    );
    expect(alto.find((e) => e.id === "riesgo")!.probabilidad).toBeGreaterThan(
      bajo.find((e) => e.id === "riesgo")!.probabilidad,
    );
  });

  it("el tope del usuario limita y ordena por probabilidad", () => {
    // 8 metas → pool de 10+ candidatos; el tope corta.
    const metas = Array.from({ length: 8 }, (_, i) =>
      obj({ id: `m${i}`, fecha_objetivo: T_MAX + (10 + i * 5) * DIA }),
    );
    const diez = construirEscenarios(
      entradaBase({ objetivos: metas, horizonte: 60 * DIA }),
    );
    expect(diez.length).toBeLessThanOrEqual(10);
    const cuatro = construirEscenarios(
      entradaBase({
        objetivos: metas,
        horizonte: 60 * DIA,
        maxEscenarios: 4,
      }),
    );
    expect(cuatro).toHaveLength(4);
    // Ordenados por probabilidad descendente.
    for (let i = 1; i < cuatro.length; i++) {
      expect(cuatro[i].probabilidad).toBeLessThanOrEqual(
        cuatro[i - 1].probabilidad,
      );
    }
  });

  it("los escenarios cambian al enfocar otro instante del río", () => {
    // Historia en dos épocas: cognitiva al inicio, conductual al final.
    const eventos = [
      ...Array.from({ length: 5 }, (_, i) =>
        ev({ id: `c${i}`, ts: T_MIN + (i + 1) * DIA, dimension: "cognitiva" }),
      ),
      ...Array.from({ length: 5 }, (_, i) =>
        ev({
          id: `k${i}`,
          ts: T_MAX - (5 - i) * DIA,
          dimension: "conductual",
        }),
      ),
    ];
    const temprano = construirEscenarios(
      entradaBase({ eventos, origen: T_MIN + 10 * DIA }),
    );
    const tardio = construirEscenarios(entradaBase({ eventos }));
    expect(
      temprano.find((e) => e.id === "continuidad")!.sustrato,
    ).toBe("cognitiva");
    expect(
      tardio.find((e) => e.id === "continuidad")!.sustrato,
    ).toBe("conductual");
    // Los cauces brotan de su propio origen, no siempre del ahora.
    expect(temprano[0].puntos[0].x).toBeCloseTo(
      xEnTiempo(T_MIN + 10 * DIA, ctx),
      6,
    );
  });

  it("los eventos posteriores al origen no cuentan", () => {
    const eventos = [
      ...base,
      // Futuro lejano lleno de somática — invisible si enfoco antes.
      ...Array.from({ length: 10 }, (_, i) =>
        ev({
          id: `f${i}`,
          ts: T_MAX + (i + 1) * DIA,
          dimension: "somatica",
        }),
      ),
    ];
    const es = construirEscenarios(entradaBase({ eventos }));
    expect(es.find((e) => e.id === "continuidad")!.sustrato).not.toBe(
      "somatica",
    );
  });

  it("todas las metas activas reciben su propio cauce", () => {
    const metas = [
      obj({ id: "m1", fecha_objetivo: T_MAX + 20 * DIA }),
      obj({ id: "m2", titulo: "segunda", fecha_objetivo: T_MAX + 40 * DIA }),
    ];
    const es = construirEscenarios(
      entradaBase({ objetivos: metas, horizonte: 60 * DIA }),
    );
    const m1 = es.find((e) => e.objetivoAncla?.id === "m1")!;
    const m2 = es.find((e) => e.objetivoAncla?.id === "m2")!;
    // Ambas llegan exactamente a su faro.
    for (const [e, meta] of [
      [m1, metas[0]],
      [m2, metas[1]],
    ] as const) {
      const fin = e.puntos[e.puntos.length - 1];
      const ancla = anclaDeObjetivo(meta, ctx);
      expect(fin.x).toBeCloseTo(ancla.x, 6);
      expect(fin.z).toBeCloseTo(ancla.z, 6);
    }
  });

  it("con meta activa, los cauces libres apuntan pero no llegan", () => {
    const meta = obj({ id: "m1", fecha_objetivo: T_MAX + 20 * DIA });
    const con = construirEscenarios(
      entradaBase({ objetivos: [meta], horizonte: 60 * DIA }),
    );
    const sin = construirEscenarios(
      entradaBase({ objetivos: [], horizonte: 60 * DIA }),
    );
    const ancla = anclaDeObjetivo(meta, ctx);
    const dist = (e: { puntos: { x: number; y: number; z: number }[] }) => {
      const f = e.puntos[e.puntos.length - 1];
      return Math.hypot(f.x - ancla.x, f.y - ancla.y, f.z - ancla.z);
    };
    for (const e of con) {
      expect(e.objetivoAncla?.id).toBe("m1");
      const libre = sin.find((s) => s.id === e.id)!;
      expect(dist(e)).toBeLessThan(dist(libre));
    }
    expect(dist(con.find((e) => e.id === "florecer")!)).toBeCloseTo(0, 5);
    expect(dist(con.find((e) => e.id === "continuidad")!)).toBeGreaterThan(5);
    expect(dist(con.find((e) => e.id === "riesgo")!)).toBeGreaterThan(5);
  });

  it("florecer sin metas queda tenue y sin ancla", () => {
    const es = construirEscenarios(entradaBase());
    const flor = es.find((e) => e.id === "florecer")!;
    expect(flor.objetivoAncla).toBeNull();
    expect(flor.probabilidad).toBeLessThanOrEqual(0.1);
  });

  it("ignora objetivos cumplidos o vencidos antes del origen", () => {
    const viejos = [
      obj({ id: "cerrado", estado: "cumplido" }),
      obj({ id: "vencido", fecha_objetivo: T_MAX - DIA }),
    ];
    const es = construirEscenarios(entradaBase({ objetivos: viejos }));
    expect(es.find((e) => e.id === "florecer")!.objetivoAncla).toBeNull();
  });

  it("historial vacío en el origen no produce cauces", () => {
    expect(
      construirEscenarios(entradaBase({ eventos: [] })),
    ).toHaveLength(0);
    // Origen anterior a cualquier evento.
    expect(
      construirEscenarios(entradaBase({ origen: T_MIN - DIA })),
    ).toHaveLength(0);
  });

  it("riesgo se hunde por debajo del cauce", () => {
    const es = construirEscenarios(entradaBase());
    const fin = es.find((e) => e.id === "riesgo")!.puntos.at(-1)!;
    expect(fin.y).toBeLessThan(0);
  });

  it("carrilFantasma diverge por dimensión", () => {
    expect(carrilFantasma("cognitiva")).toBe(-1);
    expect(carrilFantasma("conductual")).toBe(1);
    expect(carrilFantasma("somatica")).toBe(0);
    expect(carrilFantasma(null)).toBe(0);
  });
});

describe("proyección local (distribucionEn / detectarRachas)", () => {
  it("distribucionEn decae con la antigüedad del evento", () => {
    const eventos = [
      ev({ id: "viejo", ts: T_MAX - 90 * DIA, dimension: "cognitiva" }),
      ev({ id: "nuevo", ts: T_MAX - DIA, dimension: "conductual" }),
    ];
    const d = distribucionEn(eventos, T_MAX);
    expect(d.conductual).toBeGreaterThan(d.cognitiva);
    const suma = d.cognitiva + d.somatica + d.conductual;
    expect(suma).toBeCloseTo(1, 6);
  });

  it("distribucionEn da tercios iguales sin datos", () => {
    const d = distribucionEn([], T_MAX);
    expect(d.cognitiva).toBeCloseTo(1 / 3, 6);
  });

  it("detectarRachas exige mínimo 3 y marca la racha viva", () => {
    const eventos = [
      ev({ id: "a", ts: T_MAX - 10 * DIA, dimension: "cognitiva" }),
      ev({ id: "b", ts: T_MAX - 3 * DIA, dimension: "somatica" }),
      ev({ id: "c", ts: T_MAX - 2 * DIA, dimension: "somatica" }),
      ev({ id: "d", ts: T_MAX - DIA, dimension: "somatica" }),
    ];
    const rachas = detectarRachas(eventos, T_MAX);
    expect(rachas).toHaveLength(1);
    expect(rachas[0].dimension).toBe("somatica");
    expect(rachas[0].activa).toBe(true);
    // Enfocando antes de la racha, no existe.
    expect(detectarRachas(eventos, T_MAX - 5 * DIA)).toHaveLength(0);
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
