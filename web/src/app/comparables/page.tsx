import Link from "next/link";
import { Icono } from "@/components/iconos";
import { Aviso, BotonLink, Chip, PageHeader, Vacio, usd } from "@/components/ui";
import { ErrorDeApi, NOMBRE_DE_CAMPO, listarComparables, type Comparable } from "@/lib/api";
import { Revisar } from "./revisar";

export const dynamic = "force-dynamic";

/**
 * `/comparables`: el explorador del corpus (doc 07 §8).
 *
 * La decisión que define esta pantalla: **la descripción original y lo extraído
 * van uno al lado del otro**. Es "la forma más rápida de detectar que el
 * extractor se está equivocando", y la forma práctica de hacer crecer el
 * golden set.
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
      <span style={{ color: vacio ? "var(--alerta)" : "var(--texto-fuerte)", fontWeight: vacio ? 400 : 600 }}>
        {vacio ? "sin dato" : typeof valor === "boolean" ? (valor ? "sí" : "no") : String(valor)}
      </span>
    </div>
  );
}

function Aviso_({ c }: { c: Comparable }) {
  const f = c.features;
  return (
    <article className="tarjeta" style={{ marginBottom: "1rem" }}>
      <header className="fila" style={{ alignItems: "baseline" }}>
        <strong style={{ fontSize: "1.02rem", color: "var(--texto-fuerte)" }}>
          {c.address ?? "sin dirección"}
        </strong>
        <Chip>{c.source}</Chip>
        {c.neighborhood && <span className="tenue">{c.neighborhood}</span>}
        {c.cluster_id && (
          // Un aviso en un cluster pesa UNA vez en la mediana. Verlo acá es lo
          // que permite entender por qué el corpus tiene menos propiedades que
          // avisos.
          <Chip tono="marca">duplicado</Chip>
        )}
        <span className="cifra" style={{ marginLeft: "auto", fontWeight: 700, color: "var(--texto-fuerte)" }}>
          {usd(c.price) ?? "sin precio"}
        </span>
      </header>

      <p className="tenue" style={{ margin: "0.35rem 0 0.9rem", fontSize: "0.88rem" }}>
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
      <div className="dos-columnas">
        <div>
          <div className="etiqueta" style={{ marginBottom: "0.3rem" }}>
            LO QUE DICE EL AVISO
          </div>
          <div className="tarjeta-hundida" style={{ fontSize: "0.86rem", maxHeight: "11rem", overflowY: "auto", whiteSpace: "pre-wrap" }}>
            {c.description || "(el aviso no trae descripción)"}
          </div>
        </div>

        <div>
          <div className="etiqueta" style={{ marginBottom: "0.3rem" }}>
            LO QUE EXTRAJO EL SISTEMA
          </div>
          {f === null ? (
            <p className="tenue" style={{ fontSize: "0.86rem", margin: 0 }}>
              Todavía sin extraer. Se extrae la primera vez que este aviso entra como candidato a
              un informe, y queda cacheado.
            </p>
          ) : (
            <div className="pila" style={{ gap: "0.25rem" }}>
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
      <div className="contenido-panel">
        <PageHeader titulo="Comparables" />
        <Aviso tono="peligro" titulo="No pudimos leer el corpus">
          {detalle}
        </Aviso>
      </div>
    );
  }

  const conFiltros = (cambios: Record<string, string | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...p, cursor: undefined, ...cambios })) if (v) q.set(k, v);
    return `/comparables${q.size ? `?${q}` : ""}`;
  };

  const segmento = (clave: string, valor: string, texto: string) => {
    const activo = p[clave] === valor;
    return (
      <Link
        href={conFiltros({ [clave]: activo ? undefined : valor })}
        className="segmento"
        aria-current={activo ? "true" : undefined}
      >
        {texto}
      </Link>
    );
  };

  const hayFiltros = Object.entries(p).some(([k, v]) => v && k !== "cursor");

  return (
    <div className="contenido-panel">
      <PageHeader
        titulo="Comparables"
        bajada={`${datos.total_aprox.toLocaleString("es-AR")} avisos vigentes. La descripción original y lo que extrajo el sistema, uno al lado del otro.`}
      />

      <div className="toolbar">
        <form action="/comparables" method="get" role="search">
          {p.barrio && <input type="hidden" name="barrio" value={p.barrio} />}
          {p.fuente && <input type="hidden" name="fuente" value={p.fuente} />}
          <label htmlFor="q" className="visualmente-oculto">
            Buscar en el corpus
          </label>
          <input id="q" name="q" defaultValue={p.q ?? ""} placeholder="Buscar por dirección o texto del aviso" />
          <button type="submit" className="boton-secundario">
            <Icono nombre="buscar" tamano={18} />
            Buscar
          </button>
        </form>
        <nav className="segmentos" aria-label="Filtros">
          {segmento("barrio", "Palermo", "Palermo")}
          {segmento("barrio", "Belgrano", "Belgrano")}
          {segmento("fuente", "PORTAL_A", "Portal A")}
          {segmento("fuente", "PORTAL_B", "Portal B")}
          {segmento("sin_extraer", "true", "Sin extraer")}
          {segmento("para_revisar", "true", "Marcados para revisar")}
        </nav>
        {hayFiltros && (
          <BotonLink href="/comparables" variante="terciario" icono="cerrar" chico>
            Limpiar
          </BotonLink>
        )}
      </div>

      {datos.items.length === 0 ? (
        <Vacio
          icono="comparables"
          titulo="Ningún aviso con esos filtros"
          texto="Probá con otro barrio o sacá un filtro."
          accion={
            <BotonLink href="/comparables" variante="secundario">
              Ver todo el corpus
            </BotonLink>
          }
        />
      ) : (
        datos.items.map((c) => <Aviso_ key={c.id} c={c} />)
      )}

      {datos.next_cursor && (
        <p>
          <BotonLink href={conFiltros({ cursor: datos.next_cursor })} variante="secundario" icono="flecha">
            Ver más avisos
          </BotonLink>
        </p>
      )}
    </div>
  );
}
