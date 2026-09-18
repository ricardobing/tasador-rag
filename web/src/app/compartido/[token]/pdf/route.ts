/** El PDF del informe compartido. Público: el token es la credencial. */
const BASE = process.env.API_INTERNAL_URL ?? "http://api:8000";

export async function GET(_req: Request, ctx: { params: Promise<{ token: string }> }) {
  const { token } = await ctx.params;
  const r = await fetch(`${BASE}/v1/shared/${encodeURIComponent(token)}/pdf`, {
    cache: "no-store",
  });
  if (!r.ok) return new Response("No hay un PDF para ese informe.", { status: r.status });
  return new Response(r.body, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": 'inline; filename="informe.pdf"',
    },
  });
}
