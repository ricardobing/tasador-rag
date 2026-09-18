"use client";

import { useState } from "react";

/**
 * «Preguntale al informe» (doc 18 §5). La respuesta viene con las citas que
 * la sostienen —cada una es un hecho del informe o un párrafo de la
 * metodología— o viene rechazada: cuando no hay evidencia, la API no llama
 * al modelo y lo dice. Nada de lo que se muestra acá es una cifra nueva.
 */
type Cita = { id: string; texto: string; fuente: string };
type Respuesta = {
  respuesta: string;
  citas: Cita[];
  rechazada: boolean;
  motivo: string | null;
  cost_usd: number;
  duration_ms: number;
};

const EJEMPLOS = [
  "¿Por qué se descartaron algunos comparables?",
  "¿Por qué se usa la mediana y no el promedio?",
  "¿Qué significa el nivel de confianza?",
];

export function Preguntar({ id }: { id: string }) {
  const [pregunta, setPregunta] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historial, setHistorial] = useState<{ pregunta: string; r: Respuesta }[]>([]);

  async function enviar(texto: string) {
    const p = texto.trim();
    if (p.length < 3 || ocupado) return;
    setOcupado(true);
    setError(null);
    try {
      const res = await fetch(`/informes/${id}/api/preguntar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pregunta: p }),
      });
      if (!res.ok) throw new Error(`La API devolvió ${res.status}`);
      const r = (await res.json()) as Respuesta;
      setHistorial((h) => [{ pregunta: p, r }, ...h]);
      setPregunta("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo preguntar.");
    } finally {
      setOcupado(false);
    }
  }

  return (
    <section style={{ marginTop: "1.5rem" }} aria-labelledby="preguntar-titulo">
      <h2 id="preguntar-titulo">Preguntale al informe</h2>
      <p className="tenue" style={{ marginTop: 0 }}>
        Responde solo con lo que está en este informe y en la metodología, y cita de dónde lo
        sacó. Si no está, lo dice.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void enviar(pregunta);
        }}
        style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}
      >
        <label htmlFor="pregunta" className="visualmente-oculto">
          Pregunta
        </label>
        <input
          id="pregunta"
          value={pregunta}
          onChange={(e) => setPregunta(e.target.value)}
          placeholder="¿Por qué no se usó el aviso de…?"
          maxLength={500}
          style={{ flex: "1 1 20rem" }}
          disabled={ocupado}
        />
        <button type="submit" disabled={ocupado || pregunta.trim().length < 3}>
          {ocupado ? "Buscando…" : "Preguntar"}
        </button>
      </form>
      <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginTop: "0.5rem" }}>
        {EJEMPLOS.map((e) => (
          <button
            key={e}
            type="button"
            className="secundario"
            onClick={() => void enviar(e)}
            disabled={ocupado}
            style={{ fontSize: "0.85rem" }}
          >
            {e}
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" style={{ color: "var(--ambar)" }}>
          {error}
        </p>
      )}
      {historial.map(({ pregunta: q, r }, i) => (
        <div key={i} className="tarjeta" style={{ marginTop: "0.8rem" }}>
          <p style={{ marginTop: 0 }}>
            <strong>{q}</strong>
          </p>
          <p style={{ marginBottom: r.citas.length ? "0.5rem" : 0 }}>
            {r.rechazada ? <em>{r.respuesta}</em> : r.respuesta}
          </p>
          {r.citas.length > 0 && (
            <details>
              <summary className="tenue">
                {r.citas.length} {r.citas.length === 1 ? "cita" : "citas"}
              </summary>
              <ul style={{ marginBottom: 0, fontSize: "0.9rem" }}>
                {r.citas.map((c) => (
                  <li key={c.id}>
                    <code>[{c.id}]</code> {c.texto}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <p className="tenue" style={{ fontSize: "0.75rem", marginBottom: 0 }}>
            {r.rechazada ? `Rechazada: ${r.motivo ?? "sin evidencia"}` : "Verificada: cada cifra existe en las citas"} ·{" "}
            {(r.duration_ms / 1000).toFixed(1)} s · USD {r.cost_usd.toFixed(4)}
          </p>
        </div>
      ))}
    </section>
  );
}
