# 15 — Captura por voz del informe de visita

> **Origen:** idea del 13/08/2026. Es, de lejos, la mejor idea que apareció desde el
> arranque del proyecto, porque resuelve el agujero de datos más grande que tiene el
> sistema. Este documento la diseña y corrige un supuesto de costo.

---

## 1. El problema que resuelve

Repasemos [05 §4](05-metodologia-de-valuacion.md): el valor sale de ajustar cada
comparable por sus diferencias con la propiedad sujeto. Los factores de ajuste son:

| Factor | Peso máximo |
|---|---|
| Estado de conservación | ±15% |
| Piso sin ascensor | −12% |
| Antigüedad | ±6% |
| Orientación | ±10% |
| Amenities | +5% |
| Expensas altas | −4% |

**Todos esos datos vienen de la propiedad sujeto. Y nadie los carga.**

El formulario de `/informes/nuevo` tiene 15 campos opcionales. Un agente que acaba de
salir de una visita, en el auto, con el celular, **no llena 15 campos**. Va a poner
dirección, ambientes y metros, y listo. Resultado: la mitad de los coeficientes quedan
en 1,00 por falta de dato y el sistema tasa como si toda propiedad fuera "muy buena,
lateral, con ascensor". Es decir: **el motor de ajustes queda desperdiciado**.

Pero ese mismo agente **sí habla 90 segundos** apenas sale:

> *"Cabildo 2530, cuarto B. Tres ambientes, 72 cubiertos más balcón. Al frente,
> muy luminoso. El edificio está impecable, tiene ascensor y encargado permanente.
> El departamento necesita laburo: cocina y baño son originales de los 70, hay que
> hacer todo. Expensas noventa y cinco mil, que para la zona está bien. Cochera no
> tiene. Cuarto piso. La dueña quiere vender rápido."*

De esos 90 segundos salen: `orientation=frente`, `condition=a_refaccionar`,
`has_elevator=true`, `floor_number=4`, `expenses_ars=95000`, `parking_spaces=0`,
`amenities=[encargado]`, `surface_covered=72` — **más de lo que el formulario iba a
conseguir**, en un tercio del tiempo y sin fricción.

---

## 2. Por qué encaja tan bien en esta arquitectura

Porque **el componente que hace falta ya existe**. El nodo 4 (`extract_features`)
convierte texto libre de un aviso en JSON tipado y validado. Una transcripción es
texto libre. Es el mismo extractor apuntado a otra entrada:

```
aviso de Portal A  ──┐
                      ├──▶  nodo 4  ──▶  atributos tipados (Pydantic)
nota de voz del agente ┘
```

La diferencia es de confianza, no de mecanismo: lo que dice el agente **vale más** que
lo que dice un aviso publicitario, porque el agente estuvo adentro. Eso se refleja en
`verified_by` y en que sus datos pisan a cualquier inferencia.

---

## 3. ⚠️ Corrección: la API de audio de OpenAI no es gratis

Preguntaste por "la API de audio gratis de OpenAI". **No existe una versión gratuita.**

| Opción | Costo | Privacidad | Veredicto |
|---|---|---|---|
| OpenAI `whisper-1` | ~USD 0,006/min | audio a un tercero | — |
| OpenAI `gpt-4o-mini-transcribe` | ~USD 0,003/min | idem | — |
| **Groq** (Whisper large-v3-turbo) | free tier generoso, después centavos | audio a un tercero | Buen fallback |
| **Whisper local** (`faster-whisper`) en el VPS | **USD 0** | **el audio no sale de nuestro servidor** | ✅ **Elegida** |
| Web Speech API del navegador | USD 0 | audio a Google | Calidad pobre en es-AR con términos del rubro |

**El costo no es el argumento decisivo**, y conviene decirlo: 40 tasaciones/mes × 2
minutos = 80 min/mes. Aun pagando OpenAI serían **USD 0,48/mes**. Irrelevante.

**El argumento decisivo es la privacidad.** Escuchá de nuevo el ejemplo de §1: termina
con *"la dueña quiere vender rápido"*. Una nota de voz de una visita inmobiliaria
contiene, inevitablemente, comentarios sobre la situación personal del propietario —
separaciones, sucesiones, urgencias de plata. Eso es información sensible de un tercero
que **no es usuario del sistema y nunca dio consentimiento**.

