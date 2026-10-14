"use client";

/**
 * Preferencias del río temporal persistidas en PostgreSQL.
 *
 * Mismo contrato que `usar-prefs-cerebro`: el documento vive en la
 * tabla `configuracion` bajo la clave `universo`; localStorage solo
 * cachea el primer pintado. La fuente de verdad es el backend.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { PuertoMemoria } from "@/application/puertos";
import { PREFS_UNIVERSO_DEFAULT, type PrefsUniverso } from "./prefs";

const CLAVE = "universo";
const CLAVE_CACHE = "nous.universo.prefs";

/** Lee la caché local para el primer pintado instantáneo. */
function leerCache(): Partial<PrefsUniverso> {
  try {
    const crudo = localStorage.getItem(CLAVE_CACHE);
    return crudo ? (JSON.parse(crudo) as Partial<PrefsUniverso>) : {};
  } catch {
    return {};
  }
}

/** Combina defaults, caché local y documento remoto. */
function mezclar(parcial: Partial<PrefsUniverso>): PrefsUniverso {
  return {
    ...PREFS_UNIVERSO_DEFAULT,
    ...parcial,
    colores: { ...PREFS_UNIVERSO_DEFAULT.colores, ...parcial.colores },
    fantasmaDims:
      parcial.fantasmaDims ?? PREFS_UNIVERSO_DEFAULT.fantasmaDims,
  };
}

/**
 * Hook de preferencias del universo: carga desde PostgreSQL al
 * montar (con caché local instantánea) y guarda con debounce.
 */
export function usePrefsUniverso(memoria: PuertoMemoria | null) {
  const [prefs, setPrefs] = useState<PrefsUniverso>(PREFS_UNIVERSO_DEFAULT);
  const temporizador = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tocado = useRef(false);
  const memoriaRef = useRef(memoria);
  useEffect(() => {
    memoriaRef.current = memoria;
  });

  useEffect(() => {
    let vivo = true;
    queueMicrotask(() => {
      if (vivo) setPrefs((p) => mezclar({ ...p, ...leerCache() }));
    });
    if (!memoria) return;
    memoria
      .leerPreferencias(CLAVE)
      .then((doc) => {
        if (!vivo || tocado.current) return;
        setPrefs((p) => {
          const fusion = mezclar({
            ...p,
            ...(doc as Partial<PrefsUniverso>),
          });
          try {
            localStorage.setItem(CLAVE_CACHE, JSON.stringify(fusion));
          } catch {
            /* caché opcional */
          }
          return fusion;
        });
      })
      .catch(() => undefined);
    return () => {
      vivo = false;
    };
  }, [memoria]);

  /** Actualiza una preferencia y programa su persistencia. */
  const setPref = useCallback(
    <K extends keyof PrefsUniverso>(clave: K, valor: PrefsUniverso[K]) => {
      tocado.current = true;
      setPrefs((prev) => {
        const siguiente = { ...prev, [clave]: valor };
        try {
          localStorage.setItem(CLAVE_CACHE, JSON.stringify(siguiente));
        } catch {
          /* caché opcional */
        }
        if (temporizador.current) clearTimeout(temporizador.current);
        temporizador.current = setTimeout(() => {
          memoriaRef.current
            ?.guardarPreferencias(CLAVE, siguiente)
            .catch(() => undefined);
        }, 600);
        return siguiente;
      });
    },
    [],
  );

  return { prefs, setPref };
}
