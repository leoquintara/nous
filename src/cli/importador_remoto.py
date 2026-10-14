"""Importación remota vía API MCP (streamable-HTTP) desde otras memorias.

``gbrain`` y ``memlord`` son servidores MCP propios del usuario que
sirven HTTP con Bearer token — en vez de pedirle un export manual, Nous
se conecta a la instancia (local o remota), descubre sus tools con
``tools/list`` y descarga las memorias llamándolos.

Protocolo: JSON-RPC 2.0 sobre POST. El servidor puede responder
``application/json`` o ``text/event-stream``; tras ``initialize`` se
reutiliza el header ``Mcp-Session-Id`` si el servidor lo emite.

Privacidad: la conexión la dispara el usuario desde /importar con la
URL y el token que él mismo configura — nada sale sin gesto explícito.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import httpx

from src.cli.transferencia import FilaEvento

logger = logging.getLogger("nous.cli.importador_remoto")

_PROTOCOLO_MCP = "2025-03-26"
_MAX_PAGINAS_GBRAIN = 500
_MAX_FACTS_GBRAIN = 500
_MAX_MEMORIAS = 2_000

# Tools candidatos para listar memorias en memlord (por orden de
# preferencia); se elige el primero que el servidor anuncie en
# tools/list.
_TOOLS_MEMORIAS = (
    "list_memories",
    "export_memories",
    "dump_memories",
    "retrieve_memory",
    "recall_memory",
    "recall",
    "search_memory",
    "get_memories",
)


class ErrorRemoto(Exception):
    """Fallo al hablar con la instancia MCP remota."""


class ClienteMCP:
    """Cliente mínimo MCP streamable-HTTP para importaciones puntuales."""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        timeout: float = 60.0,
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._sesion: str | None = None
        self._seq = 0
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            transport=transporte,
        )

    async def __aenter__(self) -> ClienteMCP:
        await self._inicializar()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _inicializar(self) -> None:
        """Handshake MCP: initialize + notifications/initialized."""
        resp = await self._post(
            "initialize",
            {
                "protocolVersion": _PROTOCOLO_MCP,
                "capabilities": {},
                "clientInfo": {"name": "nous-importer", "version": "1.0"},
            },
        )
        self._sesion = resp.headers.get("mcp-session-id")
        # Notificación: sin id; algunos servidores la exigen antes de tools/*.
        try:
            await self._post(
                "notifications/initialized", {}, notificacion=True
            )
        except ErrorRemoto:
            pass

    async def listar_tools(self) -> dict[str, dict[str, Any]]:
        """Devuelve {nombre: schema} de las tools que anuncia el servidor."""
        datos = await self.llamar_metodo("tools/list", {})
        return {t["name"]: t for t in datos.get("tools", []) if "name" in t}

    async def llamar(self, tool: str, args: dict[str, Any]) -> Any:
        """Invoca ``tools/call`` y devuelve el payload parseado."""
        datos = await self.llamar_metodo(
            "tools/call", {"name": tool, "arguments": args}
        )
        if datos.get("isError"):
            raise ErrorRemoto(f"tool {tool} rechazada: {_texto(datos)[:200]}")
        if "structuredContent" in datos:
            return datos["structuredContent"]
        return _parsear_contenido(datos)

    async def llamar_metodo(
        self, metodo: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """POST JSON-RPC y devuelve el objeto ``result``."""
        resp = await self._post(metodo, params)
        cuerpo = _cuerpo_jsonrpc(resp)
        if "error" in cuerpo:
            raise ErrorRemoto(
                f"{metodo}: {cuerpo['error'].get('message', 'error JSON-RPC')}"
            )
        resultado = cuerpo.get("result")
        return resultado if isinstance(resultado, dict) else {}

    async def _post(
        self, metodo: str, params: dict[str, Any], notificacion: bool = False
    ) -> httpx.Response:
        self._seq += 1
        mensaje: dict[str, Any] = {"jsonrpc": "2.0", "method": metodo}
        if not notificacion:
            mensaje["id"] = self._seq
        if params:
            mensaje["params"] = params
        headers = (
            {"mcp-session-id": self._sesion} if self._sesion else {}
        )
        try:
            resp = await self._http.post(
                self._url, json=mensaje, headers=headers
            )
        except httpx.HTTPError as exc:
            raise ErrorRemoto(f"servidor inalcanzable: {exc}") from exc
        if resp.status_code >= 400:
            raise ErrorRemoto(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp


def _cuerpo_jsonrpc(resp: httpx.Response) -> dict[str, Any]:
    """Extrae el JSON-RPC del body — JSON plano o stream SSE."""
    tipo = resp.headers.get("content-type", "")
    if "text/event-stream" in tipo:
        ultimo: dict[str, Any] | None = None
        for linea in resp.text.splitlines():
            if linea.startswith("data:"):
                try:
                    ultimo = json.loads(linea[5:].strip())
                except json.JSONDecodeError:
                    continue
        if ultimo is not None:
            return ultimo
        raise ErrorRemoto("respuesta SSE sin evento JSON válido")
    try:
        cuerpo = resp.json()
    except json.JSONDecodeError as exc:
        raise ErrorRemoto("respuesta no es JSON-RPC") from exc
    # Las notificaciones pueden devolver 202 sin body útil.
    return cuerpo if isinstance(cuerpo, dict) else {}


def _texto(datos: dict[str, Any]) -> str:
    """Concatena los bloques text de un result MCP."""
    partes = [
        b.get("text", "")
        for b in datos.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return "\n".join(p for p in partes if p)


def _parsear_contenido(datos: dict[str, Any]) -> Any:
    """Parsea el text de un result: JSON si lo es, si no el texto crudo."""
    texto = _texto(datos).strip()
    if not texto:
        return datos
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return texto


def _extraer_lista(datos: Any, claves: tuple[str, ...]) -> list[dict[str, Any]]:
    """Encuentra la lista de filas dentro de una respuesta MCP variable."""
    if isinstance(datos, list):
        return [d for d in datos if isinstance(d, dict)]
    if not isinstance(datos, dict):
        return []
    for clave in claves:
        valor = datos.get(clave)
        if isinstance(valor, list):
            return [d for d in valor if isinstance(d, dict)]
    for valor in datos.values():
        if isinstance(valor, list) and valor and isinstance(valor[0], dict):
            return valor
    return []


async def leer_fuente_remota(
    url: str,
    token: str | None,
    formato: str,
    fuente: str,
    transporte: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Conecta con una instancia MCP remota y traduce sus memorias.

    Args:
        url: Endpoint MCP streamable-HTTP (p. ej. ``http://host:8477/mcp``).
        token: Bearer token de la instancia (None si no lo requiere).
        formato: ``gbrain`` o ``memlord``.
        fuente: Nombre a grabar en ``eventos.fuente``.

    Returns:
        (filas, vínculos por índice, nº de errores) — igual que
        ``leer_fuente`` de importadores.

    Raises:
        ErrorRemoto: Si el servidor no responde o rechaza la conexión.
    """
    async with ClienteMCP(url, token, transporte=transporte) as cli:
        if formato == "gbrain":
            return await _leer_gbrain_api(cli, fuente)
        if formato == "memlord":
            return await _leer_memlord_api(cli, fuente)
        raise ErrorRemoto(f"importación remota no soportada: {formato}")


