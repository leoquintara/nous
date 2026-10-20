"""Fixtures y shims de la suite unitaria.

Los tests de embeddings/reranker mockean ``sentence_transformers``
vía ``patch("sentence_transformers.X")`` — para que el patch
resuelva el target sin instalar el extra ``torch`` (pesado, ~2 GB)
se inyecta un módulo falso en ``sys.modules`` cuando la
dependencia real no está disponible.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock

try:
    import sentence_transformers  # noqa: F401
except ModuleNotFoundError:
    falso = ModuleType("sentence_transformers")
    falso.SentenceTransformer = MagicMock(name="SentenceTransformer")  # type: ignore[attr-defined]
    falso.CrossEncoder = MagicMock(name="CrossEncoder")  # type: ignore[attr-defined]
    sys.modules["sentence_transformers"] = falso
