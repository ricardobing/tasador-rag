/**
 * Proxy del login.
 *
 * Existe por una razón concreta: la cookie de sesión la emite la API, y la API
 * no es visible desde el navegador —`api:8000` solo resuelve dentro de la red
 * de Docker—. Este handler recibe el POST del formulario, se lo pasa a la API,
 * y **reenvía el `Set-Cookie` al navegador**.
 *
 * Efecto colateral sano: la contraseña nunca viaja a un origen distinto del que
 * sirvió la página, así que la cookie puede ser `SameSite=Lax` sin fricción.
 */
const BASE = process.env.API_INTERNAL_URL ?? "http://api:8000";

export async function POST(req: Request) {
  const body = await req.text();

  const r = await fetch(`${BASE}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    cache: "no-store",
  });

  const cuerpo = await r.text();
  const cabeceras = new Headers({ "Content-Type": "application/json" });

  // `getSetCookie()` y no `get()`: si mañana la API manda más de una cookie,
  // `get()` devolvería solo la primera y la sesión se perdería en silencio.
  for (const galleta of r.headers.getSetCookie?.() ?? []) {
    cabeceras.append("set-cookie", galleta);
  }

  return new Response(cuerpo, { status: r.status, headers: cabeceras });
}

export async function DELETE() {
  const r = await fetch(`${BASE}/v1/auth/logout`, { method: "POST", cache: "no-store" });
  const cabeceras = new Headers({ "Content-Type": "application/json" });
  for (const galleta of r.headers.getSetCookie?.() ?? []) {
    cabeceras.append("set-cookie", galleta);
  }
  return new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: cabeceras });
}
