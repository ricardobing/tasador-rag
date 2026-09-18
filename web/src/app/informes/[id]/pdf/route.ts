/**
 * Proxy de descarga del PDF.
 *
 * El archivo lo sirve la API desde disco y NO se regenera: un informe entregado
 * en septiembre tiene que ser byte por byte el mismo en diciembre. Este proxy
 * solo lo reenvía, preservando el `X-Content-SHA256` para que quien lo recibe
 * pueda probar que es el que se generó.
 */
import { cookies } from "next/headers";

const BASE = process.env.API_INTERNAL_URL ?? "http://api:8000";
const ORG_DE_DESARROLLO = process.env.TASADOR_ORG_SLUG ?? "";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  // La sesión del usuario, reenviada. Sin esto la API devuelve 401 y el
  // usuario ve "no hay PDF" cuando el PDF está y es suyo.
  const sesion = (await cookies()).get("tasador_sesion")?.value;
  const r = await fetch(`${BASE}/v1/reports/${id}/pdf`, {
    headers: sesion
      ? { Cookie: `tasador_sesion=${sesion}` }
      : ORG_DE_DESARROLLO
        ? { "X-Org-Slug": ORG_DE_DESARROLLO }
        : {},
    cache: "no-store",
  });

  if (!r.ok) {
    return new Response("No hay un PDF para ese informe.", { status: r.status });
  }

  return new Response(r.body, {
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": `attachment; filename="informe-${id}.pdf"`,
      "X-Content-SHA256": r.headers.get("X-Content-SHA256") ?? "",
    },
  });
}
