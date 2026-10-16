/**
 * Matemáticas del universo 4D que dibuja FondoUniverso.
 * Funciones puras y deterministas — toda la aleatoriedad viene de un
 * RNG con semilla, así el fondo es idéntico en cada visita y testeable.
 */

export interface Punto {
  x: number;
  y: number;
}

export interface EventoUniverso {
  /** posición normalizada sobre la worldline, 0..1 */
  t: number;
  /** amplitud del evento — tamaño y brillo del nodo, 0..1 */
  amplitud: number;
  /** índice de dimensión: 0 cognitiva, 1 somática, 2 conductual */
  dimension: 0 | 1 | 2;
}

export interface Rama {
  /** t sobre la worldline donde nace la rama */
  origen: number;
  /** 1 = se curva hacia arriba, -1 = hacia abajo */
  direccion: 1 | -1;
  /** qué tan lejos llega (0..1 del alto de viewport) */
  alcance: number;
}

/** PRNG determinista (mulberry32) — misma semilla, mismo universo. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a += 0x6d2b79f5;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Punto sobre la línea de universo. La línea fluye de izquierda a
 * derecha (x = t) y serpentea verticalmente con dos armónicos suaves.
 * `overshoot` extiende la línea fuera del viewport en los extremos
 * para que el cursor nunca "salga" de escena.
 */
export function worldlinePoint(
  t: number,
  width: number,
  height: number,
): Punto {
  const overshoot = width * 0.08;
  const x = -overshoot + t * (width + overshoot * 2);
  const cY = height * 0.52;
  const y =
    cY +
    Math.sin(t * Math.PI * 2.2 + 0.4) * height * 0.16 +
    Math.sin(t * Math.PI * 5.1) * height * 0.05;
  return { x, y };
}

/**
 * Curva de una rama (tributario fantasma): bezier que nace en el
 * punto `origen` de la worldline y se curva en `direccion` hasta
 * desvanecerse. Devuelve los 4 puntos de control de la cúbica.
 */
export function branchCurve(
  rama: Rama,
  width: number,
  height: number,
): [Punto, Punto, Punto, Punto] {
  const p0 = worldlinePoint(rama.origen, width, height);
  const alcance = rama.alcance * height * 0.42;
  const dx = width * 0.16 + rama.alcance * width * 0.1;
  const p1 = { x: p0.x + dx * 0.25, y: p0.y };
  const p2 = { x: p0.x + dx * 0.7, y: p0.y + alcance * rama.direccion };
  const p3 = { x: p0.x + dx, y: p0.y + alcance * rama.direccion * 0.85 };
  return [p0, p1, p2, p3];
}

/** Eventos distribuidos sobre la worldline — deterministas por semilla. */
export function eventosSobreLinea(
  cantidad: number,
  seed: number,
): EventoUniverso[] {
  const rng = seededRandom(seed);
  const eventos: EventoUniverso[] = [];
  for (let i = 0; i < cantidad; i++) {
    const t = Math.min(0.995, Math.max(0.005, i / cantidad + rng() * 0.02));
    eventos.push({
      t,
      amplitud: 0.25 + rng() * 0.75,
      dimension: Math.floor(rng() * 3) as 0 | 1 | 2,
    });
  }
  return eventos;
}

/** Ramas nacidas de algunos eventos — deterministas por semilla. */
export function ramasDesdeEventos(
  eventos: EventoUniverso[],
  seed: number,
): Rama[] {
  const rng = seededRandom(seed * 7 + 13);
  const ramas: Rama[] = [];
  for (const ev of eventos) {
    if (ev.amplitud > 0.72 && rng() > 0.45) {
      ramas.push({
        origen: ev.t,
        direccion: rng() > 0.5 ? 1 : -1,
        alcance: 0.3 + rng() * 0.7,
      });
    }
  }
  return ramas;
}

/**
 * Posición del cursor del observador: viaja por la worldline según el
 * progreso (0..1). El rango útil se recorta en los bordes para que el
 * cursor sea visible durante todo el recorrido.
 */
export function cursorParaProgreso(
  progress: number,
  width: number,
  height: number,
): Punto {
  return worldlinePoint(cursorT(progress), width, height);
}

/** t sobre la worldline correspondiente al progreso.
 *  El overshoot es 8% del ancho: la línea entra al viewport en t≈0.069
 *  y sale en t≈0.931, así que el rango útil va con margen interior. */
export function cursorT(progress: number): number {
  const p = Math.min(1, Math.max(0, progress));
  return 0.09 + p * 0.82;
}

/**
 * Proyecciones fantasma: `cantidad` tributarios punteados que nacen en
 * el cursor y divergen hacia adelante — los futuros probables.
 * Devuelve las curvas bezier de cada proyección.
 */
export function proyeccionesDesdeCursor(
  progress: number,
  cantidad: number,
  width: number,
  height: number,
): [Punto, Punto, Punto, Punto][] {
  const t = cursorT(progress);
  const p0 = worldlinePoint(t, width, height);
  const curvas: [Punto, Punto, Punto, Punto][] = [];
  const n = Math.max(1, Math.min(10, Math.floor(cantidad)));
  for (let i = 0; i < n; i++) {
    const spread = ((i + 0.5) / n - 0.5) * 2; // -1..1
    const dx = width * (0.1 + 0.03 * i);
    const dy = spread * height * 0.22;
    curvas.push([
      p0,
      { x: p0.x + dx * 0.3, y: p0.y },
      { x: p0.x + dx * 0.75, y: p0.y + dy },
      { x: p0.x + dx, y: p0.y + dy * 1.05 },
    ]);
  }
  return curvas;
}

/** Estrellas del fondo — campo tenue de partículas. */
export function campoEstrellas(cantidad: number, seed: number): Punto[] {
  const rng = seededRandom(seed * 31 + 7);
  return Array.from({ length: cantidad }, () => ({
    x: rng(),
    y: rng(),
  }));
}
