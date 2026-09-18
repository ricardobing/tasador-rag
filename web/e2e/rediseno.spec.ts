import { expect, test, type Page } from "@playwright/test";

/**
 * Lo que el rediseño (doc 20) agregó al front. Cada test es una de las filas
 * de la auditoría de doc 20 §1 que decía "falta": si vuelve a faltar, esto lo
 * dice.
 */

async function abrirUnInformeTerminado(page: Page) {
  await page.goto("/informes?estado=SUCCEEDED");
  const fila = page.locator("table tbody tr").first();
  if ((await fila.count()) === 0) test.skip(true, "no hay ningún informe SUCCEEDED en la base");
  await fila.getByRole("link").first().click();
  await page.waitForURL(/\/informes\/[0-9a-f-]{36}/);
}

test.describe("la ficha es un documento: propiedad, valor, respaldo", () => {
  test("empieza por la propiedad tasada, con todos sus campos", async ({ page }) => {
    // doc 20 §1.2: la ficha no decía qué propiedad era. Ahora la API devuelve
    // `property` y el front la muestra ARRIBA, antes del número.
    await abrirUnInformeTerminado(page);
    const propiedad = page.getByRole("region", { name: /La propiedad tasada/i });
    await expect(propiedad).toBeVisible();
    for (const campo of ["Tipo", "Ambientes", "Baños", "Superficie total", "Estado", "Orientación"]) {
      await expect(propiedad.getByText(new RegExp(`^${campo}:`))).toBeVisible();
    }
    // Lo que falta se dice, no se esconde.
    const texto = await propiedad.innerText();
    expect(texto).not.toMatch(/undefined|null|NaN/);

    const yPropiedad = (await propiedad.boundingBox())!.y;
    const yValor = (await page.getByText("Precio de publicación sugerido").boundingBox())!.y;
    expect(yPropiedad).toBeLessThan(yValor);
  });

  test("los comparables están, chicos y al final, con usados y descartados", async ({ page }) => {
    // doc 20 §1.1: la API devolvía la tabla y el front no la mostraba. Va al
    // final porque sostiene el número, no es el número.
    await abrirUnInformeTerminado(page);
    const respaldo = page.getByRole("region", { name: /Respaldo: los \d+ avisos/ });
    await expect(respaldo).toBeVisible();
    await expect(respaldo.locator("tbody tr").first()).toBeVisible();
    await expect(respaldo.getByText("usado").first()).toBeVisible();

    const yValor = (await page.getByText("Precio de publicación sugerido").boundingBox())!.y;
    const yRespaldo = (await respaldo.boundingBox())!.y;
    expect(yRespaldo).toBeGreaterThan(yValor);

    // Más chica que el cuerpo: la tabla no compite con el número.
    const tamanoTabla = await respaldo.locator("tbody td").first().evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    const tamanoCuerpo = await page.locator("body").evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(tamanoTabla).toBeLessThan(tamanoCuerpo);
  });

  test("regenerar abre el formulario de la visita, precargado, sin encolar nada", async ({ page }) => {
    // doc 20 §1.1: el botón mandaba `{}`. Ahora abre los campos de
    // `PropertyPatch`. Se cancela: encolar gasta y tarda minutos.
    await abrirUnInformeTerminado(page);
    await page.getByRole("button", { name: "Regenerar" }).click();
    const dialogo = page.getByRole("dialog", { name: /Regenerar con los datos de la visita/ });
    await expect(dialogo).toBeVisible();
    await expect(dialogo.getByLabel("Dormitorios")).toBeVisible();
    await expect(dialogo.getByLabel("Cocheras")).toBeVisible();
    await expect(dialogo.getByLabel("Expensas (ARS por mes)")).toBeVisible();
    await expect(dialogo.getByLabel("Ambientes")).not.toHaveValue("");
    await dialogo.getByRole("button", { name: "Cancelar" }).click();
    await expect(dialogo).toBeHidden();
  });
});

