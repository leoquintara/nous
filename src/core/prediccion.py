"""Fachada pública de proyección futura.

La lógica vive en :mod:`src.core.temporal` (detección de rachas,
ponderación por recencia y boost de trayectoria activa). Este módulo
re-exporta la superficie de predicción para mantener la estructura
de ``core/`` prevista en la arquitectura sin duplicar implementación.
"""

from src.core.temporal import BOOST_RACHA_ACTIVA, VIDA_MEDIA_DIAS, proyectar

__all__ = ["BOOST_RACHA_ACTIVA", "VIDA_MEDIA_DIAS", "proyectar"]
