/**
 * El documento: lo que ve el agente en la ficha y el propietario en el link
 * compartido, en el mismo orden que el PDF (doc 20 §2.4):
 *
 *   1. la propiedad tasada        2. el valor, con confianza y respaldo
 *   3. la narrativa               4. (acciones y preguntas, solo en la ficha)
 *   5. el respaldo: los comparables, chicos, al final
 *   6. la trazabilidad
 */
import { MOTIVO_DE_DESCARTE, type ComparableDelInforme, type Propiedad } from "@/lib/api";
import { Chip, ChipDeConfianza, KeyValue, Stat, usd } from "./ui";

const TIPOS: Record<string, string> = { departamento: "departamento", casa: "casa", ph: "PH" };

const ESTADOS: Record<string, string> = {
  a_estrenar: "a estrenar",
  excelente: "excelente",
  muy_bueno: "muy bueno",
  bueno: "bueno",
  regular: "regular",
  a_refaccionar: "a refaccionar",
};

export function FichaDePropiedad({
  p,
  direccion,
  barrio,
}: {
  p?: Propiedad | null;
  direccion?: string | null;
  barrio?: string | null;
}) {
  const m2 = (v: number | null | undefined) => (v ? `${v.toLocaleString("es-AR")} m²` : null);
  const siNo = (v: boolean | null | undefined) => (v === null || v === undefined ? null : v ? "sí" : "no");
  const dir = p?.address ?? direccion ?? "sin dirección";
  const barrioCrudo = p?.neighborhood ?? barrio;
  // La dirección normalizada suele traer el barrio ("Cerviño 4400, Palermo"):
  // no repetirlo al lado.
  const bar = barrioCrudo && !dir.toLowerCase().includes(barrioCrudo.toLowerCase()) ? barrioCrudo : null;
  return (
    <section className="doc-propiedad" aria-labelledby="propiedad-titulo">
      <div className="etiqueta" id="propiedad-titulo">
        La propiedad tasada
      </div>
      <div className="direccion">
        {dir}
        {bar && <span className="tenue" style={{ fontWeight: 500 }}> · {bar}</span>}
      </div>
      <KeyValue
        datos={[
          { k: "Tipo", v: p?.property_type ? (TIPOS[p.property_type] ?? p.property_type) : null },
          { k: "Ambientes", v: p?.rooms },
          { k: "Dormitorios", v: p?.bedrooms },
          { k: "Baños", v: p?.bathrooms },
          { k: "Superficie total", v: m2(p?.surface_total) },
          { k: "Superficie cubierta", v: m2(p?.surface_covered) },
          { k: "Estado", v: p?.condition ? (ESTADOS[p.condition] ?? p.condition) : null },
          { k: "Orientación", v: p?.orientation },
          { k: "Piso", v: p?.floor_number },
          { k: "Ascensor", v: siNo(p?.has_elevator) },
          { k: "Antigüedad", v: p?.age_years !== null && p?.age_years !== undefined ? `${p.age_years} años` : null },
          { k: "Cocheras", v: p?.parking_spaces || null },
          { k: "Expensas", v: p?.expenses_ars ? `ARS ${Math.round(p.expenses_ars).toLocaleString("es-AR")}` : null },
        ]}
      />
    </section>
  );
}

export function BloqueDeValor({
  moneda,
  medio,
  bajo,
  alto,
  cierreBajo,
  cierreAlto,
  confianza,
  score,
  usados,
  encontrados,
  usdM2,
  sinRespaldo = false,
}: {
  sinRespaldo?: boolean;
  moneda?: string | null;
  medio: number | null | undefined;
  bajo: number | null | undefined;
  alto: number | null | undefined;
  cierreBajo: number | null | undefined;
  cierreAlto: number | null | undefined;
  confianza: string | null | undefined;
  score?: number | null;
  usados: number | null | undefined;
  encontrados: number | null | undefined;
  usdM2: number | null | undefined;
}) {
  const m = moneda ?? "USD";
  const plata = (v: number | null | undefined) =>
    v === null || v === undefined ? "sin dato" : `${m} ${Math.round(v).toLocaleString("es-AR")}`;
  return (
    <section className="doc-valor" aria-label="El valor">
      <div className="principal">
        <div className="etiqueta" style={{ color: "var(--exito)" }}>
          Precio de publicación sugerido
        </div>
        <div className="cifra-grande">{plata(medio)}</div>
        <div style={{ fontSize: "0.88rem", lineHeight: 1.5 }}>
          Rango de confianza: <span className="cifra">{plata(bajo)} a {plata(alto)}</span>
          <br />
          {/* El rango de cierre va ARRIBA y no escondido: es el dato que evita
              la conversación incómoda tres meses después (doc 07 §6.1). */}
          Rango esperado de cierre:{" "}
          <strong className="cifra">
            {plata(cierreBajo)} a {plata(cierreAlto)}
          </strong>
          <br />
          <span className="tenue">
            Es lo que se espera cerrar si se publica al valor sugerido. Los precios de publicación
            son la referencia; el cierre queda por debajo.
          </span>
        </div>
      </div>
      <div className="lateral">
        <Stat
          etiqueta="Confianza"
          valor={<ChipDeConfianza nivel={confianza} />}
          detalle={score !== null && score !== undefined ? `score ${score.toFixed(2).replace(".", ",")}` : undefined}
        />
        {!sinRespaldo && (
          <Stat
            etiqueta="Respaldo"
            valor={
              <span className="cifra">
                {usados ?? 0} de {encontrados ?? 0}
              </span>
            }
            detalle="avisos comparables usados"
          />
        )}
        {usdM2 ? (
          <Stat etiqueta="Valor por m²" valor={<span className="cifra">{usd(usdM2)}</span>} />
        ) : null}
      </div>
    </section>
  );
}

