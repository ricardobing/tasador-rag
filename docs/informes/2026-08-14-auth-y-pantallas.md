# Informe — Auth, pantallas y la varianza de los evals

**Fecha:** 14/08/2026 · **Continúa:** [la auditoría del mismo día](2026-08-14-auditoria.md)

---

## 1. Datos nuevos: no había

Se revisó el árbol completo del scraper, no solo `output/`:

```
17 archivos con extensión csv/json/jsonl
   3 nuestros    -> ya ingeridos (mismo sha256)
  14 ajenos      -> metadata.json de los perfiles de Chrome del scraper
```

Los CSV de Portal B tienen **517 bytes de puro encabezado** —cero filas— y
`debug/` está lleno de `blocked_CAPTCHA_*.png`. El corpus queda en 312 avisos.

El `.json` y el `.jsonl` tienen **las mismas 47 claves** que el CSV: no hay
datos que el CSV pierda, cosa que había que verificar antes de afirmarla.

### 1.1 Lo que sí cambió: el escáner

El scraper dejó de usar una sola carpeta y ahora crea una por corrida
(`output/`, `output_zp_t1/`). Con el glob plano anterior, **una tanda nueva en
una carpeta nueva no se ingería y el resumen decía "0 archivos nuevos"** — que
era cierto para la carpeta que estaba mirando, y engañoso.

Ahora es recursivo y acepta los tres formatos. Dos decisiones que costó tomar
bien:

**Los archivos se reconocen por CONTENIDO, no por la ruta.** El escaneo
recursivo empezó a encontrar los `metadata.json` de los perfiles de Chrome del
scraper, y cada uno producía un `IngestRun` con una fila en `PARSE_ERROR`.
Ruido permanente en `/admin/fuentes`, que es justo la pantalla que existe para
avisar cuando la ingesta se rompe. Un tablero con errores que no significan
nada es un tablero que nadie mira. El filtro pide `portal_property_id` + una
url: el scraper ya cambió su estructura de carpetas una vez y va a volver a
cambiarla, las claves del archivo no.

**Un archivo VACÍO no es lo mismo que uno ajeno.** Un CSV nuestro con cero
filas es una corrida que volvió vacía —por CAPTCHA— y ese es exactamente el
síntoma que hay que ver. Meterlo en la misma bolsa que un `metadata.json` de
Chrome lo esconde.

---

## 2. Autenticación

Era el bloqueante #1 y ya no está. El tenant salía del header `X-Org-Slug`, sin
secreto: **quien supiera el slug de otra inmobiliaria veía sus informes.**

Lo incómodo de decir es que eso volvía teatro al gate de aislamiento
multi-tenant que se había construido esa misma mañana: verifica que toda
consulta filtre por `org_id` —y lo hacen— sobre un `org_id` que elegía el
cliente.

### 2.1 Lo que hay

| Consumidor | Mecanismo |
|---|---|
| Integraciones | `Authorization: Bearer tsk_live_...`, hash argon2id |
| La UI | Cookie `HttpOnly; SameSite=Lax`, JWT de 12 h |

Verificado contra el contenedor:

```
API key válida    -> {"org_slug":"inmo-demo","via":"api_key"}
API key falsa     -> 401
login OK          -> {"email":"ricardo@...","role":"owner","via":"sesion"}
login mal         -> 401  {"detail":"No autorizado."}
/v1/reports       -> 200 con key, 200 con cookie
```

### 2.2 Cuatro decisiones

**Las API keys se buscan por PREFIJO.** Un hash argon2 lleva sal: dos hasheos
de la misma clave dan strings distintos, así que buscar por `key_hash` no puede
funcionar por más que la columna tenga un UNIQUE. El prefijo
(`tsk_live_2cz3NND_`) encuentra al candidato y lo que autentica es la
verificación argon2 del secreto completo.

**El usuario se relee de la base en cada request.** La sesión es un JWT y no se
revoca, pero desactivar a alguien corta su sesión **al instante**: hay un test
que lo prueba. Es lo que permite no montar un almacén de sesiones.

