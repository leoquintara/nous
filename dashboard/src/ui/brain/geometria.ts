/**
 * Geometría procedural del cerebro — port fiel del plugin DSH Memory.
 *
 * Construye la membrana bilobular con gyri/sulci por ruido FBM, el
 * cerebelo con folia, el tronco encefálico, bulbos olfatorios y las
 * estructuras profundas (cuerpo calloso, ventrículos, tálamos,
 * hipocampo, amígdalas, caudados, nervios ópticos, mesencéfalo).
 */

import * as THREE from "three";

/** Elipsoide de contención de la membrana. */
export interface Membrana {
  x: number;
  y: number;
  z: number;
}

/** Hash determinista para el ruido de valor. */
function h3(x: number, y: number, z: number): number {
  const h = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453;
  return h - Math.floor(h);
}

/** Ruido de valor trilineal suavizado. */
function ruido(x: number, y: number, z: number): number {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const zi = Math.floor(z);
  const xf = x - xi;
  const yf = y - yi;
  const zf = z - zi;
  const u = xf * xf * (3 - 2 * xf);
  const v = yf * yf * (3 - 2 * yf);
  const w = zf * zf * (3 - 2 * zf);
  const L = (a: number, b: number, t: number) => a + (b - a) * t;
  return L(
    L(
      L(h3(xi, yi, zi), h3(xi + 1, yi, zi), u),
      L(h3(xi, yi + 1, zi), h3(xi + 1, yi + 1, zi), u),
      v,
    ),
    L(
      L(h3(xi, yi, zi + 1), h3(xi + 1, yi, zi + 1), u),
      L(h3(xi, yi + 1, zi + 1), h3(xi + 1, yi + 1, zi + 1), u),
      v,
    ),
    w,
  );
}

/** FBM de 4 octavas sobre el ruido de valor. */
export function fbm(x: number, y: number, z: number): number {
  let a = 0;
  let f = 1;
  let w = 1;
  let s = 0;
  for (let i = 0; i < 4; i++) {
    a += ruido(x * f, y * f, z * f) * w;
    s += w;
    f *= 2.15;
    w *= 0.5;
  }
  return a / s;
}

/** Material fresnel-glow de la membrana (shader aditivo). */
export function materialMembrana(
  color: string,
  fuerza = 1,
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uColor: { value: new THREE.Color(color) },
      uTime: { value: 0 },
      uStrength: { value: fuerza },
    },
    vertexShader: `
      varying vec3 vN; varying vec3 vV; varying float vUp; varying vec3 vObj;
      uniform float uTime;
      void main() {
        // Respiración cortical: undulación lenta y local sobre la normal.
        float respiracion =
          sin(uTime * 1.15 + position.y * 3.1 + position.x * 2.2) * 0.013
          + sin(uTime * 0.57 + position.z * 2.6 + position.x) * 0.007;
        vec3 p = position + normal * respiracion;
        vN = normalize(normalMatrix * normal);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vV = normalize(-mv.xyz);
        vUp = normal.y;
        vObj = p;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      uniform vec3 uColor; uniform float uTime; uniform float uStrength;
      varying vec3 vN; varying vec3 vV; varying float vUp; varying vec3 vObj;
      void main() {
        float f = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 2.6);
        float pulse = 0.8 + 0.2 * sin(uTime * 0.8);
        float shade = 0.5 + 0.5 * vUp;
        float wave = max(0.0, sin(uTime * 1.2 - vObj.y * 2.4 + vObj.z * 1.7)) * 0.07;
        // Bandas de activación viajeras: barrido lento por la corteza.
        float banda = pow(
          max(0.0, sin(uTime * 0.75 - vObj.y * 4.2 + vObj.x * 1.9)), 6.0
        ) * 0.12;
        gl_FragColor = vec4(
          uColor, (f * 0.55 + shade * 0.045 + wave + banda) * uStrength * pulse
        );
      }`,
    transparent: true,
    side: THREE.BackSide,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
}

