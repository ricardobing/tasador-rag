import { proxy } from "@/lib/proxy";

export async function POST(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return proxy(`/v1/reports/${id}/share`, { method: "POST" });
}
