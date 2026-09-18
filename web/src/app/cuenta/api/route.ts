import { proxy } from "@/lib/proxy";

export async function PATCH(req: Request) {
  return proxy("/v1/auth/password", { method: "PATCH", body: await req.text() });
}
