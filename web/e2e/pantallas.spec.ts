import { expect, test } from "@playwright/test";

/**
 * Las pantallas de doc 07, contra el stack real.
 *
 * Cada test verifica una decisión de producto, no que el HTML tenga un div.
 * Los `expect` están escritos contra lo que doc 07 dice que el usuario tiene
 * que poder hacer o ver.
 */

test.describe("/informes — el listado", () => {
  test("muestra la dirección y el barrio, no una columna de UUIDs", async ({ page }) => {
    await page.goto("/informes");
    await expect(page.getByRole("heading", { name: "Informes" })).toBeVisible();

    const primera = page.locator("table tbody tr").first();
    await expect(primera).toBeVisible();
    // La dirección linkea a la ficha: es la forma en que el agente reconoce
    // sus informes (doc 07 §3).
    const link = primera.getByRole("link").first();
    await expect(link).toHaveAttribute("href", /\/informes\/[0-9a-f-]{36}/);
    await expect(link).not.toHaveText(/^[0-9a-f-]{36}$/);
  });

  test("todo valor viene con su nivel de confianza al lado", async ({ page }) => {
    // doc 07 §12: "ningún número aparece sin su nivel de confianza al lado".
    await page.goto("/informes");
    const filas = page.locator("table tbody tr");
    const n = await filas.count();
    expect(n).toBeGreaterThan(0);

    for (let i = 0; i < Math.min(n, 5); i++) {
      const fila = filas.nth(i);
      const valor = await fila.locator("td").nth(3).innerText();
      if (!valor.includes("USD")) continue;
      const confianza = await fila.locator("td").nth(5).innerText();
      expect(confianza).toMatch(/ALTA|MEDIA|BAJA/);
    }
  });

  test("INSUFFICIENT_DATA se muestra en ámbar y NO como error", async ({ page }) => {
    await page.goto("/informes");
    const sinDatos = page.getByText("SIN DATOS").first();
    if ((await sinDatos.count()) === 0) test.skip(true, "no hay informes sin datos en la base");
    // El texto no dice "ERROR" ni "FALLÓ": el sistema funcionó (doc 07 §3).
    await expect(sinDatos).toBeVisible();
    const color = await sinDatos.evaluate((el) => getComputedStyle(el).color);
    expect(color).not.toBe("rgb(255, 0, 0)");
  });

  test("la tabla no rompe el layout en un celular", async ({ page }, info) => {
    test.skip(info.project.name !== "celular", "solo aplica al viewport chico");
    await page.goto("/informes");
    // doc 07 §12, mobile first: el agente genera el informe desde el auto. La
    // tabla puede scrollear adentro de su contenedor, pero la PÁGINA no.
    const anchoBody = await page.evaluate(() => document.body.scrollWidth);
    const anchoViewport = page.viewportSize()!.width;
    expect(anchoBody).toBeLessThanOrEqual(anchoViewport + 1);
  });
});

