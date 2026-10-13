"use client";

/**
 * Preferencias del cerebro 3D persistidas en PostgreSQL.
 *
 * El documento vive en la tabla `configuracion` bajo la clave
 * `cerebro` vía `GET/PUT /api/preferencias/cerebro`. localStorage se
 * usa solo como caché de arranque instantáneo; la fuente de verdad es
 * el backend.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { PuertoMemoria } from "@/application/puertos";
import { PREFS_DEFAULT, type PrefsCerebro } from "./tipos";

const CLAVE = "cerebro";
const CLAVE_CACHE = "nous.cerebro.prefs";

/** Lee la caché local para el primer pintado instantáneo. */
function leerCache(): Partial<PrefsCerebro> {
  try {
    const crudo = localStorage.getItem(CLAVE_CACHE);
    return crudo ? (JSON.parse(crudo) as Partial<PrefsCerebro>) : {};
  } catch {
    return {};
  }
}

/** Combina defaults, caché local y documento remoto. */
function mezclar(parcial: Partial<PrefsCerebro>): PrefsCerebro {
  return { ...PREFS_DEFAULT, ...parcial };
}

/**
 * Hook de preferencias: carga desde PostgreSQL al montar (con caché
 * local instantánea) y guarda con debounce en cada cambio.
 */
export function usePrefsCerebro(memoria: PuertoMemoria | null) {
  // Estado inicial siempre PREFS_DEFAULT: el SSR y el cliente deben
  // renderizar lo mismo (hidratación). La caché local se aplica tras
  // montar y el documento remoto la sobrescribe cuando llega.
  const [prefs, setPrefs] = useState<PrefsCerebro>(PREFS_DEFAULT);
  const [cargadas, setCargadas] = useState(false);
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
        // Si el usuario ya ajustó algo, su estado gana al remoto.
        if (!vivo || tocado.current) {
          setCargadas(true);
          return;
        }
        setPrefs((p) => {
          const fusion = mezclar({ ...p, ...(doc as Partial<PrefsCerebro>) });
          try {
            localStorage.setItem(CLAVE_CACHE, JSON.stringify(fusion));
          } catch {
            /* caché opcional */
          }
          return fusion;
        });
        setCargadas(true);
      })
      .catch(() => setCargadas(true));
    return () => {
      vivo = false;
    };
  }, [memoria]);

  /** Actualiza una preferencia y programa su persistencia. */
  const setPref = useCallback(
    <K extends keyof PrefsCerebro>(clave: K, valor: PrefsCerebro[K]) => {
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

  return { prefs, setPref, cargadas };
}
