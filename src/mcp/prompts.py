"""Prompts MCP de Nous: flujos guiados de reflexión y análisis.

A diferencia de tools (acciones) y resources (datos), los prompts son
plantillas de instrucciones que el cliente inyecta en su conversación
para orquestar un uso coherente de la memoria 4D. Cada prompt indica
qué resources leer y qué tools invocar, en qué orden.
"""

from mcp.server.fastmcp.prompts import base

from src.mcp.server import mcp_server


@mcp_server.prompt()
def reflexion_diaria() -> list[base.Message]:
    """Guía una reflexión estructurada sobre la actividad del día.

    USAR CUANDO: el usuario pida cerrar su día, hacer un check-in o
    revisar cómo le fue hoy. Orquesta resources de solo lectura y una
    escritura opcional al final.

    Returns:
        Mensaje con instrucciones paso a paso para el agente.
    """
    return [
        base.UserMessage(
            "Guía una reflexión diaria usando la memoria de Nous:\n"
            "1. Lee el resource memoria://resumen/diario para ver la "
            "actividad de hoy por dimensión.\n"
            "2. Lee memoria://patrones/flow y menciona solo las "
            "trayectorias activas relevantes a hoy.\n"
            "3. Resume en 3 líneas: qué dominó (cognitiva/somática/"
            "conductual), qué faltó, y el evento de mayor amplitud.\n"
            "4. Haz UNA pregunta de reflexión al usuario sobre su día.\n"
            "5. Solo si el usuario responde algo nuevo, guárdalo con "
            "la tool recordar_evento (no dupliques lo ya registrado)."
        )
    ]


@mcp_server.prompt()
def analisis_semanal() -> list[base.Message]:
    """Guía un análisis de patrones de la última semana.

    USAR CUANDO: el usuario pida una retrospectiva semanal o quiera
    entender sus patrones recientes. Es análisis, no solo resumen:
    debe cruzar dimensiones y detectar correlaciones.

    Returns:
        Mensaje con instrucciones paso a paso para el agente.
    """
    return [
        base.UserMessage(
            "Realiza un análisis semanal usando la memoria de Nous:\n"
            "1. Lee memoria://resumen/semanal para los agregados por "
            "dimensión.\n"
            "2. Lee memoria://patrones/flow para las rachas de los "
            "últimos 30 días.\n"
            "3. Lee memoria://proyeccion/actual y úsala como punto de "
            "partida (es una extrapolación, no una certeza).\n"
            "4. Identifica: la dimensión dominante, rachas que se "
            "repiten, y posibles correlaciones (p. ej. picos "
            "somáticos tras rachas conductuales).\n"
            "5. Cierra con 2 observaciones accionables. Si el usuario "
            "quiere extrapolar, usa la tool proyectar_trayectoria."
        )
    ]


@mcp_server.prompt()
def preparar_decision(tema: str) -> list[base.Message]:
    """Guía la recuperación de contexto antes de tomar una decisión.

    USAR CUANDO: el usuario vaya a decidir algo y quiera consultar
    su memoria relevante antes. Requiere que el agente busque con la
    tool consultar_memoria, no solo leer resources.

    Args:
        tema: Tema de la decisión (p. ej. "cambiar de stack").

    Returns:
        Mensaje con instrucciones paso a paso para el agente.
    """
    return [
        base.UserMessage(
            f"Prepara el contexto para decidir sobre: {tema}\n"
            f"1. Invoca la tool consultar_memoria con '{tema}' y "
            "top_n=5 para traer memorias relevantes.\n"
            "2. De cada resultado, considera el contexto_temporal: "
            "qué pasó antes y después de esa memoria.\n"
            "3. Si la búsqueda devuelve poco, reformula el tema con "
            "sinónimos y consulta de nuevo (una vez).\n"
            "4. Resume: qué pensé/decidí antes sobre esto, cómo me "
            "sentí (dimensión somática) y qué hice (conductual).\n"
            "5. NO decidas por el usuario: presenta el contexto y "
            "termina con una pregunta que aclare el trade-off."
        )
    ]


@mcp_server.prompt()
def evaluar_decision(dilema: str, opciones: str = "") -> list[base.Message]:
    """Evalúa un dilema con el consejero prescriptivo de Nous (Pro).

    USAR CUANDO: el usuario pida ayuda para decidir entre opciones
    concretas y quiera el veredicto calibrado alimentado por su línea
    de universo. Orquesta contexto + ``consultar_decision`` y fija
    cómo presentar el resultado sin usurpar la decisión.

    Args:
        dilema: La decisión planteada ("¿acepto el proyecto X?").
        opciones: Opciones separadas por coma; vacío = sí/no.

    Returns:
        Mensaje con instrucciones paso a paso para el agente.
    """
    paso_opciones = (
        f"las opciones: {opciones}"
        if opciones
        else "las opciones por defecto ['sí', 'no'] — pregunta al "
        "usuario si hay opciones concretas que no mencionó"
    )
    return [
        base.UserMessage(
            f"Evalúa el dilema con la memoria 4D de Nous: {dilema}\n"
            f"1. Invoca la tool consultar_decision con el dilema y "
            f"{paso_opciones}.\n"
            "2. Si devuelve licencia_requerida/402, explica que es "
            "feature Pro y ofrece el contexto igualmente: invoca "
            "consultar_memoria con el dilema (top_n=5) y "
            "compromisos_abiertos() para los loops que pesan.\n"
            "3. Si disponible=false (sin provider), el dict trae el "
            "contexto ensamblado — úsalo para razonar y dilo.\n"
            "4. Con veredicto: presenta recomendacion + probabilidad "
            "+ distribucion por opción, menciona riesgo_oculto si "
            ">0.5 y la confianza del veredicto.\n"
            "5. El modelo opina con datos; quien decide es el "
            "usuario — cierra preguntando qué factor pesa más "
            "para él."
        )
    ]
