"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * Alta de usuario. Si no se escribe contraseña, la API genera una y se muestra
 * UNA vez — igual que una API key: mejor que dejar que el admin invente una
 * débil, y no queda en ningún lado en claro.
 */
export function CrearUsuario() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [nombre, setNombre] = useState("");
  const [rol, setRol] = useState("agent");
  const [creado, setCreado] = useState<{ email: string; password_generada: string | null } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  async function crear(e: React.FormEvent) {
    e.preventDefault();
    setOcupado(true);
    setError(null);
    try {
      const r = await fetch("/admin/usuarios/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, full_name: nombre || null, role: rol }),
      });
      if (r.status === 409) throw new Error("Ya existe un usuario con ese email.");
      if (!r.ok) throw new Error(`La API devolvió ${r.status}`);
      setCreado(await r.json());
      setEmail("");
      setNombre("");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear.");
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div style={{ marginTop: "1.5rem" }}>
      <h2>Nuevo usuario</h2>
      {creado && (
        <div className="aviso" style={{ marginBottom: "1rem" }}>
          <strong>Usuario creado: {creado.email}</strong>
          {creado.password_generada && (
            <p style={{ margin: "0.4rem 0" }}>
              Contraseña generada — pasásela por un canal seguro, no se vuelve a mostrar:
              <br />
              <code style={{ userSelect: "all" }}>{creado.password_generada}</code>
            </p>
          )}
        </div>
      )}
      {/* Labels reales, no `placeholder` (doc 07 §12, H-39). El placeholder
          desaparece al escribir, no lo anuncian todos los lectores de pantalla
          y no amplía el área clickeable. */}
      <form onSubmit={crear} style={{ display: "grid", gap: "0.6rem", maxWidth: "26rem" }}>
        <label htmlFor="usuario-email">Email</label>
        <input
          id="usuario-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="email@inmobiliaria.com.ar"
          required
        />
        <label htmlFor="usuario-nombre">Nombre completo (opcional)</label>
        <input
          id="usuario-nombre"
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          maxLength={200}
        />
        <label htmlFor="usuario-rol">Rol</label>
        <select id="usuario-rol" value={rol} onChange={(e) => setRol(e.target.value)}>
          <option value="agent">agent — genera informes</option>
          <option value="viewer">viewer — solo mira</option>
          <option value="admin">admin — administra el tenant</option>
          <option value="owner">owner — todo</option>
        </select>
        <button disabled={ocupado || !email.trim()}>{ocupado ? "Creando…" : "Crear usuario"}</button>
      </form>
      {error && <p style={{ color: "var(--ambar)" }}>{error}</p>}
    </div>
  );
}

export function ToggleActivo({ id, email, activo }: { id: string; email: string; activo: boolean }) {
  const router = useRouter();
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (
      activo &&
      !window.confirm(`¿Desactivar a ${email}? Su sesión deja de funcionar al instante.`)
    )
      return;
    setOcupado(true);
    setError(null);
    try {
      const r = await fetch(`/admin/usuarios/api/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: !activo }),
      });
      if (r.status === 422) {
        const cuerpo = await r.json().catch(() => null);
        throw new Error(cuerpo?.detail ?? "No se pudo.");
      }
      if (!r.ok) throw new Error(`La API devolvió ${r.status}`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo.");
    } finally {
      setOcupado(false);
    }
  }

  return (
    <>
      <button onClick={toggle} disabled={ocupado} style={{ fontSize: "0.85rem" }}>
        {ocupado ? "…" : activo ? "Desactivar" : "Reactivar"}
      </button>
      {error && (
        <div className="tenue" style={{ fontSize: "0.75rem", color: "var(--ambar)" }}>
          {error}
        </div>
      )}
    </>
  );
}
