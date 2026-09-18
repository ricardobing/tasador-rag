"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

/**
 * `/login` (doc 07 §2).
 *
 * Sin registro público: los usuarios los crea un admin en `/admin/usuarios`.
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
    <div className="login">
      <form onSubmit={enviar} className="tarjeta pila">
        <div className="marca" style={{ padding: 0 }}>
          <span className="marca-isotipo" aria-hidden="true">
            T
          </span>
          Tasador
        </div>
        <div>
          <h1 style={{ marginBottom: "0.2rem" }}>Entrar</h1>
          <p className="ayuda" style={{ margin: 0 }}>
            {org ? `Organización: ${org}` : "Informes de mercado comparativo."}
          </p>
        </div>
        <div className="campo">
          <label htmlFor="email">Email</label>
          <input id="email" name="email" type="email" required autoComplete="username" />
        </div>
        <div className="campo">
          <label htmlFor="password">Contraseña</label>
          <input id="password" name="password" type="password" required autoComplete="current-password" />
        </div>

        {error && (
          <p role="alert" style={{ color: "var(--peligro)", margin: 0, fontSize: "0.9rem" }}>
            {error}
          </p>
        )}

        <button type="submit" disabled={enviando}>
          {enviando ? "Entrando…" : "Entrar"}
        </button>

        <p className="ayuda" style={{ margin: 0 }}>
          Los usuarios los da de alta un administrador. Si no podés entrar, escribile.
        </p>
      </form>
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
