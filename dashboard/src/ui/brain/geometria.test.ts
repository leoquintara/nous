/**
 * Tests de la geometría procedural del cerebro (geometria.ts).
 *
 * THREE construye geometrías headless (sin WebGL) así que se validan
 * estructura y determinismo del ruido sin renderizar.
 */

import { describe, expect, it } from "vitest";

import { construirMembrana, fbm, materialMembrana } from "./geometria";

describe("fbm", () => {
  it("es determinista para la misma entrada", () => {
    expect(fbm(1.5, 2.5, 3.5)).toBe(fbm(1.5, 2.5, 3.5));
  });

  it("devuelve valores en el rango [0, 1]", () => {
    for (let i = 0; i < 50; i++) {
      const v = fbm(i * 0.31, i * 0.17, i * 0.23);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
  });

  it("entradas distintas producen salidas distintas", () => {
    expect(fbm(0.1, 0.2, 0.3)).not.toBe(fbm(9.9, 8.8, 7.7));
  });
});

describe("materialMembrana", () => {
  it("crea un ShaderMaterial transparente con aditivo", () => {
    const mat = materialMembrana("#8ba4d8", 0.5);
    expect(mat.transparent).toBe(true);
    expect(mat.uniforms.uStrength.value).toBe(0.5);
  });
});

describe("construirMembrana", () => {
  it("construye el grupo con corteza, cerebelo, tronco y bulbos", () => {
    const { grupo, tractos, material, materiales } = construirMembrana({
      x: 400,
      y: 320,
      z: 400,
    });
    // 2 lóbulos + cerebelo + tronco + 2 bulbos + grupo profundo.
    expect(grupo.children.length).toBeGreaterThanOrEqual(7);
    // 9 tractos tubulares registrados para el flujo de pulsos:
    // calloso, fórnix ×2, hipocampos ×2, caudados ×2, ópticos ×2.
    expect(tractos.length).toBe(9);
    expect(material.uniforms.uColor).toBeDefined();
    // Corteza + estructuras profundas comparten el uniform de tiempo.
    expect(materiales).toHaveLength(2);
    for (const m of materiales) expect(m.uniforms.uTime).toBeDefined();
  });

  it("es reproducible: dos construcciones tienen la misma estructura", () => {
    const mem = { x: 100, y: 80, z: 100 };
    const a = construirMembrana(mem);
    const b = construirMembrana(mem);
    expect(a.grupo.children.length).toBe(b.grupo.children.length);
    expect(a.tractos.length).toBe(b.tractos.length);
  });
});
