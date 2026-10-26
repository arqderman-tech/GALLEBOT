"""
precios_store.py
================
Único lugar que sabe cómo se guarda el histórico de precios.

El histórico ya NO es un solo archivo. Está partido en archivos numerados que
nunca pasan de MAX_BYTES (GitHub rechaza archivos de más de 100 MB y avisa
desde 50 MB):

    data/precios/precios_0001.csv
    data/precios/precios_0002.csv      <- parte activa (la última)
    data/precios/manifest.json         <- resumen de las partes (para web / JS)

Reglas (ver DATOS_PARTICIONADOS.md):
  1. Ningún script lee ni escribe esos CSV directo. Todos pasan por este módulo.
  2. Leer  -> leer_todo()                      (todas las partes juntas)
  3. Grabar -> guardar_dia(df_dia, "YYYYMMDD") (reemplaza ese día si ya estaba)
  4. Un día siempre queda entero en UNA parte; si no entra en la activa se abre
     la siguiente. Las partes cerradas no se vuelven a tocar (salvo que se
     regrabe un día viejo), así git no vuelve a guardar 50 MB todos los días.

Uso por consola (desde la raíz del repo):
    python precios_store.py estado
"""

import json
import os
import re
import sys
from pathlib import Path

import pandas as pd

DIR_DATA = Path("data")
DIR_PRECIOS = DIR_DATA / "precios"
MANIFEST = DIR_PRECIOS / "manifest.json"
LEGADO = DIR_DATA / "precios_compacto.csv"      # formato viejo (un solo archivo)

# Tope por parte. GitHub bloquea a 100 MB y avisa a partir de 50 MB.
MAX_BYTES = 50 * 1024 * 1024

COLUMNAS = ["ean", "nombre", "categoria", "cat_principal", "precio", "fecha"]
DTYPES = {"ean": str, "fecha": str}

_PATRON = re.compile(r"^precios_(\d{4})\.csv$")


# ── helpers ──────────────────────────────────────────────────────────────────
def _nombre_parte(n: int) -> Path:
    return DIR_PRECIOS / f"precios_{n:04d}.csv"


def listar_partes() -> list[Path]:
    """Partes existentes, ordenadas de la más vieja a la activa."""
    if not DIR_PRECIOS.exists():
        return []
    return sorted(p for p in DIR_PRECIOS.iterdir() if _PATRON.match(p.name))


def _numero(p: Path) -> int:
    return int(_PATRON.match(p.name).group(1))


def _leer_parte(p: Path) -> pd.DataFrame:
    return pd.read_csv(p, dtype=DTYPES)


def _bytes_csv(df: pd.DataFrame, header: bool) -> int:
    return len(df.to_csv(index=False, header=header).encode("utf-8"))


def _escribir_atomico(df: pd.DataFrame, destino: Path) -> None:
    tmp = destino.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, destino)


def _validar(df: pd.DataFrame) -> pd.DataFrame:
    faltan = [c for c in COLUMNAS if c not in df.columns]
    if faltan:
        raise ValueError(f"Al histórico le faltan columnas: {faltan}")
    df = df[COLUMNAS].copy()
    df["ean"] = df["ean"].astype(str)
    df["fecha"] = df["fecha"].astype(str)
    return df


def _escribir_manifest(partes_df: list[tuple[Path, pd.DataFrame]]) -> None:
    info = []
    for p, df in partes_df:
        info.append({
            "archivo": p.name,
            "filas": int(len(df)),
            "bytes": p.stat().st_size,
            "fecha_min": df["fecha"].min() if len(df) else None,
            "fecha_max": df["fecha"].max() if len(df) else None,
        })
    DIR_PRECIOS.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps({"max_bytes": MAX_BYTES, "partes": info},
                   ensure_ascii=False, indent=2), encoding="utf-8")


# ── API pública ──────────────────────────────────────────────────────────────
def hay_datos() -> bool:
    return bool(listar_partes())


def hay_legado() -> bool:
    return LEGADO.exists()


def leer_todo() -> pd.DataFrame:
    """Todo el histórico (todas las partes), con ean y fecha como texto."""
    partes = listar_partes()
    if not partes:
        return pd.DataFrame(columns=COLUMNAS)
    return pd.concat([_leer_parte(p) for p in partes], ignore_index=True)


