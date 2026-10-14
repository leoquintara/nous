/**
 * Materiales WebGL del río temporal — reutilizan el lenguaje visual
 * del cerebro (orbe "átomo": núcleo blanco → rim del color semántico).
 */

import * as THREE from "three";

import {
  COLORES_DIMENSION,
  COLORES_ESTADO,
  colorFuente,
} from "@/ui/brain/tipos";

/** Color numérico de una dimensión (paleta semántica del cerebro). */
export function colorDimension(dimension: string): number {
  return Number.parseInt(
    (COLORES_DIMENSION[dimension as keyof typeof COLORES_DIMENSION] ??
      "#8b93a1").slice(1),
    16,
  );
}

/** Color numérico del estado de tejido. */
export function colorEstado(estado: string): number {
  return Number.parseInt(
    (COLORES_ESTADO[estado as keyof typeof COLORES_ESTADO] ??
      "#8b93a1").slice(1),
    16,
  );
}

/** Color numérico estable por fuente (misma asignación que el cerebro). */
export function colorDeFuente(
  fuente: string,
  fuentes: { id: string; count: number }[],
): number {
  return Number.parseInt(colorFuente(fuente, fuentes).slice(1), 16);
}

/**
 * Shader de orbe estilo átomo para InstancedMesh: el núcleo queda
 * blanco candente y degrada al color de instancia hacia el rim
 * (fresnel). El color lleva el boost de actividad por instancia.
 */
export function materialAtomo(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    vertexShader: `
      varying vec3 vN; varying vec3 vV; varying vec3 vC;
      void main() {
        vec4 mp = vec4(position, 1.0);
        vec3 mn = normal;
        #ifdef USE_INSTANCING
          mp = instanceMatrix * mp;
          mn = mat3(instanceMatrix) * mn;
        #endif
        #ifdef USE_INSTANCING_COLOR
          vC = instanceColor;
        #else
          vC = vec3(1.0);
        #endif
        vN = normalize(normalMatrix * mn);
        vec4 mv = modelViewMatrix * mp;
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      varying vec3 vN; varying vec3 vV; varying vec3 vC;
      void main() {
        float fres = 1.0 - abs(dot(normalize(vN), normalize(vV)));
        float borde = pow(fres, 0.85);
        vec3 col = mix(vec3(1.12), vC, borde);
        gl_FragColor = vec4(col, 1.0);
      }`,
  });
}

/**
 * Puntos suaves (partículas): disco redondo con alpha radial — los
 * "sedimentos" que fluyen por los afluentes y los ecos fantasma.
 * Escala por atributo ``aSize`` con atenuación de distancia.
 */
export function materialPuntosSuaves(escalaPx: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: { uEscala: { value: escalaPx } },
    vertexShader: `
      attribute float aSize;
      varying vec3 vC;
      uniform float uEscala;
      void main() {
        vC = color;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSize * (uEscala / -mv.z);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      varying vec3 vC;
      void main() {
        float d = length(gl_PointCoord - 0.5);
        float a = smoothstep(0.5, 0.06, d);
        gl_FragColor = vec4(vC, a * 0.8);
      }`,
    vertexColors: true,
  });
}
