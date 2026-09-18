/**
 * El kit — doc 20 §2.3. Piezas sin estado, usables desde server components.
 * Lo interactivo (confirmación, menú del celular) vive en sus propios
 * archivos con "use client".
 */
import Link from "next/link";
import type { ReactNode } from "react";
import { Icono, type NombreDeIcono } from "./iconos";

export function PageHeader({
  titulo,
  bajada,
  migas,
  acciones,
}: {
  titulo: string;
  bajada?: ReactNode;
  migas?: { href: string; texto: string }[];
  acciones?: ReactNode;
}) {
  return (
    <header className="encabezado">
      <div>
        {migas && migas.length > 0 && (
          <nav className="migas" aria-label="Estás en">
            {migas.map((m, i) => (
              <span key={m.href}>
                {i > 0 && " / "}
                <Link href={m.href}>{m.texto}</Link>
              </span>
            ))}
            {" / "}
          </nav>
        )}
        <h1>{titulo}</h1>
        {bajada && <p>{bajada}</p>}
      </div>
      {acciones && <div className="encabezado-acciones">{acciones}</div>}
    </header>
  );
}

export function Seccion({
  titulo,
  bajada,
  acciones,
  children,
  id,
}: {
  titulo: string;
  bajada?: ReactNode;
  acciones?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section className="seccion" aria-labelledby={id ? `${id}-titulo` : undefined}>
      <div className="seccion-titulo">
        <div>
          <h2 id={id ? `${id}-titulo` : undefined}>{titulo}</h2>
          {bajada && <p className="ayuda">{bajada}</p>}
        </div>
        {acciones}
      </div>
      {children}
    </section>
  );
}

export type TonoDeChip = "exito" | "alerta" | "peligro" | "neutro" | "marca";

/** Un estado siempre lleva su palabra: el color solo nunca alcanza. */
export function Chip({ tono = "neutro", children }: { tono?: TonoDeChip; children: ReactNode }) {
  return <span className={`chip chip-${tono}`}>{children}</span>;
}

export const TONO_DE_CONFIANZA: Record<string, TonoDeChip> = {
  ALTA: "exito",
  MEDIA: "alerta",
  BAJA: "neutro",
};

export function ChipDeConfianza({ nivel }: { nivel: string | null | undefined }) {
  if (!nivel) return <span className="tenue">sin nivel</span>;
  return <span className={`chip chip-${nivel}`}>{nivel}</span>;
}

export const ESTADO_DE_INFORME: Record<string, { texto: string; tono: TonoDeChip }> = {
  QUEUED: { texto: "EN COLA", tono: "marca" },
  RUNNING: { texto: "GENERANDO", tono: "marca" },
  SUCCEEDED: { texto: "SUCCEEDED", tono: "exito" },
  // INSUFFICIENT_DATA en ámbar y no en rojo: el sistema funcionó, y su
  // respuesta correcta fue "no hay datos con qué" (doc 07 §3).
  INSUFFICIENT_DATA: { texto: "SIN DATOS", tono: "alerta" },
  FAILED: { texto: "FALLÓ", tono: "peligro" },
};

export function ChipDeEstado({ estado }: { estado: string }) {
  const e = ESTADO_DE_INFORME[estado] ?? { texto: estado, tono: "neutro" as TonoDeChip };
  return <Chip tono={e.tono}>{e.texto}</Chip>;
}

export function Stat({
  etiqueta,
  valor,
  detalle,
}: {
  etiqueta: string;
  valor: ReactNode;
  detalle?: ReactNode;
}) {
  return (
    <div className="stat">
      <div className="etiqueta">{etiqueta}</div>
      <div className="valor">{valor}</div>
      {detalle && <div className="detalle">{detalle}</div>}
    </div>
  );
}

/** Pares clave/valor; lo que falta se dice («sin declarar»), no se esconde. */
export function KeyValue({
  datos,
  vacio = "sin declarar",
}: {
  datos: { k: string; v: ReactNode | null | undefined }[];
  vacio?: string;
}) {
  return (
    <dl className="kv">
      {datos.map((d) => (
        <div key={d.k}>
          <dt className="k" style={{ display: "inline" }}>
            {d.k}:{" "}
          </dt>
          <dd style={{ display: "inline", margin: 0 }}>
            {d.v === null || d.v === undefined || d.v === "" ? (
              <span className="v nd">{vacio}</span>
            ) : (
              <span className="v">{d.v}</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Aviso({
  tono = "info",
  titulo,
  children,
}: {
  tono?: "info" | "alerta" | "peligro" | "exito";
  titulo?: ReactNode;
  children?: ReactNode;
}) {
  const icono: NombreDeIcono =
    tono === "exito" ? "tilde" : tono === "info" ? "info" : "alerta";
  return (
    <div className={`aviso aviso-${tono}`} role={tono === "peligro" ? "alert" : undefined}>
      <Icono nombre={icono} />
      <div style={{ minWidth: 0, flex: 1 }}>
        {titulo && <strong style={{ display: "block", marginBottom: "0.2rem" }}>{titulo}</strong>}
        {children}
      </div>
    </div>
  );
}

/** Un vacío sin salida es una pantalla rota: siempre qué falta y qué hacer. */
export function Vacio({
  icono = "informes",
  titulo,
  texto,
  accion,
}: {
  icono?: NombreDeIcono;
  titulo: string;
  texto?: ReactNode;
  accion?: ReactNode;
}) {
  return (
    <div className="vacio">
      <Icono nombre={icono} tamano={28} />
      <h2>{titulo}</h2>
      {texto && <p>{texto}</p>}
      {accion && <div className="fila">{accion}</div>}
    </div>
  );
}

export function Esqueleto({ filas = 4 }: { filas?: number }) {
  return (
    <div className="pila" aria-busy="true" aria-label="Cargando">
      {Array.from({ length: filas }).map((_, i) => (
        <div key={i} className="esqueleto" style={{ height: "2.4rem" }} />
      ))}
    </div>
  );
}

/** Link con forma de botón, para acciones que navegan. */
export function BotonLink({
  href,
  variante = "primario",
  icono,
  children,
  chico,
  ...resto
}: {
  href: string;
  variante?: "primario" | "secundario" | "terciario";
  icono?: NombreDeIcono;
  children: ReactNode;
  chico?: boolean;
} & Omit<React.ComponentProps<typeof Link>, "href" | "className">) {
  const clase = ["boton", variante !== "primario" ? `boton-${variante}` : "", chico ? "boton-chico" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <Link href={href} className={clase} {...resto}>
      {icono && <Icono nombre={icono} tamano={18} />}
      {children}
    </Link>
  );
}

export const usd = (v: number | null | undefined) =>
  v === null || v === undefined ? null : `USD ${Math.round(v).toLocaleString("es-AR")}`;

export const fecha = (iso: string | null | undefined, conHora = false) =>
  iso
    ? new Date(iso).toLocaleDateString(
        "es-AR",
        conHora
          ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
          : { day: "2-digit", month: "2-digit", year: "2-digit" },
      )
    : null;
