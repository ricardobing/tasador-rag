import { cookies } from "next/headers";

/** Proxy del marcado. La API no es visible desde el navegador. */
const BASE = process.env.API_INTERNAL_URL ?? "http://api:8000";
const ORG_DE_DESARROLLO = process.env.TASADOR_ORG_SLUG ?? "";

export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const sesion = (await cookies()).get("tasador_sesion")?.value;
  const r = await fetch(`${BASE}/v1/comparables/${id}/revisar`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(sesion
        ? { Cookie: `tasador_sesion=${sesion}` }
        : ORG_DE_DESARROLLO
          ? { "X-Org-Slug": ORG_DE_DESARROLLO }
          : {}),
    },
    body: await req.text(),
    cache: "no-store",
  });
  return new Response(await r.text(), {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}
