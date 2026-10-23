"""Tests unitarios de semanticización — el sueño destila gists."""

import uuid

from src.core.memoria.semanticizacion import (
    MIN_CLUSTER,
    EpisodioGist,
    agrupar_episodios,
    plan_gists,
)


def _ep(contenido: str, dim_dominante: int, n: int = 8) -> EpisodioGist:
    """Episodio con embedding one-hot en la dimensión dada."""
    emb = [1.0 if i == dim_dominante else 0.0 for i in range(n)]
    return EpisodioGist(
        id=str(uuid.uuid4()),
        contenido=contenido,
        weight=1.0,
        embedding=emb,
    )


def test_agrupar_episodios_junta_afines() -> None:
    """Embeddings casi iguales caen en el mismo cluster."""
    a = _ep("deploy lunes 1", 0)
    b = _ep("deploy lunes 2", 0)
    c = _ep("comprar pan", 5)
    clusters = agrupar_episodios([a, b, c])
    tamanos = sorted(len(c_) for c_ in clusters)
    assert tamanos == [1, 2]


def test_episodio_sin_embedding_no_entra_al_cluster() -> None:
    """Sin vector no hay similitud — queda fuera del gist."""
    sin_emb = EpisodioGist(
        id=str(uuid.uuid4()), contenido="x", weight=1.0, embedding=None
    )
    clusters = agrupar_episodios([_ep("a", 0), sin_emb])
    assert sum(len(c) for c in clusters) == 1


def test_plan_gists_exige_min_cluster() -> None:
    """Dos episodios son anécdota; el gist exige patrón (≥3)."""
    par = [_ep(f"deploy {i}", 0) for i in range(MIN_CLUSTER - 1)]
    assert plan_gists(par) == []


def test_plan_gists_destila_medoide_como_contenido() -> None:
    """El gist usa texto real del medoide — abstracción honesta."""
    cluster = [_ep(f"deploy del lunes #{i}", 0) for i in range(4)]
    planes = plan_gists(cluster)
    assert len(planes) == 1
    plan = planes[0]
    assert plan.contenido.startswith("Gist de 4 episodios:")
    assert "deploy del lunes" in plan.contenido
    assert len(plan.miembros) == 4
    assert {e.id for e in cluster} == set(plan.miembros)


def test_plan_gists_ordena_por_cluster_mayor() -> None:
    """Varios clusters: primero el patrón con más evidencia."""
    grande = [_ep(f"deploy {i}", 0) for i in range(5)]
    chico = [_ep(f"gym {i}", 5) for i in range(3)]
    planes = plan_gists(grande + chico)
    assert len(planes) == 2
    assert "deploy" in planes[0].contenido
    assert "gym" in planes[1].contenido


def test_plan_gists_respeta_maximo_por_corrida() -> None:
    """El sueño no spammea: tope de gists por corrida."""
    eps = [
        _ep(f"tema{d} #{i}", d % 8)
        for d in range(8)
        for i in range(3)
    ]
    planes = plan_gists(eps, max_gists=2)
    assert len(planes) == 2
