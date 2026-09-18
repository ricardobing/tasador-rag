import { ErrorDeApi, getUsuarios } from "@/lib/api";
import { CrearUsuario, ToggleActivo } from "./acciones";

export const dynamic = "force-dynamic";

const fecha = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleDateString("es-AR", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "nunca";

/**
 * doc 07 — alta de usuarios. No hay registro público: los crea un admin.
 * Desactivar corta la sesión al instante — la API relee al usuario de la base
 * en cada request, así que no hay ningún token que revocar.
 */
export default async function Usuarios() {
  let usuarios;
  try {
    usuarios = await getUsuarios();
  } catch (e) {
    if (e instanceof ErrorDeApi && e.status === 403) {
      return (
        <div className="aviso">
          <h2 style={{ marginTop: 0 }}>Solo para administradores</h2>
          <p>Administrar usuarios requiere una sesión de owner o admin.</p>
        </div>
      );
    }
    throw e;
  }

  return (
    <>
      <h1>Usuarios</h1>

      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th scope="col">Email</th>
              <th scope="col">Nombre</th>
              <th scope="col">Rol</th>
              <th scope="col">Último login</th>
              <th scope="col">Estado</th>
              <th scope="col"></th>
            </tr>
          </thead>
          <tbody>
            {usuarios.map((u) => (
              <tr key={u.id} style={{ opacity: u.active ? 1 : 0.55 }}>
                <td>{u.email}</td>
                <td className="tenue">{u.full_name ?? "—"}</td>
                <td>
                  <span className="chip">{u.role}</span>
                </td>
                <td className="tenue">{fecha(u.last_login_at)}</td>
                <td>
                  <span className="chip" style={{ color: u.active ? "var(--alta)" : "var(--baja)" }}>
                    {u.active ? "activo" : "desactivado"}
                  </span>
                </td>
                <td>
                  <ToggleActivo id={u.id} email={u.email} activo={u.active} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <CrearUsuario />
    </>
  );
}
