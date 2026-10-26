"""
analizar_precios_gallega.py
============================
Lee los CSVs de data/output/ y genera los JSONs para la web.

- Una fila por producto por día en data/precios/precios_NNNN.csv (partido en
  archivos de máx. 50 MB; todo el acceso pasa por precios_store.py)
- Índices % acumulados día a día
- Comparaciones vs día/7d/30d/6m/1y
- Categorías principales
"""

import json
import glob
import sys
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

import precios_store as store

DIR_DATA = Path("data")

ORDEN_CATS = [
    "Almacén",
    "Bebidas",
    "Frescos",
    "Congelados",
    "Limpieza",
    "Cuidado Personal",
]

PERIODOS = {"7d": 7, "30d": 30, "6m": 180, "1y": 365}


# ── CARGA ────────────────────────────────────────────────────────────────────
def cargar_csvs_hoy():
    hoy = datetime.now().strftime("%Y%m%d")
    patron = f"data/output/gallega_{hoy}*.csv"
    dfs = []
    for archivo in glob.glob(patron):
        try:
            df = pd.read_csv(archivo, encoding="utf-8-sig")
            dfs.append(df)
            print(f"  Cargado: {archivo} ({len(df)} prods)")
        except Exception as e:
            print(f"  ERROR: {archivo}: {e}")
    if not dfs:
        print("ERROR: No se encontraron CSVs de hoy.")
        return None
    return pd.concat(dfs, ignore_index=True)


def preparar_df_dia(df_raw, fecha_str):
    df = df_raw.copy()
    df["precio"] = pd.to_numeric(df["precio"], errors="coerce")
    df = df.dropna(subset=["precio"])
    df = df[df["precio"] > 0]
    df["ean"] = df["ean"].astype(str)
    df = df.drop_duplicates(subset=["ean"], keep="first")
    df["fecha"] = fecha_str
    if "cat_principal" not in df.columns:
        df["cat_principal"] = df["categoria"]
    return df[["ean", "nombre", "categoria", "cat_principal", "precio", "fecha"]]


# ── ALMACENAMIENTO ────────────────────────────────────────────────────────────
# El histórico vive en data/precios/ (ver precios_store.py y DATOS_PARTICIONADOS.md).
def guardar_compacto(df_dia, fecha_str):
    """Guarda el día (reemplazándolo si ya estaba) y devuelve todo el histórico."""
    DIR_DATA.mkdir(parents=True, exist_ok=True)
    return store.guardar_dia(df_dia, fecha_str)


def reconstruir_historico():
    """Rearma todo el histórico desde los CSV diarios de data/output/.
    Sirve si se perdió el histórico: los crudos tienen la misma información."""
    por_dia = {}
    for archivo in sorted(glob.glob("data/output/gallega_*.csv")):
        fecha = Path(archivo).name.split("_")[1]
        try:
            df = pd.read_csv(archivo, encoding="utf-8-sig")
        except Exception as e:
            print(f"  ERROR: {archivo}: {e}")
            continue
        if not df.empty:
            por_dia.setdefault(fecha, []).append(df)
    dias = []
    for fecha, dfs in sorted(por_dia.items()):
        d = preparar_df_dia(pd.concat(dfs, ignore_index=True), fecha)
        if not d.empty:
            dias.append(d)
    if not dias:
        return pd.DataFrame(columns=store.COLUMNAS)
    print(f"  Reconstruyendo histórico desde data/output/ ({len(dias)} días)")
    return store.reescribir_todo(pd.concat(dias, ignore_index=True))


def asegurar_historico():
    """Garantiza que exista el histórico en partes, venga de donde venga."""
    if store.hay_datos():
        return
    if store.hay_legado():
        store.migrar_legado()
    else:
        print("  No hay histórico guardado: se reconstruye desde data/output/")
        reconstruir_historico()


# ── COMPARACIÓN ───────────────────────────────────────────────────────────────
def snapshot_anterior(df_hist, fecha_hoy):
    fechas = sorted(df_hist["fecha"].unique(), reverse=True)
    for f in fechas:
        if f < fecha_hoy:
            return df_hist[df_hist["fecha"] == f].copy()
    return None


def snapshot_en_fecha(df_hist, fecha_objetivo):
    fechas = sorted(df_hist["fecha"].unique())
    candidato = None
    for f in fechas:
        if f <= fecha_objetivo:
            candidato = f
    if candidato is None:
        return None
    return df_hist[df_hist["fecha"] == candidato].copy()