**Una credencial presentada y mala NO cae al siguiente mecanismo.** Si un
Bearer inválido cayera al header de desarrollo, presentar una credencial falsa
sería una forma de entrar.

**El header viejo está apagado en producción POR CÓDIGO**, no por
configuración. Sobrevive porque 279 tests y los scripts lo usan, y sacarlo de
golpe habría sido cambiar dos cosas a la vez. La diferencia importa: una
variable de entorno mal puesta en un deploy reabre el agujero; una condición
sobre `env == "production"` no. Hay un test que lo impone.

**El middleware de Next NO es la autorización.** Rebota al login por comodidad
y se saltea pegándole a la API directamente. Está escrito en el archivo para
que nadie confíe en él.

---

## 3. Las pantallas, probadas con Playwright

**44 tests** en escritorio y celular, contra el stack real y sin mockear la
API. La contrapartida se dice: necesitan el stack arriba y todavía no corren en
el CI.

La suite corre en dos modos, y el que vale es el segundo:

```
npm run test:e2e            modo desarrollo (X-Org-Slug), rápido de iterar
E2E_CON_AUTH=1 …            login real, sesión guardada, como en producción
```

Con el respaldo de desarrollo encendido el middleware no rebota y la cookie no
se usa: la suite estaría verde sobre un camino que en producción no existe. Es
la misma lección que la auditoría dejó tres veces.

Los tests verifican decisiones de doc 07, no que el HTML tenga un div: que
ningún valor aparezca sin su confianza al lado, que `INSUFFICIENT_DATA` no se
vea como error, que la tabla no rompa el layout en 375 px, que la superficie
implausible **advierta y no bloquee**, que la cookie no sea legible desde JS, y
que el PDF baje con su SHA-256.

### 3.1 Tres bugs, todos míos y todos de los tests

1. `getByRole("alert")` engancha el route announcer que Next inyecta. El error
   ("strict mode violation") suena a bug de la app.
2. Los tests de la ficha hacían click en **la primera fila**. El test del flujo
   completo crea un informe sin comparables, y a partir de ahí los cinco tests
   de la ficha se salteaban en silencio: cinco tests verdes que no probaban
   nada. Ahora buscan un `SUCCEEDED`.
3. El contenedor `web` no arrancaba con `email-validator`: una dependencia
   nueva en `pyproject.toml` **no existe en la imagen** hasta reconstruirla. Al
   menos falla fuerte —crash loop— y no en silencio.

---

## 4. El hallazgo: 17,6 puntos de varianza

Al cablear el gate del golden set hizo falta correr el eval de extracción
varias veces. Sin tocar una línea de código ni de prompt:

```
60,8%    77,0%    75,7%    78,4%    79,7%
         mediana 76,4%  ·  AMPLITUD 17,6 pp
```

**El 68% que el informe de la Etapa 3 reportó, y que ESTADO.md citó durante un
día, es una muestra de esa distribución.** No estaba mal medido: estaba mal
leído. Y lo más incómodo es que el propio informe ya avisaba —"son UNA corrida;
no alcanzan para declarar una mejora chica"— y el número se citó igual como si
fuera EL número.

Consecuencias concretas:

- Ninguna iteración de prompt se puede evaluar con una corrida.
- La tolerancia de 2 pp que había escrito para el gate de componentes era
  absurda: se habría disparado con ruido puro, y un gate que se dispara con
  ruido enseña a ignorarlo.
- El gate ahora compara contra la **mediana de las últimas 5** y usa la
  **amplitud observada** como umbral. El número no se elige: se mide.

Contraste útil: el backtest end-to-end **sí** es estable (14,5% y 15,0% con
distinta semilla). La inestabilidad está en el eval de componente, que corre 74
comparaciones sobre 24 avisos y depende de una salida de LLM por lote.

---

## 5. Lo que quedó roto y hay que decirlo

**`eval_curaduria.py` da recall 0%**, contra el 100% que documenta la Etapa 3.
El juez identifica correctamente los 7 descartes del golden set:

```
descarte sin cita, no se aplica  motivo=permuta_o_financiacion  ref=56776515
descarte sin cita, no se aplica  motivo=en_pozo_o_construccion  ref=58689453
... (7 de 7)

Recall 0%   ·   Precisión 100%   ·   Descartes sin cita, NO aplicados: 7
```

