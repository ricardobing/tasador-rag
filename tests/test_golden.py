"""Los golden sets — `tasador.eval.golden`.

El test que importa es `test_un_candidato_sin_revisar_NO_se_mide`. Sin él, los
90 candidatos que `scripts/golden_set.py` prepara —con `esperado` todo en
`null`— entrarían al eval como si fueran anotación humana. El resultado sería
una exactitud altísima contra un set que dice "el aviso no menciona nada": el
número subiría **justo cuando el trabajo no se hizo**.

Es la misma familia de error que el eval del nodo 4 de la Etapa 3, que medía un
fragmento del sistema y no avisaba. La diferencia entre un instrumento y un
adorno es si puede dar bien por el motivo equivocado.
"""

from __future__ import annotations

from pathlib import Path

from tasador.eval.golden import cargar, resumen


def _escribir(d: Path, nombre: str, cuerpo: str) -> None:
    (d / nombre).write_text(cuerpo, encoding="utf-8")


def test_un_candidato_sin_revisar_no_se_mide(tmp_path: Path):
    _escribir(
        tmp_path,
        "a.yaml",
        """
version: 1
avisos:
  - id: "1"
    esperado: {condition: bueno}
    revisado: true
  - id: "2"
    esperado: {condition: null}
    revisado: false
""",
    )
    avisos, conteos = cargar(directorio=tmp_path)
    assert [a["id"] for a in avisos] == ["1"]
    assert conteos == {"archivos": 1, "total": 2, "revisados": 1, "sin_revisar": 1}
    assert "1 SIN revisar, no se miden" in resumen(conteos)


def test_la_ausencia_de_revisado_cuenta_como_revisado(tmp_path: Path):
    """El golden set original de Belgrano es anterior a este campo y se anotó
    entero a mano. Tratarlo como "sin revisar" habría vaciado el eval de golpe."""
    _escribir(tmp_path, "viejo.yaml", 'version: 1\navisos:\n  - id: "1"\n    esperado: {}\n')
    avisos, conteos = cargar(directorio=tmp_path)
    assert len(avisos) == 1
    assert conteos["sin_revisar"] == 0


def test_se_cargan_todos_los_archivos_y_no_solo_extraccion(tmp_path: Path):
    """Cada eval abría `extraccion.yaml` con un Path hardcodeado, así que un
    archivo nuevo —el de Palermo— no lo veía nadie."""
    _escribir(tmp_path, "extraccion.yaml", 'version: 1\navisos:\n  - id: "1"\n    esperado: {}\n')
    _escribir(tmp_path, "palermo.yaml", 'version: 1\navisos:\n  - id: "2"\n    esperado: {}\n')
    avisos, conteos = cargar(directorio=tmp_path)
    assert {a["id"] for a in avisos} == {"1", "2"}
    assert conteos["archivos"] == 2


def test_un_yaml_roto_no_voltea_el_eval_entero(tmp_path: Path):
    _escribir(tmp_path, "bueno.yaml", 'version: 1\navisos:\n  - id: "1"\n    esperado: {}\n')
    _escribir(tmp_path, "roto.yaml", "esto: no: es: yaml\n")
    avisos, conteos = cargar(directorio=tmp_path)
    assert [a["id"] for a in avisos] == ["1"]
    assert conteos["archivos"] == 1


def test_el_estrato_viaja_con_el_aviso(tmp_path: Path):
    """El estrato `enriquecido` junta descartes rápido pero solo encuentra los
    OBVIOS: un recall calculado solo sobre él sale inflado. Sin el estrato en
    cada aviso, esa distinción no se puede hacer al reportar."""
    _escribir(
        tmp_path,
        "a.yaml",
        "version: 1\navisos:\n"
        '  - {id: "1", esperado: {}, estrato: enriquecido}\n'
        '  - {id: "2", esperado: {}}\n',
    )
    avisos, _ = cargar(directorio=tmp_path)
    assert {a["id"]: a["estrato"] for a in avisos} == {"1": "enriquecido", "2": "azar"}


def test_el_archivo_de_palermo_del_repo_esta_bien_formado():
    """Se versiona con 90 candidatos sin revisar. Si el YAML estuviera roto, el
    eval lo saltearía en silencio y el trabajo de anotación se perdería."""
    import yaml

    ruta = Path(__file__).resolve().parent / "golden" / "palermo-para-anotar.yaml"
    if not ruta.exists():
        return
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    avisos = datos["avisos"]
    assert len(avisos) == 90
    assert all(a["id"] and "_texto" in a for a in avisos)
    assert {a["estrato"] for a in avisos} == {"azar", "enriquecido"}
