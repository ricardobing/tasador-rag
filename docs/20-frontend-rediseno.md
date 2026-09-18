# 20 · El frontend, ordenado: auditoría, sistema visual y plan

**19/09/2026.** El front (`web/`, Next.js 15) creció pantalla por pantalla al ritmo de la
API, con CSS plano y estilos en línea. Funciona, pero se ve como lo que es: nueve
pantallas de nueve días distintos, una barra con siete links sueltos, tablas que compiten
con el número, y acciones que están donde cayeron. Este documento hace tres cosas antes
de tocar código: **audita** qué puede hacer el sistema y qué de eso se puede hacer desde
el front, **define** el sistema visual (tokens, kit, patrones), y **planifica** la
implementación pantalla por pantalla.

Herencia: la disciplina de [taste-skill](https://github.com/Leonxlnx/taste-skill) (un
acento, un sistema de radios, cero rayas largas, estados completos, nada de emoji como
ícono, nada de tres tarjetas iguales) bajada a producto como lo hizo TomaNota en su
skill `tomanota-diseno`: sidebar con grupos, tabla que colapsa a tarjetas, campo con
etiqueta arriba y ayuda, acción primaria en el encabezado. Tasador no es una landing: es
un panel que un agente usa entre visitas, y un documento que un propietario lee una vez.

---

## 1. Auditoría: capacidad del sistema ↔ el front

Regla: **todo lo que el sistema puede hacer tiene su lugar en el front**, o está
declarado como "solo CLI" con el porqué. Se relevaron los 24 endpoints de `/v1`, los
scripts y la configuración.

### 1.1 Lo que ya tiene pantalla (y qué le falta)

| Capacidad | Endpoint | Pantalla hoy | Estado | Qué falta |
|---|---|---|---|---|
| Listar informes | `GET /reports` (cursor, `estado`) | `/informes` | ✅ | El filtro por estado existe en la API y no en la pantalla; no hay búsqueda por dirección |
| Crear informe | `POST /reports` | `/informes/nuevo` | ✅ | Estética; el bloque "más datos" sin cochera ni expensas (el modelo los tiene) |
| Ver informe | `GET /reports/{id}` (`incluir=descartados`) | `/informes/[id]` | ⚠️ | **No muestra la propiedad tasada** (la API no la devuelve: gap de backend), **no muestra la tabla de comparables** (la API sí la devuelve), orden invertido (número antes que propiedad) |
| PDF | `GET /reports/{id}/pdf` | botón | ✅ | — |
| Regenerar tras la visita | `POST /reports/{id}/regenerate` (`PropertyPatch`) | botón | ⚠️ | Manda `{}`: **no deja cargar lo que se aprendió en la visita**, que es la razón de existir del endpoint |
| Compartir con el propietario | `POST /reports/{id}/share` | botón | ✅ | El link se pierde al navegar; no se puede volver a ver |
| Preguntale al informe | `POST /reports/{id}/ask` | caja en la ficha | ✅ | Estética |
| Informe compartido | `GET /shared/{token}` (+pdf) | `/compartido/[token]` | ✅ | Mismo orden que la ficha: propiedad, valor, respaldo |
| Explorar el corpus | `GET /comparables` (filtros) | `/comparables` | ✅ | Filtros como chips sueltos; sin búsqueda visible (`q` existe en la API) |
| Marcar extracción incorrecta | `POST /comparables/{id}/revisar` | botón | ✅ | — |
| Calidad | `GET /calidad` | `/calidad` | ✅ | Estética; tablas anchas |
| Fuentes | `GET /admin/fuentes` | `/admin/fuentes` | ✅ | Estética; tarjetas iguales |
| Usuarios: listar, crear, activar/desactivar, **cambiar rol** | `GET/POST/PATCH /admin/usuarios` | `/admin/usuarios` | ⚠️ | El cambio de rol existe en la API (`role`) y no en la pantalla; `window.confirm` |
| Organización, cuota y API keys | `GET /admin/organizacion`, `POST/DELETE /admin/api-keys` | `/admin/organizacion` | ✅ | Estética; `window.confirm` |
| Sesión | `POST /auth/login`, `/auth/logout`, `GET /auth/me` | `/login`, botón Salir | ✅ | No hay "quién soy" visible (nombre, rol, organización) |
| Inventario del panel | `POST /inventory/snapshot` | — | ✅ solo API | Es para que otro sistema empuje; no tiene pantalla por diseño (doc 06) |

### 1.2 Lo que no tiene pantalla ni endpoint (gaps reales)