Mandar ese audio a un proveedor externo, cuando existe una alternativa local que corre
en un servidor que ya está pago, sería una decisión difícil de defender. Y contradiría
la regla que ya establecimos en [10 §3](10-seguridad-y-legal.md): *la mejor forma de
proteger un dato es no tenerlo* — o al menos, no repartirlo.

### 3.1 La decisión

**`faster-whisper` con el modelo `small`, en CPU, dentro del contenedor `worker`.**

- Modelo `small` (~460 MB en int8): buena calidad en español rioplatense, y suficiente
  para vocabulario inmobiliario común.
- En 4 vCPU, un audio de 2 minutos transcribe en **~25-40 segundos**. Como la
  generación del informe ya tarda ~90 s y todo va por cola, no cambia la experiencia.
- Costo marginal: **cero**.
- Sin conexión a internet para esta parte.

**Igual se abstrae**, mismo criterio que el ADR-003 con los modelos de texto:

```python
class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, lang: str = "es") -> Transcript: ...

# implementaciones: LocalWhisper (default) · Groq · OpenAI · Fixture (tests)
```

`TRANSCRIBER_STRATEGY=local|groq|openai|fixture` en el `.env`. Si un cliente prefiere
pagar por más calidad, es un cambio de variable de entorno.

> **ADR-010 — Transcripción local por defecto.** Fundamento: privacidad de terceros no
> consentientes, costo cero y ausencia de dependencia externa en un camino que puede
> contener información sensible. Se abstrae para poder cambiarlo, pero el default es el
> que protege.

---

## 4. Dónde se construye: **en el Tasador, no en el panel de la inmobiliaria**

Preguntaste si convenía incorporarlo "en aquel proyecto". La respuesta es **no**, y por
la razón que vos mismo pusiste sobre la mesa: **este desarrollo tiene que ser
independiente del dueño de la inmobiliaria.**

| Si se construye en el panel | Si se construye en el Tasador |
|---|---|
| Necesita que Gastón lo apruebe y deployar su producción | Se hace y ya |
| Es una funcionalidad regalada a un sistema que ya entregaste | Es una funcionalidad **del producto que querés vender** |
| Solo sirve a la inmobiliaria | Sirve a cualquier inmobiliaria cliente |
| Acopla dos repos | Cero acoplamiento |

**Implementación:** una página web móvil del Tasador (`/informes/[id]/voz`), con
`MediaRecorder` del navegador. El agente abre un link, aprieta un botón, habla, y
listo. Sin instalar nada, sin app store, sin permisos raros. Funciona en Chrome Android
y Safari iOS.

**El punto de entrada sí puede vivir en el panel más adelante**: un botón "Grabar
informe de visita" en la ficha de tasación que abre nuestra URL con un token firmado.
Eso es la Etapa 7, opcional, media hora de trabajo, y no cambia nada de nuestro lado.

---

## 5. El flujo

```
1. El agente termina la visita, abre el link, aprieta "Grabar"
2. Habla 60-120 segundos. Puede escuchar y regrabar antes de enviar
3. POST /v1/reports/{id}/voice-note   (audio/webm, máx 10 MB / 5 min)
4. Worker:
   a. transcribe            → faster-whisper local
   b. extract_subject       → nodo 4, mismo extractor, prompt propio
   c. muestra para CONFIRMAR ← el agente valida antes de que impacte
5. Los atributos confirmados actualizan `subject_properties`
6. Se regenera el informe con los coeficientes ahora sí aplicados
```

### 5.1 La confirmación no es opcional

El agente ve lo extraído **antes** de que toque un número:

```
De tu audio entendimos:

  Estado          A refaccionar        "cocina y baño originales de los 70"
  Orientación     Frente               "al frente, muy luminoso"
  Piso            4                    "cuarto piso"
  Ascensor        Sí                   "tiene ascensor"
  Expensas        $95.000              "expensas noventa y cinco mil"
  Cochera         No                   "cochera no tiene"
  Amenities       Encargado permanente

  [ Confirmar ]   [ Corregir ]   [ Descartar ]
```

Cada campo con **la frase de la que salió**. Si el modelo entendió mal, se ve al
instante. Una extracción silenciosa que se equivoca en `condition` mueve el precio un
18% sin que nadie se entere — eso es exactamente lo que este paso impide.

---

## 6. Qué pasa con lo que el agente dice y no es un dato

La transcripción tiene dos partes y se tratan distinto:

