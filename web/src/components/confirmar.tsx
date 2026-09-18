"use client";

import { useEffect, useRef } from "react";

/**
 * Confirmación de una acción destructiva. Reemplaza `window.confirm`, que no
 * se puede estilar, no dice qué va a pasar con el estilo del producto y en
 * algunos navegadores ni aparece. Usa `<dialog>` nativo: atrapa el Tab y
 * devuelve el foco al cerrar sin librerías.
 */
export function Confirmar({
  abierto,
  titulo,
  texto,
  verbo,
  peligro = true,
  ocupado = false,
  onConfirmar,
  onCancelar,
}: {
  abierto: boolean;
  titulo: string;
  texto: string;
  verbo: string;
  peligro?: boolean;
  ocupado?: boolean;
  onConfirmar: () => void;
  onCancelar: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (abierto && !d.open) d.showModal();
    if (!abierto && d.open) d.close();
  }, [abierto]);

  return (
    <dialog className="dialogo" ref={ref} onClose={onCancelar} aria-labelledby="dialogo-titulo">
      <div className="dialogo-cuerpo">
        <h2 id="dialogo-titulo" style={{ marginBottom: "0.4rem" }}>
          {titulo}
        </h2>
        <p>{texto}</p>
      </div>
      <div className="dialogo-acciones">
        <button type="button" className="boton-secundario" onClick={onCancelar} disabled={ocupado}>
          Cancelar
        </button>
        <button
          type="button"
          className={peligro ? "boton-peligro" : ""}
          onClick={onConfirmar}
          disabled={ocupado}
        >
          {ocupado ? "Un momento…" : verbo}
        </button>
      </div>
    </dialog>
  );
}