test.describe("/informes con filtro por estado", () => {
  test("el filtro es un link y la API lo aplica", async ({ page }) => {
    await page.goto("/informes");
    await page.getByRole("link", { name: "Sin datos", exact: true }).click();
    await expect(page).toHaveURL(/estado=INSUFFICIENT_DATA/);
    const filas = page.locator("table tbody tr");
    const n = await filas.count();
    for (let i = 0; i < n; i++) await expect(filas.nth(i)).toContainText("SIN DATOS");
    if (n === 0) await expect(page.getByText("Ningún informe con ese estado")).toBeVisible();
  });
});

test.describe("navegación", () => {
  test("la barra lateral agrupa y marca la pantalla activa", async ({ page }, info) => {
    test.skip(info.project.name === "celular", "en el celular la barra vive detrás del botón Menú");
    await page.goto("/comparables");
    const nav = page.getByRole("complementary", { name: "Navegación" });
    await expect(nav.getByText("Trabajo")).toBeVisible();
    await expect(nav.getByText("Corpus")).toBeVisible();
    const activo = nav.locator("a[aria-current=page]");
    await expect(activo).toHaveCount(1);
    await expect(activo).toHaveText(/Comparables/);
  });

  test("en el celular la navegación se abre con el botón Menú", async ({ page }, info) => {
    test.skip(info.project.name !== "celular", "solo aplica al viewport chico");
    await page.goto("/informes");
    const link = page.getByRole("link", { name: "Calidad del motor" });
    await expect(link).toBeHidden();
    await page.getByRole("button", { name: "Menú" }).click();
    await expect(link).toBeVisible();
  });
});

test.describe("/cuenta", () => {
  test("dice quién soy y dónde se cambia la contraseña", async ({ page }) => {
    await page.goto("/cuenta");
    await expect(page.getByRole("heading", { name: "Mi cuenta" })).toBeVisible();
    await expect(page.getByText(/^Organización:/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Cambiar la contraseña" })).toBeVisible();
    // Con el modo desarrollo (sin usuario) no hay contraseña que cambiar, y
    // la pantalla lo dice en vez de mostrar un formulario que va a dar 403.
    // Con sesión real, está el formulario.
    const cuerpo = await page.locator("body").innerText();
    expect(cuerpo).toMatch(/Contraseña actual|En el modo desarrollo no hay contraseña/);
  });
});

test.describe("/admin/usuarios: rol y confirmación", () => {
  test("el rol se cambia desde la fila", async ({ page }) => {
    // doc 20 §1.1: `role` existía en la API y no en la pantalla.
    await page.goto("/admin/usuarios");
    const selector = page.getByLabel(/^Rol de /).first();
    await expect(selector).toBeAttached();
    expect(await selector.locator("option").count()).toBe(4);
  });

  test("desactivar pide confirmación propia, no window.confirm", async ({ page }) => {
    await page.goto("/admin/usuarios");
    let nativo = false;
    page.on("dialog", (d) => {
      nativo = true;
      void d.dismiss();
    });
    const boton = page.getByRole("button", { name: "Desactivar" }).filter({ hasNot: page.locator("[disabled]") });
    const habilitados = await boton.evaluateAll((els) => els.filter((e) => !(e as HTMLButtonElement).disabled).length);
    if (habilitados === 0) test.skip(true, "ningún otro usuario activo para desactivar");
    await boton.first().click();
    const dialogo = page.getByRole("dialog", { name: /Desactivar a / });
    await expect(dialogo).toBeVisible();
    await dialogo.getByRole("button", { name: "Cancelar" }).click();
    await expect(dialogo).toBeHidden();
    expect(nativo).toBe(false);
  });
});

test.describe("disciplina visual (taste-skill)", () => {
  test("ninguna pantalla usa rayas largas ni emoji como ícono", async ({ page }) => {
    for (const ruta of ["/informes", "/informes/nuevo", "/comparables", "/calidad", "/admin/fuentes", "/admin/usuarios", "/admin/organizacion", "/cuenta"]) {
      await page.goto(ruta);
      const texto = await page.locator("body").innerText();
      expect(texto, ruta).not.toMatch(/—/);
      expect(texto, ruta).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u);
    }
  });
});
