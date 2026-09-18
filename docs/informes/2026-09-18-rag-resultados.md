# RAG sobre el corpus real: lo medido, en el orden en que se midió

**18/09/2026** · ejecución de [doc 18](../18-rag-propuesta.md). Los resultados se publican
como salen (doc 09 §7). Lo que todavía no se midió está marcado ⏳ y se completa en
este mismo archivo cuando exista.

---

## 1. Lo que la medición corrigió antes de la tabla

Cinco veces la medición contradijo lo escrito. Se registran primero porque cambian cómo
se lee todo lo demás.

| Lo escrito | Lo medido | Consecuencia |
|---|---|---|
| «`bge-m3` vía `fastembed`» (ADR-004, `settings`) | `fastembed` 0.8.0 no lo sirve | ADR-010: elegir entre lo que la librería sirve |
| «`e5-large` como reemplazo» (doc 18 §3.1) | **0,4–0,8 pasajes/s en CPU**: 6-12 h para 17.500 chunks; 35 min de indexado escribieron 199 | MiniLM-L12 multilingüe (13/s) para poder comparar chunkers en una tarde; e5 queda como fila pendiente sobre un subconjunto |
| «Entre un cuarto y un tercio de los avisos no entra en 512 tokens» (estimado por caracteres) | **28,1%** con el tokenizador real, sin truncar (p50 374 · p90 710 · p99 1.176 · máx 2.400, n=8.514) | La estimación estaba bien. La PRIMERA medición dio p90 = p99 = máx = 512: el tokenizador con el que se embebe trunca, y contar con él responde «ninguno» por construcción |
| «Los comparables de informes pasados son juicios de relevancia» (doc 18 §4.1) | Sobre 23 consultas, `juzgados@25` = 0,27 y en 17 fue **cero**: la escalera cambió el 15/08 y el pool de entonces ya no existe | Pooling: el propio pipeline (nodos 4-7) juzga la unión del top-N de los sistemas que se comparan (`eval/juicios.py`) |
| «El umbral de coseno rechaza sin llamar al modelo» (doc 18 §5.3) | Con MiniLM, las preguntas con respuesta dan 0,43-0,74 y las que no, 0,32-0,51: **no separan**. Umbral 0,80: rechazo 0,31, citas 0,00 | Compuerta doble —coseno **y** solapamiento léxico— y el modelo decide con `sin_evidencia` |

Y dos de rendimiento que no cambian el diseño pero sí el costo de medir:

- El chunker por oraciones re-tokenizaba el candidato entero en cada paso: **323 s solo
  en contar** sobre 8.514 avisos (O(n²)). Una tokenización por oración y el chunk es la
  suma.
- El juez del nodo 6 iba lote por lote en serie: con pools de ~160 avisos del eval, ~5
  minutos por consulta. Ahora los lotes van en paralelo (semáforo 4), como en el nodo 4.
  Es un cambio que también acorta el informe real.

## 2. El índice

```
modelo    sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 · 384 d · 128 tokens · Apache
corpus    8.514 avisos vigentes con descripción (Palermo 8.248 · Belgrano 298 · dos portales)

chunker C (oraciones + encabezado, objetivo 100 / máx 120)   83.063 chunks · 9,76 por aviso
                                                             p50 92 · p90 118 · p99 120 tokens · 4.020 s
chunker A (truncar a 120)                                     8.514 chunks · 1 por aviso · 528 s
```

Con e5-large (objetivo 320 / máx 480) el mismo chunker C daba 2,06 chunks por aviso, p50
276, máx 480: el número de chunks es una función del contexto del modelo, no del texto.

## 3. La tabla de ablación ⏳

Consultas: 53 informes pasados + 60 avisos del corpus leídos como sujeto (leave-one-out).
Pool: unión del top-30 de cada sistema, juzgado por el pipeline. Métricas sobre la lista
condensada, con intervalo del 95% por bootstrap sobre las consultas; Δ apareada contra A.

```
                                   nDCG@25   recall@30   MRR   bpref   juzgados@25   ms
A  SQL + recencia (hoy)               ⏳
B  A + denso, truncar (A-truncar-120)  ⏳
C  A + denso, oraciones+encabezado     ⏳
D  A + léxico (FTS spanish)            ⏳
E  C + D fusionados con RRF            ⏳
F  E + rerank bge-reranker-base        ⏳  (subconjunto: 1,7 pares/s en CPU)
G  E + rerank jina-v2 multilingual     ⏳  (subconjunto; CC-BY-NC, solo para comparar)
```

**Primer tercio, ya juzgado con pooling** (las 34 consultas que vienen de informes
pasados; pools de 90-100 avisos, `juzgados@25` = 1,00 en los cinco sistemas):

```
                                   nDCG@25            recall@30          MRR               bpref
A  SQL + recencia                0,688 [0,673–0,708]  0,262             0,387             0,325
B  denso, truncar                0,817 [0,809–0,829]  0,302             1,000             0,369
C  denso, oraciones+encabezado   0,707 [0,697–0,719]  0,285             0,559             0,351
D  léxico (FTS spanish)          0,844 [0,834–0,850]  0,296             1,000             0,401
E  C + D con RRF                 0,702 [0,696–0,713]  0,274             0,529             0,473

Δ vs A (apareada, 95%):  B +0,130 [0,121–0,136] · C +0,020 [0,008–0,028] · D +0,156 [0,128–0,176] · E +0,015 [0,003–0,023]
```

