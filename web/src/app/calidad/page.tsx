import { Aviso, PageHeader, Seccion, Stat, Vacio, fecha } from "@/components/ui";
import { ErrorDeApi, getCalidad, type Backtest, type CorridaDeComponente } from "@/lib/api";

export const dynamic = "force-dynamic";

const pct = (v: number | null | undefined, dec = 1) =>
  v === null || v === undefined ? "sin dato" : `${(v * 100).toFixed(dec).replace(".", ",")} %`;

/**
 * doc 07 §9: la pantalla que hace el proyecto demostrable.
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
        <div className="contenido-panel">
          <PageHeader titulo="Calidad del motor" />
          <Aviso tono="info" titulo="Solo para administradores">
            Esta pantalla muestra las métricas internas del motor.
          </Aviso>
        </div>
      );
    }
    throw e;
  }

  const ultimo = datos.backtests[0];

  return (
    <div className="contenido-panel">
      <PageHeader
        titulo="Calidad del motor"
        bajada="Lo que el motor mide de sí mismo: el error contra ventas reales y la salud de cada componente."
      />

      {!ultimo ? (
        <Vacio
          icono="calidad"
          titulo="Todavía no hay backtests guardados"
          texto={
            <>
              Corré <code>python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300</code> y
              el resultado aparece acá.
            </>
          }
        />
      ) : (
        <ComparacionQueImporta b={ultimo} />
      )}

      {datos.backtests.length > 0 && (
        <Seccion id="serie" titulo="Serie de backtests" bajada="Una fila por corrida, la más nueva arriba.">
          <div className="tabla scroll-x">
            <table>
              <thead>
                <tr>
                  <th scope="col">Fecha</th>
                  <th scope="col">Dataset</th>
                  <th scope="col" className="num">Casos</th>
                  <th scope="col" className="num">MdAPE</th>
                  <th scope="col" className="num">Baseline</th>
                  <th scope="col" className="num">PPE20</th>
                  <th scope="col" className="num">Hit rate</th>
                  <th scope="col" className="num">Cobertura</th>
                  <th scope="col" className="num">Sesgo</th>
                  <th scope="col">Motor</th>
                </tr>
              </thead>
              <tbody>
                {datos.backtests.map((b) => (
                  <tr key={b.id}>
                    <td className="tenue">{fecha(b.created_at, true)}</td>
                    <td>{b.dataset}</td>
                    <td className="num">{b.n_evaluated}</td>
                    <td className="num">
                      <strong>{pct(b.mdape)}</strong>
                    </td>
                    <td className="num tenue">{pct(b.baseline_mdape)}</td>
                    <td className="num">{pct(b.ppe20)}</td>
                    <td className="num">{pct(b.hit_rate)}</td>
                    <td className="num">{pct(b.coverage)}</td>
                    <td className="num">{pct(b.bias)}</td>
                    <td className="tenue">{b.engine_version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Seccion>
      )}

      <Componentes corridas={datos.componentes} />

      {ultimo && Object.keys(ultimo.por_barrio).length > 0 && (
        <Seccion id="barrios" titulo="Error por barrio" bajada="Del último backtest, de menor a mayor error.">
          <div className="tabla" style={{ maxWidth: "28rem" }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Barrio</th>
                  <th scope="col" className="num">MdAPE</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(ultimo.por_barrio)
                  .sort(([, a], [, b]) => a - b)
                  .map(([barrio, mdape]) => (
                    <tr key={barrio}>
                      <td>{barrio}</td>
                      <td className="num">{pct(mdape)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Seccion>
      )}
    </div>
  );
}

/** Arriba, la comparación que importa: sistema contra baseline (doc 07 §9). */
function ComparacionQueImporta({ b }: { b: Backtest }) {
  const mejora =
    b.mdape !== null && b.baseline_mdape !== null && b.baseline_mdape > 0
      ? (b.baseline_mdape - b.mdape) / b.baseline_mdape
      : null;
  return (
    <section className="tarjeta pila" aria-labelledby="comparacion-titulo">
      <div>
        <h2 id="comparacion-titulo" style={{ marginBottom: "0.2rem" }}>
          Sistema contra baseline
        </h2>
        <p className="ayuda" style={{ margin: 0 }}>
          {b.dataset} · {b.n_evaluated} casos · semilla {b.seed} · motor {b.engine_version} · método{" "}
          {b.method_version}
        </p>
      </div>
      <div className="stats">
        <Stat etiqueta="MdAPE del sistema" valor={pct(b.mdape)} detalle="error mediano contra ventas reales" />
        <Stat etiqueta="MdAPE del baseline" valor={pct(b.baseline_mdape)} detalle="mediana del barrio, sin motor" />
        <Stat etiqueta="PPE20" valor={pct(b.ppe20)} detalle="casos con error menor al 20 %" />
        <Stat etiqueta="Hit rate" valor={pct(b.hit_rate)} detalle="ventas dentro del rango" />
        <Stat etiqueta="Cobertura" valor={pct(b.coverage)} detalle="casos que sí pudo tasar" />
      </div>
      {mejora !== null && (
        <div>
          {mejora > 0 ? (
            <Aviso tono="exito">{pct(mejora, 0)} mejor que el baseline.</Aviso>
          ) : (
            // Si el sistema no le gana al baseline, se ve inmediatamente.
            <Aviso tono="alerta">El sistema NO le gana al baseline ({pct(-mejora, 0)} peor).</Aviso>
          )}
        </div>
      )}
      <div className="tabla scroll-x">
        <table>
          <thead>
            <tr>
              <th scope="col">Serie</th>
              <th scope="col" className="num">MdAPE</th>
              <th scope="col" className="num">PPE20</th>
              <th scope="col" className="num">Hit rate</th>
              <th scope="col" className="num">Cobertura</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <strong>Sistema</strong>
              </td>
              <td className="num">
                <strong>{pct(b.mdape)}</strong>
              </td>
              <td className="num">{pct(b.ppe20)}</td>
              <td className="num">{pct(b.hit_rate)}</td>
              <td className="num">{pct(b.coverage)}</td>
            </tr>
            <tr className="tenue">
              <td>Baseline (mediana del barrio)</td>
              <td className="num">{pct(b.baseline_mdape)}</td>
              <td className="num">{pct(b.baseline_ppe20)}</td>
              <td className="num">no aplica</td>
              <td className="num">no aplica</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
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
    <Seccion
      id="componentes"
      titulo="Evals de componente"
      bajada="Se lee la mediana de las últimas 5 corridas: una corrida sola es una muestra, no un número."
    >
      <div className="tabla scroll-x">
        <table>
          <thead>
            <tr>
              <th scope="col">Componente</th>
              <th scope="col" className="num">Mediana (últ. 5)</th>
              <th scope="col" className="num">Amplitud</th>
              <th scope="col" className="num">Objetivo</th>
              <th scope="col" className="num">n</th>
              <th scope="col" className="num">Corridas</th>
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
                  <td className="num">
                    <strong>{pct(mediana)}</strong>
                  </td>
                  <td className="num tenue">{pct(amplitud)}</td>
                  <td className="num tenue">{objetivo !== null ? pct(objetivo) : "sin objetivo"}</td>
                  <td className="num tenue">{primera.n}</td>
                  <td className="num tenue">{serie.length}</td>
                  <td className="tenue">{fecha(primera.created_at, true)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Seccion>
  );
}
