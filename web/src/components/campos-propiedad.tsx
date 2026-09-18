"use client";

import type { Propiedad } from "@/lib/api";

/**
 * Los campos que se aprenden EN la visita (doc 07 §4). Los usan dos pantallas:
 * el alta, donde van colapsados porque antes de la visita se sabe poco, y
 * «Regenerar», donde son el motivo de la regeneración.
 *
 * Son inputs no controlados: la pantalla que los incluye lee el `FormData` y
 * arma el cuerpo con `leerCamposDeVisita`. Un `null` en `valores` deja el
 * campo vacío, que es lo que la API entiende como «sin declarar».
 */
export const CONDICIONES: [string, string][] = [
  ["a_estrenar", "A estrenar"],
  ["excelente", "Excelente"],
  ["muy_bueno", "Muy bueno"],
  ["bueno", "Bueno"],
  ["a_refaccionar", "A refaccionar"],
];

export const ORIENTACIONES: [string, string][] = [
  ["frente", "Frente"],
  ["contrafrente", "Contrafrente"],
  ["lateral", "Lateral"],
  ["interno", "Interno"],
];

const s = (v: number | string | null | undefined) => (v === null || v === undefined ? "" : String(v));

export function CamposDeVisita({ valores, prefijo = "" }: { valores?: Propiedad | null; prefijo?: string }) {
  const id = (k: string) => `${prefijo}${k}`;
  return (
    <>
      <div className="grilla-campos">
        <div className="campo">
          <label htmlFor={id("bedrooms")}>Dormitorios</label>
          <input id={id("bedrooms")} name="bedrooms" type="number" min={0} max={12} defaultValue={s(valores?.bedrooms)} />
        </div>
        <div className="campo">
          <label htmlFor={id("bathrooms")}>Baños</label>
          <input id={id("bathrooms")} name="bathrooms" type="number" min={0} max={10} defaultValue={s(valores?.bathrooms)} />
        </div>
        <div className="campo">
          <label htmlFor={id("age_years")}>Antigüedad (años)</label>
          <input id={id("age_years")} name="age_years" type="number" min={0} max={200} defaultValue={s(valores?.age_years)} />
        </div>
        <div className="campo">
          <label htmlFor={id("floor_number")}>Piso</label>
          <input id={id("floor_number")} name="floor_number" type="number" min={-5} max={200} defaultValue={s(valores?.floor_number)} />
        </div>
        <div className="campo">
          <label htmlFor={id("has_elevator")}>Ascensor</label>
          <select
            id={id("has_elevator")}
            name="has_elevator"
            defaultValue={valores?.has_elevator === null || valores?.has_elevator === undefined ? "" : valores.has_elevator ? "si" : "no"}
          >
            <option value="">Sin dato</option>
            <option value="si">Sí</option>
            <option value="no">No</option>
          </select>
        </div>
        <div className="campo">
          <label htmlFor={id("condition")}>Estado</label>
          <select id={id("condition")} name="condition" defaultValue={s(valores?.condition)}>
            <option value="">Sin dato</option>
            {CONDICIONES.map(([v, t]) => (
              <option key={v} value={v}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div className="campo">
          <label htmlFor={id("orientation")}>Orientación</label>
          <select id={id("orientation")} name="orientation" defaultValue={s(valores?.orientation)}>
            <option value="">Sin dato</option>
            {ORIENTACIONES.map(([v, t]) => (
              <option key={v} value={v}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div className="campo">
          <label htmlFor={id("parking_spaces")}>Cocheras</label>
          <input id={id("parking_spaces")} name="parking_spaces" type="number" min={0} max={10} defaultValue={s(valores?.parking_spaces)} />
        </div>
        <div className="campo">
          <label htmlFor={id("expenses_ars")}>Expensas (ARS por mes)</label>
          <input id={id("expenses_ars")} name="expenses_ars" type="number" min={0} step="1" defaultValue={s(valores?.expenses_ars)} />
        </div>
      </div>
      <div className="campo">
        <label htmlFor={id("notes")}>Notas de la visita</label>
        <textarea id={id("notes")} name="notes" maxLength={4000} defaultValue={s(valores?.notes)} />
        <span className="ayuda">Lo que no entra en un campo: luminosidad, ruido, estado del edificio.</span>
      </div>
    </>
  );
}

/** Convierte el `FormData` en el cuerpo que espera la API: vacío es `undefined`. */
export function leerCamposDeVisita(datos: FormData): Record<string, unknown> {
  const numero = (k: string) => {
    const v = datos.get(k);
    return v === null || v === "" ? undefined : Number(v);
  };
  const texto = (k: string) => {
    const v = datos.get(k);
    return v === null || v === "" ? undefined : String(v);
  };
  const ascensor = datos.get("has_elevator");
  return {
    bedrooms: numero("bedrooms"),
    bathrooms: numero("bathrooms"),
    age_years: numero("age_years"),
    floor_number: numero("floor_number"),
    has_elevator: ascensor === "si" ? true : ascensor === "no" ? false : undefined,
    condition: texto("condition"),
    orientation: texto("orientation"),
    parking_spaces: numero("parking_spaces"),
    expenses_ars: numero("expenses_ars"),
    notes: texto("notes"),
  };
}
