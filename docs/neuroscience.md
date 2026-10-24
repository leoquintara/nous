# Nous × Neurociencia — fundamentos de los mecanismos de memoria

Las cuatro rondas de mejora trasladan hallazgos de neurociencia de la
memoria a la arquitectura de Nous. Este documento registra qué se
implementó, dónde vive en el código y la literatura que lo motiva.

## Ronda 1 — base

### 1. Memoria de trabajo — cache de embeddings

**Código:** `src/core/embeddings.py` (`GeneradorEmbeddings._cache_*`)

Buffer de acceso rápido acotado (LRU, `NOUS_CACHE_EMBEDDINGS_MAX=256`)
que reusa vectores ya computados: una consulta repetida no vuelve a
pagar ~30-100ms de inferencia. Los contadores `estadisticas_cache`
exponen hits/misses para benchmarks.

*Fundamento:* el sistema de memoria de trabajo (Baddeley) mantiene
trazas activas de acceso inmediato en un buffer de capacidad limitada.

### 2. Reconsolidación — `refuerzos` frenan el olvido

**Código:** migración `0003_refuerzos`, `EventoRepository.reforzar`,
`_re_scored` en `src/core/busqueda.py`

Cada búsqueda que devuelve un evento incrementa `eventos.refuerzos`.
La vida media del decaimiento por recencia se multiplica por
`1 + log1p(refuerzos)`: una huella evocada muchas veces se olvida más
despacio que una reciente nunca consultada.

*Fundamento:* reconsolidación — recuperar una memoria la reactiva y la
fortalece (Nader et al., 2000; McGaugh, 2000). La curva de olvido de
Ebbinghaus se aplana con el repaso espaciado.

### 3. Separación de patrones — dedup semántico en ventana temporal

**Código:** `_recordar` en `src/mcp/tools.py`
(`NOUS_DEDUP_UMBRAL=0.95`, `NOUS_DEDUP_VENTANA_SEGUNDOS=3600`)

Un evento casi idéntico (coseno ≥ 0.95) dentro de la última hora no se
duplica: consolida el existente (`deduplicado: true`). Fuera de la
ventana sí crea huella nueva — el contexto temporal forma parte de la
traza, así las rutinas diarias ("medité") siguen generando rachas.

*Fundamento:* el giro dentado del hipocampo separa patrones de entrada
solapados en representaciones distintas (Treves & Rolls, 1994);
Marr — la misma experiencia re-evocada consolida, no duplica.

### 4. Activación difusa — priming de corto plazo

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

### 5. Segmentación episódica + poda sináptica

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

### Variable derivada: piso de similitud

`_fusionar` descarta hits vectoriales con coseno < `UMBRAL_COSENO_MIN`
(0.25): una consulta sin señal suficiente no "completa el patrón"
devolviendo el menos malo — retorna `[]`, como un sistema de memoria
que reconoce no recordar.

## Ronda 2 — sueño, emoción y metamemoria

### 6. Consolidación-sueño — `consolidar_memoria`

**Código:** `src/core/memoria/consolidacion.py`
(`decidir_consolidacion`, `aplicar_consolidacion`)

Corrida batch (CLI `nous dormir`, tool `consolidar_memoria`) que
replica el rol del sueño: replay selectivo. Las huellas fuertes se
promueven (`weight` sube), las de retención media se fortalecen, las
débiles se archivan de forma reversible y auditable
(`reportes_consolidacion` guarda ids y razones). Compromisos y
preferencias nunca se archivan; el tier `curado` es inmune.

*Fundamento:* replay hipocampal durante el sueño de ondas lentas —
la reactivación selectiva decide qué pasa a corteza (Rasch & Born,
2013; Diekelmann & Born, 2010).

### 7. Metamemoria — `confianza` por resultado

**Código:** `retrieval.py` (`confianza`, `confianza_de_evento`)

Cada hit declara además del score una confianza calibrada
(`conf 0.82`) que integra refuerzos, antigüedad y acuerdo entre las
señales BM25/vectorial. El agente sabe cuánto fiarse del recuerdo, no
solo qué tan relevante es — ~1µs por resultado.

*Fundamento:* metamemoria — el sistema que evalúa la propia memoria
(feeling-of-knowing, Koriat & Goldsmith, 1996).

### 8. Coactivación hebbiana — aristas `coactiva`

**Código:** `retrieval.py` (`pares_coactivos`, `peso_coactiva`),
`RelacionRepository.reforzar_coactivas`

