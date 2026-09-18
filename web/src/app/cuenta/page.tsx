import { Aviso, KeyValue, PageHeader, Seccion } from "@/components/ui";
import { quienSoy } from "@/lib/api";
import { CambiarPassword } from "./cambiar-password";

export const dynamic = "force-dynamic";

/** Quién soy y cambiar la propia contraseña (`PATCH /v1/auth/password`). */
export default async function Cuenta() {
  const yo = await quienSoy();

  return (
    <div className="contenido-formulario">
      <PageHeader titulo="Mi cuenta" bajada="Con qué sesión estás trabajando y tu contraseña." />

      {!yo ? (
        <Aviso tono="alerta" titulo="Sin sesión">
          No hay una sesión válida. Volvé a entrar.
        </Aviso>
      ) : (
        <>
          <section className="tarjeta" aria-labelledby="sesion-titulo">
            <h2 id="sesion-titulo">Sesión</h2>
            <KeyValue
              datos={[
                { k: "Email", v: yo.email },
                { k: "Nombre", v: yo.full_name },
                { k: "Rol", v: yo.role },
                { k: "Organización", v: `${yo.org_name} (${yo.org_slug})` },
                {
                  k: "Entraste con",
                  v:
                    yo.via === "sesion"
                      ? "usuario y contraseña"
                      : yo.via === "header_dev"
                        ? "el modo desarrollo (sin usuario)"
                        : yo.via,
                },
              ]}
            />
          </section>

          <Seccion
            id="password"
            titulo="Cambiar la contraseña"
            bajada="Se pide la actual aunque haya sesión: una pestaña abierta no puede convertirse en un cambio silencioso."
          >
            {yo.via === "sesion" ? (
              <CambiarPassword />
            ) : (
              <Aviso tono="info">
                Solo con una sesión de usuario. En el modo desarrollo no hay contraseña que cambiar.
              </Aviso>
            )}
          </Seccion>
        </>
      )}
    </div>
  );
}