def reescribir_todo(df: pd.DataFrame) -> pd.DataFrame:
    """Reparte TODO el histórico en partes desde cero (migración / reconstrucción).
    Un día nunca se corta entre dos partes."""
    df = _validar(df).sort_values("fecha", kind="stable").reset_index(drop=True)
    DIR_PRECIOS.mkdir(parents=True, exist_ok=True)

    partes_nuevas: list[list[pd.DataFrame]] = [[]]
    usados = _bytes_csv(df.iloc[0:0], header=True)
    for _, dia in df.groupby("fecha", sort=True):
        peso = _bytes_csv(dia, header=False)
        if partes_nuevas[-1] and usados + peso > MAX_BYTES:
            partes_nuevas.append([])
            usados = _bytes_csv(df.iloc[0:0], header=True)
        partes_nuevas[-1].append(dia)
        usados += peso

    for p in listar_partes():
        p.unlink()
    escritas = []
    for i, dias in enumerate(partes_nuevas, start=1):
        parte = pd.concat(dias, ignore_index=True) if dias else df.iloc[0:0]
        destino = _nombre_parte(i)
        _escribir_atomico(parte, destino)
        escritas.append((destino, parte))
    _escribir_manifest(escritas)
    print(f"  [store] {len(df)} filas en {len(escritas)} parte(s) "
          f"(tope {MAX_BYTES // 1024 // 1024} MB c/u)")
    return df


def migrar_legado(borrar: bool = True) -> pd.DataFrame:
    """Convierte data/precios_compacto.csv (un solo archivo) a partes.
    Por defecto borra el archivo viejo para que el commit lo elimine."""
    print(f"  [store] Migrando {LEGADO} a partes ...")
    df = reescribir_todo(pd.read_csv(LEGADO, dtype=DTYPES))
    if borrar:
        LEGADO.unlink()
        print(f"  [store] Borrado {LEGADO}")
    return df


def guardar_dia(df_dia: pd.DataFrame, fecha: str) -> pd.DataFrame:
    """Guarda el snapshot de un día. Si ese día ya estaba, lo reemplaza.
    Devuelve el histórico completo ya actualizado."""
    df_dia = _validar(df_dia)
    df_dia["fecha"] = fecha

    if not hay_datos() and hay_legado():
        migrar_legado()          # nunca arrancar de cero si había histórico

    DIR_PRECIOS.mkdir(parents=True, exist_ok=True)
    partes = listar_partes()
    cargadas: list[tuple[Path, pd.DataFrame]] = []

    # 1) sacar el día de la parte donde estuviera (normalmente la activa)
    for p in partes:
        df = _leer_parte(p)
        if (df["fecha"] == fecha).any():
            df = df[df["fecha"] != fecha]
            _escribir_atomico(df, p)
        cargadas.append((p, df))

    # 2) elegir parte: la activa si el día entra; si no, abrir la siguiente
    peso = _bytes_csv(df_dia, header=False)
    if cargadas:
        activa, df_activa = cargadas[-1]
        if len(df_activa) and activa.stat().st_size + peso > MAX_BYTES:
            nueva = _nombre_parte(_numero(activa) + 1)
            cargadas.append((nueva, df_activa.iloc[0:0]))
    else:
        cargadas.append((_nombre_parte(1), df_dia.iloc[0:0]))
    destino, df_destino = cargadas[-1]

    # 3) agregar el día a esa parte
    df_destino = pd.concat([df_destino, df_dia], ignore_index=True)
    if peso > MAX_BYTES:
        print(f"  [store] AVISO: el día {fecha} pesa más que el tope de una parte")
    _escribir_atomico(df_destino, destino)
    cargadas[-1] = (destino, df_destino)

    _escribir_manifest(cargadas)
    total = sum(len(d) for _, d in cargadas)
    print(f"  [store] {destino.name}: {len(df_destino)} filas "
          f"({destino.stat().st_size / 1024 / 1024:.1f} MB) · "
          f"histórico total {total} filas en {len(cargadas)} parte(s)")
    return pd.concat([d for _, d in cargadas], ignore_index=True)


def estado() -> None:
    partes = listar_partes()
    if not partes:
        print("Sin partes." + (" Hay archivo viejo para migrar." if hay_legado() else ""))
        return
    for p in partes:
        df = _leer_parte(p)
        print(f"{p.name}: {len(df):>8} filas  {p.stat().st_size / 1024 / 1024:6.1f} MB  "
              f"{df['fecha'].min()} → {df['fecha'].max()}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "estado":
        estado()
    else:
        print(__doc__)