test.describe("/informes/nuevo — el alta", () => {
  test("con dirección y tipo alcanza para generar", async ({ page }) => {
    // El bloque 2 está colapsado a propósito: antes de la visita se sabe poco
    // (doc 07 §4).
    await page.goto("/informes/nuevo");
    await expect(page.getByLabel("Dirección *")).toBeVisible();
    await expect(page.getByRole("button", { name: "Generar informe" })).toBeEnabled();
    await expect(page.getByLabel("Dormitorios")).toHaveCount(0);
  });

  test("el bloque de más datos se despliega", async ({ page }) => {
    await page.goto("/informes/nuevo");
    await page.getByRole("button", { name: /Tengo más datos/ }).click();
    await expect(page.getByLabel("Dormitorios")).toBeVisible();
    await expect(page.getByLabel("Orientación")).toBeVisible();
  });

  test("cubierta mayor que total bloquea, y se dice por qué", async ({ page }) => {
    await page.goto("/informes/nuevo");
    await page.getByLabel("Dirección *").fill("Thames 1800");
    await page.getByLabel("Sup. total (m²)").fill("60");
    await page.getByLabel("Sup. cubierta (m²)").fill("90");

    // `getByRole("alert")` a secas engancha también el route announcer que
    // Next inyecta en el DOM (`#__next-route-announcer__`), así que se acota al
    // nuestro. Vale la pena dejarlo escrito: el error de Playwright ("strict
    // mode violation") suena a bug de la app y era del test.
    await expect(page.locator("p[role=alert]")).toContainText("no puede ser mayor");
    await expect(page.getByRole("button", { name: "Generar informe" })).toBeDisabled();
  });

  test("una superficie implausible ADVIERTE pero no bloquea", async ({ page }) => {
    // La diferencia con el test de arriba es la decisión de producto: un
    // monoambiente de 20 m² declarado como 2 ambientes puede ser real, y
    // bloquearlo sería decidir por quien sabe más que nosotros (doc 07 §4).
    await page.goto("/informes/nuevo");
    await page.getByLabel("Dirección *").fill("Thames 1800");
    await page.getByLabel("Ambientes").fill("4");
    await page.getByLabel("Sup. total (m²)").fill("30");

    await expect(page.getByText(/por ambiente es poco habitual/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Generar informe" })).toBeEnabled();
  });

  test("el microcopy dice que se puede regenerar después", async ({ page }) => {
    await page.goto("/informes/nuevo");
    await expect(page.getByText(/regenerar después de la visita/)).toBeVisible();
  });
});

test.describe("/informes/[id] — la ficha", () => {
  /**
   * Abre un informe TERMINADO, no "el primero".
   *
   * La primera versión de estos tests hacía click en la primera fila y se
   * salteaba si resultaba ser un `INSUFFICIENT_DATA`. Eso los volvía
   * dependientes del orden de los datos: el test del flujo completo crea un
   * informe sin comparables, y a partir de ahí los cinco tests de la ficha se
   * salteaban en silencio. Cinco tests verdes que no probaban nada.
   */
  async function abrirUnInformeTerminado(page: import("@playwright/test").Page) {
    await page.goto("/informes");
    const terminado = page
      .locator("table tbody tr")
      .filter({ hasText: "SUCCEEDED" })
      .first();
    if ((await terminado.count()) === 0) {
      test.skip(true, "no hay ningún informe SUCCEEDED en la base");
    }
    await terminado.getByRole("link").first().click();
    await page.waitForURL(/\/informes\/[0-9a-f-]{36}/);
  }

  test("el resultado muestra el rango de cierre ARRIBA, no escondido", async ({ page }) => {
    await abrirUnInformeTerminado(page);
    // doc 07 §6.1: es el dato que evita la conversación incómoda tres meses
    // después, así que no puede estar al pie.
    await expect(page.getByText(/Rango esperado de cierre/)).toBeVisible();
    await expect(page.getByText(/Confianza/)).toBeVisible();
  });

  test("el recuadro de limitaciones está y NO está colapsado", async ({ page }) => {
    await abrirUnInformeTerminado(page);
    // doc 07 §6.6 + doc 05 §9: siempre visible, nunca colapsado. La leyenda de
    // no-tasación-legal tiene que viajar con el número.
    await expect(page.getByText(/no constituye una tasación con validez legal/)).toBeVisible();
  });

  test("«Preguntale al informe» responde con citas o rechaza, nunca inventa", async ({ page }) => {
    // doc 18 §5 / ADR-013. Una pregunta sobre la metodología tiene que volver con
    // al menos una cita; una que no se puede responder con el informe tiene que
    // volver RECHAZADA. En los dos casos la caja lo dice en pantalla. La primera
    // pregunta a un informe construye el índice (hasta ~2 min en frío).
    test.setTimeout(240_000);
    await abrirUnInformeTerminado(page);
    const caja = page.getByRole("region", { name: /Preguntale al informe/ });
    await expect(caja).toBeVisible();

    await caja.getByRole("button", { name: /mediana y no el promedio/ }).click();
    const respuesta = caja.locator(".tarjeta").first();
    await expect(respuesta).toBeVisible({ timeout: 200_000 });
    await expect(respuesta.getByText(/Verificada: cada cifra existe en las citas/)).toBeVisible();
    await expect(respuesta.getByText(/\d+ citas?/)).toBeVisible();

    await caja.getByLabel("Pregunta").fill("¿Cuánto va a valer la propiedad dentro de dos años?");
    await caja.getByRole("button", { name: "Preguntar" }).click();
    const rechazo = caja.locator(".tarjeta").first();
    await expect(rechazo.getByText(/Rechazada:/)).toBeVisible({ timeout: 60_000 });
  });

  test("el PDF se descarga de verdad", async ({ page }) => {
    await abrirUnInformeTerminado(page);
    const r = await page.request.get(page.url() + "/pdf");
    expect(r.status()).toBe(200);
    expect(r.headers()["content-type"]).toContain("application/pdf");
    // El SHA-256 viaja para que quien recibe el archivo pueda probar que es el
    // que se generó: un informe entregado en septiembre tiene que ser byte por
    // byte el mismo en diciembre.
    expect(r.headers()["x-content-sha256"]).toMatch(/^[0-9a-f]{64}$/);
  });

  test("un id inexistente da 404 y no una pantalla rota", async ({ page }) => {
    const r = await page.goto("/informes/00000000-0000-0000-0000-000000000000");
    expect(r?.status()).toBe(404);
  });
});

test.describe("transversales", () => {
  test("no hay errores de consola en ninguna pantalla", async ({ page }) => {
    const errores: string[] = [];
    page.on("console", (m) => m.type() === "error" && errores.push(m.text()));
    page.on("pageerror", (e) => errores.push(e.message));

    for (const ruta of ["/informes", "/informes/nuevo"]) {
      await page.goto(ruta);
      await page.waitForLoadState("networkidle");
    }
    expect(errores).toEqual([]);
  });

  test("la raíz redirige al listado", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/informes$/);
  });

  test("los campos del formulario tienen labels reales", async ({ page }) => {
    // doc 07 §12: accesibilidad — labels reales, no placeholders.
    await page.goto("/informes/nuevo");
    expect(await camposSinLabel(page)).toEqual([]);
  });
});

