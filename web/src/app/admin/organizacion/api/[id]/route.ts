import { proxy } from "@/lib/proxy";

export async function DELETE(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return proxy(`/v1/admin/api-keys/${id}`, { method: "DELETE" });
}
