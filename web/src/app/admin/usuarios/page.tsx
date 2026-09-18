import { Aviso, Chip, PageHeader, Seccion, fecha } from "@/components/ui";
import { ErrorDeApi, getUsuarios, quienSoy } from "@/lib/api";
import { AccionesDeUsuario, CrearUsuario } from "./acciones";

export const dynamic = "force-dynamic";

/**
 * doc 07: alta de usuarios. No hay registro público: los crea un admin.
 * Desactivar corta la sesión al instante: la API relee al usuario de la base
 * en cada request, así que no hay ningún token que revocar.
 */
export default async function Usuarios() {
  let usuarios;
  try {
    usuarios = await getUsuarios();
  } catch (e) {
    if (e instanceof ErrorDeApi && e.status === 403) {
      return (
        <div className="contenido-panel">
          <PageHeader titulo="Usuarios" />
          <Aviso tono="info" titulo="Solo para administradores">
            Administrar usuarios requiere una sesión de owner o admin.
          </Aviso>
        </div>
      );
    }
    throw e;
  }
  const yo = await quienSoy();

  return (
    <div className="contenido-panel">
      <PageHeader
        titulo="Usuarios"
        bajada="Quién puede entrar y qué puede hacer. Desactivar corta la sesión al instante."
      />

      <div className="tabla tabla-apilable">
        <table>
          <thead>
            <tr>
              <th scope="col">Email</th>
              <th scope="col">Nombre</th>
              <th scope="col">Rol</th>
              <th scope="col">Último ingreso</th>
              <th scope="col">Estado</th>
              <th scope="col">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {usuarios.map((u) => (
              <tr key={u.id} className={u.active ? "" : "fuera"}>
                <td data-col="Email" style={{ fontWeight: 600, color: "var(--texto-fuerte)" }}>
                  {u.email}
                  {yo?.email === u.email && <span className="tenue"> (vos)</span>}
                </td>
                <td data-col="Nombre" className="tenue">{u.full_name ?? "sin nombre"}</td>
                <td data-col="Rol">
                  <Chip tono="marca">{u.role}</Chip>
                </td>
                <td data-col="Último ingreso" className="tenue">{fecha(u.last_login_at, true) ?? "nunca"}</td>
                <td data-col="Estado">
                  <Chip tono={u.active ? "exito" : "neutro"}>{u.active ? "activo" : "desactivado"}</Chip>
                </td>
                <td data-col="Acciones">
                  <AccionesDeUsuario id={u.id} email={u.email} activo={u.active} rol={u.role} esYo={yo?.email === u.email} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Seccion
        id="alta"
        titulo="Nuevo usuario"
        bajada="Si no se escribe contraseña, se genera una y se muestra una sola vez."
      >
        <CrearUsuario />
      </Seccion>
    </div>
  );
}
