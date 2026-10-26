# Histórico de precios partido en archivos

## Por qué existe

GitHub rechaza cualquier archivo de más de **100 MB** (y avisa desde 50 MB).
`data/precios_compacto.csv` llegó a 100,18 MB y el push del scraper diario
empezó a fallar. Ahora el histórico se reparte en archivos numerados y se abre
uno nuevo antes de llegar al límite.

## Cómo está guardado

```
data/precios/
├── precios_0001.csv      <- parte cerrada (no se toca más)
├── precios_0002.csv      <- parte cerrada
├── precios_0003.csv      <- parte activa (la de número más alto)
└── manifest.json         <- resumen: archivo, filas, bytes, fecha_min, fecha_max
```

- Mismas columnas que antes, en este orden: `ean, nombre, categoria, cat_principal, precio, fecha`.
- `ean` y `fecha` (formato `YYYYMMDD`) son **texto**, no números.
- Cada parte pesa como máximo `MAX_BYTES` (50 MB, constante en `precios_store.py`).
- **Un día nunca queda cortado entre dos partes.** Si el día de hoy no entra en
  la parte activa, se abre la siguiente y el día entero va ahí.
- Las partes cerradas no se reescriben, así que git no vuelve a subir 50 MB por
  día: solo cambia la parte activa.
- Con ~0,4 MB por día, una parte dura unos 4 meses.

## Regla para todos los `.py`

**Nadie lee ni escribe `data/precios/*.csv` ni `data/precios_compacto.csv` directo.**
Todo pasa por `precios_store.py`:

| Necesito...                              | Uso                                      |
|------------------------------------------|------------------------------------------|
| Todo el histórico en un DataFrame        | `store.leer_todo()`                      |
| Guardar el snapshot de un día            | `store.guardar_dia(df_dia, "YYYYMMDD")`  |
| Saber si ya hay histórico                | `store.hay_datos()`                      |
| Ver el estado de las partes              | `python precios_store.py estado`         |
| Reescribir todo el histórico (migración) | `store.reescribir_todo(df)`              |

```python
import precios_store as store

df_hist = store.leer_todo()                   # todas las partes juntas
df_hist = store.guardar_dia(df_dia, "20261026")   # reemplaza ese día si ya estaba
                                                  # y devuelve el histórico completo
```

### Qué hace `guardar_dia`
1. Si el día ya estaba guardado, lo saca de su parte (así correr dos veces el mismo día no duplica).
2. Si la parte activa + el día superan `MAX_BYTES`, abre `precios_NNNN+1.csv`.
3. Agrega el día, regraba el `manifest.json` y devuelve el histórico completo.
4. Si no hay partes pero existe el archivo viejo `data/precios_compacto.csv`, lo
   migra primero. Nunca arranca de cero si había histórico.

## Scripts que dependen de esto

| Script                          | Qué hace con el histórico                                   |
|---------------------------------|-------------------------------------------------------------|
| `analizar_precios_gallega.py`   | Único que lo usa: `guardar_dia` y `leer_todo`.              |
| `lagallega_scraper.py`          | No lo toca. Escribe CSV crudos en `data/output/`.           |
| `generar_web_gallega.py`        | No lo toca. Lee solo los JSON de `data/`.                   |

**Si agregás un script nuevo que necesite el histórico:** `import precios_store as store`
y usá `store.leer_todo()`. No abras los CSV con `pd.read_csv`.

**Si lo lee algo que no es Python** (una página web, por ejemplo): leé primero
`data/precios/manifest.json` para saber qué partes existen, y después cada una.

## Recuperación

`analizar_precios_gallega.py` se encarga solo (`asegurar_historico()`):

1. Si ya hay partes, no hace nada.
2. Si no hay partes pero existe `data/precios_compacto.csv`, lo migra y lo borra
   (el commit del workflow elimina el archivo grande del repo).
3. Si no hay ni uno ni otro, **reconstruye todo desde `data/output/`**, porque
   los CSV diarios crudos tienen la misma información.

A mano, desde la raíz del repo:

```bash
python analizar_precios_gallega.py --migrar-legado          # un solo archivo -> partes
python analizar_precios_gallega.py --reconstruir-historico  # data/output/ -> partes
```

## Cambiar el tope

Editar `MAX_BYTES` en `precios_store.py`. No subir de ~90 MB: GitHub bloquea a 100 MB.

## Si cambian las columnas

`precios_store.py` exige las columnas de `COLUMNAS`. Si hay que agregar una,
cambiar `COLUMNAS` y `preparar_df_dia()` y luego correr
`--reconstruir-historico` para reescribir todas las partes con el formato nuevo
(las partes viejas no se actualizan solas).
