"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { CamposDeVisita, leerCamposDeVisita } from "@/components/campos-propiedad";
import { Icono } from "@/components/iconos";
import { Aviso, PageHeader } from "@/components/ui";

/**
 * Alta de la propiedad a tasar (doc 07 §4).
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
            surface_total: numero("surface_total"),
            surface_covered: numero("surface_covered"),
            ...(mas ? leerCamposDeVisita(datos) : {}),
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
    <div className="contenido-formulario">
      <PageHeader
        titulo="Nuevo informe"
        bajada="Con la dirección y el tipo alcanza. Con más datos el rango es más ajustado."
        migas={[{ href: "/informes", texto: "Informes" }]}
      />

      <form onSubmit={enviar} noValidate={false}>
        <section className="tarjeta pila" aria-labelledby="propiedad-titulo">
          <h2 id="propiedad-titulo" style={{ marginBottom: 0 }}>
            La propiedad
          </h2>
          <div className="campo">
            <label htmlFor="address_raw">Dirección *</label>
            <input
              id="address_raw"
              name="address_raw"
              required
              minLength={3}
              placeholder="Av. Cabildo 2530"
              autoComplete="off"
            />
            <span className="ayuda">Calle y altura. El barrio se deduce de la dirección.</span>
          </div>

          <div className="grilla-campos">
            <div className="campo">
              <label htmlFor="property_type">Tipo *</label>
              <select id="property_type" name="property_type" defaultValue="departamento">
                <option value="departamento">Departamento</option>
                <option value="casa">Casa</option>
                <option value="ph">PH</option>
              </select>
            </div>
            <div className="campo">
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
            <div className="campo">
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
            <div className="campo">
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
              {errorSuperficie && (
                <p className="error" role="alert" style={{ margin: 0 }}>
                  La superficie cubierta no puede ser mayor que la total.
                </p>
              )}
            </div>
          </div>

          {avisoSuperficie && !errorSuperficie && (
            <p className="advertencia" style={{ margin: 0, fontSize: "0.82rem", color: "var(--alerta)" }}>
              {(nTotal / nAmb).toFixed(1)} m² por ambiente es poco habitual. Se puede generar igual.
            </p>
          )}
        </section>

        <p style={{ margin: "1rem 0" }}>
          <button
            type="button"
            className="boton-terciario"
            onClick={() => setMas(!mas)}
            aria-expanded={mas}
            aria-controls="bloque-visita"
          >
            <Icono nombre="flecha" tamano={16} />
            {mas ? "Ocultar los datos de la visita" : "Tengo más datos"}
          </button>
        </p>

        {mas && (
          <section className="tarjeta pila" id="bloque-visita" aria-labelledby="visita-titulo">
            <h2 id="visita-titulo" style={{ marginBottom: 0 }}>
              Lo que se sabe de la visita
            </h2>
            <CamposDeVisita />
          </section>
        )}

        {error && (
          <div style={{ marginTop: "1rem" }}>
            <Aviso tono="peligro" titulo="No se pudo generar">
              {error}
            </Aviso>
          </div>
        )}

        <div className="acciones-formulario">
          <button type="submit" disabled={enviando || errorSuperficie}>
            {enviando ? "Generando…" : "Generar informe"}
          </button>
          {/* El microcopy que importa (doc 07 §4): dice que se puede empezar
              con poco, que es exactamente cómo se usa. */}
          <span className="ayuda">Podés generar ahora y regenerar después de la visita.</span>
        </div>
      </form>
    </div>
  );
}
