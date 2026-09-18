"use client";

import { useEffect, useState } from "react";

import { CLAVE_TEMA } from "@/lib/tema";

type Tema = "claro" | "oscuro" | "auto";
const OPCIONES: [Tema, string][] = [
  ["claro", "Claro"],
  ["oscuro", "Oscuro"],
  ["auto", "Sistema"],
];

/**
 * Claro por defecto; el oscuro es una elección. Se guarda en el navegador y
 * el script de `layout.tsx` la aplica antes de pintar.
 */
export function Tema() {
  const [tema, setTema] = useState<Tema>("claro");

  useEffect(() => {
    try {
      const guardado = localStorage.getItem(CLAVE_TEMA);
      if (guardado === "oscuro" || guardado === "auto") setTema(guardado);
    } catch {
      /* sin storage: queda claro */
    }
  }, []);

  function elegir(t: Tema) {
    setTema(t);
    document.documentElement.dataset.theme = t;
    try {
      localStorage.setItem(CLAVE_TEMA, t);
    } catch {
      /* sin storage: vale para esta pestaña */
    }
  }

  return (
    <div className="tema" role="group" aria-label="Tema">
      {OPCIONES.map(([v, texto]) => (
        <button key={v} type="button" aria-pressed={tema === v} onClick={() => elegir(v)}>
          {texto}
        </button>
      ))}
    </div>
  );
}
