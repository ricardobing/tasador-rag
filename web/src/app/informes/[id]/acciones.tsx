"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * Regenerar: el flujo post-visita (doc 06 §2). Crea un informe NUEVO sobre la
 * misma propiedad — el actual no se muta, es un documento — y navega al
 * stepper del nuevo.
 */
export function Regenerar({ id }: { id: string }) {
  const router = useRouter();
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function regenerar() {
    setOcupado(true);
    setError(null);
    try {
      const r = await fetch(`/informes/${id}/api/regenerar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ property: {} }),
      });
      if (!r.ok) throw new Error(`La API devolvió ${r.status}`);
      const { report_id } = await r.json();
      router.push(`/informes/${report_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo regenerar.");
      setOcupado(false);
    }
  }

  return (
    <>
      <button onClick={regenerar} disabled={ocupado}>
        {ocupado ? "Encolando…" : "Regenerar"}
      </button>
      {error && <span style={{ color: "var(--ambar)", marginLeft: "0.6rem" }}>{error}</span>}
    </>
  );
}

/** El link firmado para el propietario. Vence a los 30 días. */
export function Compartir({ id }: { id: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiado, setCopiado] = useState(false);

  async function compartir() {
    setOcupado(true);
    setError(null);
    try {
      const r = await fetch(`/informes/${id}/api/compartir`, { method: "POST" });
      if (!r.ok) throw new Error(`La API devolvió ${r.status}`);
      const datos = await r.json();
      setUrl(`${window.location.origin}${datos.url}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo generar el link.");
    } finally {
      setOcupado(false);
    }
  }

  async function copiar() {
    if (!url) return;
    await navigator.clipboard.writeText(url);
    setCopiado(true);
    setTimeout(() => setCopiado(false), 2000);
  }

  return (
    <span>
      <button onClick={compartir} disabled={ocupado} style={{ marginLeft: "0.6rem" }}>
        {ocupado ? "Generando…" : "Compartir"}
      </button>
      {error && <span style={{ color: "var(--ambar)", marginLeft: "0.6rem" }}>{error}</span>}
      {url && (
        <span style={{ display: "block", marginTop: "0.5rem", fontSize: "0.85rem" }}>
          Link para el propietario (vence en 30 días):{" "}
          <code style={{ wordBreak: "break-all", userSelect: "all" }}>{url}</code>{" "}
          <button onClick={copiar} style={{ fontSize: "0.8rem" }}>
            {copiado ? "Copiado ✓" : "Copiar"}
          </button>
        </span>
      )}
    </span>
  );
}
