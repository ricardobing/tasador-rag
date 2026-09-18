import type { Metadata } from "next";
import localFont from "next/font/local";
import { AppShell } from "@/components/app-shell";
import { quienSoy } from "@/lib/api";
import { CLAVE_TEMA } from "@/lib/tema";
import "./globals.css";

/**
 * La tipografía display (títulos, el valor, la marca) se sirve desde el
 * propio sitio: el build no depende de la red y no hay pedidos a terceros en
 * runtime. Créditos en `src/fonts/CREDITOS.md`.
 */
const display = localFont({
  src: "../fonts/bricolage-grotesque-latin.woff2",
  variable: "--font-display",
  display: "swap",
  weight: "500 800",
});

export const metadata: Metadata = {
  title: "Tasador",
  description: "Informes de mercado comparativo con comparables reales y trazabilidad.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Para decidir qué grupos mostrar. En modo desarrollo (header de org, sin
  // usuario) `via` es "header_dev" y se muestran todos: es el modo en que
  // trabajan los tests y el desarrollo local. La autorización real es de la API.
  const yo = await quienSoy();
  const esAdmin =
    yo !== null && (yo.via === "header_dev" || yo.role === "owner" || yo.role === "admin");
  const quien = yo
    ? {
        nombre: yo.full_name || yo.email || (yo.via === "header_dev" ? "Modo desarrollo" : "Sesión"),
        detalle: [yo.org_name, yo.role].filter(Boolean).join(" · "),
      }
    : null;

  return (
    <html lang="es-AR" className={display.variable} data-theme="claro" suppressHydrationWarning>
      <head>
        {/* Antes de pintar: lee el tema elegido y lo aplica. Sin esto, la
            pantalla se pinta clara y salta a oscura un instante después. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem("${CLAVE_TEMA}");if(t==="oscuro"||t==="auto")document.documentElement.dataset.theme=t}catch(e){}`,
          }}
        />
      </head>
      <body>
        <AppShell esAdmin={esAdmin} quien={quien}>
          {children}
        </AppShell>
      </body>
    </html>
  );
}
