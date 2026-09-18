"""¿El contrato tarjeta -> raw/hash se cumple, o hay campos que se pierden en silencio?"""

from tasador.ingest.core import CAMPOS_DEL_HASH, CAMPOS_DEL_RAW
from tasador.ingest.cards import Card
from tasador.sources.portal_b import Card

for clase in (Card, Card):
    campos = set(getattr(clase, "__dataclass_fields__", {})) | {
        n for n in dir(clase) if isinstance(getattr(clase, n, None), property)
    }
    falta_raw = [a for a in CAMPOS_DEL_RAW if a not in campos]
    falta_hash = [a for a in CAMPOS_DEL_HASH if a not in campos]
    print(f"{clase.__name__}:")
    print(f"  atributos de CAMPOS_DEL_RAW que la tarjeta NO tiene : {falta_raw}")
    print(f"  atributos de CAMPOS_DEL_HASH que la tarjeta NO tiene: {falta_hash}")
    print(
        f"  -> esos campos se guardan/hashean como {'ausentes, en silencio' if falta_raw or falta_hash else 'n/a'}"
    )
    print()

# La demostracion: un campo que la tarjeta no tiene no rompe nada.
from tasador.ingest.core import _content_hash, _raw

c = Card(source_id="x", url="u", price=None, currency="USD")
print("_raw sobre una tarjeta minima ->", _raw(c))
print("_content_hash ->", _content_hash(c)[:16])
print("(ningun error: getattr(card, attr, None) traga cualquier nombre mal escrito)")
