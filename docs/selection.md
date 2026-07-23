# Selección: cuántos clips salen y cuáles

Dos mandos distintos que se confunden todo el tiempo:

- **El piso de calidad decide CUÁNTOS.** `selection.quality_floor`.
- **Los pesos deciden CUÁL.** `selection.profile` y `selection.weights`.

Si te salen pocos Shorts, el dial es el piso — no los pesos. Si te salen los
clips equivocados, son los pesos — no el piso.

## El piso de calidad

`quality_floor` descarta toda candidata que puntúe por debajo de esa fracción
de **la mejor candidata de ese mismo video**. Es relativo al video, no
absoluto: una grabación con un solo momento fuerte rinde **un** Short, no tres.

Es agresivo a propósito. Medido en datos sintéticos sobrevive 1 de cada 11
candidatas, en ambos perfiles: **sin marcar, cuenta con ~1 Short por grabación**.
Lo que marcaste tú queda exento, así que el volumen real depende de cuánto
marques al grabar.

Es el primer número que hay que revisar con material propio — la distribución
real puede abrirse o comprimirse distinto. Si el volumen se queda corto, baja
`quality_floor` (por ejemplo a `0.40`). Ponlo en `0` para desactivarlo.

## Los pesos, por perfil

`selection.profile` fija una calibración base y `selection.weights` la ajusta
señal a señal. Cada número es **lo máximo que esa señal mueve la puntuación**,
así que se comparan entre sí directamente.

| Señal | Qué mide | `comentario` | `gaming` |
| --- | --- | --- | --- |
| `energy` | Picos de audio | `0.12` | `0.30` |
| `pace` | Ritmo de habla vs. la mediana del video | `0.15` | `0.20` |
| `hook` | Palabras gancho en los primeros 8 s | `0.35` | `0.30` |
| `punct` | Preguntas y exclamaciones | `0.15` | `0.15` |
| `closes` | El clip termina cerrando la idea | `0.28` | `0.18` |
| `density` | Palabras con contenido, no relleno | `0.22` | `0.12` |
| `filler` | Penalización: arranca con muletilla | `0.15` | `0.12` |
| `gaps` | Penalización: silencios muertos | `0.40` | `0.40` |
| `mark` | Lo marcaste tú al grabar | `0.50` | `0.50` |

**`comentario`** — charla tranquila: análisis, podcast, tutorial. El volumen
dice poco; manda cerrar la idea y la densidad.

**`gaming`** — reacciones y streams: los picos de audio sí señalan el momento.

Para ajustar una señal suelta sin cambiar de perfil:

```yaml
selection:
  profile: "comentario"
  weights:
    energy: 0.08
    closes: 0.32
```

## Afinar con datos, no con intuición

`report` trae vistas y likes de tus Shorts publicados, y una sección **"Qué está
funcionando"**: vistas medias por duración del clip, por tipo de gancho del
título y según si lo marcaste tú o lo eligió el bot. Ajusta los pesos hacia
donde apunten esos números.

Un resumen de tres líneas sale también como cabecera de `review` —donde
decides—, y solo con muestra suficiente: por debajo de ~6 publicados no se
muestra ninguna comparación, porque un promedio con n=3 sesga la decisión justo
cuando más pesa.

> **Cómo NO leerlo**: que "marcados por ti" rinda mejor no prueba que marcar
> mejore el rendimiento. Marcas los que ya te parecen buenos, así que la marca y
> la calidad salen del mismo sitio: tu criterio. Eso es selección, no
> causalidad — mide tu ojo, no el sistema de marcas.

## Dónde NO está el problema

Sin marcas, la heurística funciona mejor con contenido hablado y con ideas que
cierran. Con marcas, funciona con lo que tú decidas. Si publicas relleno, el
problema casi nunca está en los pesos: está arriba, en cómo se grabó. Ver
[Grabar en beats](grabar-en-beats.md).

El selector se queda simple a propósito: no modela arcos narrativos ni persigue
la viralidad a punta de heurística. Esa decisión está registrada en
[ADR-0001](adr/0001-extremos-apretados-centro-simple.md).
