import { Icono } from "@/components/iconos";
import { BloqueDeValor, FichaDePropiedad, Limitaciones, Narrativa } from "@/components/documento";
import { Aviso } from "@/components/ui";
import { getCompartido } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * Lo que ve el PROPIETARIO con el link firmado. Sin sesión, sin navegación de
 * la app: el token es la credencial y esta pantalla es el producto entregado,
 * no la herramienta. Mismo orden que el PDF: propiedad, valor, narrativa,
 * limitaciones.
 */
export default async function Compartido({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const informe = await getCompartido(token);

  if (!informe) {
    return (
      <div className="contenido-documento" style={{ padding: "3rem 1rem" }}>
        <Aviso tono="alerta" titulo="Este link no existe o venció">
          Pedile a tu inmobiliaria que te genere uno nuevo.
        </Aviso>
      </div>
    );
  }

  const v = informe.valuation;
  return (
    <div className="contenido-documento" style={{ padding: "1.5rem 1rem 4rem" }}>
      <header className="encabezado">
        <div>
          <div className="etiqueta">Informe de mercado comparativo</div>
          <h1 style={{ marginTop: "0.2rem" }}>{informe.address}</h1>
          {informe.generated_at && (
            <p>Generado el {new Date(informe.generated_at).toLocaleDateString("es-AR")}</p>
          )}
        </div>
        <div className="encabezado-acciones">
          <a href={`/compartido/${encodeURIComponent(token)}/pdf`} className="boton">
            <Icono nombre="pdf" tamano={18} />
            Descargar PDF
          </a>
        </div>
      </header>

      <FichaDePropiedad
        p={
          informe.property ?? {
            address: informe.address,
            neighborhood: informe.neighborhood,
            property_type: informe.property_type,
            rooms: informe.rooms,
            bedrooms: null,
            bathrooms: null,
            surface_total: informe.surface_total,
            surface_covered: null,
            age_years: null,
            floor_number: null,
            has_elevator: null,
            condition: null,
            orientation: null,
            parking_spaces: null,
            expenses_ars: null,
            notes: null,
          }
        }
      />

      <BloqueDeValor
        moneda={v.currency}
        medio={v.suggested_listing_price.mid}
        bajo={v.suggested_listing_price.low}
        alto={v.suggested_listing_price.high}
        cierreBajo={v.expected_closing_range.low}
        cierreAlto={v.expected_closing_range.high}
        confianza={informe.confidence.level}
        usados={undefined}
        encontrados={undefined}
        usdM2={v.price_per_m2}
        sinRespaldo
      />

      <Narrativa md={informe.narrative_md} />

      <Limitaciones items={informe.limitations} />
    </div>
  );
}
