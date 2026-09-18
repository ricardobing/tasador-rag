# S6 — Frontend

**Alcance:** `web/src/` (10 rutas), `web/e2e/`, contra `docs/07-pantallas.md`.
**Método:** correr los controles que el CI no corre, y medir las pantallas en un
navegador real contra el stack que está arriba.

---

## 1. Lo que corrí, y qué dio

```
$ cd web && npx tsc --noEmit
  tsc: OK (sin errores)

$ npx playwright test --reporter=line
  11 skipped
  63 passed (4.5m)

$ npx next lint
  ? How would you like to configure ESLint?  <- pregunta interactiva
```

`tsc --strict` está limpio y Playwright está verde. Lo que sigue son cinco cosas
que ninguno de los dos mira.

---

### H-36 · No hay ESLint: el comando documentado abre un asistente interactivo

**Sección:** S6 · **Severidad:** baja

**Qué está mal:** `web/package.json` declara `"lint": "next lint"` y **no hay
ninguna configuración de ESLint ni la dependencia instalada**. Correrlo abre el
wizard de Next.

**Cómo lo verifiqué:**

```
$ cd web && npx --no-install next lint
? How would you like to configure ESLint? https://nextjs.org/docs/app/api-reference/config/eslint
❯  Strict (recommended)
   Base
   Cancel
```

```
$ cat web/package.json | grep -E "eslint|prettier|biome"
(sin resultados)
```
No hay `.eslintrc*` ni `eslint.config.*` en `web/` (los que aparecen en un
`Get-ChildItem -Recurse` están dentro de `node_modules`).

**Por qué importa:** en un pipeline, un comando interactivo **cuelga hasta el
timeout** o falla con un error que no dice nada. Y es un comando documentado en
`package.json`, que es donde alguien lo va a buscar. Es de la misma familia que
los siete comandos que encontró `tests/architecture/test_entrypoints.py` — con la
diferencia de que ese test solo mira `Makefile`, `make.ps1`, `ci.yml` y
`ESTADO.md`, no `web/package.json`.

**Qué hay que hacer:** `npm i -D eslint eslint-config-next` y un
`eslint.config.mjs` con `next/core-web-vitals`. Y agregar `web/package.json` a
las `FUENTES` de `test_entrypoints.py`, con un chequeo de que cada script
declarado no sea interactivo.

**Cómo se verifica que quedó bien:** `npm run lint` en CI (no-tty) termina con
código 0 o 1, nunca colgado.

**Riesgo de tocarlo:** ESLint recién configurado va a encontrar cosas. Arrancar
con `--max-warnings=<lo que haya>` y bajarlo.

---

### H-37 · Ninguna pantalla tiene `error.tsx`, `loading.tsx` ni `not-found.tsx`

**Sección:** S6 · **Severidad:** media

**Qué está mal:** `web/src/` no tiene **ninguno** de los archivos con los que
Next maneja error, carga y 404. Seis páginas hacen `throw e` cuando la API
falla; ese throw llega al boundary por defecto de Next.

**Cómo lo verifiqué:**

```
$ ls web/src/**/{error,loading,not-found,global-error}.tsx
(vacío: no hay ninguno)

$ rg -n "throw e" web/src
web/src/app/admin/fuentes/page.tsx:36
web/src/app/calidad/page.tsx:31
web/src/app/admin/organizacion/page.tsx:22
web/src/app/admin/usuarios/page.tsx:34
web/src/app/informes/[id]/page.tsx:25
```

Y el efecto, medido en el navegador contra el stack real:

```js
fetch('/informes/no-es-uuid')  -> status 500
fetch('/informes/00000000-0000-0000-0000-000000000000') -> status 404
```

El 500 viene de un caso concreto: la API devuelve **422** para un UUID
malformado, `api.ts` lanza `ErrorDeApi(422)`, y `informes/[id]/page.tsx:24`
**solo contempla el 404** (`if (e.status === 404) notFound()`), así que el 422
cae al `throw e`.

**Por qué importa:** lo que el usuario ve cuando la API está caída, o cuando
escribe mal una URL, es la pantalla por defecto de Next —en producción, un
*«Application error: a server-side exception has occurred»* con un digest— sin
marca, sin explicación y sin un botón para reintentar. Doc 07 pone mucho cuidado
en que el `INSUFFICIENT_DATA` se lea como una respuesta y no como un error; el
caso de "la API no responde" no tiene ese cuidado.

El `notFound()` tampoco tiene pantalla propia: renderiza el 404 genérico de Next.

