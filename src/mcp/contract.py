"""Contrato externo en inglés para las superficies MCP/REST de Nous.

El dominio interno sigue en español (``Dimension.COGNITIVA``,
``tipo="evento"``, ``certeza="alta"``...); este módulo es la frontera de
traducción: las tools canónicas (``tools_en.py``) exponen nombres y
argumentos ingleses, los alias en español (``tools.py``) siguen
registrados para compatibilidad, y AMBOS devuelven el mismo payload
inglés — los helpers internos emiten dicts en español y
:func:`traducir_payload` los normaliza recursivamente en la frontera.

Así el contrato inglés es único, auditable y futuras claves internas
nuevas se añaden a ``CLAVES_CONTRATO`` en un solo sitio.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar, cast

# ---------------------------------------------------------------------------
# Mapas de valores enumerados  ES (interno) -> EN (contrato)
# ---------------------------------------------------------------------------

DIMENSION_EN = {
    "cognitiva": "cognitive",
    "somatica": "somatic",
    "conductual": "behavioral",
}
DIMENSION_ES = {v: k for k, v in DIMENSION_EN.items()}

TIPO_EN = {
    "evento": "event",
    "hecho": "fact",
    "creencia": "belief",
    "compromiso": "commitment",
    "preferencia": "preference",
    "censor": "censor",
    "intencion": "intention",
}
TIPO_ES = {v: k for k, v in TIPO_EN.items()}

TIPO_MEMORIA_EN = {
    "episodica": "episodic",
    "semantica": "semantic",
    "procedimental": "procedural",
}
TIPO_MEMORIA_ES = {v: k for k, v in TIPO_MEMORIA_EN.items()}

TRIGGER_EN = {
    "tiempo": "time",
    "evento": "event",
}
TRIGGER_ES = {v: k for k, v in TRIGGER_EN.items()}

CERTEZA_EN = {
    "alta": "high",
    "media": "medium",
    "baja": "low",
    "nula": "none",
}

RAZON_EN = {
    "conflicto": "conflict",
    "sin_evidencia": "no_evidence",
}

ACCION_EN = {
    "presentar_ambos": "present_both",
    "preguntar_contexto": "ask_context",
}

# Union of every ``estado`` vocabulary: intenciones, sesiones, censores,
# predicciones y miembros de workspace.
ESTADO_EN = {
    "pendiente": "pending",
    "disparada": "fired",
    "cancelada": "cancelled",
    "aplazada": "snoozed",
    "abierta": "open",
    "abierto": "open",
    "cerrada": "closed",
    "cerrado": "closed",
    "activo": "active",
    "activa": "active",
    "archivada": "archived",
    "archivado": "archived",
    "inactivo": "inactive",
    "cumplido": "fulfilled",
    "abandonado": "abandoned",
    "cancelado": "cancelled",
    "acertada": "hit",
    "fallada": "miss",
    "expirada": "expired",
    "sin_proyecciones": "no_projections",
}

VEREDICTO_EN = {
    "proceder": "proceed",
    "precaucion": "caution",
    "detener": "halt",
}

# ``estado`` de los objetivos (filtro de listar / cierre).
OBJETIVO_ESTADO_EN = {
    "activo": "active",
    "cumplido": "fulfilled",
    "abandonado": "abandoned",
}
OBJETIVO_ESTADO_ES = {v: k for k, v in OBJETIVO_ESTADO_EN.items()}

SEVERIDAD_EN = {
    "advertir": "warn",
    "bloquear": "block",
}

# ---------------------------------------------------------------------------
# Renombrado de claves  ES (interno) -> EN (contrato)
# ---------------------------------------------------------------------------
# Aplica recursivamente a todo dict que salga de un helper. Las claves
# internas de ``contexto`` (metadatos libres del usuario) también se
# normalizan — ``fuente`` -> ``source`` es igualmente válido ahí.

CLAVES_CONTRATO: dict[str, str] = {
    "accion": "action",
    "acierto": "hit",
    "acierto_empirico": "empirical_hit_rate",
    "activa": "active",
    "actualizado_en": "updated_at",
    "afirmacion": "claim",
    "agente": "agent",
    "amplitud": "amplitude",
    "amplitud_media": "mean_amplitude",
    "amplitud_promedio": "avg_amplitude",
    "aprobado": "approved",
    "aprobados": "approved_count",
    "archivado": "archived",
    "archivado_en": "archived_at",
    "archivados": "archived",
    "archivos": "files",
    "autor_id": "author_id",
    "calientes": "hot",
    "cancelada": "cancelled",
    "capacidad": "capacity",
    "catalogo": "catalog",
    "censores": "censors",
    "censores_activos": "active_censors",
    "cerrada": "closed",
    "cerrado": "closed",
    "cierre_id": "closure_id",
    "clave": "key",
    "cobertura": "coverage",
    "compromiso": "commitment",
    "compromiso_id": "commitment_id",
    "compromisos_abiertos": "open_commitments",
    "completo": "complete",
    "con_quien": "with_whom",
    "confianza": "confidence",
    "confianza_clasificacion": "classification_confidence",
    "confianza_media": "mean_confidence",
    "confiable": "trusted",
    "conflictos": "conflicts",
    "consulta": "query",
    "contenido": "content",
    "contexto": "context",
    "contexto_relevante": "relevant_context",
    "contexto_temporal": "temporal_context",
    "creada_en": "created_at",
    "creado_en": "created_at",
    "cuarentena": "quarantine",
    "curado": "curated",
    "curado_fuera_presupuesto": "curated_over_budget",
    "curva": "curve",
    "debilitados": "weakened",
    "derivada_de": "derived_from",
    "descripcion": "description",
    "desde": "since",
    "desde_unix": "from_unix",
    "desglose_confianza": "confidence_breakdown",
    "destino_id": "target_id",
    "detalle": "detail",
    "detectados": "detected",
    "dias_abierto": "days_open",
    "dias_atraso": "days_overdue",
    "dilema": "dilemma",
    "dimension": "dimension",
    "dimension_dominante": "dominant_dimension",
    "dimension_probable": "probable_dimension",
    "disponible": "available",
    "distribucion": "distribution",
    "dominio": "domain",
    "duracion": "duration",
    "duracion_horas": "duration_hours",
    "ejecutada": "executed",
    "ejecutada_en": "executed_at",
    "ejecutado_en": "executed_at",
    "eliminado": "deleted",
    "encontrado": "found",
    "ensayos": "rehearsals",
    "entidad": "entity",
    "episodios": "episodes",
    "es_laguna_total": "is_total_gap",
    "estado": "status",
    "estado_emocional": "emotional_state",
    "evaluados": "evaluated",
    "evento": "event",
    "evento_cierre_id": "closing_event_id",
    "evento_ids": "event_ids",
    "eventos_analizados": "events_analyzed",
    "eventos_considerados": "events_considered",
    "eventos_semana": "events_this_week",
    "eventos_totales": "total_events",
    "expira_dias": "expires_days",
    "expira_en": "expires_at",
    "expirado": "expired",
    "expulsado": "removed",
    "faltantes": "missing",
    "falsado": "falsified",
    "fecha_fin": "end_date",
    "fecha_inicio": "start_date",
    "fecha_objetivo": "target_date",
    "fin": "end",
    "fortalecidos": "strengthened",
    "frecuencia": "frequency",
    "fuente": "source",
    "guardado": "saved",
    "guardados": "saved_count",
    "hasta": "until",
    "hasta_unix": "to_unix",
    "hora_dia": "hour_of_day",
    "hora_inicio": "start_hour",
    "horizonte_dias": "horizon_days",
    "indexado": "indexed",
    "inicio": "start",
    "intencion": "intention",
    "intenciones": "intentions",
    "lagunas": "gaps",
    "liberados": "released",
    "loops": "loops",
    "loops_abiertos": "open_loops",
    "menciones": "mentions",
    "menciones_total": "total_mentions",
    "mensaje": "message",
    "metas_activas": "active_goals",
    "motivo": "reason",
    "n_parametricas": "parametric_count",
    "nombre": "name",
    "nucleo": "core",
    "objetivo": "goal",
    "olvido_dirigido": "directed_forgetting",
    "omitidos": "omitted",
    "origen_id": "origin_id",
    "parametrica": "parametric",
    "pasos": "steps",
    "pendientes": "pending",
    "permitido": "allowed",
    "peso": "weight",
    "peso_efectivo": "effective_weight",
    "preparacion": "prepared_context",
    "presupuesto": "budget",
    "previos": "before",
    "primera_unix": "first_unix",
    "probabilidad": "probability",
    "promovidos": "promoted",
    "proxima_revision": "next_review",
    "proyectada": "projected",
    "rama": "branch",
    "rama_origen": "origin_branch",
    "razon": "reason",
    "razon_archivo": "archive_reason",
    "rechazado": "rejected",
    "recomendacion": "recommendation",
    "recordatorio": "reminder",
    "recordatorios": "reminders",
    "reemplazado_por": "superseded_by",
    "refuerzos": "reinforcements",
    "reformulada": "reformulated",
    "relaciones": "relations",
    "resueltas": "resolved",
    "resumen": "summary",
    "resultados": "results",
    "restaurado": "restored",
    "riesgo_oculto": "hidden_risk",
    "rinde": "delivered",
    "rol": "role",
    "salvados": "salvaged",
    "score_card": "score_card",
    "score_bm25": "bm25_score",
    "score_final": "final_score",
    "score_vectorial": "vector_score",
    "segundos": "seconds",
    "semanticos": "semantic_count",
    "sesgo_sobreconfianza": "overconfidence_bias",
    "severidad": "severity",
    "siguientes": "after",
    "similitud": "similarity",
    "soporte": "support",
    "sugerencia": "suggestion",
    "tiene_handoff": "has_handoff",
    "tipo": "kind",
    "tipo_memoria": "memory_kind",
    "titulo": "title",
    "trayectoria": "trajectory",
    "trayectorias": "trajectories",
    "trigger_tipo": "trigger_kind",
    "trigger_valor": "trigger_value",
    "truncado": "truncated",
    "ultima_unix": "last_unix",
    "umbral": "threshold",
    "usuario_id": "user_id",
    "variantes": "variants",
    "ventana_sugerida": "suggested_window",
    "veredicto": "verdict",
    "verificacion": "verdict",
    "vinculos": "links",
    "vinculos_borrados": "links_removed",
    "vinculos_creados": "links_created",
    "dias": "days",
    "estado_en": "asof",
    "ganador": "winner",
    "certeza": "certainty",
    "por": "by",
    "por_estado": "by_status",
    "por_dimension": "by_dimension",
    # resources: resumen/patrones/proyección
    "ventana_dias": "window_days",
    "variable_objetivo": "target_variable",
    "vector_proyectado": "projected_vector",
    "rama_decision": "probable_dimension",
    "eventos_base": "base_events",
    "aviso": "notice",
    "generado_en": "generated_at",
    "eventos_destacados": "top_events",
    "periodos_validos": "valid_periods",
    "eventos": "events",
    "periodo": "period",
    "total_eventos": "total_events",
    # claves dimensionadas (valores-enum usados como clave)
    "cognitiva": "cognitive",
    "somatica": "somatic",
    "conductual": "behavioral",
    # respuestas puntuales detectadas en la auditoría REST/dashboard
    "aplazada_hasta": "snoozed_until",
    "descartado": "discarded",
    "promovido": "promoted",
    "licenciado": "licensed",
    "max_usuarios": "max_users",
    "usuarios": "users",
    "sin_datos": "no_data",
    "total": "total",
    "entidades_nuevas": "new_entities",
    "reemplaza": "supersedes",
    "reconsolida": "reconsolidated",
    "resultado": "result",
    # metamemoria desglosada (confianza señal por señal)
    "evidencia": "evidence",
    "senectud": "senescence",
    "penalizacion": "penalty",
    "procedencia": "provenance",
    "techo": "ceiling",
    "casi_recuerdo": "almost_remembered",
    # motor predictivo de escenarios IA (/ai/predict, /ai/providers)
    "proveedor": "provider",
    "modelo": "model",
    "modelos": "models",
    "razonamiento": "reasoning",
    "escenarios": "scenarios",
    "latencia_ms": "latency_ms",
    "sustrato": "substrate",
    "habilitado": "enabled",
    "incluir_contenido": "include_content",
    "predeterminado": "default",
    "api_key_mascara": "api_key_mask",
    # consejero prescriptivo + licencia
    "motor": "engine",
    "expira": "expires",
    "firma_verificada": "signature_verified",
    "archivo_eliminado": "file_deleted",
    # intenciones / predicciones / bootstrap
    "dispara_en": "fires_at",
    "resuelto_en": "resolved_at",
    "presupuesto_usado": "budget_used",
}

# Claves cuyo SUBÁRBOL es documento libre del usuario (metadata de fuente,
# detalle de sesión, resultado de importación...). La clave se renombra
# igualmente, pero su valor pasa intacto — traducir claves dentro de un
# documento ajeno al contrato corrompería datos del usuario.
_PAYLOAD_LIBRE = frozenset({"metadata", "detalle", "detail"})

# Tabla de valores por clave TRADUCIDA (inglés): si el valor es str y la
# clave figura aquí, se traduce; valores desconocidos pasan intactos
# (free text como ``motivo``/"reason" nunca se corrompe).
_VALORES_POR_CLAVE: dict[str, dict[str, str]] = {
    "dimension": DIMENSION_EN,
    "dominant_dimension": DIMENSION_EN,
    "probable_dimension": DIMENSION_EN,
    "substrate": DIMENSION_EN,
    "kind": {
        **TIPO_EN,
        "agente": "agent",
        # Rachas dimensionales: ``racha_{dimension}`` -> ``{dimension}_streak``
        "racha_cognitiva": "cognitive_streak",
        "racha_somatica": "somatic_streak",
        "racha_conductual": "behavioral_streak",
        # Tipos de relación (aristas neurocognitivas)
        "explicita": "explicit",
        "semantica": "semantic",
        "inferida": "inferred",
        "contradice": "contradicts",
        "reconsolida": "reconsolidates",
        "reemplaza": "supersedes",
        "gist": "gist",
        "rem": "rem",
        "coactiva": "coactivation",
    },
    "memory_kind": TIPO_MEMORIA_EN,
    "certainty": CERTEZA_EN,
    "reason": RAZON_EN,
    "action": ACCION_EN,
    "status": ESTADO_EN,
    "verdict": VEREDICTO_EN,
    "severity": SEVERIDAD_EN,
    "trigger_kind": TRIGGER_EN,
    "via": {"coactiva": "coactivation", "tags": "tags"},
}

_T = TypeVar("_T")


def traducir_payload(nodo: _T) -> _T:
    """Traduce recursivamente un payload ES (helpers internos) al contrato EN.

    - Dicts: renombra claves por ``CLAVES_CONTRATO`` y recurre.
    - Strings: los traduce solo cuando la clave padre es enumerada
      (``dimension``->``"cognitive"``); el texto libre pasa intacto.
    - Listas/tuplas: recurre elemento a elemento.
    - Escalares: sin cambios.
    """
    if isinstance(nodo, dict):
        out: dict[str, Any] = {}
        for clave, valor in nodo.items():
            clave_en = CLAVES_CONTRATO.get(clave, clave) if isinstance(clave, str) else clave
            if isinstance(clave, str) and clave in _PAYLOAD_LIBRE:
                out[clave_en] = valor
                continue
            traducido = traducir_payload(valor)
            tabla = _VALORES_POR_CLAVE.get(clave_en) if isinstance(clave_en, str) else None
            if tabla is not None and isinstance(traducido, str):
                traducido = tabla.get(traducido, traducido)
            out[clave_en] = traducido
        return out  # type: ignore[return-value]
    if isinstance(nodo, list | tuple):
        return [traducir_payload(item) for item in nodo]  # type: ignore[return-value]
    return nodo


_AwaitableFn = Callable[..., Awaitable[Any]]

# Tools cuyo retorno ES un documento libre del usuario (preferencias
# almacenadas): el payload completo pasa sin traducir.
_PASSTHROUGH_TOOLS = frozenset({"leer_preferencia", "read_preference"})


def ingles(fn: _AwaitableFn) -> _AwaitableFn:
    """Decorador: normaliza el retorno de una tool alias al contrato EN.

    Aplica ``functools.wraps`` para que FastMCP introspeccione la firma
    española original (los alias conservan sus argumentos históricos).
    """
    if fn.__name__ in _PASSTHROUGH_TOOLS:
        return fn

    @functools.wraps(fn)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return traducir_payload(await fn(*args, **kwargs))

    return _wrapper


# ---------------------------------------------------------------------------
# Traducción de argumentos EN (tools canónicas) -> ES (helpers internos)
# ---------------------------------------------------------------------------


DimensionES = Literal["cognitiva", "somatica", "conductual"]


def dimension_es(valor: str | None) -> DimensionES | None:
    """Dimensión del contrato inglés -> valor interno."""
    if valor is None:
        return None
    return cast("DimensionES", DIMENSION_ES.get(valor, valor))


def tipo_es(valor: str | None) -> str | None:
    """Tipo epistémico del contrato inglés -> interno."""
    if valor is None:
        return None
    return TIPO_ES.get(valor, valor)


def tipo_memoria_es(valor: str | None) -> str | None:
    """Tipo de memoria del contrato inglés -> interno."""
    if valor is None:
        return None
    return TIPO_MEMORIA_ES.get(valor, valor)


def trigger_es(valor: str | None) -> str | None:
    """Trigger de intención del contrato inglés -> interno."""
    if valor is None:
        return None
    return TRIGGER_ES.get(valor, valor)


def severidad_es(valor: str | None) -> str | None:
    """Severidad de censor del contrato inglés -> interno."""
    if valor is None:
        return None
    invertido = {v: k for k, v in SEVERIDAD_EN.items()}
    return invertido.get(valor, valor)


def estado_objetivo_es(valor: str | None) -> str | None:
    """Estado de objetivo del contrato inglés -> interno."""
    if valor is None:
        return None
    return OBJETIVO_ESTADO_ES.get(valor, valor)
