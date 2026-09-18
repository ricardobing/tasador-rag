"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { CamposDeVisita, leerCamposDeVisita } from "@/components/campos-propiedad";
import { Icono } from "@/components/iconos";
import type { Propiedad } from "@/lib/api";

/**
 * Regenerar: el flujo post-visita (doc 06 §2). Crea un informe NUEVO sobre la
 * misma propiedad con lo que se aprendió en la visita; el actual no se muta,
 * es un documento. Navega al stepper del nuevo.
 *
 * Los campos vienen precargados con lo que el informe ya sabe: lo que se
 * cambia pisa, lo que se deja igual se manda igual (la API solo toma lo que
 * viene, así que no hay forma de «borrar» un dato desde acá; es a propósito).
 */
export function Regenerar({ id, propiedad }: { id: string; propiedad?: Propiedad | null }) {
  const router = useRouter();
  const ref = useRef<HTMLDialogElement>(null);
  const [abierto, setAbierto] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (abierto && !d.open) d.showModal();
    if (!abierto && d.open) d.close();
  }, [abierto]);

  async function enviar(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setOcupado(true);
    setError(null);
    const datos = new FormData(e.currentTarget);
    const numero = (k: string) => {
      const v = datos.get(k);
      return v === null || v === "" ? undefined : Number(v);
    };
    try {
      const r = await fetch(`/informes/${id}/api/regenerar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          property: {
            rooms: numero("rooms"),
            surface_total: numero("surface_total"),
            surface_covered: numero("surface_covered"),
            ...leerCamposDeVisita(datos),
          },
        }),
      });
      if (!r.ok) {
        const cuerpo = await r.json().catch(() => null);
        throw new Error(
          typeof cuerpo?.detail === "string" ? cuerpo.detail : `La API devolvió ${r.status}`,
        );
      }
      const { report_id } = await r.json();
      router.push(`/informes/${report_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo regenerar.");
      setOcupado(false);
    }
  }

  const s = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));

  return (
    <>
      <button type="button" className="boton-secundario" onClick={() => setAbierto(true)}>
        <Icono nombre="regenerar" tamano={18} />
        Regenerar
      </button>
      <dialog
        className="dialogo"
        ref={ref}
        onClose={() => setAbierto(false)}
        aria-labelledby="regenerar-titulo"
        style={{ maxWidth: "40rem" }}
      >
        <form onSubmit={enviar}>
          <div className="dialogo-cuerpo pila">
            <div>
              <h2 id="regenerar-titulo" style={{ marginBottom: "0.3rem" }}>
                Regenerar con los datos de la visita
              </h2>
              <p style={{ margin: 0 }}>
                Se genera un informe nuevo sobre la misma propiedad. Este queda como está.
              </p>
            </div>
            <div className="grilla-campos">
              <div className="campo">
                <label htmlFor="rg-rooms">Ambientes</label>
                <input id="rg-rooms" name="rooms" type="number" min={1} max={15} defaultValue={s(propiedad?.rooms)} />
              </div>
              <div className="campo">
                <label htmlFor="rg-surface_total">Sup. total (m²)</label>
                <input id="rg-surface_total" name="surface_total" type="number" step="0.1" defaultValue={s(propiedad?.surface_total)} />
              </div>
              <div className="campo">
                <label htmlFor="rg-surface_covered">Sup. cubierta (m²)</label>
                <input id="rg-surface_covered" name="surface_covered" type="number" step="0.1" defaultValue={s(propiedad?.surface_covered)} />
              </div>
            </div>
            <CamposDeVisita valores={propiedad} prefijo="rg-" />
            {error && (
              <p className="error" role="alert" style={{ margin: 0, color: "var(--peligro)", fontSize: "0.85rem" }}>
                {error}
              </p>
            )}
          </div>
          <div className="dialogo-acciones">
            <button type="button" className="boton-secundario" onClick={() => setAbierto(false)} disabled={ocupado}>
              Cancelar
            </button>
            <button type="submit" disabled={ocupado}>
              {ocupado ? "Encolando…" : "Generar el informe nuevo"}
            </button>
          </div>
        </form>
      </dialog>
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
    <>
      <button type="button" className="boton-secundario" onClick={compartir} disabled={ocupado}>
        <Icono nombre="compartir" tamano={18} />
        {ocupado ? "Generando…" : "Compartir"}
      </button>
      {error && (
        <span role="alert" style={{ color: "var(--peligro)", fontSize: "0.85rem" }}>
          {error}
        </span>
      )}
      {url && (
        <div className="aviso aviso-exito" style={{ flexBasis: "100%" }}>
          <Icono nombre="tilde" />
          <div style={{ minWidth: 0, flex: 1 }}>
            <strong style={{ display: "block", marginBottom: "0.2rem" }}>
              Link para el propietario, vence en 30 días
            </strong>
            <code style={{ wordBreak: "break-all", userSelect: "all" }}>{url}</code>
          </div>
          <button type="button" className="boton-secundario boton-chico" onClick={copiar}>
            <Icono nombre={copiado ? "tilde" : "copiar"} tamano={16} />
            {copiado ? "Copiado" : "Copiar"}
          </button>
        </div>
      )}
    </>
  );
}
