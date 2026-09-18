import Link from "next/link";
import {
  ErrorDeApi,
  NOMBRE_DE_CAMPO,
  listarComparables,
  type Comparable,
} from "@/lib/api";
import { Revisar } from "./revisar";

export const dynamic = "force-dynamic";

const usd = (v: number | null) =>
  v === null ? "—" : `USD ${Math.round(v).toLocaleString("es-AR")}`;

/**
 * `/comparables` — el explorador del corpus (doc 07 §8).
 *
 * La decisión que define esta pantalla: **la descripción original y lo extraído
 * van uno al lado del otro**. Doc 07 dice que es "la forma más rápida de
 * detectar que el extractor se está equivocando", y hoy es además la única
 * forma práctica de hacer crecer el golden set — que es lo que bloquea poder
 * medir el nodo 4 (varianza de 17,6 pp sobre 24 avisos anotados).
 *
 * Lo que NO hace: dejar corregir la feature desde acá. La corrección es
 * anotación humana y va al golden set; si la UI escribiera la feature
 * "arreglada" en el corpus, el eval mediría el acuerdo del modelo con lo último
 * que alguien tocó.
 */
function Valor({ campo, valor }: { campo: string; valor: unknown }) {
  const vacio = valor === null || valor === undefined;
  return (
    <div style={{ display: "flex", gap: "0.5rem", fontSize: "0.86rem" }}>
      <span className="tenue" style={{ minWidth: "6.5rem" }}>
        {NOMBRE_DE_CAMPO[campo] ?? campo}
      </span>
      <span style={{ color: vacio ? "var(--ambar)" : "var(--texto)", fontWeight: vacio ? 400 : 600 }}>
        {vacio ? "sin dato" : typeof valor === "boolean" ? (valor ? "sí" : "no") : String(valor)}
      </span>
    </div>
  );
}

function Fila({ c }: { c: Comparable }) {
  const f = c.features;
  return (
    <article className="tarjeta" style={{ marginBottom: "1rem" }}>
      <header
        style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "baseline" }}
      >
        <strong style={{ fontSize: "1.02rem" }}>{c.address ?? "sin dirección"}</strong>
        <span className="chip">{c.source}</span>
        {c.neighborhood && <span className="tenue">{c.neighborhood}</span>}
        {c.cluster_id && (
          // Un aviso en un cluster pesa UNA vez en la mediana. Verlo acá es lo
          // que permite entender por qué el corpus tiene menos propiedades que
          // avisos.
          <span className="chip" style={{ color: "var(--acento)" }}>
            duplicado
          </span>
        )}
        <span style={{ marginLeft: "auto", fontWeight: 700 }}>{usd(c.price)}</span>
      </header>

      <p className="tenue" style={{ margin: "0.35rem 0 0.9rem" }}>
        {c.surface_weighted ? `${c.surface_weighted} m² pond.` : "sin superficie"}
        {c.usd_per_m2 ? ` · ${c.usd_per_m2.toLocaleString("es-AR")} USD/m²` : ""}
        {c.rooms ? ` · ${c.rooms} amb` : ""}
        {c.url && (
          <>
            {" · "}
            <a href={c.url} target="_blank" rel="noreferrer noopener">
              ver el aviso
            </a>
          </>
        )}
      </p>

      {/* Las dos mitades. En celular se apilan; en escritorio van al lado. */}
      <div
        style={{
          display: "grid",
          gap: "1rem",
          gridTemplateColumns: "repeat(auto-fit, minmax(19rem, 1fr))",
        }}
      >
        <div>
          <div className="tenue" style={{ fontSize: "0.78rem", marginBottom: "0.3rem" }}>
            LO QUE DICE EL AVISO
          </div>
          <div
            style={{
              fontSize: "0.86rem",
              maxHeight: "11rem",
              overflowY: "auto",
              whiteSpace: "pre-wrap",
              border: "1px solid var(--borde)",
              borderRadius: 7,
              padding: "0.6rem 0.7rem",
            }}
          >
            {c.description || "(el aviso no trae descripción)"}
          </div>
        </div>

        <div>
          <div className="tenue" style={{ fontSize: "0.78rem", marginBottom: "0.3rem" }}>
            LO QUE EXTRAJO EL SISTEMA
          </div>
          {f === null ? (
            <p className="aviso" style={{ fontSize: "0.86rem" }}>
              Todavía sin extraer. Se extrae la primera vez que este aviso entra
              como candidato a un informe, y queda cacheado.
            </p>
          ) : (
            <div style={{ display: "grid", gap: "0.25rem" }}>
              {(["condition", "orientation", "floor_number", "has_elevator", "age_years"] as const).map(
                (k) => (
                  <Valor key={k} campo={k} valor={f[k]} />
                ),
              )}
              <div className="tenue" style={{ fontSize: "0.76rem", marginTop: "0.5rem" }}>
                {f.extractor_model} · {f.extractor_version}
                {f.confidence !== null && ` · confianza ${f.confidence.toFixed(2)}`}
              </div>
              <Revisar id={c.id} marcado={f.needs_review} />
            </div>
          )}
        </div>
      </div>

      {c.faltantes.length > 0 && (
        // "Sin dato, sin ajuste" (doc 05 §4.2). Que se vea cuántos coeficientes
        // quedaron apagados es la explicación de por qué el rango es ancho.
        <p className="tenue" style={{ fontSize: "0.8rem", margin: "0.8rem 0 0" }}>
          {c.faltantes.length} de 5 coeficientes sin dato:{" "}
          {c.faltantes.map((x) => NOMBRE_DE_CAMPO[x] ?? x).join(", ")}
        </p>
      )}
    </article>
  );
}

