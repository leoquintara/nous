# Nous × Neurociencia — fundamentos de los mecanismos de memoria

Las cinco rondas de mejora trasladan hallazgos de neurociencia de la
memoria a la arquitectura de Nous. Este documento registra qué se
implementó, dónde vive en el código y la literatura que lo motiva.

## 1. Memoria de trabajo — cache de embeddings

**Código:** `src/core/embeddings.py` (`GeneradorEmbeddings._cache_*`)

Buffer de acceso rápido acotado (LRU, `NOUS_CACHE_EMBEDDINGS_MAX=256`)
que reusa vectores ya computados: una consulta repetida no vuelve a
pagar ~30-100ms de inferencia. Los contadores `estadisticas_cache`
exponen hits/misses para benchmarks.

*Fundamento:* el sistema de memoria de trabajo (Baddeley) mantiene
trazas activas de acceso inmediato en un buffer de capacidad limitada.

## 2. Reconsolidación — `refuerzos` frenan el olvido

**Código:** migración `0003_refuerzos`, `EventoRepository.reforzar`,
`_re_scored` en `src/core/busqueda.py`

Cada búsqueda que devuelve un evento incrementa `eventos.refuerzos`.
La vida media del decaimiento por recencia se multiplica por
`1 + log1p(refuerzos)`: una huella evocada muchas veces se olvida más
despacio que una reciente nunca consultada.

*Fundamento:* reconsolidación — recuperar una memoria la reactiva y la
fortalece (Nader et al., 2000; McGaugh, 2000). La curva de olvido de
Ebbinghaus se aplana con el repaso espaciado.

## 3. Separación de patrones — dedup semántico en ventana temporal

**Código:** `_recordar` en `src/mcp/tools.py`
(`NOUS_DEDUP_UMBRAL=0.95`, `NOUS_DEDUP_VENTANA_SEGUNDOS=3600`)

Un evento casi idéntico (coseno ≥ 0.95) dentro de la última hora no se
duplica: consolida el existente (`deduplicado: true`). Fuera de la
ventana sí crea huella nueva — el contexto temporal forma parte de la
traza, así las rutinas diarias ("medité") siguen generando rachas.

*Fundamento:* el giro dentado del hipocampo separa patrones de entrada
solapados en representaciones distintas (Treves & Rolls, 1994);
Marr — la misma experiencia re-evocada consolida, no duplica.

## 4. Activación difusa — priming de corto plazo

**Código:** `BusquedaHibrida4D._primar`/`_activaciones`,
`BOOST_PRIMING=0.15`, `NOUS_PRIMING_SEGUNDOS=300`

Tras cada búsqueda, los resultados (fuerza 1.0) y sus vecinos
temporales (0.5) quedan activados por 5 minutos. La siguiente consulta
los favorece multiplicativamente. La activación expira — el priming
real es efímero.

*Fundamento:* spreading activation (Collins & Loftus, 1975) y el
efecto de contigüidad del Temporal Context Model (Howard & Kahana,
2002): evocar un recuerdo hace más accesibles los recuerdos
temporalmente próximos.

## 5. Segmentación episódica + poda sináptica

**Código:** `src/core/episodios.py`, tool `obtener_episodios`;
`EventoRepository.podar`, `nous prune`

- **Episodios:** la línea continua se corta en episodios donde el
  hueco de inactividad supera `gap_max` (4h). Cada episodio resume
  dimensión dominante y amplitud media — la estructura narrativa del
  historial, no solo eventos sueltos.
- **Poda:** `nous prune --dias 90 --amplitud 0.3` elimina huellas
  viejas, débiles y nunca consolidadas. Dry-run por defecto;
  `--aplicar` ejecuta el DELETE. La poda respeta la intensidad
  (amplitud alta), la consolidación (refuerzos) y la recencia.

*Fundamento:* Event Segmentation Theory (Zacks et al., 2007) — el
cerebro corta la experiencia en episodios en los boundaries temporales.
Synaptic Homeostasis Hypothesis (Tononi & Cirelli, 2003) — el sueño
poda sinapsis débiles para preservar las relevantes; Nous poda
explícitamente con `nous prune`.

## Variable derivada: piso de similitud

`_fusionar` descarta hits vectoriales con coseno < `UMBRAL_COSENO_MIN`
(0.25): una consulta sin señal suficiente no "completa el patrón"
devolviendo el menos malo — retorna `[]`, como un sistema de memoria
que reconoce no recordar.

## Referencias

- Nader, Schafe & LeDoux (2000). *Fear memories require protein
  synthesis in the amygdala for reconsolidation after retrieval.*
- McGaugh (2000). *Memory — a century of consolidation.*
- Treves & Rolls (1994). *Computational analysis of the role of the
  hippocampus in memory.* (separación/completado de patrones)
- Collins & Loftus (1975). *A spreading-activation theory of semantic
  processing.*
- Howard & Kahana (2002). *A distributed representation of temporal
  context.* (Temporal Context Model, efecto de contigüidad)
- Zacks, Speer, Swallow, Braver & Reynolds (2007). *Event perception:
  a mind-brain perspective.*
- Tononi & Cirelli (2003). *Sleep and synaptic homeostasis.*
- Ebbinghaus (1885). *Über das Gedächtnis* (curva del olvido).
