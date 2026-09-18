import { proxy } from "@/lib/proxy";

export async function POST(req: Request) {
  return proxy("/v1/admin/api-keys", { method: "POST", body: await req.text() });
}