def calcular_variacion(df_hoy, df_antes):
    dh = df_hoy[["ean", "nombre", "categoria", "cat_principal", "precio"]].rename(
        columns={"precio": "precio_hoy"})
    da = df_antes[["ean", "precio"]].rename(columns={"precio": "precio_antes"})
    df = pd.merge(dh, da, on="ean", how="inner")
    df = df.dropna(subset=["precio_hoy", "precio_antes"])
    df = df[df["precio_antes"] > 0]
    df["diff_abs"] = (df["precio_hoy"] - df["precio_antes"]).round(2)
    df["diff_pct"] = ((df["diff_abs"] / df["precio_antes"]) * 100).round(2)
    return df


def calcular_variacion_cats(df_var):
    resumen = df_var.groupby("cat_principal").agg(
        variacion_pct_promedio=("diff_pct", "mean"),
        productos_subieron=("diff_pct", lambda x: (x > 0).sum()),
        productos_bajaron=("diff_pct", lambda x: (x < 0).sum()),
        total_productos=("diff_pct", "count"),
    ).reset_index().rename(columns={"cat_principal": "categoria"})
    resumen["variacion_pct_promedio"] = resumen["variacion_pct_promedio"].round(2)
    orden = {c: i for i, c in enumerate(ORDEN_CATS)}
    resumen["_ord"] = resumen["categoria"].map(lambda x: orden.get(x, 999))
    return resumen.sort_values("_ord").drop(columns="_ord")


def top_productos(df_var, n=20, asc=False):
    df = df_var.sort_values("diff_pct", ascending=asc).head(n)
    return df[["ean", "nombre", "categoria", "precio_antes", "precio_hoy", "diff_abs", "diff_pct"]].to_dict("records")


