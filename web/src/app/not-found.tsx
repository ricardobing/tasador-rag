/**
 * El 404 del producto (H-37).
 *
 * `notFound()` sin este archivo renderiza el 404 genérico de Next: fondo
 * blanco, "404 | This page could not be found", en inglés y sin navegación.
 *
 * Los dos caminos que llegan acá son un informe que no existe (o que es de
 * otra inmobiliaria, que para el cliente es lo mismo; ver el gate de
 * aislamiento) y una URL mal tipeada.
 */

import { BotonLink, Vacio } from "@/components/ui";

export default function NoEncontrado() {
  return (
    <div className="contenido-documento">
      <Vacio
        icono="buscar"
        titulo="No encontramos eso"
        texto="El informe no existe, o el enlace está mal. Si te lo pasaron por mensaje, puede haberse cortado al copiarlo."
        accion={
          <BotonLink href="/informes" icono="informes">
            Ver los informes
          </BotonLink>
        }
      />
    </div>
  );
}
