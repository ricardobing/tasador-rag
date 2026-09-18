"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Icono, type NombreDeIcono } from "./iconos";
import { Salir } from "./salir";

type Item = { href: string; texto: string; icono: NombreDeIcono; exacto?: boolean };
type Grupo = { titulo: string; items: Item[]; soloAdmin?: boolean };

/**
 * La navegación con grupos (doc 20 §2.4): en escritorio una barra lateral,
 * en el celular una barra superior con el menú desplegable. El ítem activo se
 * marca con tres señales (fondo, peso y barra), no con el color solo.
 *
 * `esAdmin` solo esconde grupos: la autorización real la hace la API, que
 * responde 403 aunque alguien escriba la URL a mano.
 */
const GRUPOS: Grupo[] = [
  {
    titulo: "Trabajo",
    items: [
      { href: "/informes", texto: "Informes", icono: "informes" },
      { href: "/informes/nuevo", texto: "Nuevo informe", icono: "nuevo", exacto: true },
    ],
  },
  {
    titulo: "Corpus",
    items: [
      { href: "/comparables", texto: "Comparables", icono: "comparables" },
      { href: "/admin/fuentes", texto: "Fuentes de datos", icono: "fuentes" },
    ],
    soloAdmin: false,
  },
  {
    titulo: "Calidad",
    items: [{ href: "/calidad", texto: "Calidad del motor", icono: "calidad" }],
    soloAdmin: true,
  },
  {
    titulo: "Administración",
    items: [
      { href: "/admin/usuarios", texto: "Usuarios", icono: "usuarios" },
      { href: "/admin/organizacion", texto: "Organización", icono: "organizacion" },
    ],
    soloAdmin: true,
  },
];

function activo(ruta: string, item: Item): boolean {
  if (item.exacto) return ruta === item.href;
  if (item.href === "/informes") return ruta === "/informes" || /^\/informes\/(?!nuevo)/.test(ruta);
  return ruta === item.href || ruta.startsWith(item.href + "/");
}

export function AppShell({
  esAdmin,
  quien,
  children,
}: {
  esAdmin: boolean;
  quien: { nombre: string; detalle: string } | null;
  children: ReactNode;
}) {
  const ruta = usePathname();
  const [abierta, setAbierta] = useState(false);

  useEffect(() => setAbierta(false), [ruta]);

  // El propietario recibe un documento, no la herramienta; el login tampoco
  // lleva navegación.
  if (ruta.startsWith("/compartido") || ruta.startsWith("/login")) {
    return <div className="shell-solo">{children}</div>;
  }

  const grupos = GRUPOS.filter((g) => !g.soloAdmin || esAdmin);
  const fuentesVisibles = esAdmin;

  const nav = (
    <>
      <Link href="/informes" className="marca">
        <span className="marca-isotipo" aria-hidden="true">
          T
        </span>
        Tasador
      </Link>
      {grupos.map((g) => (
        <div className="nav-grupo" key={g.titulo}>
          <div className="etiqueta">{g.titulo}</div>
          {g.items
            .filter((it) => it.href !== "/admin/fuentes" || fuentesVisibles)
            .map((it) => (
              <Link
                key={it.href}
                href={it.href}
                className="nav-link"
                aria-current={activo(ruta, it) ? "page" : undefined}
              >
                <Icono nombre={it.icono} tamano={18} />
                {it.texto}
              </Link>
            ))}
        </div>
      ))}
      <div className="sidebar-pie">
        {quien && (
          <div className="quien">
            <strong>{quien.nombre}</strong>
            {quien.detalle}
          </div>
        )}
        <Link
          href="/cuenta"
          className="nav-link"
          aria-current={ruta.startsWith("/cuenta") ? "page" : undefined}
        >
          <Icono nombre="cuenta" tamano={18} />
          Mi cuenta
        </Link>
        <Salir />
      </div>
    </>
  );

  return (
    <div className="shell">
      <aside className="sidebar" data-abierta={abierta} aria-label="Navegación">
        {nav}
      </aside>
      {abierta && <div className="scrim" onClick={() => setAbierta(false)} aria-hidden="true" />}
      <div style={{ minWidth: 0 }}>
        <div className="barra-movil">
          <Link href="/informes" className="marca" style={{ padding: 0 }}>
            <span className="marca-isotipo" aria-hidden="true">
              T
            </span>
            Tasador
          </Link>
          <button
            type="button"
            className="boton-secundario boton-chico"
            aria-expanded={abierta}
            aria-controls="menu-movil"
            onClick={() => setAbierta((a) => !a)}
          >
            <Icono nombre={abierta ? "cerrar" : "menu"} tamano={18} />
            Menú
          </button>
        </div>
        <main className="contenido">{children}</main>
      </div>
    </div>
  );
}