# ── GRÁFICOS ──────────────────────────────────────────────────────────────────
def generar_graficos_data(df_hist):
    if df_hist.empty:
        return {}
    df_hist = df_hist.copy()
    df_hist["fecha_dt"] = pd.to_datetime(df_hist["fecha"], format="%Y%m%d")
    df_hist = df_hist.sort_values(["fecha_dt", "ean"])
    hoy = pd.Timestamp.now().normalize()
    resultado = {}

    for periodo, dias in PERIODOS.items():
        fecha_inicio = hoy - timedelta(days=dias)
        df_p = df_hist[df_hist["fecha_dt"] >= fecha_inicio].copy()
        fechas = sorted(df_p["fecha_dt"].unique())
        if not fechas:
            resultado[periodo] = {"total": [], "categorias": {}}
            continue

        fecha_str_0 = fechas[0].strftime("%Y-%m-%d")
        serie_total = [{"fecha": fecha_str_0, "pct": 0.0}]
        acum = 0.0
        for i in range(1, len(fechas)):
            dv = calcular_variacion(
                df_p[df_p["fecha_dt"] == fechas[i]],
                df_p[df_p["fecha_dt"] == fechas[i - 1]]
            )
            var = float(dv["diff_pct"].mean()) if not dv.empty else 0.0
            acum = round(acum + var, 2)
            serie_total.append({"fecha": fechas[i].strftime("%Y-%m-%d"), "pct": acum})

        series_cats = {}
        for cat in ORDEN_CATS:
            df_cat = df_p[df_p["cat_principal"] == cat]
            if df_cat.empty:
                continue
            serie = [{"fecha": fecha_str_0, "pct": 0.0}]
            acum_cat = 0.0
            for i in range(1, len(fechas)):
                dv = calcular_variacion(
                    df_cat[df_cat["fecha_dt"] == fechas[i]],
                    df_cat[df_cat["fecha_dt"] == fechas[i - 1]]
                )
                var = float(dv["diff_pct"].mean()) if not dv.empty else 0.0
                acum_cat = round(acum_cat + var, 2)
                serie.append({"fecha": fechas[i].strftime("%Y-%m-%d"), "pct": acum_cat})
            series_cats[cat] = serie

        resultado[periodo] = {"total": serie_total, "categorias": series_cats}

    return resultado


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    solo_graficos = "--solo-graficos" in sys.argv
    if "--reconstruir-historico" in sys.argv:
        reconstruir_historico()
        return
    if "--migrar-legado" in sys.argv:
        if store.hay_legado():
            store.migrar_legado()
        else:
            print(f"No existe {store.LEGADO}")
        return
    print(f"\n{'='*60}")
    print(f"  ANÁLISIS LA GALLEGA — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if solo_graficos:
        print("  MODO: solo gráficos")
    print(f"{'='*60}\n")

    fecha_hoy = datetime.now().strftime("%Y%m%d")
    DIR_DATA.mkdir(parents=True, exist_ok=True)

    if solo_graficos:
        asegurar_historico()
        df_hist = store.leer_todo()
        if df_hist.empty:
            print("ERROR: No hay histórico de precios")
            return
        fecha_hoy = sorted(df_hist["fecha"].unique())[-1]
        df_dia = df_hist[df_hist["fecha"] == fecha_hoy].copy()
        print(f"  Usando fecha más reciente: {fecha_hoy} ({len(df_dia)} prods)")
    else:
        print("[1/5] Cargando CSVs de hoy ...")
        df_raw = cargar_csvs_hoy()
        if df_raw is None:
            return
        df_dia = preparar_df_dia(df_raw, fecha_hoy)
        asegurar_historico()
        print("\n[2/5] Guardando histórico ...")
        df_hist = guardar_compacto(df_dia, fecha_hoy)

    print("\n[3/5] Calculando variaciones ...")
    resumen = {
        "fecha": fecha_hoy,
        "total_productos": len(df_dia),
        "variacion_dia": None, "variacion_7d": None,
        "variacion_mes": None, "variacion_anio": None,
        "categorias_dia": [],
        "ranking_baja_dia": [],
        "productos_subieron_dia": 0,
        "productos_bajaron_dia": 0,
        "productos_sin_cambio_dia": 0,
    }

    df_ayer = snapshot_anterior(df_hist, fecha_hoy)
    if df_ayer is not None:
        dv = calcular_variacion(df_dia, df_ayer)
        if not dv.empty:
            resumen["variacion_dia"]            = round(float(dv["diff_pct"].mean()), 2)
            resumen["productos_subieron_dia"]   = int((dv["diff_pct"] > 0).sum())
            resumen["productos_bajaron_dia"]    = int((dv["diff_pct"] < 0).sum())
            resumen["productos_sin_cambio_dia"] = int((dv["diff_pct"] == 0).sum())
            resumen["ranking_baja_dia"]         = top_productos(dv, 10, True)
            resumen["categorias_dia"]           = calcular_variacion_cats(dv).to_dict("records")
            print(f"  Variación día: {resumen['variacion_dia']}%")
            with open(DIR_DATA / "ranking_dia.json", "w", encoding="utf-8") as f:
                json.dump(top_productos(dv, 20), f, ensure_ascii=False, indent=2)

    for label, dias, key, fname in [
        ("7d",  7,   "variacion_7d",   "ranking_7d.json"),
        ("30d", 30,  "variacion_mes",  "ranking_mes.json"),
        ("1y",  365, "variacion_anio", "ranking_anio.json"),
    ]:
        f_ref = (datetime.now() - timedelta(days=dias)).strftime("%Y%m%d")
        df_ref = snapshot_en_fecha(df_hist, f_ref)
        if df_ref is not None:
            dv = calcular_variacion(df_dia, df_ref)
            if not dv.empty:
                resumen[key] = round(float(dv["diff_pct"].mean()), 2)
                print(f"  Variación {label}: {resumen[key]}%")
                with open(DIR_DATA / fname, "w", encoding="utf-8") as f:
                    json.dump(top_productos(dv, 20), f, ensure_ascii=False, indent=2)

    print("\n[4/5] Guardando resumen.json ...")
    with open(DIR_DATA / "resumen.json", "w", encoding="utf-8") as f:
        json.dump(resumen, f, ensure_ascii=False, indent=2)

    print("\n[5/5] Generando graficos.json ...")
    graficos = generar_graficos_data(df_hist)
    with open(DIR_DATA / "graficos.json", "w", encoding="utf-8") as f:
        json.dump(graficos, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  LISTO — {resumen['total_productos']} productos")
    for k, v in [("Día", resumen["variacion_dia"]), ("30d", resumen["variacion_mes"]), ("1y", resumen["variacion_anio"])]:
        if v is not None:
            print(f"  {k}: {'📈' if v > 0 else '📉'} {v}%")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