| Contenido | Destino | Efecto en el precio |
|---|---|---|
| **Atributos** ("cuarto piso", "al frente", "a refaccionar") | `subject_properties`, tras confirmación | **Sí**, vía coeficientes de [05 §4](05-metodologia-de-valuacion.md) |
| **Opinión del agente** ("el edificio es lindo", "yo lo pondría en 180") | `subject_properties.agent_notes` | **Ninguno.** Nunca |
| **Datos de personas** ("la dueña se separa", "necesitan la plata") | **Se descarta en la ingesta** | Ninguno |

Las tres reglas duras:

1. **La opinión del agente no mueve el número.** Si dice "esto vale 200.000", el
   sistema lo ignora para calcular. Puede aparecer citado y atribuido en el informe
   ("el agente estima..."), nunca fundido con el cálculo. Esto es
   [ADR-002](01-arquitectura.md) aplicado a una entrada nueva: si dejáramos que la
   opinión entre al cálculo, el sistema sería un espejo del agente con formato lindo.
2. **El audio se borra tras la transcripción** (o a los 7 días, configurable). Se
   guarda el texto, no la voz. Menos superficie de riesgo.
3. **Se filtra PII en la transcripción.** Un paso de redacción quita nombres propios,
   teléfonos y referencias a la situación personal del propietario **antes** de que el
   texto se guarde o entre a cualquier prompt. Con log de qué se redactó, sin el
   contenido.

---

## 7. Modelo de datos

```sql
alter table core.subject_properties
  add column agent_notes text,           -- opinión, NO entra al cálculo
  add column data_source text not null default 'FORM'
      check (data_source in ('FORM','VOICE','MIXED','IMPORTED'));

create table core.voice_notes (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid not null references core.organizations(id),
  subject_property_id uuid not null references core.subject_properties(id) on delete cascade,
  duration_seconds  smallint,
  transcript        text,                      -- ya redactado de PII
  transcriber       text not null,             -- 'faster-whisper:small'
  language          text not null default 'es',
  extracted         jsonb not null default '{}',
  confirmed         boolean not null default false,
  confirmed_by      uuid references core.users(id),
  confirmed_at      timestamptz,
  redactions        smallint not null default 0,   -- cuántas, no cuáles
  audio_deleted_at  timestamptz,
  created_by        uuid not null references core.users(id),
  created_at        timestamptz not null default now()
);
create index idx_voice_subject on core.voice_notes (subject_property_id, created_at desc);
```

**No obvio:** `transcript` guarda el texto **ya redactado**. La versión cruda no se
persiste nunca — vive en memoria del worker el tiempo que dura el pipeline.
`redactions` guarda cuántas redacciones hubo, no cuáles: sirve para detectar que el
filtro está funcionando sin reintroducir el dato que se quería sacar.

---

## 8. Lo que esto habilita más adelante

Vale anotarlo, sin construirlo ahora:

- **Calibración de los coeficientes con datos reales.** Hoy los coeficientes de
  [05 §4.3](05-metodologia-de-valuacion.md) son criterio, no medición. Con N tasaciones
  que tengan `condition` real (de la voz) **y** precio de captación real, se puede
  correr la regresión hedónica sobre datos propios y no solo sobre BA Data, que no trae
  estado ni antigüedad. **La nota de voz es lo que genera el dato que hoy no existe en
  ninguna fuente.**
- Comparar lo que el agente dijo contra lo que terminó pasando (¿se vendió? ¿a cuánto?)
  → medir qué agentes calibran bien.
- Detectar propiedades subvaluadas: si el agente describe algo excelente y el sistema
  da un valor bajo, hay algo que mirar.

---

## 9. Impacto en el plan

| Etapa | Tarea |
|---|---|
| **3** | 3.15 — `Transcriber` con `faster-whisper` local + `Fixture` para tests |
| **3** | 3.16 — Prompt `extract_subject`: transcripción → atributos con la cita de origen |
| **3** | 3.17 — Redacción de PII en la transcripción, con test |
| **4** | 4.14 — `/informes/[id]/voz`: grabador móvil con `MediaRecorder`, escuchar y regrabar |
| **4** | 4.15 — Pantalla de confirmación campo por campo, con la frase de origen |
| **7** | 7.6 — (opcional) Botón en el panel que abre nuestra URL con token firmado |

Costo estimado: **+4 días**. Y es lo que hace que el motor de ajustes sirva de verdad
en vez de quedar apagado por falta de datos.

**Prioridad:** alta. Sin esto, la mitad de la metodología de valuación es decorativa.
