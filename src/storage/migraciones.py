"""Migraciones Alembic sin ``alembic.ini`` — para el paquete instalado.

El wheel no lleva ``alembic.ini`` (vive en la raíz del repo). Este
módulo construye el ``Config`` programáticamente apuntando a
``src/storage/migrations`` — que sí viaja en el paquete — de modo que
``nous db upgrade`` y ``nous upgrade`` funcionen tras ``pip install``
desde cualquier directorio. El ``env.py`` de las migraciones toma la
URL de ``NOUS_DATABASE_URL`` vía settings, así que el ini no aporta
nada más que el ``script_location``.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

_MIGRACIONES = Path(__file__).resolve().parent / "migrations"


def config_alembic() -> Config:
    """Devuelve un ``Config`` de Alembic apuntando al paquete.

    ``script_location`` se resuelve desde ``__file__`` — válido tanto
    en el repo como en site-packages. ``env.py`` completa el resto
    (``sqlalchemy.url`` desde settings).
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRACIONES))
    return cfg


def migrar_head() -> None:
    """Aplica ``upgrade head`` — idempotente, no-op si ya está al día."""
    command.upgrade(config_alembic(), "head")