export function Narrativa({ html, md }: { html?: string | null; md?: string | null }) {
  if (!html && !md) return null;
  return (
    <section className="doc-narrativa" aria-label="El informe">
      {/* El HTML lo genera la API con el MISMO renderer que el PDF: CommonMark
          sin HTML embebido, así que lo crudo del markdown se escapa. Eso es lo
          que hace seguro el innerHTML de acá (bug 16 de la Etapa 3). */}
      {html ? (
        <div dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <div style={{ whiteSpace: "pre-wrap" }}>{md}</div>
      )}
    </section>
  );
}

export function Limitaciones({ items }: { items: string[] | undefined }) {
  if (!items || items.length === 0) return null;
  // Nunca colapsado (doc 07 §6.6).
  return (
    <section className="tarjeta-hundida" style={{ marginTop: "1.5rem" }} aria-labelledby="limitaciones-titulo">
      <h2 id="limitaciones-titulo">Limitaciones</h2>
      <ul style={{ margin: 0, paddingLeft: "1.2rem" }}>
        {items.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </section>
  );
}

/** Los comparables, chicos y al final: sostienen el número, no son el número. */
export function Respaldo({
  items,
  usados,
  encontrados,
}: {
  items: ComparableDelInforme[] | undefined;
  usados: number | null | undefined;
  encontrados: number | null | undefined;
}) {
  if (!items || items.length === 0) return null;
  const ordenados = [...items].sort((a, b) => Number(b.included) - Number(a.included));
  const n = (v: number | null | undefined) => (v === null || v === undefined ? null : Math.round(v).toLocaleString("es-AR"));
  return (
    <section className="doc-respaldo" aria-labelledby="respaldo-titulo">
      <h2 id="respaldo-titulo">Respaldo: los {encontrados ?? items.length} avisos analizados</h2>
      <p className="ayuda">
        Primero los {usados ?? 0} que sustentan el valor; después los descartados, cada uno con su
        motivo. Precios de publicación, no de cierre. «Ajustado» es el USD/m² llevado a las
        características de la propiedad tasada.
      </p>
      <div className="tabla tabla-chica scroll-x">
        <table>
          <thead>
            <tr>
              <th scope="col" className="num">#</th>
              <th scope="col">Origen</th>
              <th scope="col">Dirección</th>
              <th scope="col" className="num">Precio</th>
              <th scope="col" className="num">m²</th>
              <th scope="col" className="num">USD/m²</th>
              <th scope="col" className="num">Ajustado</th>
              <th scope="col">Uso</th>
            </tr>
          </thead>
          <tbody>
            {ordenados.map((c, i) => (
              <tr key={`${c.address}-${i}`} className={c.included ? "" : "fuera"}>
                <td className="num">{i + 1}</td>
                <td>{c.source}</td>
                <td>
                  {c.url ? (
                    <a href={c.url} target="_blank" rel="noreferrer noopener">
                      {c.address ?? "sin dirección"}
                    </a>
                  ) : (
                    (c.address ?? "sin dirección")
                  )}
                </td>
                <td className="num">{n(c.price) ?? "sin dato"}</td>
                <td className="num">{n(c.surface_weighted) ?? "sin dato"}</td>
                <td className="num">{n(c.raw_price_per_m2) ?? "sin dato"}</td>
                <td className="num">{n(c.adjusted_price_per_m2) ?? "sin dato"}</td>
                <td>
                  {c.included ? (
                    <Chip tono="exito">usado</Chip>
                  ) : (
                    <span style={{ fontStyle: "italic" }}>
                      {MOTIVO_DE_DESCARTE[c.exclusion_reason ?? ""] ?? c.exclusion_reason ?? "descartado"}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Trazabilidad({ partes }: { partes: (string | null | undefined)[] }) {
  return <p className="doc-pie">{partes.filter(Boolean).join(" · ")}</p>;
}
