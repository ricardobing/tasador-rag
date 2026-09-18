import Link from "next/link";
import { listarInformes, type Confianza, ErrorDeApi } from "@/lib/api";

export const dynamic = "force-dynamic";

const usd = (v: number | null) =>
  v === null ? "—" : `USD ${Math.round(v).toLocaleString("es-AR")}`;

function ChipDeConfianza({ nivel }: { nivel: Confianza | null }) {
  if (!nivel) return <span className="tenue">—</span>;
  return <span className={`chip chip-${nivel}`}>{nivel}</span>;
}

/** doc 07 §3: el listado es la pantalla donde vive el usuario. */
export default async function Informes({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string }>;
}) {
  const { cursor } = await searchParams;

  let datos;
  try {
    datos = await listarInformes(cursor);
  } catch (e) {
    // Un backend caído no puede verse como "no tenés informes": son cosas
    // distintas y confundirlas hace que el usuario crea que perdió su trabajo.
    const detalle = e instanceof ErrorDeApi ? `La API devolvió ${e.status}.` : "No respondió.";
    return (
      <div className="aviso">
        <h2 style={{ marginTop: 0 }}>No pudimos contactar al servidor</h2>
        <p>{detalle} Los informes están guardados; esto es un problema de conexión.</p>
      </div>
    );
  }

  if (datos.items.length === 0) {
    return (
      <>
        <h1>Informes</h1>
        <p className="tenue">Todavía no generaste ninguno.</p>
        <Link href="/informes/nuevo">
          <button>Generar el primero</button>
        </Link>
      </>
    );
  }

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Informes</h1>
        <Link href="/informes/nuevo">
          <button>Nuevo informe</button>
        </Link>
      </div>

      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th scope="col">Dirección</th>
              <th scope="col">Barrio</th>
              <th scope="col">Tipo</th>
              <th scope="col">Valor sugerido</th>
              <th scope="col">USD/m²</th>
              <th scope="col">Confianza</th>
              <th scope="col">Comparables</th>
              <th scope="col">Estado</th>
            </tr>
          </thead>
          <tbody>
            {datos.items.map((i) => (
              <tr key={i.report_id}>
                <td>
                  <Link href={`/informes/${i.report_id}`}>{i.address ?? "—"}</Link>
                </td>
                <td>{i.neighborhood ?? "—"}</td>
                <td className="tenue">
                  {i.property_type} · {i.rooms ?? "?"} amb
                  {i.surface_total ? ` · ${i.surface_total} m²` : ""}
                </td>
                <td>
                  {usd(i.value_mid)}
                  {i.value_low !== null && i.value_high !== null && (
                    <div className="tenue" style={{ fontSize: "0.8rem" }}>
                      {usd(i.value_low)} – {usd(i.value_high)}
                    </div>
                  )}
                </td>
                <td>{i.price_per_m2 ? Math.round(i.price_per_m2).toLocaleString("es-AR") : "—"}</td>
                <td>
                  <ChipDeConfianza nivel={i.confidence} />
                </td>
                <td>
                  {i.comparables_used ?? 0} de {i.comparables_found ?? 0}
                </td>
                <td>
                  {/* INSUFFICIENT_DATA en ámbar y no en rojo: el sistema
                      funcionó, no es un error (doc 07 §3). */}
                  <span
                    className="chip"
                    style={{
                      color: i.status === "INSUFFICIENT_DATA" ? "var(--ambar)" : undefined,
                    }}
                  >
                    {i.status === "INSUFFICIENT_DATA" ? "SIN DATOS" : i.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {datos.next_cursor && (
        <p style={{ marginTop: "1.2rem" }}>
          <Link href={`/informes?cursor=${encodeURIComponent(datos.next_cursor)}`}>
            Ver más informes →
          </Link>
        </p>
      )}
    </>
  );
}
