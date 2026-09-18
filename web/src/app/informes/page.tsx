import Link from "next/link";
import { ErrorDeApi, listarInformes } from "@/lib/api";
import {
  Aviso,
  BotonLink,
  ChipDeConfianza,
  ChipDeEstado,
  PageHeader,
  Vacio,
  fecha,
  usd,
} from "@/components/ui";

export const dynamic = "force-dynamic";

/** Los estados por los que se puede filtrar; el texto es el del chip. */
const FILTROS: { valor: string; texto: string }[] = [
  { valor: "", texto: "Todos" },
  { valor: "SUCCEEDED", texto: "Terminados" },
  { valor: "INSUFFICIENT_DATA", texto: "Sin datos" },
  { valor: "RUNNING", texto: "En curso" },
  { valor: "FAILED", texto: "Fallidos" },
];

/** doc 07 §3: el listado es la pantalla donde vive el usuario. */
export default async function Informes({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string; estado?: string }>;
}) {
  const { cursor, estado } = await searchParams;
  const estadoValido = FILTROS.some((f) => f.valor === (estado ?? "")) ? estado : undefined;

  let datos;
  try {
    datos = await listarInformes(cursor, 25, estadoValido);
  } catch (e) {
    // Un backend caído no puede verse como "no tenés informes": son cosas
    // distintas y confundirlas hace que el usuario crea que perdió su trabajo.
    const detalle = e instanceof ErrorDeApi ? `La API devolvió ${e.status}.` : "No respondió.";
    return (
      <div className="contenido-panel">
        <PageHeader titulo="Informes" />
        <Aviso tono="peligro" titulo="No pudimos contactar al servidor">
          {detalle} Los informes están guardados; esto es un problema de conexión.
        </Aviso>
      </div>
    );
  }

  const segmentos = (
    <nav className="segmentos" aria-label="Filtrar por estado">
      {FILTROS.map((f) => (
        <Link
          key={f.valor}
          href={f.valor ? `/informes?estado=${f.valor}` : "/informes"}
          className="segmento"
          aria-current={(estadoValido ?? "") === f.valor ? "true" : undefined}
        >
          {f.texto}
        </Link>
      ))}
    </nav>
  );

  return (
    <div className="contenido-panel">
      <PageHeader
        titulo="Informes"
        bajada="Cada informe es un documento: el valor, su confianza y los avisos que lo sostienen."
        acciones={
          <BotonLink href="/informes/nuevo" icono="nuevo">
            Nuevo informe
          </BotonLink>
        }
      />

      <div className="toolbar">{segmentos}</div>

      {datos.items.length === 0 ? (
        estadoValido ? (
          <Vacio
            titulo="Ningún informe con ese estado"
            texto="Probá con otro filtro o generá uno nuevo."
            accion={
              <BotonLink href="/informes" variante="secundario">
                Ver todos
              </BotonLink>
            }
          />
        ) : (
          <Vacio
            titulo="Todavía no generaste ninguno"
            texto="Con la dirección y el tipo alcanza para el primero. Después de la visita se regenera con más datos."
            accion={
              <BotonLink href="/informes/nuevo" icono="nuevo">
                Generar el primero
              </BotonLink>
            }
          />
        )
      ) : (
        <div className="tabla tabla-apilable">
          <table>
            <thead>
              <tr>
                <th scope="col">Dirección</th>
                <th scope="col">Barrio</th>
                <th scope="col">Tipo</th>
                <th scope="col" className="num">Valor sugerido</th>
                <th scope="col" className="num">USD/m²</th>
                <th scope="col">Confianza</th>
                <th scope="col" className="num">Comparables</th>
                <th scope="col">Estado</th>
              </tr>
            </thead>
            <tbody>
              {datos.items.map((i) => (
                <tr key={i.report_id}>
                  <td data-col="Dirección">
                    <div>
                      <Link href={`/informes/${i.report_id}`} style={{ fontWeight: 600 }}>
                        {i.address ?? "sin dirección"}
                      </Link>
                      <div className="tenue" style={{ fontSize: "0.75rem" }}>
                        {fecha(i.created_at)}
                      </div>
                    </div>
                  </td>
                  <td data-col="Barrio">{i.neighborhood ?? <span className="tenue">sin barrio</span>}</td>
                  <td data-col="Tipo" className="tenue">
                    {i.property_type}
                    {i.rooms ? ` · ${i.rooms} amb` : ""}
                    {i.surface_total ? ` · ${i.surface_total} m²` : ""}
                  </td>
                  <td data-col="Valor sugerido" className="num">
                    {usd(i.value_mid) ? (
                      <div>
                        <span className="cifra" style={{ fontWeight: 600 }}>
                          {usd(i.value_mid)}
                        </span>
                        {i.value_low !== null && i.value_high !== null && (
                          <div className="tenue" style={{ fontSize: "0.75rem" }}>
                            {usd(i.value_low)} a {usd(i.value_high)}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="tenue">sin valor</span>
                    )}
                  </td>
                  <td data-col="USD/m²" className="num cifra">
                    {i.price_per_m2 ? Math.round(i.price_per_m2).toLocaleString("es-AR") : <span className="tenue">sin dato</span>}
                  </td>
                  <td data-col="Confianza">
                    <ChipDeConfianza nivel={i.confidence} />
                  </td>
                  <td data-col="Comparables" className="num cifra">
                    {i.comparables_used ?? 0} de {i.comparables_found ?? 0}
                  </td>
                  <td data-col="Estado">
                    <ChipDeEstado estado={i.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {datos.next_cursor && (
        <p style={{ marginTop: "1.2rem" }}>
          <BotonLink
            href={`/informes?cursor=${encodeURIComponent(datos.next_cursor)}${estadoValido ? `&estado=${estadoValido}` : ""}`}
            variante="secundario"
            icono="flecha"
          >
            Ver más informes
          </BotonLink>
        </p>
      )}
    </div>
  );
}
