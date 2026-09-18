"use client";

import { useState } from "react";
import { Icono } from "@/components/iconos";

/**
 * "Reportar extracción incorrecta" (doc 07 §8).
 *
 * MARCA, no corrige. Lo que se marca acá entra como candidato al golden set,
 * que es el único lugar donde una respuesta cuenta como correcta (doc 09 §3.3).
 * Si este botón escribiera la feature "arreglada" en el corpus, el eval pasaría
 * a medir el acuerdo del modelo con lo último que alguien tocó, y ese número
 * subiría solo, sin que nada mejore.
 */
export function Revisar({ id, marcado }: { id: string; marcado: boolean }) {
  const [estado, setEstado] = useState(marcado);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="fila" style={{ marginTop: "0.4rem" }}>
      <button
        type="button"
        className={estado ? "boton-secundario boton-chico" : "boton-terciario boton-chico"}
        disabled={enviando}
        aria-pressed={estado}
        onClick={async () => {
          setEnviando(true);
          setError(null);
          const r = await fetch(`/comparables/api/${id}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ needs_review: !estado }),
          });
          if (r.ok) setEstado(!estado);
          else setError(`La API devolvió ${r.status}`);
          setEnviando(false);
        }}
      >
        <Icono nombre={estado ? "tilde" : "alerta"} tamano={16} />
        {estado ? "Marcado para revisar" : "Reportar extracción incorrecta"}
      </button>
      {error && (
        <span role="alert" style={{ color: "var(--peligro)", fontSize: "0.8rem" }}>
          {error}
        </span>
      )}
    </div>
  );
}
