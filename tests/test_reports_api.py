"""El contrato HTTP de `/v1/reports` — doc 06 §2.

`v1/reports.py` estaba al **37% de cobertura** el 14/08, con 218 tests en el
repo. Ni el POST, ni el GET, ni la idempotencia, ni el `/pdf`, ni las
sugerencias del `INSUFFICIENT_DATA` tenían un test.

Es una asimetría que vale la pena nombrar: el motor de valuación —lo difícil de
razonar— está al 94%, y la superficie que consume el producto —lo fácil de
romper sin darse cuenta— no estaba probada. Los tests siguen a donde uno teme
equivocarse, no a donde uno se equivoca.

Lo que se prueba acá es lo que la UI necesita creer:

  · un 202 con `report_id` y `poll_url` que sirvan;
  · que dos clicks del botón no cuesten el doble;
  · que `INSUFFICIENT_DATA` sea 200 con sugerencias accionables y no un error;
  · que el progreso nodo a nodo salga en el orden del grafo;
  · que un PDF registrado pero ausente del disco NO devuelva 200.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def cliente(db: AsyncSession):
    from fastapi.testclient import TestClient

    from tasador.db.base import get_session
    from tasador.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def org(db: AsyncSession):
    from tasador.db.models import Organization

    o = Organization(name="la inmobiliaria", slug="inmo-demo")
    db.add(o)
    await db.commit()
    return o


PROPIEDAD = {
    "address_raw": "Av. Cabildo 2530",
    "property_type": "departamento",
    "rooms": 3,
    "surface_total": 95,
    "surface_covered": 88,
}


# ── POST ─────────────────────────────────────────────────────────────────
async def test_crear_informe_devuelve_202_y_lo_deja_encolado(db: AsyncSession, cliente, org):
    from sqlalchemy import select

    from tasador.db.models import Report

    r = cliente.post("/v1/reports", json={"property": PROPIEDAD})
    assert r.status_code == 202
    cuerpo = r.json()
    assert cuerpo["status"] == "QUEUED"
    assert cuerpo["poll_url"] == f"/v1/reports/{cuerpo['report_id']}"

    fila = (await db.execute(select(Report))).scalar_one()
    assert fila.org_id == org.id
    # Las tres versiones se estampan al CREAR, no al terminar: si se estamparan
    # al final, un informe que corrió con el bundle viejo y terminó después de
    # un deploy quedaría atribuido al nuevo, y el backtest compararía peras con
    # manzanas (doc 03 §3.5).
    assert fila.engine_version and fila.method_version and fila.prompt_bundle_version


async def test_la_idempotencia_no_cobra_dos_veces_el_mismo_click(db: AsyncSession, cliente, org):
    from sqlalchemy import func, select

    from tasador.db.models import Report

    cab = {"Idempotency-Key": "el-mismo-click"}
    primera = cliente.post("/v1/reports", json={"property": PROPIEDAD}, headers=cab)
    segunda = cliente.post("/v1/reports", json={"property": PROPIEDAD}, headers=cab)

    assert primera.status_code == 202
    # 200 y no 202: el segundo pedido no creó nada. El código de estado es la
    # forma de decirlo sin que el cliente tenga que comparar ids.
    assert segunda.status_code == 200
    assert primera.json()["report_id"] == segunda.json()["report_id"]

    assert (await db.execute(select(func.count()).select_from(Report))).scalar_one() == 1


async def test_superficie_cubierta_mayor_que_la_total_es_422(cliente, org):
    r = cliente.post(
        "/v1/reports",
        json={"property": {**PROPIEDAD, "surface_total": 60, "surface_covered": 90}},
    )
    assert r.status_code == 422
    assert "cubierta" in r.json()["detail"]


async def test_sin_organizacion_activa_da_401(db: AsyncSession, cliente):
    """Sin tenant no hay informe: ninguna consulta del sistema corre sin
    `org_id`, y eso no cambia cuando llegue la auth real."""
    r = cliente.post("/v1/reports", json={"property": PROPIEDAD})
    assert r.status_code == 401


async def test_con_varias_organizaciones_hay_que_decir_cual(db: AsyncSession, cliente, org):
    from tasador.db.models import Organization

    db.add(Organization(name="Otra", slug="otra"))
    await db.commit()

    ambiguo = cliente.post("/v1/reports", json={"property": PROPIEDAD})
    assert ambiguo.status_code == 400

    explicito = cliente.post(
        "/v1/reports", json={"property": PROPIEDAD}, headers={"X-Org-Slug": "otra"}
    )
    assert explicito.status_code == 202


# ── GET ──────────────────────────────────────────────────────────────────
async def _informe(db: AsyncSession, org_id: uuid.UUID, **campos):
    from tasador.db.models import Report, SubjectProperty

    sujeto = SubjectProperty(
        org_id=org_id,
        address_raw=campos.pop("address", "Av. Cabildo 2530"),
        property_type="departamento",
        rooms=campos.pop("rooms", 3),
        surface_total=campos.pop("surface_total", Decimal("95")),
    )
    db.add(sujeto)
    await db.flush()

    # El CHECK `valores_coherentes` hace IMPOSIBLE un SUCCEEDED sin rango, y con
    # low <= mid <= high. Es exactamente lo que tiene que pasar (doc 03 §3.5) y
    # la primera versión de este helper se lo llevó por delante: la base atajó
    # un informe "exitoso" vacío que la aplicación habría dejado pasar.
    if campos.get("status") == "SUCCEEDED":
        campos.setdefault("value_low", Decimal("240000"))
        campos.setdefault("value_mid", Decimal("283385"))
        campos.setdefault("value_high", Decimal("373326"))
    # Y el gemelo: `insufficient_tiene_razon` impide un "no hay datos" mudo.
    # Es el CHECK que garantiza que la pantalla de doc 07 §7 —la que EXPLICA en
    # vez de dar error— siempre tenga algo que explicar.
    if campos.get("status") == "INSUFFICIENT_DATA":
        campos.setdefault("insufficient_reason", "Hacen falta al menos 5 comparables.")

    informe = Report(
        org_id=org_id,
        subject_property_id=sujeto.id,
        engine_version="test",
        method_version="test",
        prompt_bundle_version="test",
        **campos,
    )
    db.add(informe)
    await db.flush()
    return informe


async def test_el_progreso_sale_nodo_a_nodo_en_el_orden_del_grafo(db: AsyncSession, cliente, org):
    from tasador.db.models import ReportEvent

    informe = await _informe(db, org.id, status="RUNNING")
    for seq, nodo, ms in ((1, "normalize_subject", 28), (2, "retrieve_candidates", 82)):
        db.add(
            ReportEvent(
                report_id=informe.id, seq=seq, node=nodo, status="OK", duration_ms=ms, detail={}
            )
        )
    await db.commit()

    p = cliente.get(f"/v1/reports/{informe.id}").json()["progress"]
    assert [s["node"] for s in p["steps"]] == ["normalize_subject", "retrieve_candidates"]
    assert p["completed"] == 2
    # El siguiente nodo pendiente es el que la UI muestra girando. Con el nodo 3
    # apagado en agents.yaml, después del 2 viene el 4.
    assert p["current_node"] == "extract_features"
    assert p["total"] == 10, "los 11 nodos menos ondemand_capture, que está apagado"


async def test_insufficient_data_es_200_con_sugerencias_accionables(db: AsyncSession, cliente, org):
    """No es un error: el sistema funcionó y su respuesta correcta es que no hay
    datos con qué. Devolverlo como 404 o 500 haría que el cliente lo trate como
    una falla y reintente, que es justo lo que no hay que hacer."""
    from tasador.db.models import Listing, Neighborhood, ReportEvent

    # Con el corpus VACÍO la sugerencia correcta es otra ("no hay ningún aviso
    # vigente, hay que capturar"). Para probar la que importa —la que nombra el
    # barrio y trae el comando— el corpus tiene que tener algo, aunque sea de
    # otra zona: es exactamente el estado real de hoy (24 avisos, todos de
    # Belgrano) y el caso donde la sugerencia genérica no sirve.
    barrio = Neighborhood(name="Belgrano", city="CABA", province="CABA")
    db.add(barrio)
    await db.flush()
    db.add(
        Listing(
            source="PORTAL_B",
            source_id="1",
            url="https://example.test/1",
            operation="SALE",
            neighborhood_id=barrio.id,
            price=Decimal("300000"),
            currency="USD",
            content_hash="0" * 64,
            active=True,
        )
    )

    informe = await _informe(
        db,
        org.id,
        status="INSUFFICIENT_DATA",
        insufficient_reason="Se encontraron 3 comparables y hacen falta 5.",
        comparables_found=11,
        methodology={
            "detail": [
                {"included": False, "exclusion_reason": "precio_no_usd"},
                {"included": False, "exclusion_reason": "precio_no_usd"},
                {"included": False, "exclusion_reason": "sin_superficie"},
                {"included": True},
            ]
        },
    )
    db.add(
        ReportEvent(
            report_id=informe.id,
            seq=2,
            node="retrieve_candidates",
            status="OK",
            detail={"barrio": "Palermo", "escalera": [{"encontrados": 0}, {"encontrados": 0}]},
        )
    )
    await db.commit()

    r = cliente.get(f"/v1/reports/{informe.id}")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["status"] == "INSUFFICIENT_DATA"
    assert cuerpo["detail"]["candidates_found"] == 11
    assert cuerpo["detail"]["excluded"] == [
        {"reason": "precio_no_usd", "count": 2},
        {"reason": "sin_superficie", "count": 1},
    ]
    # La diferencia entre un callejón sin salida y una tarea: la sugerencia
    # tiene que nombrar el barrio y traer el comando, no una frase genérica.
    sugerencias = " ".join(cuerpo["detail"]["suggestions"])
    assert "Palermo" in sugerencias
    assert "ingest_csv.py" in sugerencias


async def test_un_informe_exitoso_trae_el_rango_la_confianza_y_las_limitaciones(
    db: AsyncSession, cliente, org
):
    informe = await _informe(
        db,
        org.id,
        status="SUCCEEDED",
        currency="USD",
        value_low=Decimal("240000"),
        value_mid=Decimal("283385"),
        value_high=Decimal("373326"),
        closing_low=Decimal("240877"),
        closing_high=Decimal("269216"),
        price_per_m2=Decimal("2983.00"),
        confidence="MEDIA",
        confidence_score=Decimal("0.610"),
        comparables_found=22,
        comparables_used=12,
        narrative_md="El mercado de Belgrano...",
        finished_at=datetime.now(UTC),
    )
    await db.commit()

    c = cliente.get(f"/v1/reports/{informe.id}").json()
    assert c["valuation"]["suggested_listing_price"] == {
        "low": 240000.0,
        "mid": 283385.0,
        "high": 373326.0,
    }
    assert c["valuation"]["expected_closing_range"]["high"] == 269216.0
    # Ningún número aparece sin su nivel de confianza al lado (doc 07 §12).
    assert c["confidence"]["level"] == "MEDIA"
    # `found`/`used` eran lo ÚNICO que devolvía. Doc 06 §2 documenta también
    # `excluded` e `items[]`, que es la tabla que justifica el número — lo que
    # hace al informe auditable (H-27). Este informe de prueba no tiene filas en
    # `report_comparables`, así que `items` viene vacío pero la clave está.
    assert c["comparables"]["found"] == 22
    assert c["comparables"]["used"] == 12
    assert c["comparables"]["excluded"] == 10
    assert c["comparables"]["items"] == []
    # El recuadro de limitaciones nunca es opcional (doc 05 §9): "no es una
    # tasación con validez legal" tiene que viajar con el número, no al lado.
    assert any("validez legal" in x for x in c["limitations"])


async def test_un_informe_inexistente_da_404(cliente, org):
    assert cliente.get(f"/v1/reports/{uuid.uuid4()}").status_code == 404


# ── Listado ──────────────────────────────────────────────────────────────
async def test_el_listado_pagina_por_keyset_sin_repetir_ni_saltear(db: AsyncSession, cliente, org):
    """Con OFFSET, un informe nuevo entre dos pedidos corre todas las filas un
    lugar: el usuario ve repetido lo que ya vio, o nunca ve lo que quedó del
    otro lado del borde."""
    for i in range(5):
        await _informe(db, org.id, status="SUCCEEDED", address=f"Calle {i}")
    await db.commit()

    p1 = cliente.get("/v1/reports?limit=2").json()
    assert len(p1["items"]) == 2
    assert p1["next_cursor"]

    p2 = cliente.get(f"/v1/reports?limit=2&cursor={p1['next_cursor']}").json()
    p3 = cliente.get(f"/v1/reports?limit=2&cursor={p2['next_cursor']}").json()

    vistos = [i["report_id"] for i in p1["items"] + p2["items"] + p3["items"]]
    assert len(vistos) == 5
    assert len(set(vistos)) == 5, "la paginación repitió un informe"
    assert p3["next_cursor"] is None


async def test_el_listado_trae_la_direccion_y_no_solo_el_uuid(db: AsyncSession, cliente, org):
    await _informe(db, org.id, status="SUCCEEDED", address="Juramento 1500")
    await db.commit()
    item = cliente.get("/v1/reports").json()["items"][0]
    assert item["address"] == "Juramento 1500"
    assert item["rooms"] == 3


async def test_un_cursor_corrupto_es_422_y_no_un_500(cliente, org):
    assert cliente.get("/v1/reports?cursor=cualquier-cosa").status_code == 422


async def test_el_listado_filtra_por_estado(db: AsyncSession, cliente, org):
    await _informe(db, org.id, status="SUCCEEDED")
    await _informe(db, org.id, status="INSUFFICIENT_DATA")
    await db.commit()
    r = cliente.get("/v1/reports?estado=INSUFFICIENT_DATA").json()
    assert len(r["items"]) == 1
    assert r["items"][0]["status"] == "INSUFFICIENT_DATA"


# ── PDF ──────────────────────────────────────────────────────────────────
async def test_un_pdf_registrado_pero_ausente_del_disco_no_devuelve_200(
    db: AsyncSession, cliente, org, tmp_path: Path
):
    """El peor caso posible: 200 apuntando a un archivo que no está. El cliente
    baja cero bytes y cree que el informe salió mal."""
    from tasador.db.models import ReportArtifact

    informe = await _informe(db, org.id, status="SUCCEEDED")
    db.add(
        ReportArtifact(
            report_id=informe.id,
            pdf_path=str(tmp_path / "no-existe.pdf"),
            sha256="0" * 64,
        )
    )
    await db.commit()

    assert cliente.get(f"/v1/reports/{informe.id}/pdf").status_code == 404


async def test_el_pdf_se_sirve_con_su_sha256(db: AsyncSession, cliente, org, tmp_path: Path):
    """El hash viaja en un header para que el que recibe el archivo pueda
    probar que es el mismo que se generó. Un informe entregado en septiembre
    tiene que ser byte por byte el mismo en diciembre."""
    from tasador.db.models import ReportArtifact

    archivo = tmp_path / "informe.pdf"
    archivo.write_bytes(b"%PDF-1.7\n")
    informe = await _informe(db, org.id, status="SUCCEEDED")
    db.add(
        ReportArtifact(report_id=informe.id, pdf_path=str(archivo), sha256="a" * 64, pdf_bytes=9)
    )
    await db.commit()

    r = cliente.get(f"/v1/reports/{informe.id}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["X-Content-SHA256"] == "a" * 64


async def test_un_informe_sin_pdf_da_404_y_no_lo_genera_al_vuelo(db: AsyncSession, cliente, org):
    informe = await _informe(db, org.id, status="SUCCEEDED")
    await db.commit()
    assert cliente.get(f"/v1/reports/{informe.id}/pdf").status_code == 404


# ── La respuesta completa que doc 06 §2 documenta (H-27) ─────────────────
#
# Faltaban cinco cosas, y la que más importa es `comparables.items[]`: es la
# tabla que justifica el número, lo que hace al informe auditable. Estaba en
# `report_comparables` en la base y la única forma de conseguirla por API era…
# no había. `pdf_url` es cómo el cliente sabe que hay PDF; antes tenía que
# adivinar la URL y comerse un 404. Y `external_ref` volvía SIEMPRE en `None`,
# que es el camino de vuelta del informe a la propiedad en el CRM del cliente.


async def _con_comparables(db: AsyncSession, org_id, *, external_ref=None, con_pdf=False):
    from tasador.db.models import (
        Listing,
        Neighborhood,
        ReportArtifact,
        ReportComparable,
        SubjectProperty,
    )

    informe = await _informe(
        db,
        org_id,
        status="SUCCEEDED",
        currency="USD",
        price_per_m2=Decimal("3628.00"),
        confidence="ALTA",
        confidence_score=Decimal("0.810"),
        comparables_found=3,
        comparables_used=2,
        narrative_md="## Resumen\n\n...",
        finished_at=datetime.now(UTC),
        methodology={
            "notes": ["9 comparables", "dispersión 11%"],
            "market_context": {"barrio": "Palermo", "stock": {"usd_m2_mediano": 3839}},
        },
    )
    if external_ref is not None:
        sujeto = await db.get(SubjectProperty, informe.subject_property_id)
        assert sujeto is not None
        sujeto.external_ref = external_ref

    # Get-or-create: hay tests que arman DOS informes en la misma base y
    # `neighborhood_unique` es (city, name).
    from sqlalchemy import select as _select

    barrio = (
        await db.execute(_select(Neighborhood).where(Neighborhood.name == "Palermo"))
    ).scalar_one_or_none()
    if barrio is None:
        barrio = Neighborhood(name="Palermo", city="CABA", province="CABA")
        db.add(barrio)
        await db.flush()

    for i, (incluido, motivo) in enumerate([(True, None), (True, None), (False, "aviso_vencido")]):
        aviso = Listing(
            source="PORTAL_B",
            # El `source_id` es único por fuente: se cuelga del informe para que
            # dos llamadas a este helper no colisionen.
            source_id=f"zp-{informe.id}-{i}",
            url=f"https://portal_b/{informe.id}/{i}",
            operation="SALE",
            address_raw=f"Thames {1800 + i}",
            neighborhood_id=barrio.id,
            price=Decimal("246645"),
            currency="USD",
            content_hash=f"h-{informe.id}-{i}",
            published_at=(datetime.now(UTC) - timedelta(days=38)).date(),
            raw={"rooms": "3", "surface_weighted": "66.0"},
        )
        db.add(aviso)
        await db.flush()
        db.add(
            ReportComparable(
                report_id=informe.id,
                listing_id=aviso.id,
                included=incluido,
                exclusion_reason=motivo,
                distance_m=320,
                snapshot_price=Decimal("246645"),
                snapshot_currency="USD",
                snapshot_surface=Decimal("66.0"),
                raw_price_per_m2=Decimal("3737"),
                adjusted_price_per_m2=Decimal("3628"),
                adjustments={"total": "1.0300"},
            )
        )

    if con_pdf:
        db.add(ReportArtifact(report_id=informe.id, pdf_path="/data/artifacts/x.pdf"))

    await db.commit()
    return informe


async def test_la_respuesta_trae_los_comparables_que_justifican_el_numero(
    db: AsyncSession, cliente, org
):
    informe = await _con_comparables(db, org.id)
    c = cliente.get(f"/v1/reports/{informe.id}").json()

    items = c["comparables"]["items"]
    assert len(items) == 2, "por defecto vienen solo los incluidos"
    uno = items[0]
    # Los 13 campos de doc 06 §2, más el motivo de descarte.
    assert set(uno) == {
        "source",
        "url",
        "address",
        "price",
        "currency",
        "surface_weighted",
        "rooms",
        "raw_price_per_m2",
        "adjusted_price_per_m2",
        "adjustments",
        "distance_m",
        "days_published",
        "included",
        "exclusion_reason",
    }
    assert uno["source"] == "PORTAL_B"
    assert uno["price"] == 246645.0
    assert uno["rooms"] == 3
    assert uno["adjusted_price_per_m2"] == 3628.0
    assert uno["days_published"] == 38
    assert uno["included"] is True


async def test_los_descartados_estan_detras_de_un_parametro(db: AsyncSession, cliente, org):
    """Con 60 comparables la respuesta pesa ~40 KB. Quien mira un informe
    quiere ver los que se usaron; el "¿por qué no usaste el de Cabildo 2500?"
    se pide aparte."""
    informe = await _con_comparables(db, org.id)

    sin = cliente.get(f"/v1/reports/{informe.id}").json()["comparables"]["items"]
    con = cliente.get(f"/v1/reports/{informe.id}?incluir=descartados").json()["comparables"][
        "items"
    ]

    assert len(sin) == 2
    assert len(con) == 3
    descartado = next(x for x in con if not x["included"])
    assert descartado["exclusion_reason"] == "aviso_vencido"


async def test_el_precio_del_comparable_es_el_del_momento_del_informe(
    db: AsyncSession, cliente, org
):
    """Un informe es un documento, no una vista (doc 03). Si el aviso baja de
    precio después, el informe entregado no puede cambiar."""
    from sqlalchemy import select

    from tasador.db.models import Listing

    informe = await _con_comparables(db, org.id)
    for aviso in (await db.execute(select(Listing))).scalars():
        aviso.price = Decimal("100000")
    await db.commit()

    items = cliente.get(f"/v1/reports/{informe.id}").json()["comparables"]["items"]
    assert all(x["price"] == 246645.0 for x in items), "el informe mutó con el corpus"


async def test_pdf_url_dice_si_hay_pdf(db: AsyncSession, cliente, org):
    sin_pdf = await _con_comparables(db, org.id)
    assert cliente.get(f"/v1/reports/{sin_pdf.id}").json()["pdf_url"] is None

    con_pdf = await _con_comparables(db, org.id, con_pdf=True)
    url = cliente.get(f"/v1/reports/{con_pdf.id}").json()["pdf_url"]
    assert url == f"/v1/reports/{con_pdf.id}/pdf"


async def test_external_ref_vuelve_al_crm_del_cliente(db: AsyncSession, cliente, org):
    """Se devolvía `None` fijo, así que el camino de vuelta del informe a la
    propiedad en el sistema del cliente no existía."""
    informe = await _con_comparables(db, org.id, external_ref="CRM-4417")
    assert cliente.get(f"/v1/reports/{informe.id}").json()["external_ref"] == "CRM-4417"


async def test_la_confianza_dice_por_que(db: AsyncSession, cliente, org):
    informe = await _con_comparables(db, org.id)
    conf = cliente.get(f"/v1/reports/{informe.id}").json()["confidence"]
    assert conf["level"] == "ALTA"
    assert conf["notes"] == ["9 comparables", "dispersión 11%"]


async def test_el_contexto_de_mercado_sale_por_la_api(db: AsyncSession, cliente, org):
    """Se calcula en el nodo 8, se paga, y solo llegaba al PDF."""
    informe = await _con_comparables(db, org.id)
    ctx = cliente.get(f"/v1/reports/{informe.id}").json()["market_context"]
    assert ctx["barrio"] == "Palermo"
    assert ctx["stock"]["usd_m2_mediano"] == 3839


async def test_no_falta_ninguna_clave_de_doc_06(db: AsyncSession, cliente, org):
    """El gate: la lista sale del contrato, no de lo que el código devuelve.

    Es el mismo chequeo que hace la sonda `s4b.py`, adentro del suite para que
    corra en cada commit y no cuando alguien se acuerda.
    """
    informe = await _con_comparables(db, org.id, external_ref="CRM-1", con_pdf=True)
    c = cliente.get(f"/v1/reports/{informe.id}").json()

    del_contrato = {
        "report_id",
        "status",
        "external_ref",
        "generated_at",
        "valuation",
        "confidence",
        "comparables",
        "market_context",
        "narrative_md",
        "methodology_version",
        "pdf_url",
        "limitations",
    }
    assert not (del_contrato - set(c)), f"faltan del contrato: {sorted(del_contrato - set(c))}"


async def test_la_narrativa_sale_tambien_como_html_seguro(db: AsyncSession, cliente, org):
    """La pantalla del informe mostraba el markdown CRUDO: `## Resumen
    ejecutivo` y `**USD 134.667**` con los símbolos a la vista, en el
    entregable. Era texto plano por un motivo bueno —el bug 16 fue un
    `<script>` del markdown que llegó entero al PDF— y la solución ya existía:
    `markdown_a_html`, CommonMark con `html: false`, la que usa el PDF.

    Este test es lo que hace seguro el `dangerouslySetInnerHTML` del front.
    """
    informe = await _informe(
        db,
        org.id,
        status="SUCCEEDED",
        currency="USD",
        confidence="ALTA",
        finished_at=datetime.now(UTC),
        narrative_md=(
            "## Resumen\n\nSugerimos **USD 134.667**.\n\n"
            "<script>alert('xss')</script>\n\n"
            "[click](javascript:alert(1))\n"
        ),
    )
    await db.commit()

    c = cliente.get(f"/v1/reports/{informe.id}").json()
    html = c["narrative_html"]

    # Renderiza de verdad: el markdown se convirtió en etiquetas.
    assert "<h2>" in html and "<strong>" in html
    assert "##" not in html and "**" not in html

    # Y NO deja pasar lo que no es markdown.
    assert "<script>" not in html, "el HTML crudo del markdown llegó sin escapar"
    assert "&lt;script&gt;" in html, "tiene que estar escapado, no borrado en silencio"
    # Lo peligroso es un `href`, no que la palabra aparezca: markdown-it
    # rechaza el esquema y deja `[click](javascript:...)` como texto plano
    # dentro del `<p>`, que es exactamente el resultado seguro.
    assert "href=" not in html, "markdown-it tiene que rechazar el esquema javascript:"
    assert "<a " not in html

    # El crudo sigue viajando: el PDF y cualquier cliente que prefiera markdown
    # no dependen de esto.
    assert c["narrative_md"].startswith("## Resumen")


async def test_la_ficha_trae_la_propiedad_tasada_completa(db: AsyncSession, cliente, org):
    """19/09: el número sin decir qué propiedad es no sirve. Lo no declarado
    viene como null, no desaparece: el front lo imprime como «sin declarar»."""
    r = cliente.post(
        "/v1/reports",
        json={
            "property": {
                "address_raw": "Gorriti 5000",
                "property_type": "departamento",
                "rooms": 3,
                "surface_total": 70,
            }
        },
    )
    assert r.status_code == 202
    rid = r.json()["report_id"]
    ficha = cliente.get(f"/v1/reports/{rid}").json()
    prop = ficha["property"]
    assert prop["address"] == "Gorriti 5000"
    assert prop["property_type"] == "departamento"
    assert prop["rooms"] == 3
    assert prop["surface_total"] == 70.0
    for campo in ("bedrooms", "bathrooms", "condition", "orientation", "floor_number"):
        assert campo in prop
        assert prop[campo] is None
