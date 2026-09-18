"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * Crear una API key. La clave en claro aparece UNA vez, acá: la API no la
 * guarda y no hay forma de volver a pedirla — ese es el diseño, no un límite.
 */
export function CrearApiKey() {
  const router = useRouter();
  const [nombre, setNombre] = useState("");
  const [creada, setCreada] = useState<{ api_key: string; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

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

  return (
    <div style={{ marginTop: "1rem" }}>
      {creada && (
        <div className="aviso" style={{ marginBottom: "1rem" }}>
          <strong>Clave creada: {creada.name}</strong>
          <p style={{ margin: "0.4rem 0" }}>
            Copiala ahora — no se vuelve a mostrar:
            <br />
            <code style={{ wordBreak: "break-all", userSelect: "all" }}>{creada.api_key}</code>
          </p>
        </div>
      )}
      {/* Label real, no `placeholder` (doc 07 §12, H-39). */}
      <form onSubmit={crear} style={{ display: "flex", gap: "0.6rem", alignItems: "end" }}>
        <div style={{ flex: 1, maxWidth: "24rem" }}>
          <label htmlFor="apikey-nombre">Nombre de la API key</label>
          <input
            id="apikey-nombre"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="ej: panel de la inmobiliaria"
            required
            maxLength={100}
          />
        </div>
        <button disabled={ocupado || !nombre.trim()}>
          {ocupado ? "Creando…" : "Crear API key"}
        </button>
      </form>
      {error && <p style={{ color: "var(--ambar)" }}>{error}</p>}
    </div>
  );
}

export function RevocarApiKey({ id, nombre }: { id: string; nombre: string }) {
  const router = useRouter();
  const [ocupado, setOcupado] = useState(false);

  async function revocar() {
    // confirm() nativo: revocar corta a una integración en producción y no
    // tiene deshacer. Para esta pantalla interna alcanza y sobra.
    if (!window.confirm(`¿Revocar la clave "${nombre}"? La integración que la use deja de andar.`))
      return;
    setOcupado(true);
    try {
      await fetch(`/admin/organizacion/api/${id}`, { method: "DELETE" });
      router.refresh();
    } finally {
      setOcupado(false);
    }
  }

  return (
    <button onClick={revocar} disabled={ocupado} style={{ fontSize: "0.85rem" }}>
      {ocupado ? "…" : "Revocar"}
    </button>
  );
}
