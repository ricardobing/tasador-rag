/**
 * El 404 del producto — H-37.
 *
 * `notFound()` sin este archivo renderiza el 404 genérico de Next: fondo
 * blanco, "404 | This page could not be found", en inglés y sin la cabecera.
 *
 * Los dos caminos que llegan acá son un informe que no existe (o que es de
 * otra inmobiliaria, que para el cliente es lo mismo — ver el gate de
 * aislamiento) y una URL mal tipeada.
 */

import Link from "next/link";

export default function NoEncontrado() {
  return (
    <div className="tarjeta" style={{ maxWidth: "40rem" }}>
      <h1>No encontramos eso</h1>
      <p>
        El informe no existe, o el enlace está mal. Si te lo pasaron por mensaje,
        puede haberse cortado al copiarlo.
      </p>
      <p style={{ marginTop: "1.25rem" }}>
        <Link href="/">Ver los informes</Link>
      </p>
    </div>
  );
}
