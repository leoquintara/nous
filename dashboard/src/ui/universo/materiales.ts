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
import type { IdEscenario } from "./geometria";

/** Color semántico por escenario de futuro (no por dimensión). */
export const COLORES_ESCENARIO: Record<IdEscenario, number> = {
  continuidad: 0x6aa9ff,
  riesgo: 0xe0664f,
  florecer: 0x6fe3a5,
};

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
 * "sedimentos" que fluyen por los afluentes, la corriente del cauce
 * y el polvo ambiental. ``aFase`` + ``uDeriva`` añaden vaivén suave
 * (senos de baja frecuencia) sin tocar la posición lógica.
 */
export function materialPuntosSuaves(
  escalaPx: number,
  deriva = 0,
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uEscala: { value: escalaPx },
      uT: { value: 0 },
      uDeriva: { value: deriva },
    },
    vertexShader: `
      attribute float aSize;
      attribute float aFase;
      varying vec3 vC;
      uniform float uEscala;
      uniform float uT;
      uniform float uDeriva;
      void main() {
        vC = color;
        vec3 p = position + uDeriva * vec3(
          sin(uT * 0.13 + aFase * 1.7) * 5.0,
          sin(uT * 0.21 + aFase) * 3.0,
          cos(uT * 0.17 + aFase * 1.3) * 5.0
        );
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
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

/**
 * Cauce con corriente luminosa interna: bandas de energía viajan a
 * lo largo del tubo (uv.x) + fresnel suave en el rim. ``intensidad``
 * regula el brillo — fantasmas la modulan por probabilidad y el
 * cauce principal la mantiene como fluir constante del agua.
 */
export function materialFlujo(
  color: number,
  intensidad: number,
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
    uniforms: {
      uColor: { value: new THREE.Color(color) },
      uT: { value: 0 },
      uIntensidad: { value: intensidad },
    },
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        vUv = uv;
        vN = normalize(normalMatrix * normal);
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      varying vec2 vUv;
      varying vec3 vN;
      varying vec3 vV;
      uniform vec3 uColor;
      uniform float uT;
      uniform float uIntensidad;
      void main() {
        // Bandas de corriente viajando hacia el futuro (uv.x crece con t).
        float onda = pow(0.5 + 0.5 * sin(vUv.x * 55.0 - uT * 1.7), 3.0);
        float latido = 0.9 + 0.1 * sin(uT * 1.1 + vUv.x * 18.0);
        float fres = 0.45 + 0.55 * pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 1.6);
        float energia = (0.16 + onda * 0.85) * latido * fres * uIntensidad;
        gl_FragColor = vec4(uColor * energia, energia);
      }`,
  });
}