Los eventos devueltos juntos en un recall forman pares `coactiva` con
peso incremental (`+0.15`, cap 3.0). Los que se evocan juntos se
cablean juntos — la asociación se aprende del uso, no del contenido.

*Fundamento:* "neurons that fire together wire together" — regla de
Hebb (1949); el uso sostenido refuerza la conexión (LTP).

### 9. Extinción emocional — `extincion_valencia`

**Código:** `retrieval.py` (`EXTINCION_FACTOR=0.12`,
`UMBRAL_ESTADO_NEUTRO=0.2`)

Reevocar un recuerdo emocional en estado neutro atenúa su
`estado_emocional` un 12% hacia el neutro por evocación — el miedo se
desensibiliza con exposición segura. Mínimo 0.05: la huella nunca
pierde del todo su marca.

*Fundamento:* extinción de la memoria emocional — la reevocación en
contexto seguro forma una traza inhibitoria nueva (Bouton, 2004;
Quirk & Mueller, 2008).

### 10. Reconsolidación lábil — `labil_hasta`

**Código:** `retrieval.py` (`ventana_labil`, `esta_labil`),
`relaciones.tipo='contradice'`, `EventoModel.reemplazado_por`

Un recuerdo recién evocado entra en ventana lábil (~6h): si llega
información que lo contradice, la nueva lo reemplaza con arqueología
completa (`reemplazado_por` + arista `contradice`) — la historia no
se pierde, se supersede.

*Fundamento:* la memoria reactivada se desestabiliza y puede
modificarse antes de reconsolidar (Nader et al., 2000) — base clínica
de la actualización de memorias traumáticas.

### 11. Sorpresa = error de predicción — `amplitud_con_sorpresa`

**Código:** `novedad.py` (`sorpresa`, `amplitud_con_sorpresa`,
`BOOST_SORPRESA_MAX=0.25`)

Al indexar, el embedding del evento se compara con los 20 vecinos
recientes: cuanto más lejos de la trayectoria, mayor la sorpresa — y
un boost de amplitud en la codificación (hasta +0.25). Lo inesperado
dice "esto importa".

*Fundamento:* la señal dopaminérgica codifica error de predicción
(Schultz, 1998): el cerebro marca como relevante lo que viola el
modelo interno, no lo rutinario.

### 12. Espaciado y primacía en la curva de olvido

**Código:** `decay.py` (`factor_espaciado`, `intervalo_medio`,
`media_vida_dias(primacia=…)`), `ritmos.py` (`es_primero_de_epoca`,
`FACTOR_PRIMACIA=1.15`)

La vida media se ajusta por dos efectos documentados: repasos
espaciados (intervalos regulares alargan la huella más que el mismo
número de repasos amontonados) y primacía (el primer evento de una
época — `inaugura_epoca` — recuerda mejor, ×1.15 de vida media).

*Fundamento:* efecto de espaciado (Cepeda et al., 2006; Ebbinghaus)
y posición serial (primacía/recencia, Murdock, 1962).

### 13. Ritmo circadiano del sueño — `ventana_optima_sueno`

**Código:** `ritmos.py` (`VentanaSueno`, `VENTANA_SUENO_HORAS=4`)

La consolidación sugiere su ventana horaria según el patrón real de
actividad del usuario — consolidar a las 3am de su actividad no es lo
mismo que a su mediodía.

*Fundamento:* la consolidación depende del estado fisiológico — el
sueño no es un batch job cualquiera (Walker & Stickgold, 2006).

### 14. Buffer de trabajo persistente — borradores

**Código:** `trabajo.py` (`TTL_BORRADOR_SEG=1800`,
`ENSAYOS_PARA_PROMOVER=2`, `CAPACIDAD_MAX=15`), API `/borradores`

Pensamientos en curso viven en un buffer con TTL (30min) y capacidad
acotada. Ensayarlos (repetir) los promueve a memoria permanente;
dejarlos expira los olvida solos.

*Fundamento:* el ensayo (rehearsal) es lo que transfiere del buffer
de trabajo a la memoria de largo plazo (Atkinson & Shiffrin, 1968).

### 15. Semanticización — gists

**Código:** `semanticizacion.py` (`agrupar_episodios`, `plan_gists`,
`UMBRAL_CLUSTER=0.82`, `MIN_CLUSTER=3`)

