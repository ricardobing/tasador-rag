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
| «F = E + rerank» con el tope de latencia de producción (15 s) | En CPU, 60 pares tardan 17-35 s: el reranker **degradaba a E en todas las consultas** y la fila F medía la fila E sin decirlo | En el eval, tope de 300 s y el sistema explota si degrada; el tope de producción es una decisión aparte (§3) |
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

## 3. La tabla de ablación

Consultas: 53 informes pasados + 59 avisos del corpus leídos como sujeto (leave-one-out;
el set de 60 se fijó en disco y uno dejó de calificar cuando sus features cambiaron).
Pool: unión del top-30 de cada sistema, juzgado por el pipeline (nodos 4-7); 16 juicios
de la segunda mitad se rehicieron porque el gateway se reinició en el medio y el nodo 6
degrada sin juez (§6). Métricas sobre la lista condensada, intervalo del 95% por
bootstrap sobre las consultas; Δ apareada contra A. `descartados@30` es la fracción del
top-30 juzgado que la curaduría no usa —el criterio (b) de doc 18 §4.4—.

```
112 consultas · k=25 · juzgados@25 = 1,00 en todos

                                  nDCG@25              Δ vs A (95%)          recall@30  MRR    bpref  descartados@30
A  SQL + recencia (hoy)           0,748 [0,719–0,774]       —                  0,314     0,649  0,390  0,292
B  A + denso, truncar 120         0,805 [0,782–0,827]  +0,057 [+0,033, +0,081]  0,346     0,905  0,441  0,218
C  A + denso, oraciones+encab.    0,759 [0,730–0,783]  +0,012 [−0,010, +0,031]  0,333     0,753  0,444  0,247
D  A + léxico (FTS spanish)       0,821 [0,795–0,845]  +0,073 [+0,049, +0,096]  0,345     0,940  0,456  0,218
E  C + D fusionados con RRF       0,780 [0,753–0,804]  +0,032 [+0,014, +0,050]  0,336     0,795  0,534  0,241
F  E + rerank bge-reranker-base   ⏳
H  B + D fusionados con RRF       ⏳  (no estaba en el plan: la agregó la fila B)
G  E + rerank jina-v2             ⏳  (CC-BY-NC; solo para comparar)
```

Lo que dice la tabla, en orden de sorpresa:

1. **Todo puntaje le gana a la recencia** salvo el chunking por oraciones solo (C), que
   roza el cero. B, D y E están fuera del intervalo en nDCG@25, y **descartan menos**:
   la curaduría tira el 29% del top-30 de A y el 22-24% del de B, D y E.
2. **El léxico solo (D) es el mejor sistema individual**, y el denso con *truncar* (B) le
   sigue de cerca. Con un modelo de 128 tokens, quedarse con el encabezado estructurado
   + el arranque de la descripción (B) rinde más que partir la descripción en oraciones
   (C): los chunks de oraciones traen avisos que hablan de lo mismo pero no son
   comparables. Es exactamente lo contrario de lo que decía doc 18 §3.2.
3. **La fusión (E) hereda lo peor de C en nDCG pero tiene el mejor bpref por lejos**
   (0,534 contra 0,44-0,46): pone arriba más de lo juzgado relevante en relación con
   lo juzgado irrelevante, aunque el orden fino sea peor. De ahí la fila H: fusionar
   el denso que funciona (B) con el léxico.
4. Las 34 consultas de informes pasados (sin texto libre) y las 59 de avisos (con la
   descripción como notas) cuentan la misma historia: no es un artefacto del tipo de
   consulta.

**Criterio (c), el MdAPE.** El backtest de `VIGENTES` no pasaba por el nodo 2 (elegía
comparables por superficie), así que ningún recuperador podía moverle el número. Se
agregó `--seleccion A|E` (doc 09 §3.2 bis): los mismos casos, los comparables que
trae cada recuperador, el mismo motor después.

```
VIGENTES · 300 casos sorteados por semilla · casos con sujeto: 183 / 173 / 176

                          semilla 42   43      44      media   amplitud
superficie (300 casos)      23,1%    23,8%   22,1%    23,0%   1,7 pp
A  SQL + recencia           22,7%    21,4%   22,8%    22,3%   1,4 pp
E  híbrido                  22,1%    20,2%   20,4%    20,9%   1,8 pp
```

E le gana a A en las tres semillas (0,6 · 1,2 · 2,4 pp): del tamaño del ruido, pero
consistente en signo. Con el doble de casos, y la línea base por superficie recortada
a los MISMOS casos con sujeto:

```
VIGENTES · 600 casos sorteados por semilla · casos con sujeto: 356 / 337 / 354

                          semilla 42   43      44      media   amplitud
superficie (mismos casos)   22,6%    24,8%   24,1%    23,9%   2,2 pp
A  SQL + recencia           23,6%    24,0%   23,3%    23,6%   0,7 pp
D  léxico                   20,4%    22,2%   20,9%    21,2%   1,8 pp
E  híbrido C + D            21,4%    22,2%   20,5%    21,4%   1,7 pp
H  híbrido B + D            21,4%    21,7%   22,0%    21,7%   0,7 pp
```

E mejora sobre A en las tres semillas por 2,2 · 1,8 · 2,8 pp: más que la amplitud
entre semillas de cualquiera de los dos. El recuperador de hoy (A) no le gana a la
selección por superficie (23,6 contra 23,9); cualquier recuperador con puntaje sí, y
**entre D, E y H el MdAPE no distingue** (21,2-21,7, dentro del ruido). El precio final
se decide por la mediana robusta de 25 comparables: con que el top-25 traiga
suficientes comparables buenos alcanza, y los tres lo hacen. Lo que sí distingue entre
ellos es el orden (nDCG, MRR) y cuánto tira la curaduría: eso lo dice la tabla de
arriba.

**Criterio (doc 18 §4.4), leído contra la tabla:** (a) E supera a A en nDCG@25 fuera
del intervalo ✓; (b) el % descartado baja (29% → 24%, intervalo de la Δ sin cero) ✓;
(c) el MdAPE no empeora ✓ (mejora en las tres semillas). **`semantic.enabled` pasa a
`true`.** Con qué `modo` y qué chunker lo dice el resto de la tabla (F, H): ⏳.

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