test.describe("el flujo completo de alta", () => {
  // Es el único test que ESCRIBE. Vale lo que cuesta: es el que integra
  // formulario -> POST -> redirect -> stepper -> estado final, que son
  // justamente las cuatro costuras donde ningún test unitario mira.
  test("una dirección sin comparables termina en la pantalla que EXPLICA", async ({ page }) => {
    test.setTimeout(180_000);

    await page.goto("/informes/nuevo");
    // Villa Riachuelo no tiene un solo aviso en el corpus: el informe tiene que
    // dar INSUFFICIENT_DATA, y eso NO es un error del sistema (doc 07 §7).
    await page.getByLabel("Dirección *").fill("Av. Coronel Roca 4400, Villa Riachuelo");
    await page.getByLabel("Ambientes").fill("2");
    await page.getByLabel("Sup. total (m²)").fill("55");
    await page.getByLabel("Sup. cubierta (m²)").fill("50");
    await page.getByRole("button", { name: "Generar informe" }).click();

    await page.waitForURL(/\/informes\/[0-9a-f-]{36}/, { timeout: 30_000 });

    // El stepper en castellano llano, no `extract_features` (doc 07 §5).
    const cuerpo = page.locator("body");
    if ((await cuerpo.innerText()).includes("Generando el informe")) {
      // `.first()`: el stepper lista LOS DIEZ pasos, así que el regex engancha
      // más de uno. Que enganche varios es la señal de que está bien.
      await expect(
        page.getByText(/Normalizando la dirección|Buscando comparables/).first(),
      ).toBeVisible();
    }

    await expect(page.getByText("No pudimos generar el informe")).toBeVisible({
      timeout: 150_000,
    });

    // Lo que separa un callejón sin salida de una tarea: la pantalla tiene que
    // decir QUÉ hacer, no solo que no se pudo.
    await expect(page.getByText(/Qué podés hacer/)).toBeVisible();
    const texto = await cuerpo.innerText();
    expect(texto).not.toMatch(/\bError\b|\bFalló\b|Internal Server/);
  });
});