/** Lóbulo del cerebro con gyri, cisura de Silvio y aplanamientos. */
function geometriaLobulo(lado: number): THREE.SphereGeometry {
  const geo = new THREE.SphereGeometry(1, 110, 80);
  const pos = geo.attributes.position;
  const semilla = lado * 13.7;
  for (let i = 0; i < pos.count; i++) {
    let x = pos.getX(i);
    let y = pos.getY(i);
    let z = pos.getZ(i);
    const n = fbm(x * 2.7 + semilla, y * 2.7 + semilla * 0.3, z * 2.7 - semilla);
    const cresta = 1 - Math.abs(2 * n - 1);
    const s = 1 + (cresta - 0.5) * 0.17;
    x *= s;
    y *= s;
    z *= s;
    if (x * lado < 0) x *= 0.16;
    const lat = Math.max(0, Math.min(1, (x * lado) / 0.45));
    const banda = Math.exp(-(((y + 0.12) / 0.16) ** 2));
    const long = Math.max(0, Math.min(1, (0.55 - z) / 0.7));
    const syl = lat * banda * long;
    x *= 1 - 0.2 * syl;
    y *= 1 - 0.1 * syl;
    z *= 1 - 0.08 * syl;
    const bk = Math.max(0, Math.min(1, (z - 0.25) / 0.75));
    x *= 1 - 0.38 * bk * bk;
    y *= 1 - 0.18 * bk;
    const fr = Math.max(0, Math.min(1, (-z - 0.2) / 0.8));
    y *= 1 + 0.1 * fr;
    if (y < -0.3) y = -0.3 + (y + 0.3) * 0.72;
    pos.setXYZ(i, x, y, z);
  }
  geo.computeVertexNormals();
  return geo;
}

/** Cerebelo — bulbo con folia fina bajo el polo occipital. */
function geometriaCerebelo(): THREE.SphereGeometry {
  const geo = new THREE.SphereGeometry(1, 90, 64);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    let x = pos.getX(i);
    let y = pos.getY(i);
    let z = pos.getZ(i);
    const n = fbm(x * 4.5 + 7.1, y * 4.5, z * 4.5);
    const folia = Math.sin(y * 22 + n * 6);
    const s = 1 + (n - 0.5) * 0.1 + folia * 0.045;
    x *= s;
    y *= s;
    z *= s;
    pos.setXYZ(i, x, y, z);
  }
  geo.computeVertexNormals();
  return geo;
}

/** Tronco encefálico — tallo orgánico con bulbo pontino. */
function geometriaTronco(): THREE.CylinderGeometry {
  const geo = new THREE.CylinderGeometry(1, 1, 2, 26, 60, true);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    let x = pos.getX(i);
    const y = pos.getY(i);
    let z = pos.getZ(i);
    const tt = (y + 1) / 2;
    let r = 0.6 + 0.4 * tt;
    r *= 1 + 0.55 * Math.exp(-(((y - 0.45) / 0.28) ** 2));
    r *= 1 + (fbm(x * 3 + 9, y * 3, z * 3) - 0.5) * 0.18;
    x *= r;
    z *= r;
    z += 0.35 * (1 - tt) ** 2;
    pos.setXYZ(i, x, y, z);
  }
  geo.computeVertexNormals();
  return geo;
}

/** Desplaza una geometría con ruido para que parezca tejido. */
function organico(
  geo: THREE.BufferGeometry,
  frec: number,
  amp: number,
): THREE.BufferGeometry {
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i);
    const y = pos.getY(i);
    const z = pos.getZ(i);
    const s = 1 + (fbm(x * frec, y * frec, z * frec) - 0.5) * amp;
    pos.setXYZ(i, x * s, y * s, z * s);
  }
  geo.computeVertexNormals();
  return geo;
}

/** Resultado de construir la membrana: grupo, curvas tracto y límite. */
export interface CerebroMembrana {
  grupo: THREE.Group;
  tractos: THREE.Curve<THREE.Vector3>[];
  material: THREE.ShaderMaterial;
  /** Todos los materiales animables (corteza + estructuras profundas). */
  materiales: THREE.ShaderMaterial[];
}

/**
 * Construye la membrana cerebral completa (corteza, cerebelo, tronco,
 * bulbos, estructuras profundas) ajustada al elipsoide MEM.
 */
