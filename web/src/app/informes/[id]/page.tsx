import Link from "next/link";
import { notFound } from "next/navigation";
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

const usd = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `USD ${Math.round(v).toLocaleString("es-AR")}`;

export default async function Ficha({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let informe: Informe;
  try {
    informe = await getInforme(id);
  } catch (e) {
    // 404 **y 422**. La API devuelve 422 cuando el id no es un UUID, y acá se
    // contemplaba solo el 404: `/informes/no-es-uuid` caía al `throw e` y el
    // usuario recibía un 500. Un id que no es un UUID no existe, por
    // definición — las dos respuestas son la misma pantalla (H-37).
    if (e instanceof ErrorDeApi && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }

  if (informe.status === "QUEUED" || informe.status === "RUNNING") return <Corriendo i={informe} />;
  if (informe.status === "INSUFFICIENT_DATA") return <SinDatos i={informe} />;
  if (informe.status === "FAILED") return <Fallado i={informe} />;
  return <Resultado i={informe} />;
}

/** doc 07 §5 — el stepper en vivo, en castellano llano. */
function Corriendo({ i }: { i: Informe }) {
  const hechos = new Map(i.progress.steps.map((s) => [s.node, s]));
  const orden = Object.keys(NOMBRE_DE_NODO).filter((n) => n !== "ondemand_capture");

  return (
    <>
      {/* El polling lo hace un meta refresh y no JS: son 2 segundos y esto
          funciona aunque el bundle no haya cargado todavía. */}
      <meta httpEquiv="refresh" content="2" />
      <h1>Generando el informe…</h1>
      <p className="tenue">
        {i.progress.completed} de {i.progress.total} pasos
      </p>

      <ol style={{ listStyle: "none", padding: 0, display: "grid", gap: "0.55rem" }}>
        {orden.map((nodo) => {
          const paso = hechos.get(nodo);
          const corriendo = i.progress.current_node === nodo;
          return (
            <li key={nodo} style={{ display: "flex", gap: "0.7rem" }}>
              <span aria-hidden style={{ width: "1.2rem" }}>
                {paso ? "✓" : corriendo ? "◐" : "○"}
              </span>
              <span style={{ color: paso || corriendo ? "var(--texto)" : "var(--tenue)" }}>
                {NOMBRE_DE_NODO[nodo]}
              </span>
              {paso?.duration_ms != null && (
                <span className="tenue">{(paso.duration_ms / 1000).toFixed(1)} s</span>
              )}
            </li>
          );
        })}
      </ol>
    </>
  );
}

/**
 * doc 07 §7 — no es una pantalla de error, es una pantalla que EXPLICA.
 *
 * La API ya devuelve las sugerencias armadas con lo que el corpus sí tiene
 * cerca. Mostrarlas como texto y no como un "no hay datos" pelado es la
 * diferencia entre un callejón sin salida y una tarea.
 */
function SinDatos({ i }: { i: Informe }) {
  const d = i.detail;
  return (
    <>
      <div className="aviso">
        <h1 style={{ marginTop: 0 }}>No pudimos generar el informe</h1>
        <p>{i.insufficient_reason}</p>

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
      </div>
      <p style={{ marginTop: "1.2rem", display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        {/* Reintentar sirve cuando entraron datos nuevos al corpus desde que
            este informe dio "sin datos". */}
        <Regenerar id={i.report_id} />
        <Link href="/informes/nuevo">
          <button>Probar con otra propiedad</button>
        </Link>
      </p>
    </>
  );
}

function Fallado({ i }: { i: Informe }) {
  return (
    <div className="aviso">
      <h1 style={{ marginTop: 0 }}>El informe falló</h1>
      <p>
        Código: <code>{i.error_code ?? "desconocido"}</code>. Esto sí es un error del sistema, no
        una falta de datos.
      </p>
    </div>
  );
}

function Resultado({ i }: { i: Informe }) {
  const v = i.valuation;
  return (
    <>
      <div className="tarjeta">
        <div style={{ fontSize: "1.9rem", fontWeight: 700, letterSpacing: "-0.02em" }}>
          {usd(v?.suggested_listing_price.low)} — <strong>{usd(v?.suggested_listing_price.mid)}</strong>{" "}
          — {usd(v?.suggested_listing_price.high)}
        </div>
        <div className="tenue" style={{ marginTop: "0.2rem" }}>
          {v?.price_per_m2 ? `USD ${Math.round(v.price_per_m2).toLocaleString("es-AR")} /m²` : ""}
          {"  ·  "}
          {/* Ningún número aparece sin su confianza al lado (doc 07 §12). */}
          Confianza <span className={`chip chip-${i.confidence?.level}`}>{i.confidence?.level}</span>
        </div>

        {/* El rango de cierre va ARRIBA y no escondido: es el dato que evita la
            conversación incómoda tres meses después (doc 07 §6.1). */}
        <p style={{ marginBottom: 0 }}>
          Rango esperado de cierre:{" "}
          <strong>
            {usd(v?.expected_closing_range.low)} – {usd(v?.expected_closing_range.high)}
          </strong>
        </p>
      </div>

      <p className="tenue" style={{ marginTop: "1rem" }}>
        {i.comparables?.used} comparables usados de {i.comparables?.found} encontrados
      </p>

      <p style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", alignItems: "center" }}>
        <a href={`/informes/${i.report_id}/pdf`}>
          <button>Descargar PDF</button>
        </a>
        <Regenerar id={i.report_id} />
        <Compartir id={i.report_id} />
      </p>

      {i.narrative_md && (
        <section style={{ marginTop: "1.5rem" }}>
          <h2>Informe</h2>
          {/* El HTML lo genera la API con `markdown_a_html`, el MISMO renderer
              que usa el PDF: CommonMark con `html: false`, así que el HTML crudo
              del markdown se escapa en vez de pasar. Eso es lo que hace seguro
              el `dangerouslySetInnerHTML` de acá — el bug 16 fue justamente un
              `<script>` que llegaba entero al PDF por dejar pasar HTML.

              Hasta el 15/08 esto era `whiteSpace: pre-wrap` sobre el markdown
              crudo, así que la pantalla del informe —el entregable— mostraba
              `## Resumen ejecutivo` y `**USD 134.667**` con los símbolos a la
              vista. El fallback sigue siendo el texto plano. */}
          {i.narrative_html ? (
            <div
              className="narrativa"
              dangerouslySetInnerHTML={{ __html: i.narrative_html }}
            />
          ) : (
            <div style={{ whiteSpace: "pre-wrap" }}>{i.narrative_md}</div>
          )}
        </section>
      )}

      <Preguntar id={i.report_id} />

      {/* Nunca colapsado (doc 07 §6.6). */}
      <div className="tarjeta" style={{ marginTop: "1.8rem", background: "transparent" }}>
        <strong>Limitaciones</strong>
        <ul style={{ marginBottom: 0 }}>
          {(i.limitations ?? []).map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </div>

      <p className="tenue" style={{ fontSize: "0.8rem", marginTop: "1.5rem" }}>
        método {i.methodology_version} · prompts {i.prompt_bundle_version} ·{" "}
        {i.cost_usd !== null ? `USD ${i.cost_usd.toFixed(4)}` : ""} · id {i.report_id}
      </p>
    </>
  );
}