Durante el sueño, episodios similares (centroide ≥0.82) se abstraen
en gists semánticos (tag `gist`, máx. 5 por corrida): "medité" ×30
veces → "practico meditación". Los episodios quedan ligados por
aristas `gist` — la abstracción conserva su evidencia.

*Fundamento:* la memoria pasa de episódica a semántica con el
tiempo y la repetición (fuzzy-trace theory: gist vs. verbatim —
Brainerd & Reyna, 2002).

## Ronda 3 — olvido dirigido, prospectiva y sueños

### 16. Olvido dirigido — `olvido_dirigido`

**Código:** `decay.py` (`FACTOR_OLVIDO_DIRIGIDO=0.33`), tool
`olvidar_gradual`, REST `POST /eventos/{id}/olvidar`

El usuario puede decidir olvidar: la marca acelera el decay (vida
media ×0.33 — 28d→9.2d medida) sin borrar ni archivar. Reversible,
natural y auditable — distinto de `prune` (DELETE) y de archivar
(sacarlo de circulación intacto).

*Fundamento:* directed forgetting — las instrucciones de olvido
modulan la retención (Anderson & Hanslmayr, 2014); la supresión
motivada de recuerdos es un fenómeno real y reversible.

### 17. Repaso espaciado dirigido — `proxima_revision`

**Código:** `decay.py` (`proxima_revision`, `RETENCION_REVISION=0.6`),
`repasos_pendientes`, `EventoRepository.fijar_proximas_revisiones`

Cada evento lleva el timestamp en que su curva de retención cruzará
0.6 — repasar justo antes de olvidar es cuando más consolida. Cada
evocación recalcula la próxima fecha desde la nueva curva.

*Fundamento:* spacing effect aplicado como calendario óptimo — la
repetición en el punto de olvido inminente maximiza el beneficio por
repaso (Cepeda et al., 2006).

### 18. Cuarentena de fuentes — `fuentes.confiable`

**Código:** `EventoModel.cuarentena`, `FuenteRepository`,
`confiar_fuente`/`listar_cuarentena`

Desconfiar de una fuente aísla sus eventos del recall — indexados,
auditables, invisibles. Confiar la libera de golpe
(`aprobar_cuarentena_fuente`). La memoria aprende a discriminar
procedencias como el sistema inmune discrimina agentes.

*Fundamento:* source monitoring (Johnson et al., 1993) — la
procedencia es un atributo evaluable del recuerdo, con consecuencias
de credibilidad.

### 19. Asociaciones REM — aristas `rem`

**Código:** `rem.py` (`pares_rem`, `UMBRAL_REM_MIN=0.55`,
`UMBRAL_REM_MAX=0.78`, `REM_MAX_POR_CORRIDA=5`,
`REM_MAX_CANDIDATOS=300`)

En el sueño, pares de episodios similares-pero-distintos (banda
0.55–0.78: ni duplicados ni ajenos) se enlazan — las conexiones
lejanas que la vigilia no hace. Muestreo estratificado acota los
candidatos a 300 (O(n) efectivo, reproducible); máx. 1 arista por
episodio, 5 por corrida.

*Fundamento:* el sueño REM favorece la integración de memorias
distantes — la creatividad asociativa del sueño (Stickgold et al.,
1999; Cai et al., 2009).

### 20. Chaining episódico — aristas `encadena`

**Código:** `_indexar_luego` (`eventos.encadena`), tool
`consultar_memoria` (`contexto_temporal.vecinos`)

Cada evento indexado enlaza con el anterior de su misma fuente en
ventana de 24h: la línea de universo es navegable — "¿y después qué
pasó?" es una arista, no una búsqueda nueva.

*Fundamento:* contigüidad temporal — la organización de la memoria
episódica sigue el orden de la experiencia (Howard & Kahana, 2002).

### 21. Snooze prospectivo — `aplazada_hasta`

**Código:** `prospectiva.py` (`aplazar`, `aplazada`), columna
`intenciones_prospectivas.aplazada_hasta`, tool `aplazar_intencion`

Posponer un recordatorio sin borrarlo: el "después" humano. Ambos
triggers (tiempo y evento) respetan la ventana de aplazamiento.

*Fundamento:* la memoria prospectiva tolera retraso — la intención
sigue activa pero suspendida (Einstein & McDaniel, 1990).

### 22. Prep packs — `preparacion` en recordatorios

**Código:** `intenciones_prospectivas.preparacion` (JSONB),
`_disparar_intenciones` adjunta top-3 memorias relevantes

