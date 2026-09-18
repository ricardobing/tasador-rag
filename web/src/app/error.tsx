"use client";

/**
 * Lo que se ve cuando algo falla del lado del servidor (H-37).
 *
 * Sin este archivo, Next muestra su pantalla por defecto: en producción, un
 * «Application error: a server-side exception has occurred» con un digest
 * hexadecimal. Sin marca, sin explicación y sin forma de reintentar.
 *
 * Es el camino que ve alguien cuando la API está caída, el caso más probable
 * de todos. Doc 07 pone mucho cuidado en que un `INSUFFICIENT_DATA` se lea
 * como una respuesta y no como un error; este es el mismo cuidado para el
 * caso en que sí es un error.
 *
 * `reset()` reintenta el render sin recargar la página, que es lo correcto
 * para una API que se cayó por diez segundos.
 */

import { useEffect } from "react";
import { Icono } from "@/components/iconos";
import { BotonLink, Vacio } from "@/components/ui";

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
    <div className="contenido-documento">
      <Vacio
        icono="alerta"
        titulo="No pudimos cargar esta pantalla"
        texto={
          <>
            Es un problema nuestro, no de lo que pediste. Lo más probable es que el servicio no esté
            respondiendo en este momento. Si vuelve a pasar, avisá con la hora y qué estabas mirando.
            {error.digest ? ` Referencia: ${error.digest}.` : ""}
          </>
        }
        accion={
          <>
            <button type="button" onClick={reset}>
              <Icono nombre="regenerar" tamano={18} />
              Reintentar
            </button>
            <BotonLink href="/informes" variante="secundario">
              Volver al inicio
            </BotonLink>
          </>
        }
      />
    </div>
  );
}
