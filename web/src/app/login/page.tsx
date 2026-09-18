"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

/**
 * `/login` — doc 07 §2.
 *
 * Sin registro público: los usuarios los crea un admin
 * (`scripts/crear_usuario.py` mientras `/admin/usuarios` no exista).
 *
 * El error es genérico a propósito y del lado del servidor también: no se
 * distingue "el email no existe" de "la contraseña está mal" (doc 06 §1).
 * Decirlo acá sería tirar por la ventana lo que el backend cuida.
 */
function Formulario() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  const org = params.get("org");
  const destino = params.get("volver") ?? "/informes";

  async function enviar(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    const datos = new FormData(e.currentTarget);

    const r = await fetch("/login/api", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: datos.get("email"),
        password: datos.get("password"),
        org_slug: org || undefined,
      }),
    });

    if (r.ok) {
      // `refresh()` antes de navegar: la cookie acaba de llegar y el listado se
      // renderiza en el servidor. Sin esto, la primera pantalla se pinta con la
      // sesión vieja (ninguna) y rebota de vuelta al login.
      router.refresh();
      router.replace(destino);
      return;
    }
    setError("Credenciales inválidas.");
    setEnviando(false);
  }

  return (
    <div style={{ maxWidth: "22rem", margin: "3rem auto" }}>
      <h1>Entrar</h1>
      {org && <p className="tenue">Organización: {org}</p>}

      <form onSubmit={enviar} className="tarjeta" style={{ display: "grid", gap: "0.9rem" }}>
        <div>
          <label htmlFor="email">Email</label>
          <input id="email" name="email" type="email" required autoComplete="username" />
        </div>
        <div>
          <label htmlFor="password">Contraseña</label>
          <input
            id="password"
            name="password"
            type="password"
            required
            autoComplete="current-password"
          />
        </div>

        {error && (
          <p role="alert" style={{ color: "#b3261e", margin: 0 }}>
            {error}
          </p>
        )}

        <button type="submit" disabled={enviando}>
          {enviando ? "Entrando…" : "Entrar"}
        </button>
      </form>

      <p className="tenue" style={{ fontSize: "0.85rem", marginTop: "1rem" }}>
        Los usuarios los da de alta un administrador. Si no podés entrar, escribile.
      </p>
    </div>
  );
}

export default function Login() {
  // `useSearchParams` obliga a un límite de Suspense en el App Router.
  return (
    <Suspense fallback={null}>
      <Formulario />
    </Suspense>
  );
}
