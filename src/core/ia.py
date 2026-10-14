"""Proveedores de IA externos configurables y motor predictivo.

Local-first por defecto: Nous funciona completo sin proveedor. Si el
dev configura uno (endpoint OpenAI-compatible — OpenAI, Groq,
OpenRouter, Ollama, LM Studio…), el motor predictivo envía un RESUMEN
estadístico de la línea temporal (o contenido de eventos solo si el
usuario lo habilita explícitamente con ``incluir_contenido``) y
devuelve escenarios de futuro con probabilidades y narrativa.

Las API keys se persisten cifradas con pgcrypto dentro del documento
``configuracion['ia_proveedores']``; la API solo expone máscaras.
"""

import json
import logging
import re
import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.storage.repositories import CifradoRepository, ConfiguracionRepository

logger = logging.getLogger(__name__)

CLAVE_PROVEEDORES = "ia_proveedores"
MAX_PROVEEDORES = 8
PATRON_ID_PROVEEDOR = re.compile(r"^[a-z0-9_\-]{1,40}$")
TIMEOUT_PREDICCION = 45.0
TIMEOUT_PRUEBA = 12.0

_PASS_DEV = "nous-dev-cifrado-local"


def _passphrase() -> str:
    """Resuelve la frase de cifrado para las API keys.

    Prioriza ``NOUS_CLAVE_CIFRADO``; si falta, deriva del token de
    servicio (auto-hospedado unipersonal); sin ninguno, usa una
    constante de desarrollo — la key sigue sin viajar en claro a la DB.
    """
    settings = get_settings()
    if settings.clave_cifrado:
        return settings.clave_cifrado
    if settings.api_token:
        return f"nous:{settings.api_token}"
    logger.warning(
        "Sin NOUS_CLAVE_CIFRADO ni NOUS_API_TOKEN: las API keys se "
        "cifran con la passphrase de desarrollo"
    )
    return _PASS_DEV


def _enmascarar(api_key: str) -> str:
    """Devuelve la máscara visible de una API key (``sk-…wxyz``)."""
    if len(api_key) <= 8:
        return "…" + api_key[-3:]
    return api_key[:4] + "…" + api_key[-4:]


def _publico(proveedor: dict[str, Any]) -> dict[str, Any]:
    """Versión pública de un proveedor: nunca expone la key cifrada."""
    return {
        "id": proveedor["id"],
        "nombre": proveedor["nombre"],
        "base_url": proveedor["base_url"],
        "modelo": proveedor["modelo"],
        "habilitado": proveedor["habilitado"],
        "incluir_contenido": proveedor.get("incluir_contenido", False),
        "api_key_set": bool(proveedor.get("api_key_enc")),
        "api_key_mascara": proveedor.get("api_key_mascara"),
    }


async def _doc(session: AsyncSession) -> dict[str, Any]:
    """Lee el documento de proveedores (estructura vacía si no existe)."""
    valor = await ConfiguracionRepository(session).obtener(CLAVE_PROVEEDORES)
    if isinstance(valor, dict) and isinstance(valor.get("proveedores"), list):
        return valor
    return {"proveedores": []}


async def listar_proveedores(session: AsyncSession) -> list[dict[str, Any]]:
    """Lista los proveedores configurados con la key enmascarada."""
    doc = await _doc(session)
    return [_publico(p) for p in doc["proveedores"]]