test.describe("autenticación", () => {
  // Estos corren con el modo desarrollo APAGADO (`TASADOR_ORG_SLUG=`), que es
  // la única forma de probar lo que va a pasar en producción. Con el respaldo
  // encendido el middleware no rebota y el login nunca se ejercita: sería un
  // test verde sobre un camino que nadie recorre.
  test.skip(
    () => process.env.E2E_CON_AUTH !== "1",
    "requiere el stack con TASADOR_ORG_SLUG vacío (ver ESTADO §8)",
  );

  // Estos EMPIEZAN sin sesión: prueban el rebote al login. El resto de la
  // suite arranca logueada desde `e2e/.sesion.json`.
  test.use({ storageState: { cookies: [], origins: [] } });

  test("sin sesión, cualquier pantalla rebota al login", async ({ page }) => {
    await page.goto("/informes");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Entrar" })).toBeVisible();
  });

  test("el rebote se acuerda a dónde iba", async ({ page }) => {
    // Si alguien abre el link a un informe con la sesión vencida, después del
    // login tiene que aterrizar en ESE informe y no en el listado.
    await page.goto("/informes/00000000-0000-0000-0000-000000000000");
    await expect(page).toHaveURL(/volver=/);
  });

  test("credenciales inválidas: mensaje genérico, sin decir qué falló", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("nadie@inmo-demo.com.ar");
    await page.getByLabel("Contraseña").fill("no-es-la-clave");
    await page.getByRole("button", { name: "Entrar" }).click();

    const alerta = page.getByRole("alert").filter({ hasText: /Credenciales/ });
    await expect(alerta).toBeVisible();
    const texto = await alerta.innerText();
    // No puede decir "el email no existe" ni "la contraseña es incorrecta":
    // la diferencia es información gratis para quien está probando.
    expect(texto).not.toMatch(/no existe|incorrecta|usuario/i);
  });

  test("login correcto entra, y salir vuelve a echar", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(process.env.E2E_EMAIL!);
    await page.getByLabel("Contraseña").fill(process.env.E2E_PASSWORD!);
    await page.getByRole("button", { name: "Entrar" }).click();

    await expect(page).toHaveURL(/\/informes/);
    await expect(page.getByRole("heading", { name: "Informes" })).toBeVisible();

    await page.getByRole("button", { name: "Salir" }).click();
    await expect(page).toHaveURL(/\/login/);

    // Y la sesión murió del lado del servidor, no solo en el navegador.
    await page.goto("/informes");
    await expect(page).toHaveURL(/\/login/);
  });

  test("la cookie de sesión no es legible desde JavaScript", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(process.env.E2E_EMAIL!);
    await page.getByLabel("Contraseña").fill(process.env.E2E_PASSWORD!);
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(page).toHaveURL(/\/informes/);

    // HttpOnly: un XSS no puede robar la sesión (doc 06 §1).
    const visible = await page.evaluate(() => document.cookie);
    expect(visible).not.toContain("tasador_sesion");
  });
});

test.describe("/comparables — el explorador del corpus", () => {
  test("muestra el texto del aviso Y lo extraído, uno al lado del otro", async ({ page }) => {
    // doc 07 §8: "la forma más rápida de detectar que el extractor se está
    // equivocando". Si falta cualquiera de las dos mitades, la pantalla no
    // sirve para lo que existe.
    await page.goto("/comparables?barrio=Palermo");
    await expect(page.getByRole("heading", { name: "Comparables" })).toBeVisible();
    await expect(page.getByText("LO QUE DICE EL AVISO").first()).toBeVisible();
    await expect(page.getByText("LO QUE EXTRAJO EL SISTEMA").first()).toBeVisible();
  });

  test("dice cuántos coeficientes quedaron sin dato", async ({ page }) => {
    // "Sin dato, sin ajuste" (doc 05 §4.2). Es la explicación de por qué el
    // rango sale ancho, y sin esto el usuario solo ve el rango.
    await page.goto("/comparables?barrio=Palermo");
    await expect(page.getByText(/coeficientes sin dato/).first()).toBeVisible();
  });

  test("los filtros se combinan y son links (compartibles)", async ({ page }) => {
    await page.goto("/comparables");
    await page.getByRole("link", { name: "Palermo", exact: true }).click();
    await expect(page).toHaveURL(/barrio=Palermo/);
    await page.getByRole("link", { name: "Portal A" }).click();
    await expect(page).toHaveURL(/barrio=Palermo/);
    await expect(page).toHaveURL(/fuente=PORTAL_A/);
  });

  test("un aviso sin extraer se dice, no se esconde", async ({ page }) => {
    // El LEFT JOIN es deliberado: los avisos sin features son los que MÁS
    // interesan acá. Un INNER los escondería justo a ellos.
    await page.goto("/comparables?sin_extraer=true");
    const cuerpo = await page.locator("body").innerText();
    expect(cuerpo).toMatch(/Todavía sin extraer|Ningún aviso con esos filtros/);
  });

  test("el botón de reportar existe donde hay extracción", async ({ page }) => {
    await page.goto("/comparables?barrio=Belgrano");
    const cuerpo = await page.locator("body").innerText();
    if (!cuerpo.includes("LO QUE EXTRAJO")) test.skip(true, "sin avisos de Belgrano");
    const boton = page.getByRole("button", { name: /Reportar extracción|marcado para revisar/ });
    if ((await boton.count()) === 0) test.skip(true, "ningún aviso extraído en esta página");
    await expect(boton.first()).toBeVisible();
  });
});

