import { proxy } from "@/lib/proxy";

export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return proxy(`/v1/reports/${id}/regenerate`, { method: "POST", body: await req.text() });
}