async def guardar_proveedor(
    session: AsyncSession,
    proveedor_id: str,
    datos: dict[str, Any],
) -> dict[str, Any]:
    """Crea o actualiza un proveedor; cifra la API key si viene nueva.

    Args:
        session: Sesión de base de datos.
        proveedor_id: Identificador del proveedor (path-safe).
        datos: nombre, base_url, modelo, habilitado,
            incluir_contenido y opcionalmente ``api_key`` en claro.

    Returns:
        El proveedor en su versión pública (key enmascarada).

    Raises:
        ValueError: id inválido, cupo de proveedores o URL no http(s).
    """
    if not PATRON_ID_PROVEEDOR.fullmatch(proveedor_id):
        raise ValueError("id de proveedor inválido")
    base_url = str(datos["base_url"]).rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url debe ser http(s)")

    doc = await _doc(session)
    proveedores: list[dict[str, Any]] = doc["proveedores"]
    existente = next((p for p in proveedores if p["id"] == proveedor_id), None)
    if existente is None and len(proveedores) >= MAX_PROVEEDORES:
        raise ValueError(f"Máximo {MAX_PROVEEDORES} proveedores")

    proveedor: dict[str, Any] = (
        dict(existente) if existente else {"id": proveedor_id}
    )
    proveedor.update(
        {
            "nombre": str(datos["nombre"])[:80],
            "base_url": base_url[:300],
            "modelo": str(datos["modelo"])[:120],
            "habilitado": bool(datos["habilitado"]),
            "incluir_contenido": bool(datos.get("incluir_contenido", False)),
        }
    )
    api_key = datos.get("api_key")
    if api_key:
        clave = str(api_key).strip()
        cifrado = CifradoRepository(session)
        proveedor["api_key_enc"] = await cifrado.cifrar(clave, _passphrase())
        proveedor["api_key_mascara"] = _enmascarar(clave)

    if existente is None:
        proveedores.append(proveedor)
    else:
        # ``proveedor`` es una copia: hay que reescribirla en la lista
        # para que el update persista (no basta el update() sobre ella).
        proveedores[proveedores.index(existente)] = proveedor
    await ConfiguracionRepository(session).guardar(CLAVE_PROVEEDORES, doc)
    return _publico(proveedor)


async def eliminar_proveedor(session: AsyncSession, proveedor_id: str) -> bool:
    """Elimina un proveedor del documento. True si existía."""
    doc = await _doc(session)
    antes = len(doc["proveedores"])
    doc["proveedores"] = [p for p in doc["proveedores"] if p["id"] != proveedor_id]
    if len(doc["proveedores"]) == antes:
        return False
    await ConfiguracionRepository(session).guardar(CLAVE_PROVEEDORES, doc)
    return True


async def _proveedor_activo(
    session: AsyncSession, proveedor_id: str | None
) -> dict[str, Any] | None:
    """Devuelve el proveedor habilitado a usar (con key descifrada)."""
    doc = await _doc(session)
    candidatos = [
        p
        for p in doc["proveedores"]
        if p["habilitado"] and p.get("api_key_enc") is not None
    ]
    if proveedor_id:
        candidatos = [p for p in candidatos if p["id"] == proveedor_id]
    if not candidatos:
        return None
    elegido: dict[str, Any] = candidatos[0]
    try:
        elegido["api_key"] = await CifradoRepository(session).descifrar(
            elegido["api_key_enc"], _passphrase()
        )
    except Exception as e:  # passphrase rotada o ciphertext corrupto
        logger.warning("No se pudo descifrar la API key: %s", type(e).__name__)
        return None
    return elegido