Tres lecturas, provisorias hasta tener las 113: (1) **todo puntaje le gana a la
recencia**, fuera del intervalo; (2) el léxico solo (D) es el mejor en nDCG y el
denso con *truncar* (B) le sigue — el chunking por oraciones (C) **no** mejora sobre
truncar en este tercio, al contrario; (3) la fusión (E) hereda lo peor de C en nDCG
pero tiene el mejor bpref. Las consultas de informes no traen texto libre: la
consulta semántica es solo el encabezado estructurado, que es exactamente lo que
el léxico matchea mejor. Las 60 consultas que vienen de avisos —con la descripción
como notas— son las que pueden dar vuelta esto.

Lo que ya se sabía de la primera pasada, con juicios solo de A: el sistema A sobre su
propio pool da nDCG@25 0,64 —la recencia no empuja hacia abajo a los que la curaduría
después descarta— y recall@30 1,0 por construcción. Los sistemas B-E sobre ese pool dan
`juzgados@25` de 0,16 a 0,36, que es la razón del pooling: sin juzgar lo que ELLOS
traen, no se puede decir nada.

**Criterio, escrito antes (doc 18 §4.4):** `semantic.enabled: true` solo si E o F
superan a A en nDCG@25 fuera del intervalo, el % descartado por la curaduría baja, y el
MdAPE no empeora fuera del ruido entre semillas. ⏳

## 4. «Preguntale al informe»

Sobre un informe real de Palermo (64 hechos + 14 párrafos de metodología), 26 preguntas
por plantilla: 13 sobre hechos con chequeo determinístico, 5 sobre metodología, 8 sin
respuesta posible. Modelo: la tarea `judge` (flash). Costo: USD 0,008 las 26.

```
                              rechazo correcto   citas correctas   cifras correctas
umbral denso 0,80 (e5-style)        0,31              0,00              0,56
compuerta doble 0,40 / 0,34         0,92              0,83              0,83   (mediana de 3, con caché: idénticas)
+ citas normalizadas, «comparables»
  fuera de stopwords, «metro cuadrado»  0,96              0,89              0,94   (mediana de 3 corridas SIN caché; USD 0,004-0,008 cada una)
```

Los tres fallos que quedaban, y su arreglo: el modelo devolvía `[N]` con corchetes y la
verificación lo tomaba por un id desconocido (se normaliza); «comparables» en plural
estaba en la lista de stopwords y la pregunta «¿cuántos comparables se usaron?» no
encontraba el hecho [N] (sale de la lista); «valor por metro cuadrado» no matcheaba
«por m²» (el hecho dice las dos cosas). Con los tres arreglos y sin caché: rechazo
0,96 · citas 0,89 · cifras 0,94 (mediana de 3; guardado en `eval.component_runs`).

Lo que NO se mide todavía y hay que decir: la fidelidad de la prosa (que la respuesta
no afirme algo que el fragmento no dice) solo la controla la verificación de cifras y
de citas. Un juez LLM de fidelidad es la fila que falta.

## 5. El primer run del CI

Nunca había corrido (el repo no tenía remoto). Falló en dos jobs, los dos reproducidos
en local:

- **gitleaks:** dos falsos positivos —el placeholder de `SECRET_KEY` en `.env.example` y
  el `Bearer tsk_live_...` del manual—. `.gitleaks.toml` con allowlist por forma del
  placeholder; con la misma imagen en Docker: `no leaks found`.
- **Tests:** el paso de migraciones desde cero no tenía `REDIS_URL` y los settings
  explotan al arrancar sin él (a propósito). Con la variable, 481 tests en verde en el
  runner.

El segundo run cayó en «Imágenes», por `trivy` sobre la imagen del front: Next 15.1.6
con CVE-2025-29927 (bypass de autorización en middleware) → `next@15.5`; y el `npm`
que trae la imagen base de Node arrastra `tar` y `brace-expansion` con CVEs → se borra
del runtime (el servicio corre `node server.js`, no lo usa). Quedaba `postcss 8.4.31`
como dependencia transitiva de Next → fijado por `overrides` a `^8.5.12`. Playwright
completo tras la actualización: 50 passed · 7 skipped. ⏳ tercer run.

## 6. Lo que aprendimos, esta vez

1. **Un tokenizador que trunca no puede medir cuánto se trunca.** La primera medición
   daba «ninguno se pasa» con la cara de un resultado.
2. **Los juicios de relevancia caducan con el recuperador.** Lo que era relevante para
   el pool de agosto no dice nada del pool de hoy; o se juzga el pool actual, o se mide
   contra fantasmas.
3. **El coseno de un modelo chico no sabe decir «no hay evidencia».** Separó peor que
   una lista de palabras; la combinación de las dos señales sí.
4. **El costo de un experimento es parte del diseño.** Un modelo 30× más lento no es
   «mejor pero más caro»: es un experimento que no se puede repetir, y un experimento
   que no se repite no se puede corregir.
5. **La serie que nadie ve.** El juez iba lote por lote desde agosto; no dolía con 60
   avisos y dolió con 160. El eval de recuperación encontró un problema de latencia
   del producto.
