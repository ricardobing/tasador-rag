import { ErrorDeApi, getFuentes, type Fuente } from "@/lib/api";

export const dynamic = "force-dynamic";

const fecha = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleDateString("es-AR", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

/**
 * doc 07 §10 — la pantalla que avisa que un portal cambió su defensa ANTES de
 * que el corpus se pudra y los informes salgan mal en silencio.
 *
 * No hay botón "Correr ahora": la ingesta corre por script
 * (`scripts/ingest_csv.py`) sobre lo que deja el scraper externo. Cuando la
 * captura on-demand exista (nodo 3), el botón va acá.
 */
export default async function Fuentes() {
  let datos;
  try {
    datos = await getFuentes();
  } catch (e) {
    if (e instanceof ErrorDeApi && e.status === 403) {
      return (
        <div className="aviso">
          <h2 style={{ marginTop: 0 }}>Solo para administradores</h2>
          <p>Esta pantalla muestra la operación de las fuentes de datos.</p>
        </div>
      );
    }
    throw e;
  }

  return (
    <>
      <h1>Fuentes de datos</h1>

      {datos.fuentes.length === 0 ? (
        <p className="tenue">Todavía no hay corridas de ingesta.</p>
      ) : (
        <div style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
          {datos.fuentes.map((f) => (
            <TarjetaDeFuente key={f.source} f={f} />
          ))}
        </div>
      )}

      <h2 style={{ marginTop: "2rem" }}>Cobertura y sesgo por barrio</h2>
      <p className="tenue" style={{ fontSize: "0.85rem" }}>
        El USD/m² oficial es la serie de BA Data, que llega hasta 2019: el desvío se muestra como
        referencia histórica, no como alarma.
      </p>
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th scope="col">Barrio</th>
              <th scope="col">Avisos activos</th>
              <th scope="col">Usables</th>
              <th scope="col">USD/m² corpus</th>
              <th scope="col">USD/m² oficial (2019)</th>
              <th scope="col">Desvío</th>
              <th scope="col">Estado</th>
            </tr>
          </thead>
          <tbody>
            {datos.barrios
              .sort((a, b) => b.usables - a.usables)
              .map((b) => (
                <tr key={b.name}>
                  <td>{b.name}</td>
                  <td>{b.activos}</td>
                  <td>{b.usables}</td>
                  <td>{b.usd_m2_mediana ? Math.round(b.usd_m2_mediana).toLocaleString("es-AR") : "—"}</td>
                  <td className="tenue">
                    {b.usd_m2_oficial ? Math.round(b.usd_m2_oficial).toLocaleString("es-AR") : "—"}
                  </td>
                  <td className="tenue">
                    {b.desvio !== null ? `${(b.desvio * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td>
                    <span
                      className="chip"
                      style={{
                        color:
                          b.estado === "OK"
                            ? "var(--alta)"
                            : b.estado === "JUSTO"
                              ? "var(--ambar)"
                              : "var(--baja)",
                      }}
                    >
                      {b.estado}
                    </span>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function TarjetaDeFuente({ f }: { f: Fuente }) {
  const u = f.ultima;
  // `blocked` es la métrica a vigilar (doc 07 §10): si supera el 20% de lo
  // descubierto, el portal cambió su defensa.
  const tasaBloqueo = u && u.discovered > 0 ? u.blocked / u.discovered : 0;
  const alerta = tasaBloqueo > 0.2 || u?.status === "FAILED";

  return (
    <div className="tarjeta">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong>{f.source}</strong>
        <span className="chip" style={{ color: alerta ? "var(--ambar)" : "var(--alta)" }}>
          {alerta ? "⚠ REVISAR" : "● OK"}
        </span>
      </div>
      {u ? (
        <>
          <p style={{ margin: "0.5rem 0 0.2rem" }}>
            Última corrida: {fecha(u.started_at)} · {u.mode} · {u.status}
          </p>
          <p className="tenue" style={{ margin: 0 }}>
            Descubiertos {u.discovered} · Nuevos {u.created} · Actualizados {u.updated}
          </p>
          <p className="tenue" style={{ margin: 0 }}>
            Bloqueados {u.blocked}
            {u.discovered > 0 ? ` (${(tasaBloqueo * 100).toFixed(1)}%)` : ""} · Errores {u.errors}
          </p>
        </>
      ) : (
        <p className="tenue">Sin corridas.</p>
      )}
      <p className="tenue" style={{ margin: "0.4rem 0 0", fontSize: "0.85rem" }}>
        {f.corridas} corridas · costo del mes: USD {f.costo_del_mes.toFixed(2)}
      </p>
    </div>
  );
}