test.describe("/calidad — backtest y métricas", () => {
  test("la comparación contra el baseline está ARRIBA", async ({ page }) => {
    // doc 07 §9: si el sistema no le gana al baseline, se tiene que ver
    // inmediatamente — por eso van uno al lado del otro.
    await page.goto("/calidad");
    await expect(page.getByRole("heading", { name: "Calidad del motor" })).toBeVisible();
    await expect(page.getByText("Sistema").first()).toBeVisible();
    await expect(page.getByText(/Baseline/).first()).toBeVisible();
    await expect(page.getByText(/mejor que el baseline|NO le gana/).first()).toBeVisible();
  });

  test("los evals de componente se leen por la mediana, y lo dice", async ({ page }) => {
    // La amplitud medida es 17,6 pp con el mismo código: una corrida sola es
    // una muestra. La pantalla tiene que enseñar a leerla, no solo mostrarla.
    await page.goto("/calidad");
    await expect(page.getByText(/mediana de las últimas 5/)).toBeVisible();
    await expect(page.getByText(/Amplitud/)).toBeVisible();
  });
});

test.describe("/admin/fuentes — salud de los datos", () => {
  test("una tarjeta por fuente con la métrica de bloqueos", async ({ page }) => {
    // `blocked` es la alarma temprana de que un portal cambió su defensa
    // (doc 07 §10).
    await page.goto("/admin/fuentes");
    await expect(page.getByRole("heading", { name: "Fuentes de datos" })).toBeVisible();
    await expect(page.getByText("PORTAL_A")).toBeVisible();
    await expect(page.getByText(/Bloqueados/).first()).toBeVisible();
  });

  test("el chequeo de sesgo muestra el corpus Y el oficial, con su fecha", async ({ page }) => {
    // Mostrar el dato oficial AL LADO del propio es deliberado: si divergen,
    // el usuario lo ve y pregunta (doc 07 §6.4 aplica acá también).
    await page.goto("/admin/fuentes");
    await expect(page.getByText(/USD\/m² corpus/i)).toBeVisible();
    await expect(page.getByText(/oficial \(2019\)/i).first()).toBeVisible();
  });
});

