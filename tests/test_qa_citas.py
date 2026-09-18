"""La verificación del QA, sin modelo: citas inexistentes y cifras huérfanas
rechazan; el umbral rechaza sin llamar al modelo; los hechos del informe se
arman con los ids que después se citan."""

from __future__ import annotations

from pathlib import Path

import pytest

from tasador.rag import qa
from tasador.rag.qa import Fragmento, Indice, RespuestaQA, verificar

PERMITIDOS = {
    "C-01": "Comparable C-01: Thames 1800 (PORTAL_A), USD 240.000, 78 m², USD 3.077/m² crudo.",
    "V": "Valuación: precio sugerido USD 265.000 (rango USD 240.000 a USD 290.000).",
}


def test_una_cita_que_no_se_le_paso_rechaza():
    r = RespuestaQA(respuesta="Se usó el de Thames [C-09].", citas=["C-09"], sin_evidencia=False)
    problemas = verificar(r, PERMITIDOS)
    assert any("no se le pasaron" in p for p in problemas)


def test_una_cifra_que_no_esta_en_lo_citado_rechaza():
    r = RespuestaQA(
        respuesta="El de Thames vale USD 312.000 [C-01].", citas=["C-01"], sin_evidencia=False
    )
    problemas = verificar(r, PERMITIDOS)
    assert any("312.000" in p for p in problemas)


def test_una_respuesta_con_cifras_de_lo_citado_pasa():
    r = RespuestaQA(
        respuesta="El de Thames se publicó a USD 240.000 y se usó [C-01]; "
        "el sugerido es USD 265.000 [V].",
        citas=["C-01", "V"],
        sin_evidencia=False,
    )
    assert verificar(r, PERMITIDOS) == []


def test_sin_citas_rechaza_y_sin_evidencia_no_se_verifica():
    assert verificar(RespuestaQA(respuesta="x", citas=[], sin_evidencia=False), PERMITIDOS)
    assert (
        verificar(RespuestaQA(respuesta="no está", citas=[], sin_evidencia=True), PERMITIDOS) == []
    )


def test_las_citas_son_requeridas_en_el_schema():
    assert "citas" in RespuestaQA.model_json_schema()["required"]


def test_una_cita_con_corchetes_es_la_misma_cita():
    r = RespuestaQA(
        respuesta="Se usaron 19 [N].", citas=["[N]", " C-01 ", "[]"], sin_evidencia=False
    )
    assert r.ids_citados() == ["N", "C-01"]
    permitidos = {"N": "Comparables: se usaron 19 y se descartaron 41.", "C-01": "x"}
    assert verificar(r, permitidos) == []


def test_los_hechos_del_informe_llevan_ids_estables_y_el_motivo_en_castellano():
    informe = {
        "valuation": {
            "suggested_listing_price": {"low": 240000, "mid": 265000, "high": 290000},
            "expected_closing_range": {"low": 225250, "high": 251750},
            "price_per_m2": 3397,
            "weighted_surface": 78.0,
        },
        "confidence": {"level": "ALTA", "score": 0.81, "notes": []},
        "comparables": {
            "found": 2,
            "used": 1,
            "excluded": 1,
            "items": [
                {
                    "address": "Thames 1800",
                    "source": "PORTAL_A",
                    "price": 240000,
                    "surface_weighted": 78,
                    "rooms": 3,
                    "raw_price_per_m2": 3077,
                    "adjusted_price_per_m2": 3150,
                    "included": True,
                    "adjustments": {"condition": 0.94, "total": 0.977},
                },
                {
                    "address": "Gorriti 5000",
                    "source": "PORTAL_B",
                    "price": 300000,
                    "surface_weighted": 70,
                    "raw_price_per_m2": 4286,
                    "included": False,
                    "exclusion_reason": "en_pozo_o_construccion",
                },
            ],
        },
        "market_context": {"stock": 8383, "usd_m2_mediano": 3839},
    }
    frags = qa.fragmentos_del_informe(informe)
    ids = [f.id for f in frags]
    assert ids == ["V", "CONF", "N", "C-01", "C-02", "M"]
    assert "Usado en la valuación" in frags[3].texto
    assert "pozo" in frags[4].texto and "en_pozo_o_construccion" in frags[4].texto


def test_la_metodologia_se_parte_por_encabezado():
    frags = qa.fragmentos_de_metodologia()
    assert len(frags) >= 8
    assert any(f.id.startswith("Met §4") for f in frags)
    assert all(f.fuente == "metodologia" for f in frags)
    assert qa.fragmentos_de_metodologia(Path("no-existe.md")) == []


def test_el_indice_fusiona_denso_y_lexico_y_devuelve_el_mejor_coseno():
    frags = [
        Fragmento("A", "cochera fija en el edificio", "informe"),
        Fragmento("B", "balcón al frente con vista", "informe"),
    ]
    idx = Indice(frags, [[1.0, 0.0], [0.0, 1.0]])
    top, mejor, mejor_lex = idx.buscar([0.9, 0.1], "cochera", k=2)
    assert top[0].id == "A" and mejor == pytest.approx(0.9 / (0.9**2 + 0.1**2) ** 0.5)
    assert mejor_lex == 1.0, "la única palabra útil de la pregunta está en A"


@pytest.mark.asyncio
async def test_por_debajo_del_umbral_se_rechaza_sin_llamar_al_modelo():
    class EmbedderFalso:
        async def consulta(self, texto: str) -> list[float]:
            return [0.0, 1.0]

    class ClienteQueNoDebeUsarse:
        async def structured(self, *a, **kw):  # type: ignore[no-untyped-def]
            raise AssertionError("no tenía que llamar al modelo")

    idx = Indice([Fragmento("A", "cochera fija", "informe")], [[1.0, 0.0]])
    r = await qa.responder(
        "¿cuánto va a valer en dos años?",
        idx,
        embedder=EmbedderFalso(),  # type: ignore[arg-type]
        cliente=ClienteQueNoDebeUsarse(),  # type: ignore[arg-type]
        umbral=0.5,
    )
    assert r.rechazada and r.citas == [] and "coseno" in (r.motivo or "")


@pytest.mark.asyncio
async def test_el_solapamiento_lexico_alcanza_para_no_rechazar_sin_modelo():
    """«¿Por qué no se usó Charcas 4900?» tiene coseno bajo con MiniLM pero
    nombra al comparable: eso es evidencia y el modelo tiene que decidir."""

    class EmbedderFalso:
        async def consulta(self, texto: str) -> list[float]:
            return [0.0, 1.0]

    class ClienteQueRechaza:
        async def structured(self, *a, **kw):  # type: ignore[no-untyped-def]
            return RespuestaQA(respuesta="no está", citas=[], sin_evidencia=True), []

    idx = Indice(
        [Fragmento("C-01", "Comparable C-01: Charcas 4900, descartado", "informe")], [[1.0, 0.0]]
    )
    r = await qa.responder(
        "¿Por qué no se usó el aviso de Charcas 4900?",
        idx,
        embedder=EmbedderFalso(),  # type: ignore[arg-type]
        cliente=ClienteQueRechaza(),  # type: ignore[arg-type]
        umbral=0.5,
    )
    assert r.rechazada and r.motivo == "el modelo no encontró evidencia"
