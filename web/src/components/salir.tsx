"use client";

import { useRouter } from "next/navigation";
import { Icono } from "./iconos";

/**
 * Cerrar sesión. Le pega al proxy `/login/api` con DELETE, que reenvía el
 * `Set-Cookie` de borrado que emite la API. Borrar la cookie solo del lado del
 * cliente sería mentira: la sesión seguiría siendo válida.
 */
export function Salir() {
  const router = useRouter();
  return (
    <button
      type="button"
      className="nav-link"
      style={{ background: "transparent", border: 0, borderLeft: "3px solid transparent", width: "100%", justifyContent: "flex-start", color: "inherit", fontWeight: 500 }}
      onClick={async () => {
        await fetch("/login/api", { method: "DELETE" });
        router.refresh();
        router.replace("/login");
      }}
    >
      <Icono nombre="salir" tamano={18} />
      Salir
    </button>
  );
}