async def probar_proveedor(
    session: AsyncSession, proveedor_id: str
) -> dict[str, Any]:
    """Verifica conectividad con el endpoint (``GET /models``).

    Returns:
        ``{"ok": True, "modelos": [...]}`` o ``{"ok": False, "detalle"}``.
        Nunca propaga excepciones de red — es un diagnóstico.
    """
    doc = await _doc(session)
    proveedor = next((p for p in doc["proveedores"] if p["id"] == proveedor_id), None)
    if proveedor is None:
        return {"ok": False, "detalle": "proveedor no encontrado"}
    api_key: str | None = None
    if proveedor.get("api_key_enc"):
        try:
            api_key = await CifradoRepository(session).descifrar(
                proveedor["api_key_enc"], _passphrase()
            )
        except Exception as e:  # passphrase rotada o ciphertext corrupto
            return {
                "ok": False,
                "detalle": f"key ilegible: {type(e).__name__}",
            }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_PRUEBA) as cliente:
            resp = await cliente.get(f"{proveedor['base_url']}/models", headers=headers)
            if resp.status_code == 200:
                datos = resp.json()
                modelos = [
                    str(m.get("id", "?")) for m in datos.get("data", [])
                ][:20]
                return {"ok": True, "modelos": modelos}
            # Algunos endpoints OpenAI-compatible no exponen /models
            # (p. ej. Gemini): la prueba de verdad es un completion mínimo.
            resp = await cliente.post(
                f"{proveedor['base_url']}/chat/completions",
                headers=headers,
                json={
                    "model": proveedor["modelo"],
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
        if resp.status_code != 200:
            return {"ok": False, "detalle": f"HTTP {resp.status_code}"}
        return {"ok": True, "modelos": [proveedor["modelo"]]}
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "detalle": str(e)[:200]}


def _resumen_contexto(
    eventos: list[dict[str, Any]],
    origen: int,
    incluir_contenido: bool,
) -> dict[str, Any]:
    """Construye el resumen estadístico de la línea hasta ``origen``.

    Replica el decaimiento por recencia (vida media 30d) del backend
    para estimar el sustrato dimensional — sin contenido salvo opt-in.
    """
    vida_media = 30 * 86_400
    pesos: dict[str, float] = {"cognitiva": 0, "somatica": 0, "conductual": 0}
    recientes: dict[str, int] = {"cognitiva": 0, "somatica": 0, "conductual": 0}
    corte = origen - 30 * 86_400
    for e in eventos:
        if e["ts"] > origen:
            continue
        dim = e["dimension"]
        pesos[dim] += 0.5 ** (max(0, origen - e["ts"]) / vida_media)
        if e["ts"] >= corte:
            recientes[dim] += 1
    total = sum(pesos.values()) or 1.0
    dist = {d: round(w / total, 3) for d, w in pesos.items()}
    visibles = [e for e in eventos if e["ts"] <= origen]
    resumen: dict[str, Any] = {
        "origen_unix": origen,
        "eventos_visibles": len(visibles),
        "distribucion_dimensional": dist,
        "recientes_30d": recientes,
        "dimension_dominante": max(dist, key=lambda d: dist[d]),
    }
    if incluir_contenido:
        resumen["muestras"] = [
            {
                "dimension": e["dimension"],
                "contenido": str(e.get("contenido", ""))[:140],
                "amplitud": e["amplitud"],
            }
            for e in visibles[-14:]
        ]
    return resumen


def _prompt_prediccion(
    contexto: dict[str, Any],
    metas: list[dict[str, Any]],
    loops: int,
    horizonte_dias: int,
    candidatos: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Arma los mensajes para el endpoint chat/completions.

    Pide JSON estricto: una lista ``escenarios`` con nombre,
    probabilidad 0..1, descripción corta y sustrato dimensional
    opcional — más un ``razonamiento`` breve del análisis.
    """
    sistema = (
        "Eres el motor predictivo de Nous, una memoria temporal 4D. "
        "Recibes un resumen estadístico de la línea de universo del "
        "usuario (dimensiones cognitiva/somática/conductual, metas y "
        "compromisos abiertos). Proyecta escenarios de futuro "
        "plausibles a "
        f"{horizonte_dias} días. Responde SOLO con JSON válido: "
        '{"escenarios": [{"nombre": str, "probabilidad": 0..1, '
        '"descripcion": str (máx 200 chars), "sustrato": '
        '"cognitiva|somatica|conductual|null"}], "razonamiento": str '
        "(máx 400 chars)}. Entre 2 y 6 escenarios, probabilidades "
        "realistas (no todas altas), descripciones en español."
    )
    datos: dict[str, Any] = {
        "contexto": contexto,
        "metas_activas": metas,
        "compromisos_abiertos": loops,
    }
    if candidatos:
        datos["candidatos_heuristicos"] = candidatos
        datos["instruccion"] = (
            "Ajusta estos candidatos: conserva sus ids y nombres, pero "
            "corrige probabilidad y descripción según tu análisis. "
            "Puedes añadir hasta 2 escenarios que falten."
        )
    mensajes: list[dict[str, str]] = [
        {"role": "system", "content": sistema},
        {"role": "user", "content": json.dumps(datos, ensure_ascii=False)},
    ]
    return mensajes


def _parsear_prediccion(texto: str) -> dict[str, Any] | None:
    """Extrae el JSON de la respuesta del LLM tolerando ruido.

    Acepta respuestas envueltas en fences ```json …``` o con texto
    alrededor del primer objeto ``{…}``. Devuelve None si no parsea.
    """
    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = re.sub(r"^```[a-zA-Z]*\s*", "", limpio)
        limpio = re.sub(r"\s*```$", "", limpio)
    try:
        datos = json.loads(limpio)
    except json.JSONDecodeError:
        inicio, fin = limpio.find("{"), limpio.rfind("}")
        if inicio < 0 or fin <= inicio:
            return None
        try:
            datos = json.loads(limpio[inicio : fin + 1])
        except json.JSONDecodeError:
            return None
    escenarios = datos.get("escenarios")
    if not isinstance(datos, dict) or not isinstance(escenarios, list):
        return None
    normalizados = []
    for e in escenarios[:6]:
        if not isinstance(e, dict) or "nombre" not in e:
            continue
        prob = e.get("probabilidad", 0.1)
        try:
            prob = min(0.95, max(0.01, float(prob)))
        except (TypeError, ValueError):
            prob = 0.1
        normalizados.append(
            {
                "id": e.get("id"),
                "nombre": str(e["nombre"])[:60],
                "probabilidad": prob,
                "descripcion": str(e.get("descripcion", ""))[:400],
                "sustrato": e.get("sustrato")
                if e.get("sustrato") in ("cognitiva", "somatica", "conductual")
                else None,
            }
        )
    if not normalizados:
        return None
    return {
        "escenarios": normalizados,
        "razonamiento": str(datos.get("razonamiento", ""))[:600],
    }


async def predecir_escenarios(
    session: AsyncSession,
    *,
    origen: int,
    horizonte_dias: int,
    eventos: list[dict[str, Any]],
    metas: list[dict[str, Any]],
    loops_abiertos: int,
    candidatos: list[dict[str, Any]] | None = None,
    proveedor_id: str | None = None,
) -> dict[str, Any]:
    """Genera escenarios de futuro con el proveedor de IA habilitado.

    Args:
        session: Sesión de base de datos.
        origen: Instante Unix desde el que se proyecta.
        horizonte_dias: Días de proyección hacia adelante.
        eventos: Historial como dicts {ts, dimension, amplitud, contenido}.
        metas: Objetivos activos {titulo, fecha_objetivo}.
        loops_abiertos: Compromisos sin resolver en el origen.
        candidatos: Candidatos heurísticos a re-ponderar (opcional).
        proveedor_id: Fuerza un proveedor concreto (opcional).

    Returns:
        ``{"ok": True, "escenarios": [...], "razonamiento": str,
        "proveedor": str, "modelo": str}`` o ``{"ok": False,
        "motivo": str}`` — nunca propaga errores de red/LLM: el
        consumidor siempre puede caer a la heurística local.
    """
    proveedor = await _proveedor_activo(session, proveedor_id)
    if proveedor is None:
        return {"ok": False, "motivo": "sin_proveedor_habilitado"}

    contexto = _resumen_contexto(
        eventos, origen, bool(proveedor.get("incluir_contenido"))
    )
    mensajes = _prompt_prediccion(
        contexto, metas, loops_abiertos, horizonte_dias, candidatos
    )
    payload = {
        "model": proveedor["modelo"],
        "messages": mensajes,
        "temperature": 0.35,
        "max_tokens": 1400,
        "response_format": {"type": "json_object"},
    }
    inicio = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_PREDICCION) as cliente:
            resp = await cliente.post(
                f"{proveedor['base_url']}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {proveedor['api_key']}"},
            )
    except httpx.HTTPError as e:
        logger.warning("Predicción IA falló (red): %s", type(e).__name__)
        return {"ok": False, "motivo": f"error_red: {type(e).__name__}"}
    latencia_ms = int((time.monotonic() - inicio) * 1000)
    if resp.status_code != 200:
        logger.warning("Predicción IA HTTP %s", resp.status_code)
        return {"ok": False, "motivo": f"http_{resp.status_code}"}
    try:
        contenido = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError):
        return {"ok": False, "motivo": "respuesta_malformada"}
    parseada = _parsear_prediccion(str(contenido))
    if parseada is None:
        return {"ok": False, "motivo": "json_invalido"}
    return {
        "ok": True,
        **parseada,
        "proveedor": proveedor["nombre"],
        "modelo": proveedor["modelo"],
        "latencia_ms": latencia_ms,
    }