Cuando una intención dispara, el recordatorio no llega vacío: busca
memorias por el `query` de su trigger y las adjunta — llegar
preparado, no solo avisado.

*Fundamento:* prospective memory con cues — los recordatorios con
contexto asociado se ejecutan mejor (McDaniel & Einstein, 2007).

### 23. Efecto fan — `diversificar`

**Código:** `diversidad.py` (`UMBRAL_DUP_JACCARD=0.6`,
`PENAL_DUPLICADO=0.85`)

Los resultados de un recall se diversifican: near-duplicados por
contenido (Jaccard ≥0.6) se penalizan ×0.85 — diez hits devuelven
diez memorias distintas, no el mismo hecho reformulado.

*Fundamento:* fan effect (Anderson, 1974) — competidores similares
interfieren entre sí; el recall real diversifica, no apila.

### 24. Tip-of-the-tongue — `casi_recuerdo`

**Código:** `diversidad.py` (`es_casi_recuerdo`, `UMBRAL_CASI=0.30`)

Un hit con score bajo-pero-presente declara `casi_recuerdo: true` —
el sistema reporta honestamente "hay algo ahí pero no lo traigo"
en vez de silencio o de fingir certeza.

*Fundamento:* TOT (Brown & McNeill, 1966) — la metamemoria informa
disponibilidad aunque el recuerdo no se complete.

### 25. Frontera por sorpresa — `inaugura_epoca`

**Código:** `novedad.py` (`marca_frontera`, `UMBRAL_FRONTERA=0.65`),
`EventoRepository.marcar_inaugura_epoca`

Sorpresa >0.65 al indexar marca el evento como inicio de época —
la segmentación episódica la hace lo inesperado, no solo el hueco
temporal del mecanismo 5. Los primeros de época consolidan mejor
(primacía, mecanismo 12).

*Fundamento:* Event Segmentation Theory — los boundaries emergen en
los puntos de error de predicción alto (Zacks et al., 2007).

### 26. Salvados del olvido — métrica `salvados`

**Código:** `consolidacion.py` (`ResultadoConsolidacion.salvados`,
`resumen_legible`)

Cada corrida de sueño reporta cuántas memorias el replay rescató del
umbral de archivo — el beneficio medido de consolidar, no solo el
costo de olvidar.

*Fundamento:* la función del sueño no es olvidar sino seleccionar —
el audit trail cierra el lazo: "dormir" salvó N recuerdos.

### 27. Expansión de gist — `episodios` en resultados

**Código:** `EventoRepository.episodios_de_gist`, serialización
`episodios` (top-3 por resultado `gist`)

Un gist en los resultados trae sus episodios miembros — la
abstracción llega con su evidencia, nunca sola.

*Fundamento:* fuzzy-trace — el gist y el verbatim coexisten y se
recuperan por canales distintos (Brainerd & Reyna, 2002).

## Ronda 4 — competencia, loops y procedencia

### 28. Efecto Zeigarnik — `factor_zeigarnik`

**Código:** `retrieval.py` (`ZEIGARNIK_BOOST=1.15`),
`EventoRepository.ids_compromisos_abiertos`

Un compromiso abierto (sin arista `cierra`) recibe boost ×1.15 en el
recall: lo pendiente pesa más que lo resuelto. Al cerrarse, el boost
desaparece — la tensión se libera.

*Fundamento:* Zeigarnik (1927) — las tareas interrumpidas se recuerdan
mejor que las completadas; la intención abierta mantiene activación.

### 29. Retrieval-induced forgetting — `debilitados` por recall

**Código:** `retrieval.py` (`competidores_rif`, `RIF_FACTOR=0.97`,
`RIF_RANGO_COMPETENCIA=2`), `EventoRepository.debilitar_lote`

Cada recall debilita ×0.97 a los competidores que quedaron cerca del
podio (hasta 2×top_n por rama) pero no fueron elegidos — evocar al
ganador cuesta a los perdedores. `debilitados` cuenta cada
debilitamiento: auditable, como `refuerzos`. Curadas inmunes, piso
0.1 (el decay natural ya hizo el resto).

*Fundamento:* RIF (Anderson, Bjork & Bjork, 1994) — el recall
suprime activamente a los competidores no seleccionados; el olvido
competitivo es una propiedad del mecanismo de recuperación.

### 30. Interferencia proactiva — `es_competidor_proactivo`