| Capacidad | Hoy | Decisión |
|---|---|---|
| **Ver la propiedad tasada en la ficha** | La API no la devuelve | **Backend:** `property` en `GET /reports/{id}` (los 13 campos del sujeto). **Front:** bloque «La propiedad» arriba |
| **Cambiar la propia contraseña** | Solo el admin crea usuarios con contraseña generada; no hay forma de cambiarla | **Backend:** `PATCH /auth/password` (actual + nueva). **Front:** `/cuenta` |
| Editar nombre de la organización, tono del informe | Config y script | Fuera de esta etapa: no lo pide nadie todavía; queda anotado |
| Borrar o archivar un informe | No existe | Fuera de esta etapa: un informe es un documento; se decide con el cliente si se archiva |
| Ingesta, extracción, backtests, evals | CLI | Solo CLI, a propósito (doc 07): son trabajos largos que merecen cola y notificación propias; las pantallas muestran el resultado |
| Captura on-demand (nodo 3) | Apagado | Cuando exista, el botón va en Fuentes |

### 1.3 Lo que se ve mal, con nombre

- Navegación: siete links en línea, sin agrupar, sin activo marcado, con "Nuevo" como
  si fuera una sección.
- Dos tipografías de facto (system + nada), sin escala; títulos que compiten con el
  número.
- Estilos en línea en 30 lugares; tres tonos de gris distintos; hexadecimales en `.tsx`.
- La tabla de comparables al tamaño del cuerpo, chips con borde del color del texto,
  botones todos iguales (primario para "Copiar" y para "Generar informe").
- `window.confirm` para desactivar usuarios y revocar claves.
- Estados: el vacío existe en dos pantallas; "cargando" es un `meta refresh`; el error
  es un recuadro ámbar reutilizado para todo.
- Emoji como ícono (`⚠`, `●`, `✓`, `◐`, `▾`).
- Rayas largas en texto visible ("Contraseña generada — pasásela…").

---

## 2. El sistema visual

### 2.1 La lectura del producto

**Dos superficies, un solo idioma.**

| Superficie | Ruta | Quién | Dónde | Densidad |
|---|---|---|---|---|
| El panel | `/informes`, `/comparables`, `/calidad`, `/admin/*`, `/cuenta` | El agente, el gerente, nosotros | Escritorio; el celular entre visitas | media-alta |
| El documento | `/informes/[id]` (la ficha) y `/compartido/[token]` | El agente y el propietario | Cualquiera | baja: se lee |

Misma marca, mismos tokens. La ficha y el compartido comparten el componente del
informe; lo que cambia es el envoltorio (con o sin navegación y acciones).

### 2.2 Tokens (`web/src/app/globals.css`)

Sin Tailwind: el proyecto no lo tiene y meterlo por esto es una dependencia de build
más. Los tokens son variables CSS y el kit son clases; ningún `.tsx` lleva un
hexadecimal ni un `style={{ color }}` que no sea un token.

**Neutros, papel frío** (misma escala que TomaNota: números y estados se leen neutros):

```
--gris-50  #f7f8fa   fondo de página        --gris-500 #6b7383  texto secundario (AA)
--gris-100 #eff1f5   zebra, hundido          --gris-600 #515869  etiqueta
--gris-200 #e3e6ec   borde por defecto       --gris-700 #3d4352  cuerpo
--gris-300 #cbd0da   borde de control        --gris-800 #262b38  títulos
--gris-400 #98a0b0   placeholder, ícono      --gris-900 #151922  números protagonistas
```

**Marca: azul tinta oscuro**, uno solo para identificar y accionar. No es el azul de
TomaNota (es otro producto) y no es verde: el verde ya significa «confianza ALTA» y
«usado», y la marca no puede pisar un semántico.

```
--marca-50  #eef2f8   --marca-100 #dbe4f1   --marca-200 #b9c9e4
--marca-600 #2b4c7e   la marca y la acción · 7,3:1 con blanco encima
--marca-700 #223d66   hover      --marca-800 #1b3050
```

**Semánticos: dicen un estado, nunca decoran.**

```
--exito   #1d7a4c / fondo #e6f4ec   ALTA, usado, activa, SUCCEEDED, OK
--alerta  #9a5b00 / fondo #fdf3e1   MEDIA, SIN DATOS, JUSTO, revisar, pendiente
--peligro #b3261e / fondo #fbe9e7   FAILED, error, revocar, desactivar
--neutro  #6b7383 / fondo #eff1f5   BAJA, descartado, revocada, desactivado
```

Modo oscuro: los mismos tokens redefinidos (el front ya lo tenía; se mantiene, con el
contraste verificado en el botón primario, H-38).

**Tipografía.** Display: Bricolage Grotesque (Google Fonts vía `next/font`, se baja en
el build, cero pedidos en runtime) para el título de pantalla, el valor sugerido y la
marca. Cuerpo: la pila del sistema. Números que se comparan en vertical: `.cifra`
(`tabular-nums`). Seis tamaños:

