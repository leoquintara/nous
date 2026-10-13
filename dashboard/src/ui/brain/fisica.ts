/**
 * Física del grafo cerebral — port del motor de DSH Memory.
 *
 * Fuerzas por frame: resortes en aristas, repulsión por buckets
 * espaciales, gravedad hacia la capa de cada estado, cohesión por
 * dimensión, ruido en nodos "fresh" y contención elipsoidal.
 */

import type { Membrana } from "./geometria";
import type { Arista, EstadoNodo, Nodo, Punto } from "./tipos";
import type { PrefsCerebro } from "./tipos";

const MASA: Record<EstadoNodo, number> = {
  consolidated: 2.4,
  fresh: 0.8,
  working: 1,
  decayed: 0.6,
  dead: 0.4,
};

const CAPA: Record<EstadoNodo, number> = {
  consolidated: 40,
  fresh: 190,
  working: 160,
  decayed: 300,
  dead: 390,
};

/** Amplitud y frecuencia del temblor de potencial de reposo. */
export const TEMBLOR: Record<EstadoNodo, [number, number]> = {
  fresh: [0.07, 1.5],
  working: [0.04, 1],
  consolidated: [0.025, 0.55],
  decayed: [0.012, 0.3],
  dead: [0.008, 0.15],
};

const TAMANO_BUCKET = 100;

/** Un paso de integración de fuerzas sobre todos los nodos. */
export function pasoFisica(
  pts: Punto[],
  vel: Punto[],
  nodes: Nodo[],
  edges: Arista[],
  prefs: PrefsCerebro,
  mem: Membrana,
): void {
  if (!prefs.physics) return;
  const n = nodes.length;
  const rep = 1500 * prefs.physRep;
  const att = 0.018 * prefs.physAtt;
  const grav = 0.005 * prefs.physGrav;
  const coh = 0.003;

  const fX = new Float64Array(n);
  const fY = new Float64Array(n);
  const fZ = new Float64Array(n);

  // Centroide por dimensión (la fuente de cohesión).
  const centros = new Map<string, { x: number; y: number; z: number; n: number }>();
  for (let i = 0; i < n; i++) {
    const p = pts[i];
    const c = centros.get(nodes[i].dimension) ?? { x: 0, y: 0, z: 0, n: 0 };
    c.x += p.x;
    c.y += p.y;
    c.z += p.z;
    c.n++;
    centros.set(nodes[i].dimension, c);
  }
  for (const c of centros.values()) {
    c.x /= c.n;
    c.y /= c.n;
    c.z /= c.n;
  }

  for (const e of edges) {
    const a = pts[e.a];
    const b = pts[e.b];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const dz = b.z - a.z;
    const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
    const reposo = e.kind === "link" ? 50 : e.kind === "core" ? 115 : 95;
    const F = (att * (d - reposo)) / d;
    fX[e.a] += dx * F;
    fY[e.a] += dy * F;
    fZ[e.a] += dz * F;
    fX[e.b] -= dx * F;
    fY[e.b] -= dy * F;
    fZ[e.b] -= dz * F;
  }

  // Repulsión por vecindad con buckets espaciales.
  const buckets = new Map<string, number[]>();
  for (let i = 0; i < n; i++) {
    const p = pts[i];
    const k = `${Math.floor(p.x / TAMANO_BUCKET)},${Math.floor(p.y / TAMANO_BUCKET)},${Math.floor(p.z / TAMANO_BUCKET)}`;
    const arr = buckets.get(k);
    if (arr) arr.push(i);
    else buckets.set(k, [i]);
  }
  for (let i = 0; i < n; i++) {
    const p = pts[i];
    const bx = Math.floor(p.x / TAMANO_BUCKET);
    const by = Math.floor(p.y / TAMANO_BUCKET);
    const bz = Math.floor(p.z / TAMANO_BUCKET);
    for (let ox = -1; ox <= 1; ox++)
      for (let oy = -1; oy <= 1; oy++)
        for (let oz = -1; oz <= 1; oz++) {
          const celda = buckets.get(`${bx + ox},${by + oy},${bz + oz}`);
          if (!celda) continue;
          for (const j of celda) {
            if (j <= i) continue;
            const q = pts[j];
            const dx = q.x - p.x;
            const dy = q.y - p.y;
            const dz = q.z - p.z;
            const d2 = dx * dx + dy * dy + dz * dz;
            if (d2 > TAMANO_BUCKET * TAMANO_BUCKET * 4 || d2 < 0.01) continue;
            const F = rep / (d2 + 100);
            const s = F / Math.sqrt(d2);
            fX[i] -= dx * s;
            fY[i] -= dy * s;
            fZ[i] -= dz * s;
            fX[j] += dx * s;
            fY[j] += dy * s;
            fZ[j] += dz * s;
          }
        }
  }

  for (let i = 0; i < n; i++) {
    if (nodes[i].pinned) {
      vel[i].x = vel[i].y = vel[i].z = 0;
      continue;
    }
    const p = pts[i];
    const st = nodes[i].estado;
    const r = Math.hypot(p.x, p.y, p.z) || 1;
    const capa = CAPA[st] ?? 160;
    const gf = (grav * (capa - r)) / r;
    fX[i] += p.x * gf;
    fY[i] += p.y * gf;
    fZ[i] += p.z * gf;
    const c = centros.get(nodes[i].dimension);
    if (c) {
      fX[i] += (c.x - p.x) * coh;
      fY[i] += (c.y - p.y) * coh;
      fZ[i] += (c.z - p.z) * coh;
    }
    if (st === "fresh") {
      fX[i] += (Math.random() - 0.5) * 1.6;
      fY[i] += (Math.random() - 0.5) * 1.6;
      fZ[i] += (Math.random() - 0.5) * 1.6;
    }
    const m = MASA[st] ?? 1;
    const v = vel[i];
    v.x = (v.x + fX[i] / m) * 0.88;
    v.y = (v.y + fY[i] / m) * 0.88;
    v.z = (v.z + fZ[i] / m) * 0.88;
    const sp = Math.hypot(v.x, v.y, v.z);
    if (sp > 12) {
      const s = 12 / sp;
      v.x *= s;
      v.y *= s;
      v.z *= s;
    }
    p.x += v.x;
    p.y += v.y;
    p.z += v.z;
    if (prefs.membrane) {
      const nx = p.x / mem.x;
      const ny = p.y / mem.y;
      const nz = p.z / mem.z;
      const e2 = nx * nx + ny * ny + nz * nz;
      if (e2 > 1) {
        const c2 = 1 / Math.sqrt(e2);
        p.x *= c2;
        p.y *= c2;
        p.z *= c2;
        v.x *= -0.25;
        v.y *= -0.25;
        v.z *= -0.25;
      }
    }
  }
}