**Código:** `interferencia.py` (`INTERFERENCIA_MIN=0.80`,
`INTERFERENCIA_FACTOR=0.92`)

Codificar una versión similar-pero-distinta (coseno 0.80–dedup) de
un recuerdo existente lo debilita ×0.92 — la huella nueva ocluye a
la vieja que compite por el mismo slot. Banda más estrecha que la
del vínculo semántico: afín es tema, casi-igual es versión.
Comparte mecanismo y contador con RIF.

*Fundamento:* proactive interference — lo viejo estorba al nuevo
recuerdo y viceversa (Underwood, 1957); en Nous la codificación
compite, no solo el recall.

### 31. Flashbulb — `es_flashbulb` + `marcar_flashbulb`

**Código:** `novedad.py` (`UMBRAL_FLASHBULB_SORPRESA=0.55`,
`UMBRAL_FLASHBULB_VALENCIA=0.3`, `FLASHBULB_FACTOR=1.5`,
`FLASHBULB_WEIGHT_MAX=3.0`), `EventoRepository.marcar_flashbulb`

Sorpresa alta × emoción intensa (distancia al neutro ≥0.3 en la
escala [0,1]) codifica con weight ×1.5 (cap fisiológico 3.0) y tag
auditable `flashbulb`. Los "¿dónde estabas cuando…?" se graban más
fuerte — y el tag deja constancia de que fue decisión del motor.

*Fundamento:* flashbulb memories (Brown & Kulik, 1977) — la
confluencia de sorpresa y carga emocional produce huellas
excepcionalmente duraderas (vía amigdalar).

### 32. Amnesia de fuente — confianza por procedencia

**Código:** `retrieval.py` (`confianza(fuente_confiable=…)`),
`FuenteRepository.fuentes_confiables`

La confianza de un recuerdo integra la confiabilidad de su fuente
como factor separado del contenido — recordamos el qué mejor que el
de dónde. Una fuente no confiable resta confianza aunque el
contenido sea idéntico.

*Fundamento:* source amnesia / source-monitoring errors (Schacter,
1999; Johnson et al., 1993) — la atribución de procedencia decae
antes que el contenido y se evalúa aparte.

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
- Rasch & Born (2013). *About sleep's role in memory.*;
  Diekelmann & Born (2010). *The memory function of sleep.*
- Koriat & Goldsmith (1996). *Monitoring and control processes in
  the strategic regulation of memory accuracy.* (metamemoria, FoK)
- Hebb (1949). *The Organization of Behavior.* (coactivación)
- Bouton (2004). *Context and behavioral processes in extinction.*;
  Quirk & Mueller (2008). *Neural mechanisms of extinction learning.*
- Schultz (1998). *Predictive reward signal of dopamine neurons.*
- Cepeda, Pashler, Vul, Wixted & Rohrer (2006). *Distributed practice
  in verbal recall tasks.* (espaciado)
- Murdock (1962). *The serial position effect of free recall.*
- Walker & Stickgold (2006). *Sleep, memory, and plasticity.*
- Atkinson & Shiffrin (1968). *Human memory: a proposed system and
  its control processes.* (buffer de trabajo)
- Brainerd & Reyna (2002). *Fuzzy-trace theory and false memory.*
  (gist vs. verbatim)
- Anderson & Hanslmayr (2014). *Neural mechanisms of motivated
  forgetting.* (olvido dirigido)
- Johnson, Hashtroudi & Lindsay (1993). *Source monitoring.*
- Stickgold, Scott, Rittenhouse & Hobson (1999). *Sleep-induced
  changes in associative memory.*; Cai, Mednick, Harrison, Kanady &
  Mednick (2009). *REM, not incubation, improves creativity.*
- Einstein & McDaniel (1990). *Normal aging and prospective memory.*;
  McDaniel & Einstein (2007). *Prospective Memory.* (snooze, cues)
- Anderson (1974). *Retrieval of propositional information.*
  (fan effect)
- Brown & McNeill (1966). *The "tip of the tongue" phenomenon.*
- Zeigarnik (1927). *Das Behalten erledigter und unerledigter
  Handlungen.*
- Anderson, Bjork & Bjork (1994). *Remembering can cause forgetting.*
  (RIF)
- Underwood (1957). *Interference and forgetting.* (interferencia
  proactiva)
- Brown & Kulik (1977). *Flashbulb memories.*
- Schacter (1999). *The seven sins of memory.* (amnesia de fuente)
