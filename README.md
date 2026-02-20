# 🛒 GALLEGABOT — Tracker de Precios La Gallega

Seguimiento diario automático de precios del supermercado La Gallega (lagallega.com.ar).

## 🌐 Ver la web
👉 **https://arqderman-tech.github.io/GALLEGABOT/**

## ¿Qué hace?
- Scrapea precios todos los días a las 9:00 AM (Argentina)
- Detecta subas y bajas por producto y categoría
- Muestra gráficos históricos de evolución de precios
- Rankings de los productos que más subieron/bajaron

## Categorías relevadas
- Almacén
- Bebidas
- Carnicería
- Congelados
- Lácteos y Frescos
- Frutas y Verduras
- Limpieza
- Perfumería

## Estructura
```
├── lagallega_scraper.py        # Scraper con Playwright
├── analizar_precios_gallega.py # Procesamiento y JSONs
├── generar_web_gallega.py      # Genera el HTML
├── data/
│   ├── output/                 # CSVs diarios crudos
│   ├── precios_compacto.csv    # Histórico unificado
│   ├── resumen.json
│   ├── graficos.json
│   └── ranking_*.json
├── docs/
│   └── index.html              # GitHub Pages
└── .github/workflows/
    ├── scraper_diario.yml      # Corre todos los días a las 9AM
    └── regenerar_web.yml       # Manual — solo regenera la web
```

## Correr manualmente
En **Actions** → **Scraper Diario La Gallega** → **Run workflow**

Para solo regenerar la web sin scrapear:
**Actions** → **Regenerar Web La Gallega** → **Run workflow**