| Uso | Token |
|---|---|
| Título de pantalla | display 1,6rem 700, `letter-spacing -0.01em` |
| Título de sección | 1rem 600 gris-900 |
| Cuerpo | 0,92rem gris-700 |
| Secundario / ayuda | 0,8rem gris-500 |
| Etiqueta de campo | 0,85rem 500 gris-600 |
| Dato protagonista | display 2,1rem 700 `.cifra` |

**Forma.** Controles 10px, superficies 14px, pastillas redondas, chips 6px. Sin sombra
por defecto (superficie = borde gris-200 sobre gris-50); sombra tintada solo en lo que
flota (diálogo). Espaciado en múltiplos de 4.

### 2.3 El kit (`web/src/components/`)

| Familia | Piezas |
|---|---|
| Estructura | `AppShell` (sidebar con grupos en escritorio, barra superior con menú en celular), `PageHeader` (título, una frase, acción primaria), `Section`, `Card` |
| Datos | `DataTable` (cabecera pegajosa, zebra, colapsa a tarjetas en celular), `KeyValue`, `Stat`, `Badge` (estado con palabra, nunca color solo), `Cifra` |
| Formulario | `Field` (etiqueta arriba, ayuda, error abajo), `Input`/`Select`/`Textarea` (texto 1rem en celular), `Button` (primario, secundario, terciario, peligro), `FormActions` |
| Estado | `EmptyState` (qué falta, por qué importa, el botón que lo resuelve), `Notice` (info/alerta/error), `Skeleton`, `ConfirmDialog` (reemplaza `window.confirm`) |
| Íconos | `Icono` con un set propio inline (trazo 1.75, 20×20, `currentColor`): informes, nuevo, comparables, calidad, fuentes, usuarios, organización, cuenta, salir, pdf, compartir, regenerar, pregunta, tilde, alerta, reloj, flecha |

### 2.4 Patrones

- **Esqueleto de pantalla del panel:** `PageHeader` → (toolbar si hay > 1 filtro) →
  contenido. Ancho `max-width 72rem` para listas y tableros, `44rem` para formularios y
  para el documento.
- **Navegación con grupos:** *Trabajo* (Informes, Nuevo informe) · *Corpus*
  (Comparables, Fuentes) · *Calidad* (Calidad del motor) · *Administración* (Usuarios,
  Organización) · abajo, *Mi cuenta* y *Salir*. Activo con tres señales: fondo, peso y
  barra de 3px. Los grupos de administración solo para owner/admin (la API lo impone
  igual con 403).
- **Los cuatro estados en cada pantalla:** cargando (skeleton con la forma del
  resultado), vacío (con salida), error (mensaje propio, no el crudo), con datos.
- **Acción destructiva pide confirmación** con `ConfirmDialog` y nombra lo que va a
  hacer.
- **El documento (ficha y compartido), en este orden y con estos pesos:**
  1. **La propiedad tasada**: dirección + barrio grandes; ficha de 13 datos, «sin
     declarar» en gris para lo que falta.
  2. **El valor**: cifra protagonista con rango y cierre; a un costado confianza (chip
     + score), respaldo (usados de encontrados), USD/m².
  3. **La narrativa** (las secciones del redactor, con títulos de sección, no de
     pantalla), y las limitaciones siempre visibles.
  4. **Preguntale al informe** (solo en la ficha con sesión).
  5. **El respaldo**: la tabla de comparables **a 0,78rem**, numerada, dirección real,
     primero los usados y luego los descartados con su motivo; en celular, tarjetas.
  6. Trazabilidad al pie en 0,75rem.
  Es el mismo orden que la plantilla v2 del PDF: el documento en pantalla y en papel
  cuentan lo mismo en el mismo orden.

### 2.5 Escritura

Castellano rioplatense, voseo, minúscula de oración. Los botones dicen el verbo
("Generar informe", "Regenerar con estos datos", "Revocar clave"). Los errores dicen qué
pasó y qué hacer. Cero rayas largas en texto visible; cero emoji como ícono.

---

## 3. Plan de implementación

### Fase A · Backend (chico, con tests)

1. `GET /reports/{id}` devuelve `property` (address_raw, neighborhood, property_type,
   rooms, bedrooms, bathrooms, surface_total, surface_covered, age_years, floor_number,
   has_elevator, condition, orientation, parking_spaces, expenses_ars, notes).
   `GET /shared/{token}` ya devuelve parte; se completa igual. Doc 06 se actualiza.
2. `PATCH /auth/password` para la propia cuenta: contraseña actual + nueva (≥ 10),
   verificación con argon2, rate limit del login reutilizado. Test de contrato.

### Fase B · Sistema visual y kit

