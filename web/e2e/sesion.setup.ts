import { expect, test as setup } from "@playwright/test";

/**
 * Loguea una vez y guarda la sesión para el resto de la suite.
 *
 * El punto no es la velocidad: es que **los tests corran contra la
 * configuración de producción**. Con el respaldo de desarrollo encendido
 * (`TASADOR_ORG_SLUG`), el middleware no rebota y la cookie no se usa: la
 * suite estaría verde sobre un camino que en producción no existe.
 *
 * Es la misma lección que la auditoría del 14/08 dejó tres veces — el arreglo
 * de los bugs del contenedor vivía solo en el override de dev, el gate de
 * aislamiento apuntaba a un directorio vacío, el eval-gate llamaba a un módulo
 * inexistente. Un test que no recorre el camino real no prueba el camino real.
 */
const ARCHIVO = "e2e/.sesion.json";

setup("iniciar sesión", async ({ page }) => {
  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;
  setup.skip(
    !email || !password,
    "faltan E2E_EMAIL / E2E_PASSWORD — ver ESTADO §8 para crear el usuario",
  );

  await page.goto("/login");
  await page.getByLabel("Email").fill(email!);
  await page.getByLabel("Contraseña").fill(password!);
  await page.getByRole("button", { name: "Entrar" }).click();

  // Se espera el LISTADO y no solo la URL: si la cookie no viajó al servidor
  // de Next, la URL cambia igual y la pantalla rebota un instante después. El
  // heading es la prueba de que la sesión llegó hasta la API.
  await expect(page.getByRole("heading", { name: "Informes" })).toBeVisible();

  await page.context().storageState({ path: ARCHIVO });
});
