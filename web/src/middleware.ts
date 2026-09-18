import { NextResponse, type NextRequest } from "next/server";

/**
 * Puerta de entrada: sin sesión, al login.
 *
 * ⚠️ **Esto NO es la autorización.** Es comodidad de navegación: evita que el
 * usuario vea una pantalla vacía y después un error. Quien autoriza es la API,
 * que valida la cookie contra la base en cada request y relee al usuario para
 * que desactivar a alguien tenga efecto inmediato.
 *
 * La distinción importa porque un middleware que "protege" rutas invita a
 * confiar en él, y un middleware de Next se puede saltear pegándole a la API
 * directamente. La regla del proyecto es que la seguridad vive del lado del
 * servidor de datos, no del que pinta el HTML.
 *
 * `TASADOR_ORG_SLUG` desactiva el rebote: es el modo desarrollo, donde la API
 * todavía acepta el header y no hay usuario creado. En producción esa variable
 * no se define y la API ignora el header igual.
 */
// `/compartido` es el link firmado del propietario: el token del path es la
// credencial, no hay sesión que pedir.
const PUBLICAS = ["/login", "/api/health", "/compartido"];

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (PUBLICAS.some((p) => pathname.startsWith(p))) return NextResponse.next();
  if (process.env.TASADOR_ORG_SLUG) return NextResponse.next();
  if (req.cookies.has("tasador_sesion")) return NextResponse.next();

  const destino = req.nextUrl.clone();
  destino.pathname = "/login";
  // `volver` para no perder a dónde iba: si alguien abre el link a un informe
  // con la sesión vencida, después del login tiene que aterrizar en ESE
  // informe y no en el listado.
  destino.search = `?volver=${encodeURIComponent(pathname + req.nextUrl.search)}`;
  return NextResponse.redirect(destino);
}

export const config = {
  // Todo menos los estáticos de Next y el favicon.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