3. `globals.css`: tokens, tipografía (`next/font` Bricolage Grotesque), kit de clases,
   modo oscuro, `prefers-reduced-motion`.
4. `components/`: AppShell, PageHeader, Badge, Button, Card, DataTable, Field,
   EmptyState, Notice, ConfirmDialog, Icono, Skeleton.

### Fase C · Pantallas, en orden de uso

5. `/informes`: PageHeader con "Nuevo informe", filtro por estado, DataTable con
   colapso a tarjetas, estados vacío/error.
6. `/informes/nuevo`: formulario a 44rem en dos secciones (lo mínimo · lo que sé de la
   visita) con cochera y expensas; ayuda por campo; validaciones existentes.
7. `/informes/[id]`: el documento (§2.4) + acciones en el encabezado (PDF, Compartir,
   Regenerar con datos); el stepper como lista de pasos con tiempos; SIN DATOS y FAILED
   como `Notice` con salida; **formulario "Regenerar con lo que aprendí en la visita"**
   sobre `PropertyPatch`.
8. `/compartido/[token]`: el mismo documento sin navegación, con "Descargar PDF".
9. `/comparables`: toolbar con búsqueda (`q`) y filtros como segmentos; tarjeta aviso |
   extraído; "marcar para revisar" como botón terciario.
10. `/calidad`, `/admin/fuentes`: Stat arriba, tablas del kit, semánticos con palabra.
11. `/admin/usuarios`: tabla + alta en Card lateral; cambio de rol en línea;
    ConfirmDialog para desactivar.
12. `/admin/organizacion`: cuota como Stat, claves en tabla, ConfirmDialog para revocar,
    la clave nueva en un Notice con "Copiar".
13. `/cuenta`: quién soy, organización, rol; cambiar contraseña.
14. `/login`, `not-found`, `error`: mismo idioma.

### Fase D · Verificación

15. `npm run typecheck`, `npm run build`, Playwright completo contra el stack (los
    tests existentes se mantienen: los textos que buscan no cambian; se agregan tests
    para `property` en la ficha, el filtro de estado, el cambio de rol, `/cuenta` y la
    ausencia de `window.confirm`).
16. Lista de verificación de §2 (cero hexadecimales en `.tsx`, cero rayas largas
    visibles, cuatro estados, 44px táctil, AA, 360px).

---

## 4. Lo que se implementó (19/09/2026)

Las cuatro fases del plan están hechas. Lo que quedó distinto del plan, y por qué:

- **Nombres del kit en castellano**, como el resto del código: `PageHeader`, `Seccion`,
  `Chip`, `Stat`, `KeyValue`, `Aviso`, `Vacio`, `Esqueleto`, `BotonLink`, `Confirmar`,
  `Icono`, `AppShell`, `Salir` (`web/src/components/`). El documento (ficha y
  compartido) vive en `documento.tsx`: `FichaDePropiedad`, `BloqueDeValor`, `Narrativa`,
  `Limitaciones`, `Respaldo`, `Trazabilidad`. Los campos de la visita, que comparten el
  alta y «Regenerar», en `campos-propiedad.tsx`.
- **Tipografía self-hosted** (`web/src/fonts/`, Bricolage Grotesque, subset latino,
  créditos al lado): el build no depende de la red y no hay pedidos a terceros.
- **Salir** es un botón client que hace `DELETE /login/api` (la API borra la cookie);
  el plan lo tenía como formulario y eso no mataba la sesión del lado del servidor.
- **El texto de los roles** en el alta de usuarios pasó de «agent — genera informes» a
  «agent: genera informes» (cero rayas largas también en `<option>`); el test e2e se
  ajustó.
- **`/compartido`** no muestra la tabla de comparables: `GET /shared/{token}` no la
  devuelve, y el propietario la tiene en el PDF. Se agrega si el cliente la pide.
- **Regenerar** manda lo que el informe ya sabía más lo que se cambió: la API solo pisa
  lo que viene, así que desde el formulario no se puede «borrar» un dato. Es a
  propósito: un dato que se aprendió en la visita no se desaprende.

Verificación: `npm run typecheck` y `npm run build` en verde; Playwright completo
(`pantallas.spec.ts` + `rediseno.spec.ts`, escritorio y celular) contra el stack local.

Dos cosas del entorno que conviene saber para tocar el front:

1. **`.next` es compartido con el contenedor de desarrollo** (`./web:/app`). Un
   `npm run build` en el host mientras `next dev` corre en Docker pisa la carpeta y el
   build falla con `Cannot find module for page`. Parar `web`, compilar, borrar `.next`
   y volver a levantar. `make ci` no lo sufre porque construye la imagen.
2. **El hot reload sobre el bind mount de Windows no siempre dispara.** Si un cambio
   no aparece, `docker compose restart web`.