export default async function Comparables({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const p = await searchParams;

  let datos;
  try {
    datos = await listarComparables({
      barrio: p.barrio,
      fuente: p.fuente,
      q: p.q,
      cursor: p.cursor,
      solo_sin_extraer: p.sin_extraer,
      solo_para_revisar: p.para_revisar,
      limit: "10",
    });
  } catch (e) {
    const detalle = e instanceof ErrorDeApi ? `La API devolvió ${e.status}.` : "No respondió.";
    return (
      <div className="aviso">
        <h2 style={{ marginTop: 0 }}>No pudimos leer el corpus</h2>
        <p>{detalle}</p>
      </div>
    );
  }

  const filtro = (clave: string, valor: string, texto: string) => {
    const q = new URLSearchParams(
      Object.entries(p).filter(([k, v]) => v && k !== "cursor") as [string, string][],
    );
    if (q.get(clave) === valor) q.delete(clave);
    else q.set(clave, valor);
    const activo = p[clave] === valor;
    return (
      <Link
        href={`/comparables?${q}`}
        className="chip"
        style={{
          textDecoration: "none",
          color: activo ? "var(--acento)" : "var(--tenue)",
          fontWeight: activo ? 700 : 500,
        }}
      >
        {texto}
      </Link>
    );
  };

  return (
    <>
      <h1>Comparables</h1>
      <p className="tenue">
        {datos.total_aprox} avisos vigentes. La descripción original y lo que extrajo el
        sistema, uno al lado del otro.
      </p>

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", margin: "1rem 0 1.5rem" }}>
        {filtro("barrio", "Palermo", "Palermo")}
        {filtro("barrio", "Belgrano", "Belgrano")}
        {filtro("fuente", "PORTAL_A", "Portal A")}
        {filtro("fuente", "PORTAL_B", "Portal B")}
        {filtro("sin_extraer", "true", "sin extraer")}
        {filtro("para_revisar", "true", "marcados para revisar")}
      </div>

      {datos.items.length === 0 ? (
        <p className="tenue">Ningún aviso con esos filtros.</p>
      ) : (
        datos.items.map((c) => <Fila key={c.id} c={c} />)
      )}

      {datos.next_cursor && (
        <p>
          <Link
            href={`/comparables?${new URLSearchParams({
              ...(Object.fromEntries(
                Object.entries(p).filter(([, v]) => v),
              ) as Record<string, string>),
              cursor: datos.next_cursor,
            })}`}
          >
            Ver más →
          </Link>
        </p>
      )}
    </>
  );
}
