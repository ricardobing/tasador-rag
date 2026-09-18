import type { Metadata } from "next";
import { quienSoy } from "@/lib/api";
import { Cabecera } from "./cabecera";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tasador — Informe de Mercado Comparativo",
  description: "Valuación comparativa con comparables reales y trazabilidad.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Para decidir qué links mostrar. En modo desarrollo (header de org, sin
  // usuario) `via` es "header_dev" y se muestran todos: es el modo en que
  // trabajan los tests y el desarrollo local.
  const yo = await quienSoy();
  const esAdmin =
    yo !== null && (yo.via === "header_dev" || yo.role === "owner" || yo.role === "admin");

  return (
    <html lang="es-AR">
      <body>
        <Cabecera esAdmin={esAdmin} />
        <main>{children}</main>
      </body>
    </html>
  );
}