async def _leer_gbrain_api(
    cli: ClienteMCP, fuente: str
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Descarga páginas gbrain (list_pages paginado → get_page) y facts."""
    from src.cli.importadores import _fila_base

    filas: list[FilaEvento] = []
    errores = 0
    slugs: list[str] = []
    offset = 0
    while len(slugs) < _MAX_PAGINAS_GBRAIN:
        datos = await cli.llamar(
            "list_pages",
            {"limit": 100, "offset": offset, "sort": "created_desc"},
        )
        paginas = _extraer_lista(datos, ("pages", "items", "results"))
        if not paginas:
            break
        slugs += [str(p["slug"]) for p in paginas if p.get("slug")]
        if len(paginas) < 100:
            break
        offset += len(paginas)
    for slug in slugs[:_MAX_PAGINAS_GBRAIN]:
        try:
            pagina = await cli.llamar(
                "get_page", {"slug": slug, "include_content": True}
            )
            cuerpo = _contenido_pagina(pagina)
            if not cuerpo:
                continue
            filas.append(
                _fila_base(
                    f"{slug}\n\n{cuerpo}",
                    fuente,
                    ts=_ts_pagina(pagina),
                    evento_id=str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"gbrain:{slug}")
                    ),
                )
            )
        except (ErrorRemoto, ValueError) as exc:
            logger.warning("Página gbrain descartada %s: %s", slug, exc)
            errores += 1
    errores += await _leer_facts_gbrain(cli, fuente, filas)
    return filas, [], errores


async def _leer_facts_gbrain(
    cli: ClienteMCP, fuente: str, filas: list[FilaEvento]
) -> int:
    """Intenta exportar los facts globales (recall sin entidad)."""
    from src.cli.importadores import _fila_base

    errores = 0
    try:
        datos = await cli.llamar(
            "recall", {"since": "1970-01-01", "budget_tokens": 200_000}
        )
    except ErrorRemoto:
        return 0
    for f in _extraer_lista(datos, ("facts", "items", "results"))[
        :_MAX_FACTS_GBRAIN
    ]:
        try:
            texto = str(f.get("fact") or f.get("content") or "")
            if not texto:
                continue
            fid = f.get("fact_id") or f.get("id") or texto[:64]
            filas.append(
                _fila_base(
                    texto,
                    fuente,
                    f.get("tags") if isinstance(f.get("tags"), list) else None,
                    ts=_ts_iso(f.get("created_at") or f.get("ts")),
                    evento_id=str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"gbrain-fact:{fid}")
                    ),
                )
            )
        except ValueError as exc:
            logger.warning("Fact gbrain descartado: %s", exc)
            errores += 1
    return errores


def _contenido_pagina(pagina: Any) -> str:
    """Saca el markdown de la respuesta de get_page."""
    if isinstance(pagina, str):
        return pagina
    if not isinstance(pagina, dict):
        return ""
    for clave in ("content", "markdown", "body", "text"):
        valor = pagina.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    pagina_interior = pagina.get("page")
    if isinstance(pagina_interior, dict):
        return _contenido_pagina(pagina_interior)
    return ""


def _ts_pagina(pagina: Any) -> int | None:
    """Timestamp de la página: updated_at/created_at ISO o epoch."""
    if not isinstance(pagina, dict):
        return None
    pagina_interior = pagina.get("page")
    if isinstance(pagina_interior, dict) and not any(
        k in pagina for k in ("updated_at", "created_at")
    ):
        return _ts_pagina(pagina_interior)
    return _ts_iso(pagina.get("updated_at") or pagina.get("created_at"))


def _ts_iso(valor: Any) -> int | None:
    """Convierte ISO 8601 o epoch a timestamp unix; None si no parsea."""
    if valor is None:
        return None
    if isinstance(valor, int | float):
        return int(valor)
    if not isinstance(valor, str):
        return None
    texto = valor.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(texto).timestamp())
    except ValueError:
        return None


async def _leer_memlord_api(
    cli: ClienteMCP, fuente: str
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Descarga memorias memlord: list_memories paginado → get_memory.

    ``list_memories`` devuelve registros slim (sin contenido), así que
    el cuerpo se pide con ``get_memory(name, workspace)`` por nombre —
    N+1 deliberado, es una migración puntual.
    """
    from src.cli.importadores import _fila_desde_obj

    tools = await cli.listar_tools()
    if "list_memories" in tools and "get_memory" in tools:
        return await _memlord_paginas(cli, fuente)
    # Fallback: cualquier otra tool de listado que anuncie la instancia.
    tool = next((t for t in _TOOLS_MEMORIAS if t in tools), None)
    if tool is None:
        raise ErrorRemoto(
            "la instancia no expone una tool de listado de memorias "
            f"(tiene: {', '.join(sorted(tools)[:8])}…)"
        )
    workspaces = await _workspaces_memlord(cli, tools)
    filas: list[FilaEvento] = []
    errores = 0
    destinos: list[str | None] = list(workspaces) or [None]
    for ws in destinos:
        args = _args_memoria(tools[tool], ws)
        try:
            datos = await cli.llamar(tool, args)
        except ErrorRemoto as exc:
            logger.warning("listado memlord falló (%s): %s", ws, exc)
            errores += 1
            continue
        for obj in _extraer_lista(
            datos, ("memories", "items", "results", "data")
        )[:_MAX_MEMORIAS]:
            try:
                filas.append(_fila_desde_obj(obj, fuente, "memlord"))
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                logger.warning("Memoria memlord descartada: %s", exc)
                errores += 1
    return filas, [], errores


async def _memlord_paginas(
    cli: ClienteMCP,
    fuente: str,
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Recorre list_memories por páginas y pide el contenido por nombre."""
    filas: list[FilaEvento] = []
    errores = 0
    vistos = 0
    pagina = 1
    while len(filas) < _MAX_MEMORIAS:
        datos = await cli.llamar(
            "list_memories", {"page": pagina, "page_size": 100}
        )
        items = _extraer_lista(datos, ("items", "memories", "results"))
        total = datos.get("total") if isinstance(datos, dict) else None
        if not items:
            break
        vistos += len(items)
        for item in items:
            nombre = item.get("name")
            if not nombre:
                continue
            try:
                detalle = await cli.llamar(
                    "get_memory",
                    {
                        "name": nombre,
                        **(
                            {"workspace": str(item["workspace"])}
                            if item.get("workspace")
                            else {}
                        ),
                    },
                )
                filas.append(_fila_memlord(detalle or item, fuente))
            except (ErrorRemoto, ValueError) as exc:
                logger.warning("Memoria memlord descartada: %s", exc)
                errores += 1
        if len(items) < 100 or (
            isinstance(total, int | float) and vistos >= int(total)
        ):
            break
        pagina += 1
    return filas, [], errores


def _fila_memlord(obj: dict[str, Any], fuente: str) -> FilaEvento:
    """Normaliza un MemoryDetail de memlord a FilaEvento con id estable."""
    from src.cli.importadores import _fila_base
    nombre = str(obj.get("name") or "")
    contenido = str(obj.get("content") or obj.get("text") or "")
    if not contenido:
        raise ValueError("memoria sin contenido")
    tags = obj.get("tags")
    lista_tags = list(tags) if isinstance(tags, list | set | tuple) else []
    workspace = obj.get("workspace") or obj.get("workspace_name")
    if workspace:
        lista_tags.append(str(workspace))
    return _fila_base(
        f"{nombre}\n\n{contenido}".strip(),
        fuente,
        lista_tags or None,
        ts=_ts_iso(obj.get("created_at")),
        amplitud=0.6 if obj.get("memory_type") == "decision" else 0.5,
        evento_id=str(
            uuid.uuid5(
                uuid.NAMESPACE_URL, f"memlord:{workspace or ''}:{nombre}"
            )
        ),
    )


async def _workspaces_memlord(
    cli: ClienteMCP, tools: dict[str, dict[str, Any]]
) -> list[str]:
    """Lista los workspaces si la instancia anuncia list_workspaces."""
    if "list_workspaces" not in tools:
        return []
    try:
        datos = await cli.llamar("list_workspaces", {})
    except ErrorRemoto:
        return []
    nombres: list[str] = []
    for w in _extraer_lista(datos, ("workspaces", "items", "results")):
        nombre = w.get("name") or w.get("workspace") or w.get("slug")
        if nombre:
            nombres.append(str(nombre))
    if not nombres and isinstance(datos, str):
        nombres = [
            linea.strip() for linea in datos.splitlines() if linea.strip()
        ]
    return nombres


def _args_memoria(
    schema: dict[str, Any], workspace: str | None
) -> dict[str, Any]:
    """Construye los args del tool de listado según su inputSchema."""
    props: dict[str, Any] = (
        schema.get("inputSchema", {}).get("properties", {})
    )
    args: dict[str, Any] = {}
    for nombre in props:
        clave = nombre.lower()
        if workspace and re.search(r"workspace|ws|space", clave):
            args[nombre] = workspace
        elif re.search(r"limit|max|top_k|per_page", clave):
            args[nombre] = _MAX_MEMORIAS
        elif re.search(r"query|search|q$", clave):
            args[nombre] = ""
    return args