**Los siete se rechazan por no traer cita verificable.** No es que el juez se
equivoque: es que la verificación no acepta lo que devuelve.

Lo que impide diagnosticarlo de una: el **mismo nodo sí descartó** en el
informe de Palermo del mismo día (`en_pozo_o_construccion: 1`,
`permuta_o_financiacion: 1`). Así que no está roto siempre — y a la luz de la
§4, lo primero es medir N corridas antes de sacar conclusiones de una.

Es la misma familia que el falso positivo del crítico con la altura de la
dirección: la regla está bien y la implementación puede ser más angosta que la
regla.

Queda como el bloqueante #1 de ESTADO §5.1.

---

## 6. Lo que se agregó

- `tasador.security` + `v1/auth.py`: argon2id, API keys por prefijo, JWT de
  sesión. 20 tests.
- `scripts/crear_usuario.py`: usuarios, API keys y `--listar`. La contraseña
  **no se toma por argumento** —queda en el historial y en `ps`— sino por
  prompt sin eco o por stdin.
- `web/`: `/login`, middleware, proxy de sesión, botón de salir.
- `web/e2e/`: 44 tests de Playwright, en dos modos.
- `eval.component_runs` + `tasador.eval.componentes`: la serie de los evals de
  componente, con mediana y amplitud. Tabla aparte de `backtest_runs` a
  propósito: un MdAPE y una exactitud no son lo mismo, y compartir columna
  porque las dos son porcentajes produce una serie donde dos filas contiguas no
  significan lo mismo.
- El escáner de CSV, recursivo y multi-formato.

```
ruff · mypy --strict          ✅
pytest sin base               274 passed, 34 skipped
pytest con base               306 passed,  2 skipped
playwright (con auth real)     44 passed,  1 skipped
```

---

## 7. Lo que aprendimos

1. **Un número de una sola corrida no es un número.** 17,6 pp de amplitud, y el
   informe que lo produjo ya lo advertía. Advertirlo no alcanza: hay que hacer
   que la herramienta lo imponga.
2. **El umbral de un gate se mide, no se elige.** Los 2 pp que escribí a mano
   eran ruido puro.
3. **Un test que se saltea en silencio es peor que uno que falla.** Cinco tests
   de la ficha se salteaban porque el primer informe del listado cambió de
   estado.
4. **Un middleware de front no es autorización**, y conviene escribirlo en el
   archivo para que nadie confíe en él.
5. **Una dependencia nueva no existe en la imagen** hasta reconstruirla. Es la
   auditoría de la mañana repitiéndose por tercera vez el mismo día.
6. **Reconocer archivos por contenido y no por ruta.** El scraper ya cambió su
   estructura de carpetas una vez.

---

## 8. Apéndice — el recall 0% de la curaduría, resuelto

La §5 dejó el diagnóstico abierto. Se cerró el mismo día, y la causa era de una
línea:

```python
cita: str | None = None      # opcional CON default -> no entra en `required`
```

Un campo con default no aparece en el `required` del JSON Schema, así que el
modelo podía omitirlo **sin violar nada**. Y el prompt lo empujaba a hacerlo:
cerraba con *"los que sirven van con `descartar: false`, `motivo: null` y
`cita: null`"*.

Resultado medido: el juez detectó **los 7 descartes, los 7 correctos**, y
`aplicar_veredicto` los rechazó a todos por venir sin cita. **Recall 0% con un
juez que no se equivocó ni una vez.**

El costo no era cosmético. Sin descartar los `en_pozo`, esos avisos entran como
comparables — y su precio incluye plazo de obra y se paga en cuotas contra
avance, así que no es comparable con una venta normal. En la Etapa 3, sacarlos
bajó el techo del rango un 13% sin mover el centro: **el informe salía con el
techo inflado.**

Arreglo: `cita: str`, requerido. El modelo ahora tiene que escribir algo; si
escribe algo que no está en el aviso cae en la rama ruidosa de
`aplicar_veredicto` —un `warning`, no un `info`—, que es para lo que el
mecanismo existe: no para que el modelo no mienta, sino para que cuando mienta
se note.