**Qué hay que hacer:**
1. `web/src/app/error.tsx` con el mensaje del producto y un botón `reset()`.
2. `web/src/app/not-found.tsx` con la navegación puesta.
3. `web/src/app/informes/[id]/loading.tsx` (y en las pantallas de admin, que
   hacen fetch server-side).
4. En `informes/[id]/page.tsx:24`, tratar 404 **y** 422 como `notFound()`: un id
   que no es un UUID no existe, por definición.

**Cómo se verifica que quedó bien:**

```
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3300/informes/no-es-uuid
# 404
```
Y un test de Playwright que apague el acceso a la API (`route.abort()` sobre
`**/api/**`) y verifique que la pantalla muestra el mensaje propio.

**Riesgo de tocarlo:** ninguno. Son archivos nuevos.

---

### H-38 · El botón principal no llega a contraste AA

**Sección:** S6 · **Severidad:** baja

**Qué está mal:** doc 07 §12 pide contraste AA. El botón "Nuevo informe" —la
acción principal del producto— tiene texto blanco sobre `rgb(106,169,224)`:
**2,51:1**, contra el 4,5:1 que exige AA para texto normal.

**Cómo lo verifiqué:** cálculo de contraste WCAG sobre todas las combinaciones
de color de `/informes`, en el navegador:

```json
{"total_combinaciones": 14,
 "fallan": [{"ejemplo": "Nuevo informe", "color": "rgb(255,255,255)",
             "fondo": "rgb(106,169,224)", "px": 16,
             "ratio": 2.51, "requiere": 4.5}]}
```

Las otras 13 combinaciones pasan.

**Por qué importa:** es una sola regla de CSS y es el botón que más se usa. Con
2,51:1 el texto se pierde en una pantalla al sol o para cualquiera con visión
reducida.

**Qué hay que hacer:** oscurecer `--azul` (o el token que use ese botón) en
`web/src/app/globals.css` hasta ≥4,5:1 contra blanco — alrededor de
`rgb(43,109,168)` da 5,0:1. O poner el texto en un color oscuro.

**Cómo se verifica que quedó bien:** volver a correr la sonda de contraste:
`fallan` tiene que quedar vacío. Vale dejarla como test de Playwright.

**Riesgo de tocarlo:** ninguno, es color.

---

### H-39 · Tres formularios de admin usan `placeholder` como si fuera label

**Sección:** S6 · **Severidad:** baja

**Qué está mal:** doc 07 §12 pide "labels reales". `/admin/usuarios` tiene tres
campos y `/admin/organizacion` uno, todos identificados solo por `placeholder`.

**Cómo lo verifiqué:** recorriendo el DOM de las siete rutas:

```json
"/admin/usuarios": [
  {"tag":"INPUT","type":"email","placeholder":"email@inmobiliaria.com.ar","tiene_label":false},
  {"tag":"INPUT","placeholder":"Nombre completo (opcional)","tiene_label":false},
  {"tag":"SELECT","aria":"Rol","tiene_label":false}
]
"/admin/organizacion": [
  {"tag":"INPUT","placeholder":"Nombre (ej: panel de la inmobiliaria)","tiene_label":false}
]
```

`/informes/nuevo` tiene sus 5 campos **con label**, y es la única pantalla que
el test de Playwright mira:

```
pantallas.spec.ts:185  "los campos del formulario tienen labels reales"
    await page.goto("/informes/nuevo")     <- solo esta
```

**Por qué importa:** el placeholder desaparece al escribir, no lo anuncian todos
los lectores de pantalla y no amplía el área clickeable. Y sobre todo: **el test
que lleva el nombre de esta regla verifica una pantalla de siete**, así que en
verde dice algo que no midió (R3).

Además, los 51 `<th>` de las cinco pantallas con tabla no tienen `scope`.

**Qué hay que hacer:** `<label htmlFor>` en los cuatro campos, y `scope="col"` en
los `<th>`. Y cambiar el test de Playwright para que recorra **todas** las rutas
con formulario, no una.

**Cómo se verifica que quedó bien:** la sonda del DOM tiene que devolver
`sin_label: []` en las siete rutas.

**Riesgo de tocarlo:** ninguno.

---

### H-40 · Los tests de Playwright se saltean solos cuando la base no tiene datos

**Sección:** S6 · **Severidad:** media

**Qué está mal:** siete tests de `pantallas.spec.ts` deciden **en tiempo de
ejecución** saltearse si no encuentran el dato que iban a verificar. Sobre una
base sin informes —un runner limpio, por ejemplo— la suite sale verde sin haber
probado nada de lo que dice probar.

**Cómo lo verifiqué:**