test.describe("/admin/organizacion — claves y cuota", () => {
  test("muestra la cuota del mes y las API keys por prefijo, nunca en claro", async ({ page }) => {
    await page.goto("/admin/organizacion");
    await expect(page.getByText(/Informes del mes/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "API keys" })).toBeVisible();
    // En el listado solo puede aparecer el prefijo: la clave completa se
    // muestra UNA vez al crearla y no se guarda.
    const cuerpo = await page.locator("body").innerText();
    const clavesCompletas = cuerpo.match(/tsk_live_[A-Za-z0-9_-]{20,}/g) ?? [];
    expect(clavesCompletas).toEqual([]);
  });

  test("el formulario de alta de clave está", async ({ page }) => {
    await page.goto("/admin/organizacion");
    await expect(page.getByPlaceholder(/Nombre/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Crear API key" })).toBeVisible();
  });
});

test.describe("/admin/usuarios — el alta", () => {
  test("lista los usuarios con su rol y estado", async ({ page }) => {
    await page.goto("/admin/usuarios");
    await expect(page.getByRole("heading", { name: "Usuarios" })).toBeVisible();
    await expect(page.locator("table tbody tr").first()).toBeVisible();
  });

  test("el formulario explica qué puede cada rol", async ({ page }) => {
    // Un selector que dice "agent" a secas obliga a adivinar; el que dice
    // "agent — genera informes" enseña el modelo de permisos en el lugar donde
    // se usa.
    await page.goto("/admin/usuarios");
    await expect(page.getByText(/agent — genera informes/)).toBeAttached();
    await expect(page.getByText(/admin — administra el tenant/)).toBeAttached();
  });
});

test.describe("compartir con el propietario", () => {
  test("el link firmado abre el informe SIN sesión, y sin la cocina", async ({ page, browser }) => {
    // El circuito entero: botón -> link firmado -> pantalla pública. Es la
    // función completa; probar solo el botón sería probar la mitad.
    await page.goto("/informes");
    const terminado = page.locator("table tbody tr").filter({ hasText: "SUCCEEDED" }).first();
    if ((await terminado.count()) === 0) test.skip(true, "no hay informes SUCCEEDED");
    await terminado.getByRole("link").first().click();
    await page.waitForURL(/\/informes\/[0-9a-f-]{36}/);

    await page.getByRole("button", { name: "Compartir" }).click();
    const link = page.locator("code", { hasText: /\/compartido\// }).first();
    await expect(link).toBeVisible();
    const url = (await link.innerText()).trim();

    // Un contexto NUEVO, sin cookies: exactamente lo que tiene el propietario.
    const propietario = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const pagina = await propietario.newPage();
    await pagina.goto(url);
    await expect(pagina.getByText(/Rango esperado de cierre/)).toBeVisible();
    await expect(pagina.getByText(/no constituye una tasación/)).toBeVisible();
    // Sin la cocina: ni costos ni navegación de la app.
    const texto = await pagina.locator("body").innerText();
    expect(texto).not.toMatch(/USD 0,0|prompts |Comparables\s*Calidad/);
    await propietario.close();
  });
});

test.describe("regenerar", () => {
  test("el botón está en la ficha de un informe terminado", async ({ page }) => {
    // Solo se verifica la presencia: el click encola una corrida completa del
    // pipeline (gasta y tarda minutos), y el endpoint ya tiene test de API.
    await page.goto("/informes");
    const terminado = page.locator("table tbody tr").filter({ hasText: "SUCCEEDED" }).first();
    if ((await terminado.count()) === 0) test.skip(true, "no hay informes SUCCEEDED");
    await terminado.getByRole("link").first().click();
    await expect(page.getByRole("button", { name: "Regenerar" })).toBeVisible();
  });
});

test.describe("cuando algo falla — H-37", () => {
  /**
   * Estas tres pantallas no existían. Lo que el usuario veía cuando la API
   * estaba caída, o cuando escribía mal una URL, era el default de Next: en
   * producción un «Application error: a server-side exception has occurred»
   * con un digest hexadecimal, en inglés, sin cabecera y sin reintentar.
   *
   * Doc 07 pone mucho cuidado en que un `INSUFFICIENT_DATA` se lea como una
   * respuesta y no como un error. Esto es el mismo cuidado para cuando sí lo es.
   */

  test("un id que no es un UUID es un 404, no un 500", async ({ page }) => {
    // La API devuelve 422 para un id malformado y la ficha solo contemplaba el
    // 404, así que el 422 caía al `throw e`. Un id que no es un UUID no existe,
    // por definición.
    const r = await page.goto("/informes/no-es-uuid");
    expect(r?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "No encontramos eso" })).toBeVisible();
  });

  test("un informe inexistente muestra la pantalla del producto", async ({ page }) => {
    const r = await page.goto("/informes/00000000-0000-0000-0000-000000000000");
    expect(r?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "No encontramos eso" })).toBeVisible();
    // Con la navegación puesta: el 404 genérico de Next deja al usuario sin
    // salida más que el botón "atrás".
    await expect(page.getByRole("link", { name: "Ver los informes" })).toBeVisible();
  });

  test("una ruta que no existe también", async ({ page }) => {
    const r = await page.goto("/esta-ruta-no-existe");
    expect(r?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "No encontramos eso" })).toBeVisible();
  });

  test("con la API caída se ve el mensaje propio y un botón para reintentar", async ({
    page,
  }) => {
    // El fetch lo hace el SERVIDOR de Next, no el navegador: `page.route()` no
    // lo intercepta y no hay forma de tirar la API desde acá. Este test se
    // salta si no puede provocar el error, y el motivo dice qué falta —
    // un test que verifica que el archivo existe no mide la pantalla.
    //
    // Medido a mano el 15/08 con `docker compose stop api`:
    //   /informes -> 500 con "No pudimos cargar esta pantalla" y el botón.
    // Automatizarlo necesita que la suite pueda apagar un contenedor, que es
    // deuda anotada: hoy los e2e tampoco corren en el CI (ver H-40).
    test.skip(
      !process.env.E2E_PUEDE_TIRAR_LA_API,
      "necesita poder apagar el contenedor de la API: correr con " +
        "E2E_PUEDE_TIRAR_LA_API=1 y la API abajo",
    );
    const r = await page.goto("/informes");
    expect(r?.status()).toBe(500);
    await expect(
      page.getByRole("heading", { name: "No pudimos cargar esta pantalla" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();
  });
});

// ── Accesibilidad, en TODAS las pantallas — H-38, H-39 ───────────────────
//
// El test de labels miraba `/informes/nuevo`, la única de siete que ya los
// tenía. En verde decía algo que no había medido: `/admin/usuarios` tenía tres
// campos identificados solo por `placeholder` y `/admin/organizacion` uno.
//
// Un gate que cubre la pantalla que ya cumple no es un gate.

const RUTAS = [
  "/informes",
  "/informes/nuevo",
  "/comparables",
  "/calidad",
  "/admin/fuentes",
  "/admin/usuarios",
  "/admin/organizacion",
];

async function camposSinLabel(page: import("@playwright/test").Page): Promise<string[]> {
  return page.evaluate(() => {
    const malos: string[] = [];
    for (const el of document.querySelectorAll("input, select, textarea")) {
      if ((el as HTMLInputElement).type === "hidden") continue;
      const id = el.getAttribute("id");
      const tieneLabel = id && document.querySelector(`label[for="${CSS.escape(id)}"]`);
      // `aria-label` también identifica, pero no amplía el área clickeable:
      // se acepta solo si no hay otra forma.
      if (!tieneLabel && !el.getAttribute("aria-label") && !el.closest("label")) {
        malos.push(el.outerHTML.slice(0, 90));
      }
    }
    return malos;
  });
}

test.describe("accesibilidad — doc 07 §12", () => {
  for (const ruta of RUTAS) {
    test(`${ruta}: todo campo tiene label`, async ({ page }) => {
      await page.goto(ruta);
      expect(await camposSinLabel(page)).toEqual([]);
    });
  }

  for (const ruta of RUTAS) {
    test(`${ruta}: todo encabezado de tabla tiene scope`, async ({ page }) => {
      // Sin `scope`, un lector de pantalla no puede decir de qué columna es
      // cada celda: la tabla de comparables se lee como una lista de números.
      await page.goto(ruta);
      const sinScope = await page.evaluate(() =>
        [...document.querySelectorAll("th")]
          .filter((t) => !t.getAttribute("scope"))
          .map((t) => t.textContent?.trim() ?? ""),
      );
      expect(sinScope).toEqual([]);
    });
  }

  // ⚠️ En los DOS esquemas de color, y no es un extra.
  //
  // El 2,51:1 que encontró la auditoría era el de modo OSCURO: ahí `--acento`
  // es un celeste claro y el botón llevaba texto blanco encima. En modo claro
  // `--acento` es #1f5f9e y da 6,59:1. Un test que corre solo en el default de
  // Playwright —claro— pasaba en verde sobre el único esquema que no fallaba.
  for (const esquema of ["light", "dark"] as const) {
    test(`ningún texto queda por debajo del contraste AA (${esquema})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: esquema });
    await page.goto("/informes");
    const fallan = await page.evaluate(() => {
      const lum = (c: string) => {
        const n = (c.match(/\d+/g) ?? ["0", "0", "0"]).slice(0, 3).map((x) => {
          const v = Number(x) / 255;
          return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * (n[0] ?? 0) + 0.7152 * (n[1] ?? 0) + 0.0722 * (n[2] ?? 0);
      };
      const fondoDe = (el: Element): string => {
        for (let n: Element | null = el; n; n = n.parentElement) {
          const bg = getComputedStyle(n).backgroundColor;
          if (bg && !bg.startsWith("rgba(0, 0, 0, 0)")) return bg;
        }
        return "rgb(255,255,255)";
      };
      const malos: object[] = [];
      for (const el of document.querySelectorAll("body *")) {
        const texto = [...el.childNodes]
          .filter((n) => n.nodeType === 3)
          .map((n) => n.textContent?.trim())
          .join("");
        if (!texto) continue;
        const cs = getComputedStyle(el);
        const px = parseFloat(cs.fontSize);
        const grande = px >= 24 || (px >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
        const a = lum(cs.color);
        const b = lum(fondoDe(el));
        const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        const requiere = grande ? 3 : 4.5;
        if (ratio < requiere) {
          malos.push({ texto: texto.slice(0, 30), ratio: +ratio.toFixed(2), requiere });
        }
      }
      return malos;
    });
    expect(fallan).toEqual([]);
    });
  }
});
