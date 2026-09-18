import { defineConfig, devices } from "@playwright/test";

/**
 * Tests de las pantallas contra el STACK REAL levantado.
 *
 * No se mockea la API. La razón es la misma que dejó 20 bugs en la Etapa 3 y 12
 * en la auditoría del 14/08: **ninguno de los dos grupos se encontró leyendo
 * código, y varios solo existían al integrar**. Un test de UI contra una API
 * mockeada prueba que el componente renderiza lo que el mock devuelve, que es
 * lo único que nunca falla.
 *
 * Contrapartida honesta: estos tests necesitan el stack arriba, así que NO
 * corren en el CI todavía. Están marcados como suite aparte y se corren a mano
 * (`npm run test:e2e`) o contra un compose levantado en el job. Un test que el
 * CI no corre es un test que se pudre; queda anotado como deuda en ESTADO.
 *
 * `BASE_URL` es configurable porque en Windows el puerto 3000 suele estar
 * reservado por Hyper-V (ver docker-compose.dev.yml).
 */
const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3300";

/**
 * `E2E_CON_AUTH=1` corre la suite como en PRODUCCIÓN: el stack levantado con
 * `TASADOR_ORG_SLUG=` (vacío), login real, y la sesión guardada en disco.
 *
 * Sin la variable, la suite corre contra el modo desarrollo. Las dos formas
 * sirven —una es rápida de iterar y la otra es la que vale— pero la que se
 * corre antes de dar algo por bueno es la primera.
 */
const CON_AUTH = process.env.E2E_CON_AUTH === "1";
const SESION = CON_AUTH ? { storageState: "e2e/.sesion.json" } : {};

export default defineConfig({
  testDir: "./e2e",
  // Sin paralelismo: los tests comparten UNA base de datos y varios crean
  // informes. Correrlos en paralelo haría que el listado de uno vea las filas
  // de otro y los fallos serían intermitentes — lo peor que le puede pasar a
  // una suite, porque enseña a reintentar en vez de a mirar.
  workers: 1,
  fullyParallel: false,
  // Cero reintentos, también a propósito: un test de integración que pasa al
  // segundo intento está diciendo algo, y `retries` lo silencia.
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "es-AR",
  },
  projects: [
    // Loguea una vez y deja la sesión en disco. Ver `e2e/sesion.setup.ts`.
    ...(CON_AUTH ? [{ name: "sesion", testMatch: /sesion\.setup\.ts/ }] : []),
    {
      name: "escritorio",
      use: { ...devices["Desktop Chrome"], ...SESION },
      dependencies: CON_AUTH ? ["sesion"] : [],
    },
    // Mobile first no es un adorno en doc 07 §12: el agente genera el informe
    // desde el auto. Si la tabla rompe el layout en 375px, el producto no
    // sirve donde se usa.
    {
      name: "celular",
      use: { ...devices["Pixel 7"], ...SESION },
      dependencies: CON_AUTH ? ["sesion"] : [],
    },
  ],
});
