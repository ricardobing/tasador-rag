"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * Alta de la propiedad a tasar — doc 07 §4.
 *
 * Dos bloques, con el segundo colapsado. No es una decisión estética: refleja
 * el flujo real. **Antes de la visita se sabe poco, después se sabe todo**, y
 * por eso solo dirección + tipo son obligatorios.
 */
export default function Nuevo() {
  const router = useRouter();
  const [mas, setMas] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState("");
  const [cubierta, setCubierta] = useState("");
  const [ambientes, setAmbientes] = useState("3");

  const nTotal = Number(total) || 0;
  const nCubierta = Number(cubierta) || 0;
  const nAmb = Number(ambientes) || 0;

  // Bloquea: cubierta > total es un error de carga, y el CHECK de la base lo
  // rechaza igual. Avisarlo acá evita un 422 que el usuario no entiende.
  const errorSuperficie = nCubierta > 0 && nTotal > 0 && nCubierta > nTotal;
  // Advierte y NO bloquea: un monoambiente de 20 m² con "2 ambientes" cargado
  // puede ser real. Bloquearlo sería decidir por el que sabe más que nosotros.
  const avisoSuperficie = nTotal > 0 && nAmb > 0 && nTotal / nAmb < 12;

  async function enviar(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    const datos = new FormData(e.currentTarget);

    const numero = (k: string) => {
      const v = datos.get(k);
      return v === null || v === "" ? undefined : Number(v);
    };

    try {
      const r = await fetch("/informes/nuevo/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          property: {
            address_raw: datos.get("address_raw"),
            property_type: datos.get("property_type"),
            rooms: numero("rooms"),
            bedrooms: numero("bedrooms"),
            bathrooms: numero("bathrooms"),
            surface_total: numero("surface_total"),
            surface_covered: numero("surface_covered"),
            age_years: numero("age_years"),
            floor_number: numero("floor_number"),
            condition: datos.get("condition") || undefined,
            orientation: datos.get("orientation") || undefined,
            notes: datos.get("notes") || undefined,
          },
        }),
      });
      const cuerpo = await r.json();
      if (!r.ok) throw new Error(cuerpo?.detail ?? "No se pudo generar el informe.");
      router.push(`/informes/${cuerpo.report_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error inesperado.");
      setEnviando(false);
    }
  }

  return (
    <>
      <h1>Nuevo informe</h1>
      <form onSubmit={enviar}>
        <div className="tarjeta" style={{ display: "grid", gap: "0.9rem" }}>
          <div>
            <label htmlFor="address_raw">Dirección *</label>
            <input
              id="address_raw"
              name="address_raw"
              required
              minLength={3}
              placeholder="Av. Cabildo 2530"
            />
          </div>

          <div style={{ display: "grid", gap: "0.9rem", gridTemplateColumns: "repeat(4, 1fr)" }}>
            <div>
              <label htmlFor="property_type">Tipo *</label>
              <select id="property_type" name="property_type" defaultValue="departamento">
                <option value="departamento">Departamento</option>
                <option value="casa">Casa</option>
                <option value="ph">PH</option>
              </select>
            </div>
            <div>
              <label htmlFor="rooms">Ambientes</label>
              <input
                id="rooms"
                name="rooms"
                type="number"
                min={1}
                max={15}
                value={ambientes}
                onChange={(e) => setAmbientes(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="surface_total">Sup. total (m²)</label>
              <input
                id="surface_total"
                name="surface_total"
                type="number"
                step="0.1"
                value={total}
                onChange={(e) => setTotal(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="surface_covered">Sup. cubierta (m²)</label>
              <input
                id="surface_covered"
                name="surface_covered"
                type="number"
                step="0.1"
                value={cubierta}
                onChange={(e) => setCubierta(e.target.value)}
                aria-invalid={errorSuperficie}
              />
            </div>
          </div>

          {errorSuperficie && (
            <p style={{ color: "#b3261e", margin: 0 }} role="alert">
              La superficie cubierta no puede ser mayor que la total.
            </p>
          )}
          {avisoSuperficie && !errorSuperficie && (
            <p style={{ color: "var(--ambar)", margin: 0 }}>
              {(nTotal / nAmb).toFixed(1)} m² por ambiente es poco habitual. Se puede generar
              igual.
            </p>
          )}
        </div>

        <p style={{ margin: "1rem 0" }}>
          <button type="button" onClick={() => setMas(!mas)} style={{ background: "transparent", color: "var(--acento)", padding: 0 }}>
            {mas ? "▾" : "▸"} Tengo más datos
          </button>
        </p>

        {mas && (
          <div
            className="tarjeta"
            style={{ display: "grid", gap: "0.9rem", gridTemplateColumns: "repeat(3, 1fr)" }}
          >
            <div>
              <label htmlFor="bedrooms">Dormitorios</label>
              <input id="bedrooms" name="bedrooms" type="number" min={0} max={12} />
            </div>
            <div>
              <label htmlFor="bathrooms">Baños</label>
              <input id="bathrooms" name="bathrooms" type="number" min={0} max={10} />
            </div>
            <div>
              <label htmlFor="age_years">Antigüedad (años)</label>
              <input id="age_years" name="age_years" type="number" min={0} max={200} />
            </div>
            <div>
              <label htmlFor="floor_number">Piso</label>
              <input id="floor_number" name="floor_number" type="number" min={-5} max={200} />
            </div>
            <div>
              <label htmlFor="condition">Estado</label>
              <select id="condition" name="condition" defaultValue="">
                <option value="">Sin dato</option>
                <option value="a_estrenar">A estrenar</option>
                <option value="excelente">Excelente</option>
                <option value="muy_bueno">Muy bueno</option>
                <option value="bueno">Bueno</option>
                <option value="a_refaccionar">A refaccionar</option>
              </select>
            </div>
            <div>
              <label htmlFor="orientation">Orientación</label>
              <select id="orientation" name="orientation" defaultValue="">
                <option value="">Sin dato</option>
                <option value="frente">Frente</option>
                <option value="contrafrente">Contrafrente</option>
                <option value="lateral">Lateral</option>
                <option value="interno">Interno</option>
              </select>
            </div>
            <div style={{ gridColumn: "1 / -1" }}>
              <label htmlFor="notes">Notas</label>
              <input id="notes" name="notes" maxLength={4000} />
            </div>
          </div>
        )}

        {error && (
          <p style={{ color: "#b3261e" }} role="alert">
            {error}
          </p>
        )}

        <p style={{ marginTop: "1.4rem" }}>
          <button type="submit" disabled={enviando || errorSuperficie}>
            {enviando ? "Generando…" : "Generar informe"}
          </button>
        </p>
        {/* El microcopy que importa (doc 07 §4): dice que se puede empezar con
            poco, que es exactamente cómo se usa. */}
        <p className="tenue" style={{ fontSize: "0.88rem" }}>
          Con más datos el rango es más ajustado. Podés generar ahora y regenerar después de la
          visita.
        </p>
      </form>
    </>
  );
}
