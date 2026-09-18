"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Confirmar } from "@/components/confirmar";
import { Icono } from "@/components/iconos";

/**
 * Crear una API key. La clave en claro aparece UNA vez, acá: la API no la
 * guarda y no hay forma de volver a pedirla. Ese es el diseño, no un límite.
 */
export function CrearApiKey() {
  const router = useRouter();
  const [nombre, setNombre] = useState("");
  const [creada, setCreada] = useState<{ api_key: string; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [copiado, setCopiado] = useState(false);

  async function crear(e: React.FormEvent) {
    e.preventDefault();
    setOcupado(true);
    setError(null);
    try {
      const r = await fetch("/admin/organizacion/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nombre }),
      });
      if (!r.ok) throw new Error(`La API devolvió ${r.status}`);
      setCreada(await r.json());
      setNombre("");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear.");
    } finally {
      setOcupado(false);
    }
  }

  async function copiar() {
    if (!creada) return;
    await navigator.clipboard.writeText(creada.api_key);
    setCopiado(true);
    setTimeout(() => setCopiado(false), 2000);
  }

  return (
    <div className="pila">
      {creada && (
        <div className="aviso aviso-exito">
          <Icono nombre="tilde" />
          <div style={{ minWidth: 0, flex: 1 }}>
            <strong style={{ display: "block", marginBottom: "0.2rem" }}>Clave creada: {creada.name}</strong>
            Copiala ahora, no se vuelve a mostrar.
            <br />
            <code style={{ wordBreak: "break-all", userSelect: "all" }}>{creada.api_key}</code>
          </div>
          <button type="button" className="boton-secundario boton-chico" onClick={copiar}>
            <Icono nombre={copiado ? "tilde" : "copiar"} tamano={16} />
            {copiado ? "Copiada" : "Copiar"}
          </button>
        </div>
      )}
      {/* Label real, no `placeholder` (doc 07 §12, H-39). */}
      <form onSubmit={crear} className="tarjeta" style={{ maxWidth: "36rem" }}>
        <div className="campo">
          <label htmlFor="apikey-nombre">Nombre de la API key</label>
          <div className="fila">
            <input
              id="apikey-nombre"
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
              placeholder="ej: panel de la inmobiliaria"
              required
              maxLength={100}
              style={{ flex: "1 1 14rem" }}
            />
            <button disabled={ocupado || !nombre.trim()}>{ocupado ? "Creando…" : "Crear API key"}</button>
          </div>
          <span className="ayuda">Un nombre que diga para qué es: así se sabe cuál revocar.</span>
        </div>
        {error && (
          <p role="alert" style={{ margin: "0.6rem 0 0", color: "var(--peligro)", fontSize: "0.85rem" }}>
            {error}
          </p>
        )}
      </form>
    </div>
  );
}

export function RevocarApiKey({ id, nombre }: { id: string; nombre: string }) {
  const router = useRouter();
  const [ocupado, setOcupado] = useState(false);
  const [confirmando, setConfirmando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function revocar() {
    setOcupado(true);
    setError(null);
    try {
      const r = await fetch(`/admin/organizacion/api/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`La API devolvió ${r.status}`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo revocar.");
    } finally {
      setOcupado(false);
      setConfirmando(false);
    }
  }

  return (
    <>
      <button type="button" className="boton-terciario boton-chico" onClick={() => setConfirmando(true)} disabled={ocupado}>
        Revocar
      </button>
      {error && (
        <span role="alert" style={{ color: "var(--peligro)", fontSize: "0.78rem", marginLeft: "0.5rem" }}>
          {error}
        </span>
      )}
      <Confirmar
        abierto={confirmando}
        titulo={`¿Revocar la clave "${nombre}"?`}
        texto="La integración que la use deja de andar en el acto. No se puede deshacer; se emite otra."
        verbo="Revocar"
        ocupado={ocupado}
        onConfirmar={() => void revocar()}
        onCancelar={() => setConfirmando(false)}
      />
    </>
  );
}
