"use client";

import { useState } from "react";
import { Aviso } from "@/components/ui";

export function CambiarPassword() {
  const [actual, setActual] = useState("");
  const [nueva, setNueva] = useState("");
  const [repetir, setRepetir] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listo, setListo] = useState(false);

  const corta = nueva.length > 0 && nueva.length < 10;
  const distintas = repetir.length > 0 && nueva !== repetir;
  const igual = nueva.length > 0 && nueva === actual;
  const puede = actual && nueva.length >= 10 && nueva === repetir && !igual && !ocupado;

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (!puede) return;
    setOcupado(true);
    setError(null);
    setListo(false);
    try {
      const r = await fetch("/cuenta/api", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actual, nueva }),
      });
      if (!r.ok) {
        const c = await r.json().catch(() => null);
        throw new Error(typeof c?.detail === "string" ? c.detail : `La API devolvió ${r.status}`);
      }
      setListo(true);
      setActual("");
      setNueva("");
      setRepetir("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar.");
    } finally {
      setOcupado(false);
    }
  }

  return (
    <form onSubmit={enviar} className="tarjeta pila">
      {listo && <Aviso tono="exito">Contraseña cambiada.</Aviso>}
      <div className="campo">
        <label htmlFor="pw-actual">Contraseña actual</label>
        <input id="pw-actual" type="password" autoComplete="current-password" value={actual} onChange={(e) => setActual(e.target.value)} required />
      </div>
      <div className="campo">
        <label htmlFor="pw-nueva">Contraseña nueva</label>
        <input
          id="pw-nueva"
          type="password"
          autoComplete="new-password"
          value={nueva}
          onChange={(e) => setNueva(e.target.value)}
          minLength={10}
          required
          aria-invalid={corta || igual}
        />
        {corta ? (
          <span className="error">Al menos 10 caracteres.</span>
        ) : igual ? (
          <span className="error">Tiene que ser distinta de la actual.</span>
        ) : (
          <span className="ayuda">Al menos 10 caracteres. Una frase larga vale más que símbolos.</span>
        )}
      </div>
      <div className="campo">
        <label htmlFor="pw-repetir">Repetir la nueva</label>
        <input
          id="pw-repetir"
          type="password"
          autoComplete="new-password"
          value={repetir}
          onChange={(e) => setRepetir(e.target.value)}
          required
          aria-invalid={distintas}
        />
        {distintas && <span className="error">No coinciden.</span>}
      </div>
      {error && (
        <p role="alert" style={{ margin: 0, color: "var(--peligro)", fontSize: "0.85rem" }}>
          {error}
        </p>
      )}
      <div className="acciones-formulario" style={{ marginTop: 0 }}>
        <button type="submit" disabled={!puede}>
          {ocupado ? "Cambiando…" : "Cambiar la contraseña"}
        </button>
      </div>
    </form>
  );
}