```
$ rg -n "test.skip" web/e2e/pantallas.spec.ts
 44:  if ((await sinDatos.count()) === 0) test.skip(true, "no hay informes sin datos en la base");
 52:  test.skip(info.project.name !== "celular", ...);            <- este está bien
129:  if ((await terminado.count()) === 0) test.skip(true, "no hay ningún informe SUCCEEDED en la base");
247:  test.skip(() => process.env.E2E_CON_AUTH !== "1", ...);
351:  if (!cuerpo.includes("LO QUE EXTRAJO")) test.skip(true, "sin avisos de Belgrano");
353:  if ((await boton.count()) === 0) test.skip(true, "ningún aviso extraído en esta página");
439:  if ((await terminado.count()) === 0) test.skip(true, "no hay informes SUCCEEDED");
467:  if ((await terminado.count()) === 0) test.skip(true, "no hay informes SUCCEEDED");
```

```
$ npx playwright test --reporter=line
  11 skipped
  63 passed (4.5m)
```

Los que se saltean hoy incluyen **todo el bloque de autenticación** (11 tests,
`E2E_CON_AUTH != 1`), que es el que ESTADO §8 llama *"el modo que vale"*.

**Por qué importa:** es exactamente el patrón que este proyecto ya identificó y
cerró del lado de pytest. `tests/conftest.py` dice, textual: *«Un `pytest.skip`
ante cualquier error convierte el gate en decorativo»*, y por eso la fixture `db`
**solo** se saltea si falta `DATABASE_URL` — nunca por el estado de la base. La
suite de Playwright hace lo contrario, y es la que va a correr en CI cuando
alguien la ponga.

**Qué hay que hacer:** dar vuelta el criterio.
1. Los tests que necesitan un informe SUCCEEDED deben **crear el dato** (o
   sembrarlo con un fixture) en vez de saltearse. Hay `scripts/seed.py`.
2. Si un dato no se puede sembrar, el test debe **FALLAR** con un mensaje claro
   ("esta suite necesita al menos un informe SUCCEEDED: correr `make seed`"), no
   saltearse.
3. Dejar como skip legítimo **solo** el condicionado por viewport (línea 52) y el
   de auth — pero con un paso explícito en CI que falle si `E2E_CON_AUTH` no está
   definida, igual que el paso "Que los tests de base NO se hayan salteado" que
   ya existe para pytest en `ci.yml:74`.

**Cómo se verifica que quedó bien:** con la base vacía, `npx playwright test`
tiene que ponerse **en rojo**, no verde con skips. Hoy sale verde.

**Riesgo de tocarlo:** los tests van a pasar a depender de un sembrado, que hay
que mantener. Es el costo correcto: un test que se saltea solo no protege nada.

---

## 2. Lo que verifiqué y está bien

- **El layout aguanta en celular.** A 375×812, ninguna de las siete rutas
  desborda horizontalmente. Las tablas anchas (la de `/calidad` mide 812 px)
  viven dentro de un contenedor con scroll propio, que es la solución correcta:

  ```json
  {"ruta":"/calidad","viewport":375,"scrollWidth":375,"desborda":false,
   "peor_elemento":"TABLE","hasta_px":812}
  ```
- **`lang="es-AR"`**, landmarks `main`/`nav`/`header` presentes, un solo `<h1>`
  por pantalla, ningún botón sin texto accesible.
- **Hay una regla `:focus` y ninguna `outline: none`**, así que el foco no se
  esconde.
- **La cookie de sesión no es legible desde JS** (test de Playwright, verde).
- **Las mutaciones pasan por route handlers de Next** (`web/src/lib/proxy.ts`):
  la cookie no sale del servidor. Verificado en el código y consistente con que
  no haya CORS abierto (S4).
- **`tsc --noEmit` limpio** con `strict` (según `tsconfig.json`).

Detalle menor, no lo cuento como hallazgo: los links de navegación miden 23 px de
alto en celular, un pixel por debajo del mínimo de 24×24 de WCAG 2.5.8.

## 3. Qué NO pude verificar

- **Capturas de pantalla.** El panel del navegador no estaba visible y las
  capturas dan timeout; medí por DOM y CSS computado, que para contraste,
  labels y overflow es más preciso que mirar, pero no reemplaza ver el diseño.
- **El flujo de alta completo desde el navegador** (`POST /v1/reports`): escribe
  y gasta. Playwright sí lo cubre en el test de la línea 206.
- **La pantalla en un teléfono real.** El emulador de 375 px no reproduce
  teclado virtual ni safe-areas.
- **`/compartido/[token]`**: necesita generar un token, que es una escritura.
