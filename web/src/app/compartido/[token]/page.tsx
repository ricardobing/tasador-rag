import { getCompartido } from "@/lib/api";

export const dynamic = "force-dynamic";

const usd = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `USD ${Math.round(v).toLocaleString("es-AR")}`;

/**
 * Lo que ve el PROPIETARIO con el link firmado. Sin sesión, sin navegación de
 * la app: el token es la credencial y esta pantalla es el producto entregado,
 * no la herramienta.
 */
export default async function Compartido({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const informe = await getCompartido(token);

  if (!informe) {
    return (
      <div className="aviso" style={{ margin: "2rem auto", maxWidth: "34rem" }}>
        <h1 style={{ marginTop: 0 }}>Este link no existe o venció</h1>
        <p>Pedile a tu inmobiliaria que te genere uno nuevo.</p>
      </div>
    );
  }

  const v = informe.valuation;
  return (
    <div style={{ maxWidth: "44rem", margin: "0 auto" }}>
      <p className="tenue" style={{ fontSize: "0.85rem" }}>
        Informe de Mercado Comparativo
        {informe.generated_at &&
          ` · ${new Date(informe.generated_at).toLocaleDateString("es-AR")}`}
      </p>
      <h1 style={{ marginBottom: "0.2rem" }}>{informe.address}</h1>
      <p className="tenue" style={{ marginTop: 0 }}>
        {informe.property_type}
        {informe.rooms ? ` · ${informe.rooms} amb` : ""}
        {informe.surface_total ? ` · ${informe.surface_total} m²` : ""}
        {informe.neighborhood ? ` · ${informe.neighborhood}` : ""}
      </p>

      <div className="tarjeta">
        <div style={{ fontSize: "1.7rem", fontWeight: 700, letterSpacing: "-0.02em" }}>
          {usd(v.suggested_listing_price.low)} —{" "}
          <strong>{usd(v.suggested_listing_price.mid)}</strong> —{" "}
          {usd(v.suggested_listing_price.high)}
        </div>
        <div className="tenue" style={{ marginTop: "0.2rem" }}>
          {v.price_per_m2 ? `USD ${Math.round(v.price_per_m2).toLocaleString("es-AR")} /m²` : ""}
          {"  ·  "}Confianza{" "}
          <span className={`chip chip-${informe.confidence.level}`}>{informe.confidence.level}</span>
        </div>
        <p style={{ marginBottom: 0 }}>
          Rango esperado de cierre:{" "}
          <strong>
            {usd(v.expected_closing_range.low)} – {usd(v.expected_closing_range.high)}
          </strong>
        </p>
      </div>

      <p>
        <a href={`/compartido/${encodeURIComponent(token)}/pdf`}>
          <button>Descargar PDF</button>
        </a>
      </p>

      {informe.narrative_md && (
        <section style={{ marginTop: "1.5rem" }}>
          <div style={{ whiteSpace: "pre-wrap" }}>{informe.narrative_md}</div>
        </section>
      )}

      <div className="tarjeta" style={{ marginTop: "1.8rem", background: "transparent" }}>
        <strong>Limitaciones</strong>
        <ul style={{ marginBottom: 0 }}>
          {informe.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
