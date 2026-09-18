"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Confirmar } from "@/components/confirmar";
import { Aviso } from "@/components/ui";

export const ROLES: [string, string][] = [
  ["agent", "agent: genera informes"],
  ["viewer", "viewer: solo mira"],
  ["admin", "admin: administra el tenant"],
  ["owner", "owner: todo"],
];

/**
 * Alta de usuario. Si no se escribe contraseña, la API genera una y se muestra
 * UNA vez, igual que una API key: mejor que dejar que el admin invente una
 * débil, y no queda en ningún lado en claro.
 */
export function CrearUsuario() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [nombre, setNombre] = useState("");
  const [rol, setRol] = useState("agent");
  const [creado, setCreado] = useState<{ email: string; password_generada: string | null } | null>(null);
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
    <div className="pila">
      {creado && (
        <Aviso tono="exito" titulo={`Usuario creado: ${creado.email}`}>
          {creado.password_generada && (
            <>
              Contraseña generada. Pasásela por un canal seguro: no se vuelve a mostrar.
              <br />
              <code style={{ userSelect: "all" }}>{creado.password_generada}</code>
            </>
          )}
        </Aviso>
      )}
      {/* Labels reales, no `placeholder` (doc 07 §12, H-39). */}
      <form onSubmit={crear} className="tarjeta pila" style={{ maxWidth: "36rem" }}>
        <div className="grilla-campos">
          <div className="campo">
            <label htmlFor="usuario-email">Email</label>
            <input
              id="usuario-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="email@inmobiliaria.com.ar"
              required
              autoComplete="off"
            />
          </div>
          <div className="campo">
            <label htmlFor="usuario-nombre">Nombre completo (opcional)</label>
            <input id="usuario-nombre" value={nombre} onChange={(e) => setNombre(e.target.value)} maxLength={200} />
          </div>
          <div className="campo">
            <label htmlFor="usuario-rol">Rol</label>
            <select id="usuario-rol" value={rol} onChange={(e) => setRol(e.target.value)}>
              {ROLES.map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
          </div>
        </div>
        {error && (
          <p role="alert" style={{ margin: 0, color: "var(--peligro)", fontSize: "0.85rem" }}>
            {error}
          </p>
        )}
        <div className="acciones-formulario" style={{ marginTop: 0 }}>
          <button disabled={ocupado || !email.trim()}>{ocupado ? "Creando…" : "Crear usuario"}</button>
        </div>
      </form>
    </div>
  );
}

/** Activar, desactivar o cambiar el rol. Desactivar pide confirmación. */
export function AccionesDeUsuario({
  id,
  email,
  activo,
  rol,
  esYo,
}: {
  id: string;
  email: string;
  activo: boolean;
  rol: string;
  esYo: boolean;
}) {
  const router = useRouter();
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmando, setConfirmando] = useState(false);

  async function patch(cuerpo: Record<string, unknown>) {
    setOcupado(true);
    setError(null);
    try {
      const r = await fetch(`/admin/usuarios/api/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cuerpo),
      });
      if (r.status === 422 || r.status === 403) {
        const c = await r.json().catch(() => null);
        throw new Error(typeof c?.detail === "string" ? c.detail : "No se pudo.");
      }
      if (!r.ok) throw new Error(`La API devolvió ${r.status}`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo.");
    } finally {
      setOcupado(false);
      setConfirmando(false);
    }
  }

  return (
    <div className="pila" style={{ gap: "0.3rem" }}>
      <div className="fila">
        <label htmlFor={`rol-${id}`} className="visualmente-oculto">
          Rol de {email}
        </label>
        <select
          id={`rol-${id}`}
          value={rol}
          disabled={ocupado || esYo}
          onChange={(e) => void patch({ role: e.target.value })}
          style={{ width: "auto", minHeight: 32, fontSize: "0.82rem", padding: "0.2rem 0.5rem" }}
          title={esYo ? "Tu propio rol lo cambia otro administrador" : undefined}
        >
          {ROLES.map(([v]) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={activo ? "boton-terciario boton-chico" : "boton-secundario boton-chico"}
          onClick={() => (activo ? setConfirmando(true) : void patch({ active: true }))}
          disabled={ocupado || esYo}
          title={esYo ? "No podés desactivarte a vos mismo" : undefined}
        >
          {ocupado ? "Un momento…" : activo ? "Desactivar" : "Reactivar"}
        </button>
      </div>
      {error && (
        <span role="alert" style={{ color: "var(--peligro)", fontSize: "0.78rem" }}>
          {error}
        </span>
      )}
      <Confirmar
        abierto={confirmando}
        titulo={`¿Desactivar a ${email}?`}
        texto="Su sesión deja de funcionar al instante. Se puede reactivar después."
        verbo="Desactivar"
        ocupado={ocupado}
        onConfirmar={() => void patch({ active: false })}
        onCancelar={() => setConfirmando(false)}
      />
    </div>
  );
}
