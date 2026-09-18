"use client";

import { useState } from "react";

/**
 * "Reportar extracción incorrecta" (doc 07 §8).
 *
 * MARCA, no corrige. Lo que se marca acá entra como candidato al golden set,
 * que es el único lugar donde una respuesta cuenta como correcta (doc 09 §3.3).
 * Si este botón escribiera la feature "arreglada" en el corpus, el eval pasaría
 * a medir el acuerdo del modelo con lo último que alguien tocó — y ese número
 * subiría solo, sin que nada mejore.
 */
export function Revisar({ id, marcado }: { id: string; marcado: boolean }) {
  const [estado, setEstado] = useState(marcado);
  const [enviando, setEnviando] = useState(false);

  return (
    <button
      type="button"
      disabled={enviando}
      onClick={async () => {
        setEnviando(true);
        const r = await fetch(`/comparables/api/${id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ needs_review: !estado }),
        });
        if (r.ok) setEstado(!estado);
        setEnviando(false);
      }}
      style={{
        background: "transparent",
        color: estado ? "var(--ambar)" : "var(--tenue)",
        padding: 0,
        fontSize: "0.8rem",
        fontWeight: estado ? 700 : 400,
        textAlign: "left",
      }}
    >
      {estado ? "✓ marcado para revisar" : "Reportar extracción incorrecta"}
    </button>
  );
}
