"use client";

/**
 * Lo que se ve cuando algo falla del lado del servidor — H-37.
 *
 * Sin este archivo, Next muestra su pantalla por defecto: en producción, un
 * «Application error: a server-side exception has occurred» con un digest
 * hexadecimal. Sin marca, sin explicación y sin forma de reintentar.
 *
 * Seis páginas hacen `throw e` cuando la API no responde, así que este es el
 * camino que ve alguien cuando la API está caída — el caso más probable de
 * todos. Doc 07 pone mucho cuidado en que un `INSUFFICIENT_DATA` se lea como
 * una respuesta y no como un error; este es el mismo cuidado para el caso en
 * que sí es un error.
 *
 * `reset()` reintenta el render sin recargar la página, que es lo correcto
 * para una API que se cayó por diez segundos.
 */

import Link from "next/link";
import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // A la consola del navegador y no a la pantalla: el mensaje de una
    // excepción puede traer detalles internos, y quien mira la pantalla no
    // puede hacer nada con ellos.
    console.error(error);
  }, [error]);

  return (
    <div className="tarjeta" style={{ maxWidth: "40rem" }}>
      <h1>No pudimos cargar esta pantalla</h1>
      <p>
        Es un problema nuestro, no de lo que pediste. Lo más probable es que el
        servicio no esté respondiendo en este momento.
      </p>
      <p className="tenue">
        Si vuelve a pasar, avisá con la hora y qué estabas mirando.
        {error.digest ? ` Referencia: ${error.digest}.` : ""}
      </p>
      <p style={{ display: "flex", gap: "0.75rem", marginTop: "1.25rem" }}>
        <button type="button" onClick={reset}>
          Reintentar
        </button>
        <Link href="/">Volver al inicio</Link>
      </p>
    </div>
  );
}
