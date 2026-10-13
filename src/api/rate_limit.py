"""Rate limiting en memoria para endpoints sensibles (anti fuerza bruta).

Ventana deslizante por clave (IP de cliente): pensado para ``/api/login``
y cualquier endpoint que valide credenciales. En memoria es suficiente
para el modelo local-first de Nous (un proceso, single-user); no se
persiste nada y el estado muere con el proceso.
"""

import time
from collections import defaultdict, deque


class LimitadorIntentos:
    """Ventana deslizante de intentos por clave.

    Registra timestamps de intentos recientes por clave y permite
    consultar si el siguiente intento excede el cupo de la ventana.
    """

    def __init__(self, max_intentos: int = 5, ventana_segundos: int = 60) -> None:
        """Configura el cupo y la ventana temporal.

        Args:
            max_intentos: Intentos permitidos dentro de la ventana.
            ventana_segundos: Duración de la ventana deslizante.
        """
        self._max = max_intentos
        self._ventana = ventana_segundos
        self._intentos: dict[str, deque[float]] = defaultdict(deque)

    def permite(self, clave: str, ahora: float | None = None) -> bool:
        """Registra un intento e indica si entra en el cupo.

        Args:
            clave: Identidad del cliente (IP o similar).
            ahora: Timestamp de referencia (None = tiempo actual).

        Returns:
            True si el intento está permitido, False si excede el cupo.
        """
        ahora = time.monotonic() if ahora is None else ahora
        cola = self._intentos[clave]
        while cola and ahora - cola[0] > self._ventana:
            cola.popleft()
        if len(cola) >= self._max:
            return False
        cola.append(ahora)
        return True

    def restantes(self, clave: str, ahora: float | None = None) -> int:
        """Devuelve los intentos restantes de una clave en la ventana."""
        ahora = time.monotonic() if ahora is None else ahora
        cola = self._intentos.get(clave)
        if cola is None:
            return self._max
        while cola and ahora - cola[0] > self._ventana:
            cola.popleft()
        return max(0, self._max - len(cola))

    def reiniciar(self) -> None:
        """Borra todos los contadores (tests y rotación de estado)."""
        self._intentos.clear()
