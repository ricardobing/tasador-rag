"use client";

import { useRouter } from "next/navigation";

/**
 * Cerrar sesión.
 *
 * Le pega al proxy `/login/api` con DELETE, que reenvía el `Set-Cookie` de
 * borrado que emite la API. Borrar la cookie solo del lado del cliente sería
 * mentira: la sesión seguiría siendo válida y cualquiera con el token seguiría
 * entrando.
 */
export function Salir() {
  const router = useRouter();
  return (
    <button
      type="button"
      onClick={async () => {
        await fetch("/login/api", { method: "DELETE" });
        router.refresh();
        router.replace("/login");
      }}
      style={{
        background: "transparent",
        color: "var(--acento)",
        padding: 0,
        fontSize: "0.92rem",
        fontWeight: 400,
      }}
    >
      Salir
    </button>
  );
}