### 8.1 Y destapó un segundo defecto, en la definición del motivo

Con la cita ya obligatoria apareció un falso positivo: Av. Monroe 1378,
descartado como `en_pozo_o_construccion`. Es **el mismo aviso** que la Etapa 3
§10.1 registró como salvado por la verificación de cita —entonces el modelo
había *reconstruido* el texto y ahora lo copió bien—, así que la pregunta dejó
de ser sobre la cita y pasó a ser sobre el criterio.

El aviso describe una unidad **terminada**: balcón, cochera, dos bauleras,
amenities en funcionamiento. No hay fecha de entrega ni obra. Lo único que lo
hacía parecer un pozo era el nombre: *"Quartier Bajo Belgrano, desarrollo de
Argencons"*.

El agujero estaba escrito en el prompt: el motivo decía *"está en pozo, en
construcción **o es un emprendimiento en comercialización**"*. Esa última
cláusula hace que el nombre de un desarrollo califique solo. Se reemplazó por
el criterio real —**evidencia de que la unidad NO está terminada**— y se
nombraron los tres errores típicos: "a estrenar" a secas, el nombre de un
emprendimiento o desarrollador, y la lista de amenities.

### 8.2 Lo que quedó, y lo que NO se puede afirmar

```
                    recall   precisión   descartes sin cita
antes                  0%        100%            7
cita obligatoria     100%         86%            0     ← apareció el FP de Monroe
+ motivo acotado      67%        100%            0
                     100%        100%            0
                      83%        100%            0
```

**La precisión es 100% en las tres corridas posteriores al arreglo**: el falso
positivo se fue y no volvió. Eso sí se puede afirmar.

**El recall no.** Va de 67% a 100% entre corridas idénticas. El golden set de
curaduría tiene **6 descartes**, así que cada caso vale 16,7 pp y la métrica
solo puede tomar siete valores. El *"recall 100%"* de la Etapa 3 era una de
esas siete caras.

No se siguió ajustando el prompt: hacerlo contra un set de 6 casos es
sobreajustar a la muestra, que es exactamente lo que la §4 de este mismo
informe dice que no hay que hacer. **Lo que falta no es prompt, es golden set.**

---

## 9. El corpus creció, y trajo cinco bugs más

Entraron 6 archivos nuevos del scraper: 584 avisos vigentes contra 312, dos
portales sobre los mismos barrios. Es la primera vez que el sistema ve el caso
para el que el nodo 5 fue diseñado — el mismo inmueble publicado por dos
inmobiliarias — y **ninguno de los cinco bugs que aparecieron era un error de
razonamiento**. Eran suposiciones que 24 avisos de un solo portal nunca
contradijeron.

### 9.1 Los portales publican la altura aproximada

```
73% de las direcciones de Portal A terminan en "00"
65% de las de Portal B
```

Lo hacen para que no se pueda saltear a la inmobiliaria. «Perú 1355» se publica
como «Perú al 1300». Comparando la altura exacta, esos dos avisos —que son el
mismo inmueble— nunca colisionaban:

```
clave_direccion("Perú 1355")           -> "PERU 1355"
clave_direccion("Perú al 1300")        -> "PERU 1300"          ✗
```

Y al mismo tiempo, la unidad **rompía** el match porque quedaba pegada a la
clave:

```
clave_direccion("Guise 1900, Piso 4")  -> "GUISE 1900 PISO 4"
clave_direccion("Guise 1900")          -> "GUISE 1900"          ✗
```

**Se perdían duplicados por los dos lados a la vez.** La clave pasó a ser la
CUADRA y la unidad salió de la clave para ser un dato aparte, que es lo que es.

### 9.2 La unidad tiene tres estados, no dos

«Perú al 1300, Piso 5 Depto B» y «Perú al 1300, Piso 3 Depto B» son distintos
con certeza, aunque coincidan superficie, ambientes y precio — en una torre las
unidades de la misma línea son idénticas salvo por el piso. Pero si uno de los
dos no la declara, **"no sé" no es "no"**: hay que decidir por otro lado.

