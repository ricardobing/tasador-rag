import { ErrorDeApi, getCalidad, type Backtest, type CorridaDeComponente } from "@/lib/api";

export const dynamic = "force-dynamic";

const pct = (v: number | null | undefined, dec = 1) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(dec)}%`;

const fecha = (iso: string) =>
  new Date(iso).toLocaleDateString("es-AR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/**
 * doc 07 §9 — la pantalla que hace el proyecto demostrable.
 *
 * No hay botón "correr backtest": tarda minutos y corre por CLI
 * (`python -m tasador.eval.run`). Un botón que dispara un job largo merece
 * cola y notificación propias; hacerlo a medias sería un gate decorativo.
 */
export default async function Calidad() {
  let datos;
  try {
    datos = await getCalidad();
  } catch (e) {
    if (e instanceof ErrorDeApi && e.status === 403) {
      return (
        <div className="aviso">
          <h2 style={{ marginTop: 0 }}>Solo para administradores</h2>
          <p>Esta pantalla muestra las métricas internas del motor.</p>
        </div>
      );
    }
    throw e;
  }

  const ultimo = datos.backtests[0];

  return (
    <>
      <h1>Calidad del motor</h1>

      {!ultimo ? (
        <p className="tenue">
          Todavía no hay backtests guardados. Corré{" "}
          <code>python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300</code>.
        </p>
      ) : (
        <ComparacionQueImporta b={ultimo} />
      )}

      {datos.backtests.length > 0 && (
        <>
          <h2 style={{ marginTop: "2rem" }}>Serie de backtests</h2>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th scope="col">Fecha</th>
                  <th scope="col">Dataset</th>
                  <th scope="col">Casos</th>
                  <th scope="col">MdAPE</th>
                  <th scope="col">Baseline</th>
                  <th scope="col">PPE20</th>
                  <th scope="col">Hit rate</th>
                  <th scope="col">Cobertura</th>
                  <th scope="col">Sesgo</th>
                  <th scope="col">Motor</th>
                </tr>
              </thead>
              <tbody>
                {datos.backtests.map((b) => (
                  <tr key={b.id}>
                    <td className="tenue">{fecha(b.created_at)}</td>
                    <td>{b.dataset}</td>
                    <td>{b.n_evaluated}</td>
                    <td>
                      <strong>{pct(b.mdape)}</strong>
                    </td>
                    <td className="tenue">{pct(b.baseline_mdape)}</td>
                    <td>{pct(b.ppe20)}</td>
                    <td>{pct(b.hit_rate)}</td>
                    <td>{pct(b.coverage)}</td>
                    <td>{pct(b.bias)}</td>
                    <td className="tenue">{b.engine_version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <Componentes corridas={datos.componentes} />

      {ultimo && Object.keys(ultimo.por_barrio).length > 0 && (
        <>
          <h2 style={{ marginTop: "2rem" }}>Error por barrio (último backtest)</h2>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th scope="col">Barrio</th>
                  <th scope="col">MdAPE</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(ultimo.por_barrio)
                  .sort(([, a], [, b]) => a - b)
                  .map(([barrio, mdape]) => (
                    <tr key={barrio}>
                      <td>{barrio}</td>
                      <td>{pct(mdape)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

/** Arriba, la comparación que importa: sistema contra baseline (doc 07 §9). */
function ComparacionQueImporta({ b }: { b: Backtest }) {
  const mejora =
    b.mdape !== null && b.baseline_mdape !== null && b.baseline_mdape > 0
      ? (b.baseline_mdape - b.mdape) / b.baseline_mdape
      : null;
  return (
    <div className="tarjeta">
      <div className="scroll-x">
        <table style={{ marginBottom: 0 }}>
          <thead>
            <tr>
              <th scope="col"></th>
              <th scope="col">MdAPE</th>
              <th scope="col">PPE20</th>
              <th scope="col">Hit rate</th>
              <th scope="col">Cobertura</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <strong>Sistema</strong>
              </td>
              <td>
                <strong>{pct(b.mdape)}</strong>
              </td>
              <td>{pct(b.ppe20)}</td>
              <td>{pct(b.hit_rate)}</td>
              <td>{pct(b.coverage)}</td>
            </tr>
            <tr className="tenue">
              <td>Baseline (mediana del barrio)</td>
              <td>{pct(b.baseline_mdape)}</td>
              <td>{pct(b.baseline_ppe20)}</td>
              <td>—</td>
              <td>—</td>
            </tr>
          </tbody>
        </table>
      </div>
      {mejora !== null && (
        <p style={{ marginBottom: 0 }}>
          {mejora > 0 ? (
            <span style={{ color: "var(--alta)" }}>▲ {pct(mejora, 0)} mejor que el baseline</span>
          ) : (
            // Si el sistema no le gana al baseline, se ve inmediatamente.
            <span style={{ color: "var(--ambar)" }}>
              ▼ el sistema NO le gana al baseline ({pct(-mejora, 0)} peor)
            </span>
          )}
        </p>
      )}
      <p className="tenue" style={{ fontSize: "0.8rem", marginBottom: 0 }}>
        {b.dataset} · {b.n_evaluated} casos · semilla {b.seed} · motor {b.engine_version} · método{" "}
        {b.method_version}
      </p>
    </div>
  );
}

/**
 * Los evals de componente se leen por la MEDIANA de las últimas 5 corridas,
 * nunca por una sola: la amplitud medida es de 17,6 pp con el mismo código
 * (ESTADO §5.1). Mostrar la última corrida como "el número" sería repetir el
 * error que ya nos costó un día.
 */
function Componentes({ corridas }: { corridas: CorridaDeComponente[] }) {
  const grupos = new Map<string, CorridaDeComponente[]>();
  for (const c of corridas) {
    const clave = `${c.componente} · ${c.metrica}`;
    grupos.set(clave, [...(grupos.get(clave) ?? []), c]);
  }
  if (grupos.size === 0) return null;

  return (
    <>
      <h2 style={{ marginTop: "2rem" }}>Evals de componente</h2>
      <p className="tenue" style={{ fontSize: "0.85rem" }}>
        Se lee la mediana de las últimas 5 corridas — una corrida sola es una muestra, no un número.
      </p>
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th scope="col">Componente</th>
              <th scope="col">Mediana (últ. 5)</th>
              <th scope="col">Amplitud</th>
              <th scope="col">Objetivo</th>
              <th scope="col">n</th>
              <th scope="col">Corridas</th>
              <th scope="col">Última</th>
            </tr>
          </thead>
          <tbody>
            {[...grupos.entries()].map(([clave, serie]) => {
              // `grupos` se arma agregando: toda serie tiene al menos 1 elemento.
              const primera = serie[0]!;
              const ult5 = serie.slice(0, 5).map((c) => c.valor);
              const ordenados = [...ult5].sort((a, b) => a - b);
              const mediana = ordenados[Math.floor((ordenados.length - 1) / 2)]!;
              const amplitud = ordenados[ordenados.length - 1]! - ordenados[0]!;
              const objetivo = primera.objetivo;
              return (
                <tr key={clave}>
                  <td>{clave}</td>
                  <td>
                    <strong>{pct(mediana)}</strong>
                  </td>
                  <td className="tenue">{pct(amplitud)}</td>
                  <td className="tenue">{objetivo !== null ? pct(objetivo) : "—"}</td>
                  <td className="tenue">{primera.n}</td>
                  <td className="tenue">{serie.length}</td>
                  <td className="tenue">{fecha(primera.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
