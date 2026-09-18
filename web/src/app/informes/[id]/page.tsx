import { notFound } from "next/navigation";
import { Icono } from "@/components/iconos";
import {
  BloqueDeValor,
  FichaDePropiedad,
  Limitaciones,
  Narrativa,
  Respaldo,
  Trazabilidad,
} from "@/components/documento";
import { Aviso, BotonLink, PageHeader } from "@/components/ui";
import {
  ErrorDeApi,
  MOTIVO_DE_DESCARTE,
  NOMBRE_DE_NODO,
  getInforme,
  type Informe,
} from "@/lib/api";
import { Compartir, Regenerar } from "./acciones";
import { Preguntar } from "./preguntar";

export const dynamic = "force-dynamic";

export default async function Ficha({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let informe: Informe;
  try {
    informe = await getInforme(id, { descartados: true });
  } catch (e) {
    // 404 **y 422**. La API devuelve 422 cuando el id no es un UUID: un id
    // que no es un UUID no existe, por definición (H-37).
    if (e instanceof ErrorDeApi && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }

  if (informe.status === "QUEUED" || informe.status === "RUNNING") return <Corriendo i={informe} />;
  if (informe.status === "INSUFFICIENT_DATA") return <SinDatos i={informe} />;
  if (informe.status === "FAILED") return <Fallado i={informe} />;
  return <Resultado i={informe} />;
}

const MIGAS = [{ href: "/informes", texto: "Informes" }];

/** doc 07 §5: el stepper en vivo, en castellano llano. */
function Corriendo({ i }: { i: Informe }) {
  const hechos = new Map(i.progress.steps.map((s) => [s.node, s]));
  const orden = Object.keys(NOMBRE_DE_NODO).filter((n) => n !== "ondemand_capture");

  return (
    <div className="contenido-documento">
      {/* El polling lo hace un meta refresh y no JS: son 2 segundos y esto
          funciona aunque el bundle no haya cargado todavía. */}
      <meta httpEquiv="refresh" content="2" />
      <PageHeader
        titulo="Generando el informe…"
        bajada={`${i.progress.completed} de ${i.progress.total} pasos. Suele tardar entre uno y dos minutos.`}
        migas={MIGAS}
      />
      <FichaDePropiedad p={i.property} />
      <ol className="pasos tarjeta" style={{ marginTop: "1rem" }}>
        {orden.map((nodo) => {
          const paso = hechos.get(nodo);
          const actual = i.progress.current_node === nodo;
          const estado = paso ? "hecho" : actual ? "actual" : "pendiente";
          return (
            <li key={nodo} className="paso" data-estado={estado}>
              <Icono nombre={paso ? "tilde" : actual ? "cargando" : "circulo"} tamano={18} />
              {NOMBRE_DE_NODO[nodo]}
              {paso?.duration_ms != null && (
                <span className="tiempo">{(paso.duration_ms / 1000).toFixed(1)} s</span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/**
 * doc 07 §7: no es una pantalla de error, es una pantalla que EXPLICA.
 *
 * La API ya devuelve las sugerencias armadas con lo que el corpus sí tiene
 * cerca. Mostrarlas como texto y no como un "no hay datos" pelado es la
 * diferencia entre un callejón sin salida y una tarea.
 */
function SinDatos({ i }: { i: Informe }) {
  const d = i.detail;
  return (
    <div className="contenido-documento">
      <PageHeader titulo="No pudimos generar el informe" migas={MIGAS} />
      <FichaDePropiedad p={i.property} />
      <div style={{ marginTop: "1rem" }}>
        <Aviso tono="alerta" titulo="Sin datos suficientes en la zona">
          <p style={{ marginTop: 0 }}>{i.insufficient_reason}</p>
          {d && d.excluded.length > 0 && (
            <>
              <p style={{ marginBottom: "0.3rem" }}>
                De {d.candidates_found ?? 0} avisos encontrados en la zona:
              </p>
              <ul style={{ marginTop: 0 }}>
                {d.excluded.map((e) => (
                  <li key={e.reason}>
                    {e.count} {MOTIVO_DE_DESCARTE[e.reason] ?? e.reason}
                  </li>
                ))}
              </ul>
            </>
          )}
          {d && d.suggestions.length > 0 && (
            <>
              <p style={{ marginBottom: "0.3rem" }}>
                <strong>Qué podés hacer:</strong>
              </p>
              <ul style={{ marginTop: 0 }}>
                {d.suggestions.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </>
          )}
        </Aviso>
      </div>
      <div className="acciones-formulario">
        {/* Reintentar sirve cuando entraron datos nuevos al corpus desde que
            este informe dio "sin datos". */}
        <Regenerar id={i.report_id} propiedad={i.property} />
        <BotonLink href="/informes/nuevo" icono="nuevo">
          Probar con otra propiedad
        </BotonLink>
      </div>
    </div>
  );
}

function Fallado({ i }: { i: Informe }) {
  return (
    <div className="contenido-documento">
      <PageHeader titulo="El informe falló" migas={MIGAS} />
      <FichaDePropiedad p={i.property} />
      <div style={{ marginTop: "1rem" }}>
        <Aviso tono="peligro" titulo="Esto sí es un problema del sistema, no una falta de datos">
          Código <code>{i.error_code ?? "desconocido"}</code>. Podés regenerar; si vuelve a fallar,
          avisá con el id del pie.
        </Aviso>
      </div>
      <div className="acciones-formulario">
        <Regenerar id={i.report_id} propiedad={i.property} />
      </div>
      <Trazabilidad partes={[`id ${i.report_id}`]} />
    </div>
  );
}

function Resultado({ i }: { i: Informe }) {
  const v = i.valuation;
  return (
    <div className="contenido-documento">
      <PageHeader
        titulo="Informe de mercado comparativo"
        bajada={i.generated_at ? `Generado el ${new Date(i.generated_at).toLocaleDateString("es-AR")}` : undefined}
        migas={MIGAS}
        acciones={
          <>
            <a href={`/informes/${i.report_id}/pdf`} className="boton">
              <Icono nombre="pdf" tamano={18} />
              Descargar PDF
            </a>
          </>
        }
      />

      <FichaDePropiedad p={i.property} />

      <BloqueDeValor
        moneda={v?.currency}
        medio={v?.suggested_listing_price.mid}
        bajo={v?.suggested_listing_price.low}
        alto={v?.suggested_listing_price.high}
        cierreBajo={v?.expected_closing_range.low}
        cierreAlto={v?.expected_closing_range.high}
        confianza={i.confidence?.level}
        score={i.confidence?.score}
        usados={i.comparables?.used}
        encontrados={i.comparables?.found}
        usdM2={v?.price_per_m2}
      />

      <div className="acciones-formulario">
        <Regenerar id={i.report_id} propiedad={i.property} />
        <Compartir id={i.report_id} />
      </div>

      <Narrativa html={i.narrative_html} md={i.narrative_md} />

      <Limitaciones items={i.limitations} />

      <Preguntar id={i.report_id} />

      <Respaldo items={i.comparables?.items} usados={i.comparables?.used} encontrados={i.comparables?.found} />

      <Trazabilidad
        partes={[
          `método ${i.methodology_version}`,
          `prompts ${i.prompt_bundle_version}`,
          i.cost_usd !== null ? `costo USD ${i.cost_usd.toFixed(4)}` : null,
          `id ${i.report_id}`,
        ]}
      />
    </div>
  );
}