Solo la declaran 75 de 293 avisos en Portal A y 8 de 298 en Portal B, y las
columnas `floor`/`apartment` del scraper vienen vacías en los 584. Al mirar el
corpus real aparecieron dos formatos que la primera versión no capturaba:

```
Soldado de la Independencia al 1200 - 7° Piso "A"   -> 7A    (orden invertido)
Manuel Ugarte 2500 - 9°08                           -> 908   (unidad numérica)
```

### 9.3 Arreglar una capa rompió otra

Con la clave por cuadra, dos avisos de la misma cuadra pasaron a tener
similitud de trigramas **1,0**. Y `_capa_2` nunca miró superficie — con la
clave vieja no hacía falta. Degeneró en "misma cuadra + precio ±5%":

```
Zabala 1851      589.258   93 m²   ┐ el mismo inmueble
Zabala al 1800   589.258   93 m²   ┘
Zabala al 1800   610.400  105 m²   ← otra unidad, y volvió a fusionarse
```

Es **exactamente** el edificio que el informe de la Etapa 3 §11.1 registró como
el caso borde bien resuelto. La capa 1 lo separaba por superficie; la capa 2 no
la miraba.

La tolerancia que se agregó es PORCENTUAL y no en m² fijos, y el motivo salió
del corpus: el mismo depto publicado con 93 y 99 m² porque un portal cuenta el
balcón. Con ±2 m² esos dos se separaban.

### 9.4 Un campo opcional es un campo que el modelo va a omitir

Ya está en la §8, pero pertenece a la misma familia: `cita: str | None = None`
no entra en el `required` del JSON Schema, así que el modelo podía omitirlo sin
violar nada. **Recall 0% con un juez que acertó los 7 de 7.**

### 9.5 El efecto sobre el número

```
                     duplicados   grupos entre portales
antes                      2,7%                       5
con la clave por cuadra    4,5%                      10   ← parte era falso
con superficie en capa 2   2,7%                       8
```

El número final coincide con el inicial por casualidad: cambiaron los grupos,
no la cantidad. Y **2,7% sigue siendo un PISO**: solo cuenta las capas
determinísticas, y quedan 282 pares en zona gris. Con la altura redondeada, la
dirección da menos información y más pares caen ahí.

### 9.6 Lo que se aprendió sobre el costo del juez

Correr la capa 3 sobre los 282 pares tarda ~4 minutos por lote de 10, o sea
casi dos horas. Es información que no teníamos: el nodo 5 la acota a 15 pares
justamente porque está en el camino crítico de un informe, pero a escala de
corpus el cuello no es el número de pares —el bloqueo por (barrio, ambientes)
ya lo baja 59%— sino **la latencia por llamada**. Para los ~88.000 avisos de
CABA habría que paralelizar los lotes, igual que se hizo con el nodo 4.

---

## 10. Verificación final

Informe completo, autenticado con API key y no con el header:

```
NODO                  EST          ms        USD
normalize_subject     OK          749   0.000000
retrieve_candidates   OK           71   0.000000
extract_features      OK      185.917   0.009642   60 extraídos, 0 citas falsas
dedup_cluster         OK       63.558   0.001319   0 clusters
curate                OK      220.151   0.007816   5 en_pozo_o_construccion ←
adjust_and_value      OK           46   0.000000
market_context        OK       18.144   0.000704
write_report          OK       16.170   0.009318
critic                OK        9.635   0.029046   aprobado al primer intento
render_pdf            OK        7.128   0.000000
                                        USD 0,058

rango: USD 135.888 — 169.860 — 203.832  ·  2.831 USD/m²  ·  confianza ALTA
49 comparables usados de 60
```

**Los 5 descartes de `en_pozo_o_construccion` son la prueba en producción del
arreglo de la §8**: antes de hacer obligatoria la cita, ese nodo descartaba
cero y esos cinco emprendimientos entraban a la mediana.

```
ruff check src tests scripts migrations   ✅
mypy --strict                             ✅ 63 archivos
pytest -m "not live"                      ✅ 327 passed (295 sin base)
playwright                                ✅ 54 tests, escritorio y celular
```
