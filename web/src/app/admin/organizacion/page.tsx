import { Aviso, Chip, PageHeader, Seccion, Stat, fecha } from "@/components/ui";
import { ErrorDeApi, getOrganizacion } from "@/lib/api";
import { CrearApiKey, RevocarApiKey } from "./acciones";

export const dynamic = "force-dynamic";

/** doc 07 §11: la organización, con cuota, consumo y API keys. */
export default async function Organizacion() {
  let org;
  try {
    org = await getOrganizacion();
  } catch (e) {
    if (e instanceof ErrorDeApi && e.status === 403) {
      return (
        <div className="contenido-panel">
          <PageHeader titulo="Organización" />
          <Aviso tono="info" titulo="Solo para administradores" />
        </div>
      );
    }
    throw e;
  }

  const activas = org.api_keys.filter((k) => !k.revoked_at);
  const revocadas = org.api_keys.filter((k) => k.revoked_at);

  return (
    <div className="contenido-panel">
      <PageHeader titulo={org.name} bajada={<code>{org.slug}</code>} />

      <div className="stats">
        <Stat
          etiqueta="Informes del mes"
          valor={
            <>
              {org.informes_del_mes} <span className="tenue" style={{ fontSize: "1rem" }}>de {org.monthly_report_quota}</span>
            </>
          }
          detalle="cuota mensual"
        />
        <Stat etiqueta="Presupuesto de captura" valor={org.fetch_budget_monthly} detalle="avisos por mes" />
        <Stat etiqueta="API keys activas" valor={activas.length} />
      </div>

      <Seccion
        id="apikeys"
        titulo="API keys"
        bajada="Para integraciones (el panel de la inmobiliaria). La clave se muestra una sola vez al crearla; si se pierde, se revoca y se emite otra."
      >
        {org.api_keys.length > 0 && (
          <div className="tabla tabla-apilable" style={{ marginBottom: "1rem" }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Nombre</th>
                  <th scope="col">Prefijo</th>
                  <th scope="col">Último uso</th>
                  <th scope="col">Creada</th>
                  <th scope="col">Estado</th>
                  <th scope="col">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {[...activas, ...revocadas].map((k) => (
                  <tr key={k.id} className={k.revoked_at ? "fuera" : ""}>
                    <td data-col="Nombre" style={{ fontWeight: 600, color: "var(--texto-fuerte)" }}>{k.name}</td>
                    <td data-col="Prefijo">
                      <code>{k.prefix}…</code>
                    </td>
                    <td data-col="Último uso" className="tenue">{fecha(k.last_used_at) ?? "nunca"}</td>
                    <td data-col="Creada" className="tenue">{fecha(k.created_at)}</td>
                    <td data-col="Estado">
                      {k.revoked_at ? <Chip tono="neutro">revocada</Chip> : <Chip tono="exito">activa</Chip>}
                    </td>
                    <td data-col="Acciones">{!k.revoked_at && <RevocarApiKey id={k.id} nombre={k.name} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <CrearApiKey />
      </Seccion>
    </div>
  );
}
