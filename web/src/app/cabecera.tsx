"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Salir } from "./salir";

/**
 * La navegación. Client component por un solo motivo: en `/compartido/*` (el
 * link del propietario) y en `/login` no tiene que aparecer — el propietario
 * recibe un documento, no la herramienta.
 *
 * `esAdmin` solo esconde links: la autorización real la hace la API, que
 * responde 403 aunque alguien escriba la URL a mano.
 */
export function Cabecera({ esAdmin }: { esAdmin: boolean }) {
  const ruta = usePathname();
  if (ruta.startsWith("/compartido") || ruta.startsWith("/login")) return null;

  return (
    <header
      style={{
        borderBottom: "1px solid var(--borde)",
        padding: "0.85rem 1rem",
        display: "flex",
        gap: "1.25rem",
        alignItems: "baseline",
        flexWrap: "wrap",
      }}
    >
      <Link href="/informes" style={{ fontWeight: 700, textDecoration: "none" }}>
        Tasador
      </Link>
      <nav style={{ display: "flex", gap: "1rem", fontSize: "0.92rem", flex: 1, flexWrap: "wrap" }}>
        <Link href="/informes">Informes</Link>
        <Link href="/informes/nuevo">Nuevo</Link>
        <Link href="/comparables">Comparables</Link>
        {esAdmin && (
          <>
            <Link href="/calidad">Calidad</Link>
            <Link href="/admin/fuentes">Fuentes</Link>
            <Link href="/admin/usuarios">Usuarios</Link>
            <Link href="/admin/organizacion">Organización</Link>
          </>
        )}
      </nav>
      <Salir />
    </header>
  );
}
