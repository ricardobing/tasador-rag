/**
 * Healthcheck del contenedor `web`.
 *
 * El `healthcheck` de docker-compose.yml pega acá con `fetch`. Si esta ruta no
 * existe, el contenedor queda `unhealthy` para siempre y cualquier
 * `depends_on: {web: {condition: service_healthy}}` no arranca nunca.
 *
 * Es LIVENESS, no readiness: no consulta la API. Un front que renderiza es un
 * front vivo aunque el backend esté caído — y en ese caso el que tiene que
 * ponerse en rojo es `api`, no `web`. Mezclarlos hace que un incidente del
 * backend reinicie el front sin motivo.
 */
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok", service: "web" });
}
