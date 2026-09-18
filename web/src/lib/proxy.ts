import { cookies } from "next/headers";

/**
 * Proxy de mutaciones: el navegador le pega a un route handler de Next y este
 * reenvía a la API con la sesión. Mismo motivo que `api.ts`: `api:8000` solo
 * resuelve dentro de la red de Docker y la cookie no tiene por qué salir del
 * servidor.
 */
const BASE = process.env.API_INTERNAL_URL ?? "http://api:8000";
const ORG_DE_DESARROLLO = process.env.TASADOR_ORG_SLUG ?? "";

export async function proxy(ruta: string, init?: RequestInit): Promise<Response> {
  const sesion = (await cookies()).get("tasador_sesion")?.value;
  const r = await fetch(`${BASE}${ruta}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(sesion
        ? { Cookie: `tasador_sesion=${sesion}` }
        : ORG_DE_DESARROLLO
          ? { "X-Org-Slug": ORG_DE_DESARROLLO }
          : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  return new Response(await r.text(), {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}
