import { Aviso, Chip, PageHeader, Seccion, Stat, Vacio, fecha, type TonoDeChip } from "@/components/ui";
import { ErrorDeApi, getFuentes, type Fuente } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * doc 07 §10: la pantalla que avisa que un portal cambió su defensa ANTES de
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
        <div className="contenido-panel">
          <PageHeader titulo="Fuentes de datos" />
          <Aviso tono="info" titulo="Solo para administradores">
            Esta pantalla muestra la operación de las fuentes de datos.
          </Aviso>
        </div>
      );
    }
    throw e;
  }

  const TONO_DE_COBERTURA: Record<string, TonoDeChip> = { OK: "exito", JUSTO: "alerta" };

  return (
    <div className="contenido-panel">
      <PageHeader
        titulo="Fuentes de datos"
        bajada="De dónde salen los avisos, cómo vino la última corrida y cuánto cubre cada barrio."
      />

      {datos.fuentes.length === 0 ? (
        <Vacio icono="fuentes" titulo="Todavía no hay corridas de ingesta" texto="La ingesta corre por script; cuando haya una corrida aparece acá." />
      ) : (
        <div className="stats" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(18rem, 1fr))" }}>
          {datos.fuentes.map((f) => (
            <TarjetaDeFuente key={f.source} f={f} />
          ))}
        </div>
      )}

      <Seccion
        id="cobertura"
        titulo="Cobertura y sesgo por barrio"
        bajada="El USD/m² oficial es la serie de BA Data, que llega hasta 2019: el desvío se muestra como referencia histórica, no como alarma."
      >
        {/* Siete columnas numéricas: en el celular se lee mejor con scroll que
            apilada, porque la comparación corpus/oficial es entre columnas. */}
        <div className="tabla scroll-x">
          <table>
            <thead>
              <tr>
                <th scope="col">Barrio</th>
                <th scope="col" className="num">Avisos activos</th>
                <th scope="col" className="num">Usables</th>
                <th scope="col" className="num">USD/m² corpus</th>
                <th scope="col" className="num">USD/m² oficial (2019)</th>
                <th scope="col" className="num">Desvío</th>
                <th scope="col">Estado</th>
              </tr>
            </thead>
            <tbody>
              {datos.barrios
                .sort((a, b) => b.usables - a.usables)
                .map((b) => (
                  <tr key={b.name}>
                    <td data-col="Barrio">{b.name}</td>
                    <td data-col="Activos" className="num">{b.activos}</td>
                    <td data-col="Usables" className="num">{b.usables}</td>
                    <td data-col="USD/m² corpus" className="num">
                      {b.usd_m2_mediana ? Math.round(b.usd_m2_mediana).toLocaleString("es-AR") : "sin dato"}
                    </td>
                    <td data-col="USD/m² oficial" className="num tenue">
                      {b.usd_m2_oficial ? Math.round(b.usd_m2_oficial).toLocaleString("es-AR") : "sin dato"}
                    </td>
                    <td data-col="Desvío" className="num tenue">
                      {b.desvio !== null ? `${(b.desvio * 100).toFixed(1).replace(".", ",")} %` : "sin dato"}
                    </td>
                    <td data-col="Estado">
                      <Chip tono={TONO_DE_COBERTURA[b.estado] ?? "neutro"}>{b.estado}</Chip>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </Seccion>
    </div>
  );
}

function TarjetaDeFuente({ f }: { f: Fuente }) {
  const u = f.ultima;
  // `blocked` es la métrica a vigilar (doc 07 §10): si supera el 20 % de lo
  // descubierto, el portal cambió su defensa.
  const tasaBloqueo = u && u.discovered > 0 ? u.blocked / u.discovered : 0;
  const alerta = tasaBloqueo > 0.2 || u?.status === "FAILED";

  return (
    <article className="tarjeta pila" aria-label={`Fuente ${f.source}`}>
      <div className="fila" style={{ justifyContent: "space-between" }}>
        <strong style={{ color: "var(--texto-fuerte)" }}>{f.source}</strong>
        <Chip tono={alerta ? "alerta" : "exito"}>{alerta ? "REVISAR" : "OK"}</Chip>
      </div>
      {u ? (
        <>
          <div className="stats" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            <Stat etiqueta="Descubiertos" valor={u.discovered} />
            <Stat etiqueta="Nuevos" valor={u.created} />
            <Stat
              etiqueta="Bloqueados"
              valor={u.blocked}
              detalle={u.discovered > 0 ? `${(tasaBloqueo * 100).toFixed(1).replace(".", ",")} % de lo descubierto` : undefined}
            />
          </div>
          <p className="tenue" style={{ margin: 0, fontSize: "0.85rem" }}>
            Última corrida {fecha(u.started_at, true)} · {u.mode} · {u.status} · actualizados {u.updated} ·
            errores {u.errors}
          </p>
        </>
      ) : (
        <p className="tenue" style={{ margin: 0 }}>
          Sin corridas.
        </p>
      )}
      <p className="tenue" style={{ margin: 0, fontSize: "0.82rem" }}>
        {f.corridas} corridas · costo del mes USD {f.costo_del_mes.toFixed(2)}
      </p>
    </article>
  );
}
