"""Troceo de contenido largo para retrieval a nivel fragmento.

La memoria guarda eventos completos, pero un documento de 8k embebido
como un solo vector promedia su señal temática — una consulta precisa
sobre una sección pierde contra textos cortos densos en el término.
Trocear conserva el evento como unidad de memoria y le da a la
búsqueda fragmentos con topicalidad concentrada (paridad con los
``content_chunks`` del retrieval de gbrain).

Puro: texto entra, fragmentos salen — la capa de storage decide
persistirlos y embeberlos.
"""

import re

# Tamaño preferido por fragmento en caracteres (MiniLM rinde bien
# hasta ~512 tokens ≈ 2k caracteres españoles).
TAMANO_OBJETIVO = 1200
# Techo duro: una sección que exceda esto se rebana por líneas.
TAMANO_MAXIMO = 2000
# Líneas de contexto solapadas al rebanar secciones muy largas.
SOLAPE_CARACTERES = 150

_ENCABEZADO_ATX = re.compile(r"(?m)^#{1,6}\s")


def trocear(
    contenido: str,
    objetivo: int = TAMANO_OBJETIVO,
    maximo: int = TAMANO_MAXIMO,
    solape: int = SOLAPE_CARACTERES,
) -> list[str]:
    """Divide el contenido en fragmentos ≤ ``maximo`` respetando secciones.

    Parte por encabezados ATX (``## …``) y empaca secciones en
    fragmentos de hasta ``objetivo`` caracteres; una sección que por
    sí sola supere ``maximo`` se rebana por líneas con ``solape`` de
    contexto entre piezas.

    Args:
        contenido: Texto completo del evento.
        objetivo: Tamaño preferido por fragmento en caracteres.
        maximo: Techo duro por fragmento en caracteres.
        solape: Caracteres de cola retenidos al rebanar secciones.

    Returns:
        Lista de fragmentos no vacíos; ``[contenido]`` si cabe entero,
        ``[]`` si el contenido está vacío.
    """
    texto = contenido.strip()
    if not texto:
        return []
    if len(texto) <= objetivo:
        return [texto]
    chunks: list[str] = []
    actual = ""
    for seccion in _secciones(texto):
        for parte in _rebanar(seccion, maximo, solape):
            if actual and len(actual) + 1 + len(parte) > objetivo:
                chunks.append(actual)
                actual = ""
            actual = f"{actual}\n{parte}" if actual else parte
    if actual:
        chunks.append(actual)
    return chunks


def _secciones(texto: str) -> list[str]:
    """Corta el texto en los encabezados ATX, conservándolos en su sección."""
    cortes = [m.start() for m in _ENCABEZADO_ATX.finditer(texto)]
    if not cortes:
        return [texto]
    if cortes[0] != 0:
        cortes = [0, *cortes]
    secciones = []
    for i, ini in enumerate(cortes):
        fin = cortes[i + 1] if i + 1 < len(cortes) else len(texto)
        tramo = texto[ini:fin].strip()
        if tramo:
            secciones.append(tramo)
    return secciones


def _rebanar(texto: str, maximo: int, solape: int) -> list[str]:
    """Rebana una sección que excede ``maximo`` por líneas con solape."""
    if len(texto) <= maximo:
        return [texto]
    piezas: list[str] = []
    actual = ""
    for linea in texto.split("\n"):
        if actual and len(actual) + 1 + len(linea) > maximo:
            piezas.append(actual)
            cola: list[str] = []
            restante = solape
            for previa in reversed(actual.split("\n")):
                if restante <= 0:
                    break
                cola.insert(0, previa)
                restante -= len(previa) + 1
            actual = "\n".join([*cola, linea])
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        piezas.append(actual)
    return piezas
