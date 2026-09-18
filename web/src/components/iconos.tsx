/**
 * El set de íconos, propio e inline: trazo 1.75, 20×20, `currentColor`.
 * Ninguna pantalla dibuja un SVG suelto ni usa un emoji como ícono (doc 20
 * §2.3): un emoji no obedece a `currentColor` y sobre un botón oscuro queda
 * sucio. Si falta uno, se agrega acá con su nombre en castellano.
 */
import type { SVGProps } from "react";

export type NombreDeIcono =
  | "informes"
  | "nuevo"
  | "comparables"
  | "calidad"
  | "fuentes"
  | "usuarios"
  | "organizacion"
  | "cuenta"
  | "salir"
  | "pdf"
  | "compartir"
  | "regenerar"
  | "pregunta"
  | "tilde"
  | "alerta"
  | "info"
  | "reloj"
  | "flecha"
  | "menu"
  | "cerrar"
  | "copiar"
  | "buscar"
  | "circulo"
  | "cargando";

const TRAZOS: Record<NombreDeIcono, React.ReactNode> = {
  informes: (
    <>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v4h4M9 12h6M9 16h6" />
    </>
  ),
  nuevo: (
    <>
      <path d="M12 5v14M5 12h14" />
    </>
  ),
  comparables: (
    <>
      <rect x="3" y="4" width="8" height="16" rx="1.5" />
      <rect x="13" y="8" width="8" height="12" rx="1.5" />
    </>
  ),
  calidad: (
    <>
      <path d="M4 19h16M6 15l4-6 4 4 4-8" />
    </>
  ),
  fuentes: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" />
    </>
  ),
  usuarios: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M3 20a6 6 0 0 1 12 0M16 4a3.5 3.5 0 0 1 0 7M21 20a6 6 0 0 0-4-5.7" />
    </>
  ),
  organizacion: (
    <>
      <path d="M4 21V5l8-2 8 2v16M9 21v-5h6v5M9 9h.01M15 9h.01M9 13h.01M15 13h.01" />
    </>
  ),
  cuenta: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </>
  ),
  salir: (
    <>
      <path d="M10 4H5v16h5M14 8l4 4-4 4M18 12H9" />
    </>
  ),
  pdf: (
    <>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v4h4M12 11v6M9.5 14.5 12 17l2.5-2.5" />
    </>
  ),
  compartir: (
    <>
      <path d="M10 14 20 4M20 4h-6M20 4v6M18 13v6H5V6h6" />
    </>
  ),
  regenerar: (
    <>
      <path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5" />
    </>
  ),
  pregunta: (
    <>
      <path d="M9 9a3 3 0 1 1 4.5 2.6c-1 .6-1.5 1.2-1.5 2.4M12 17h.01" />
      <circle cx="12" cy="12" r="9" />
    </>
  ),
  tilde: (
    <>
      <path d="M5 12.5 10 17l9-10" />
    </>
  ),
  alerta: (
    <>
      <path d="M12 4 21 20H3zM12 10v4M12 17h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5h.01" />
    </>
  ),
  reloj: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  flecha: (
    <>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </>
  ),
  menu: (
    <>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </>
  ),
  cerrar: (
    <>
      <path d="M6 6l12 12M18 6 6 18" />
    </>
  ),
  copiar: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a2 2 0 0 1 2-2h9" />
    </>
  ),
  buscar: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.2-4.2" />
    </>
  ),
  circulo: (
    <>
      <circle cx="12" cy="12" r="5" />
    </>
  ),
  cargando: (
    <>
      <path d="M12 3a9 9 0 1 1-6.4 2.6" />
    </>
  ),
};

export function Icono({
  nombre,
  tamano = 20,
  ...resto
}: { nombre: NombreDeIcono; tamano?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={tamano}
      height={tamano}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...resto}
    >
      {TRAZOS[nombre]}
    </svg>
  );
}