export function construirMembrana(mem: Membrana): CerebroMembrana {
  const memMat = materialMembrana("#8ba4d8");
  const grupo = new THREE.Group();

  for (const lado of [-1, 1]) {
    const lobulo = new THREE.Mesh(geometriaLobulo(lado), memMat);
    lobulo.scale.set(mem.x * 0.85, mem.y * 0.8, mem.z * 1.02);
    lobulo.position.x = lado * mem.x * 0.2;
    grupo.add(lobulo);
  }
  const cerebelo = new THREE.Mesh(geometriaCerebelo(), memMat);
  cerebelo.scale.set(mem.x * 0.52, mem.y * 0.3, mem.z * 0.3);
  cerebelo.position.set(0, -mem.y * 0.62, mem.z * 0.58);
  grupo.add(cerebelo);

  const tronco = new THREE.Mesh(geometriaTronco(), memMat);
  tronco.scale.set(mem.x * 0.115, mem.y * 0.42, mem.x * 0.115);
  tronco.position.set(0, -mem.y * 0.92, mem.z * 0.24);
  grupo.add(tronco);

  const geoOlf = new THREE.SphereGeometry(1, 16, 12);
  for (const lado of [-1, 1]) {
    const olf = new THREE.Mesh(geoOlf, memMat);
    olf.scale.set(mem.x * 0.07, mem.y * 0.05, mem.z * 0.13);
    olf.position.set(lado * mem.x * 0.14, -mem.y * 0.6, -mem.z * 0.52);
    grupo.add(olf);
  }

  // Estructuras profundas — tono cálido, brillo menor.
  const deepMat = materialMembrana("#f0bd72", 0.5);
  const profundas = new THREE.Group();
  const tractos: THREE.Curve<THREE.Vector3>[] = [];
  const V = (x: number, y: number, z: number) =>
    new THREE.Vector3(x * mem.x, y * mem.y, z * mem.z);

  /** Crea un tracto tubular orgánico y registra su curva. */
  const tubo = (puntos: THREE.Vector3[], r: number) => {
    const curva = new THREE.CatmullRomCurve3(puntos);
    const geo = organico(
      new THREE.TubeGeometry(curva, 36, r * mem.x, 10, false),
      0.02,
      0.1,
    );
    tractos.push(curva);
    profundas.add(new THREE.Mesh(geo, deepMat));
  };
  const geoBlob = organico(new THREE.SphereGeometry(1, 24, 18), 3.5, 0.16);
  /** Crea un núcleo ovoide orgánico en posición relativa. */
  const blob = (
    x: number,
    y: number,
    z: number,
    sx: number,
    sy: number,
    sz: number,
  ) => {
    const m = new THREE.Mesh(geoBlob, deepMat);
    m.position.set(x * mem.x, y * mem.y, z * mem.z);
    m.scale.set(sx * mem.x, sy * mem.y, sz * mem.z);
    profundas.add(m);
  };

  tubo([V(0, 0.02, -0.52), V(0, 0.15, -0.1), V(0, 0.13, 0.3), V(0, -0.04, 0.5)], 0.05);
  for (const s of [-1, 1])
    tubo([V(s * 0.14, 0.07, -0.42), V(s * 0.3, 0.13, 0.02), V(s * 0.27, -0.03, 0.4)], 0.03);
  for (const s of [-1, 1]) blob(s * 0.085, -0.02, 0.04, 0.095, 0.065, 0.13);
  blob(0, -0.1, -0.03, 0.05, 0.04, 0.06);
  blob(0, -0.17, -0.07, 0.028, 0.032, 0.028);
  blob(0, 0.0, 0.3, 0.024, 0.024, 0.024);
  for (const s of [-1, 1])
    tubo([V(s * 0.3, -0.14, -0.38), V(s * 0.36, -0.18, 0.02), V(s * 0.26, -0.13, 0.4)], 0.042);
  for (const s of [-1, 1]) blob(s * 0.3, -0.11, -0.44, 0.05, 0.05, 0.05);
  for (const s of [-1, 1])
    tubo([V(s * 0.16, 0.13, -0.32), V(s * 0.26, 0.2, 0.0), V(s * 0.27, 0.05, 0.38)], 0.026);
  for (const s of [-1, 1])
    tubo([V(s * 0.11, -0.13, -0.36), V(s * 0.06, -0.12, -0.2), V(0, -0.11, -0.1)], 0.018);
  blob(0, -0.3, 0.3, 0.09, 0.07, 0.09);

  grupo.add(profundas);
  return { grupo, tractos, material: memMat, materiales: [memMat, deepMat] };
}
