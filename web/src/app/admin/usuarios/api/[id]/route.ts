import { proxy } from "@/lib/proxy";

export async function PATCH(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return proxy(`/v1/admin/usuarios/${id}`, { method: "PATCH", body: await req.text() });
}
