"""
lagallega_scraper.py
====================
Scraper PARALELIZADO para lagallega.com.ar

Cada categoria corre en su propia pestana del browser AL MISMO TIEMPO.
Con 8 categorias en paralelo, el tiempo total es el de la categoria mas larga
en lugar de la suma de todas (~8x mas rapido).

Uso:
  python lagallega_scraper.py                        # 4 categorias en paralelo
  python lagallega_scraper.py --concurrencia 8       # todas a la vez
  python lagallega_scraper.py --headless false       # ver el browser
"""

import asyncio
import csv
import re
import argparse
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

BASE_URL             = "https://www.lagallega.com.ar"
PRODUCTOS_POR_PAGINA = 50
DELAY_ENTRE_PAGINAS  = 1.0
MAX_REINTENTOS       = 2

CATEGORIAS = [
    {"nombre": "Almacen",           "nl": "03000000"},
    {"nombre": "Bebidas",           "nl": "07000000"},
    {"nombre": "Carniceria",        "nl": "13000000"},
    {"nombre": "Congelados",        "nl": "15000000"},
    {"nombre": "Lacteos Y Frescos", "nl": "08000000"},
    {"nombre": "Frutas Y Verduras", "nl": "19000000"},
    {"nombre": "Limpieza",          "nl": "05000000"},
    {"nombre": "Perfumeria",        "nl": "06000000"},
]

CAT_PRINCIPAL = {
    "Almacen":           "Almacen",
    "Bebidas":           "Bebidas",
    "Carniceria":        "Frescos",
    "Congelados":        "Congelados",
    "Lacteos Y Frescos": "Frescos",
    "Frutas Y Verduras": "Frescos",
    "Limpieza":          "Limpieza",
    "Perfumeria":        "Cuidado Personal",
}


def limpiar_precio(texto):
    if not texto:
        return None
    limpio = re.sub(r"[^\d,]", "", texto).replace(",", ".")
    try:
        return float(limpio)
    except Exception:
        return None


async def extraer_productos(page, categoria):
    productos = []
    try:
        await page.wait_for_selector("ul li img[alt*='-']", timeout=8000)
    except Exception:
        return []
    items = await page.query_selector_all("ul li")
    for item in items:
        try:
            img = await item.query_selector("img[alt*='-']")
            if not img:
                continue
            alt = await img.get_attribute("alt") or ""
            match = re.match(r"^(\d+)\s*-\s*(.+)", alt)
            if not match:
                continue
            ean, nombre = match.group(1), match.group(2).strip()
            texto = await item.inner_text()
            match_p = re.search(r"\$\s*[\d\.,]+", texto)
            precio_texto = match_p.group(0) if match_p else ""
            if ean and precio_texto:
                productos.append({
                    "categoria":     categoria,
                    "cat_principal": CAT_PRINCIPAL.get(categoria, categoria),
                    "nombre":        nombre,
                    "ean":           ean,
                    "precio":        limpiar_precio(precio_texto),
                    "precio_texto":  precio_texto,
                    "fecha":         datetime.now().strftime("%Y%m%d"),
                })
        except Exception:
            continue
    return productos


async def scrapear_categoria(context, cat, semaforo):
    nombre, nl = cat["nombre"], cat["nl"]
    todos, eans_vistos = [], set()

    async with semaforo:
        page = await context.new_page()
        try:
            url_base = f"{BASE_URL}/productosnl.asp?nl={nl}&TM=&PP={PRODUCTOS_POR_PAGINA}"
            await page.goto(url_base, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("ul li img[alt*='-']", timeout=10000)

            content = await page.content()
            match = re.search(r"(\d+)\s+de\s+(\d+)", content)
            pag_total = int(match.group(2)) if match else 1
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] {nombre}: {pag_total} pags", flush=True)

            for p_idx in range(1, pag_total + 1):
                if p_idx > 1:
                    exito = False
                    for selector in [f"a:text-is('{p_idx}')", f"a[href*='P={p_idx}']"]:
                        btn = await page.query_selector(selector)
                        if btn:
                            await btn.click()
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                            except Exception:
                                pass
                            await asyncio.sleep(DELAY_ENTRE_PAGINAS)
                            exito = True
                            break
                    if not exito:
                        print(f"  ! {nombre}: sin boton p{p_idx}", flush=True)
                        break

                prods = []
                for _ in range(MAX_REINTENTOS):
                    prods = await extraer_productos(page, nombre)
                    if prods:
                        break
                    await asyncio.sleep(1.0)

                nuevos = 0
                for p in prods:
                    if p["ean"] not in eans_vistos:
                        eans_vistos.add(p["ean"])
                        todos.append(p)
                        nuevos += 1

                print(f"  {nombre} p{p_idx}/{pag_total} +{nuevos}", flush=True)

        except Exception as e:
            print(f"  ERROR {nombre}: {e}", flush=True)
        finally:
            await page.close()

    print(f"  OK {nombre}: {len(todos)} productos [{datetime.now().strftime('%H:%M:%S')}]", flush=True)
    return todos


async def main(headless, concurrencia):
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = Path("data/output")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"gallega_{ts}.csv"

    print(f"\nLa Gallega Scraper - {datetime.now().strftime('%H:%M:%S')}")
    print(f"{len(CATEGORIAS)} categorias | {concurrencia} en paralelo | PP={PRODUCTOS_POR_PAGINA}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )

        semaforo = asyncio.Semaphore(concurrencia)
        tareas = [scrapear_categoria(context, cat, semaforo) for cat in CATEGORIAS]
        resultados = await asyncio.gather(*tareas)

        await browser.close()

    total_general = [p for r in resultados for p in r]

    campos = ["categoria", "cat_principal", "nombre", "ean", "precio", "precio_texto", "fecha"]
    with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(total_general)

    print(f"\nGuardado: {out_file} ({len(total_general)} productos)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless",     default="true")
    parser.add_argument("--concurrencia", type=int, default=4,
                        help="Categorias en paralelo (default 4, max 8)")
    args = parser.parse_args()
    asyncio.run(main(args.headless == "true", args.concurrencia))