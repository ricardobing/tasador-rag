import { crearInforme, ErrorDeApi } from "@/lib/api";

// `crearInforme` ya reenvía la sesión: el cliente de `@/lib/api` lee la cookie
// del request entrante. Este handler solo traduce errores.

/**
 * Proxy del POST del formulario.
 *
 * El navegador no puede pegarle a `api:8000` —ese nombre solo resuelve dentro
 * de la red de Docker— así que el alta pasa por acá. Y es también el punto
 * donde la sesión del usuario se reenvía a la API sin que el token vuelva a
 * salir al cliente.
 */
export async function POST(req: Request) {
  const body = await req.json();
  try {
    // Idempotencia por pedido: dos clicks del botón —o un reintento del
    // navegador— no encolan dos informes ni cuestan el doble (doc 06 §2).
    const r = await crearInforme(body, crypto.randomUUID());
    return Response.json(r, { status: 202 });
  } catch (e) {
    if (e instanceof ErrorDeApi) {
      // Se preserva el status real: un 422 de validación y un 401 sin tenant
      // son cosas distintas y el formulario tiene que poder distinguirlas.
      let detail = "No se pudo generar el informe.";
      try {
        detail = JSON.parse(e.cuerpo)?.detail ?? detail;
      } catch {
        /* el cuerpo no era JSON: queda el mensaje genérico */
      }
      return Response.json({ detail }, { status: e.status });
    }
    return Response.json({ detail: "El servidor no respondió." }, { status: 502 });
  }
}
