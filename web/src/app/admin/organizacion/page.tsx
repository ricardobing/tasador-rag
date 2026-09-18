import { ErrorDeApi, getOrganizacion } from "@/lib/api";
import { CrearApiKey, RevocarApiKey } from "./acciones";

export const dynamic = "force-dynamic";

const fecha = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("es-AR", { day: "2-digit", month: "2-digit", year: "2-digit" }) : "nunca";

/** doc 07 §11 — la organización: cuota, consumo y API keys. */
export default async function Organizacion() {
  let org;
  try {
    org = await getOrganizacion();
  } catch (e) {
    if (e instanceof ErrorDeApi && e.status === 403) {
      return (
        <div className="aviso">
          <h2 style={{ marginTop: 0 }}>Solo para administradores</h2>
        </div>
      );
    }
    throw e;
  }

  return (
    <>
      <h1>{org.name}</h1>
      <p className="tenue">
        <code>{org.slug}</code>
      </p>

      <div className="tarjeta">
        <p style={{ margin: 0 }}>
          Informes del mes: <strong>{org.informes_del_mes}</strong> de {org.monthly_report_quota}
        </p>
        <p className="tenue" style={{ margin: "0.3rem 0 0" }}>
          Presupuesto de captura mensual: {org.fetch_budget_monthly} avisos
        </p>
      </div>

      <h2 style={{ marginTop: "2rem" }}>API keys</h2>
      <p className="tenue" style={{ fontSize: "0.85rem" }}>
        Para integraciones (el panel de la inmobiliaria). La clave se muestra una sola vez al
        crearla; si se pierde, se revoca y se emite otra.
      </p>

      {org.api_keys.length > 0 && (
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th scope="col">Nombre</th>
                <th scope="col">Prefijo</th>
                <th scope="col">Último uso</th>
                <th scope="col">Creada</th>
                <th scope="col">Estado</th>
                <th scope="col"></th>
              </tr>
            </thead>
            <tbody>
              {org.api_keys.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td>
                    <code>{k.prefix}…</code>
                  </td>
                  <td className="tenue">{fecha(k.last_used_at)}</td>
                  <td className="tenue">{fecha(k.created_at)}</td>
                  <td>
                    {k.revoked_at ? (
                      <span className="chip" style={{ color: "var(--baja)" }}>
                        revocada
                      </span>
                    ) : (
                      <span className="chip" style={{ color: "var(--alta)" }}>
                        activa
                      </span>
                    )}
                  </td>
                  <td>{!k.revoked_at && <RevocarApiKey id={k.id} nombre={k.name} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CrearApiKey />
    </>
  );
}
