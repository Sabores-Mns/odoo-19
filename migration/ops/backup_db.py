"""Genera un respaldo restaurable de la base migrada.

Usa el endpoint nativo /web/database/backup de Odoo, no pg_dump, para que el
zip incluya el filestore y el manifest.json y se pueda restaurar tal cual desde
el gestor de bases (http://localhost:10020/web/database/manager).

Un pg_dump suelto no sirve: al restaurarlo faltarían los adjuntos y Odoo se
quejaría de las versiones de los módulos.

Uso:
  python /migration/ops/backup_db.py             # sólo genera el zip
  python /migration/ops/backup_db.py --verify    # además lo restaura como
                                                 # prueba19 y la borra
"""
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "etl"))

from config import EXPORT_DIR, ODOO  # noqa: E402

# Contraseña maestra del gestor de bases (admin_passwd en etc/odoo.conf).
MASTER_PWD = os.getenv("ODOO_MASTER_PWD", "admin")

# Base temporal para la prueba de restauración.
TEST_DB = os.getenv("BACKUP_TEST_DB", "prueba19")

TIMEOUT = int(os.getenv("BACKUP_TIMEOUT", "3600"))


class _Encadenado:
    """Lee cabecera, luego el archivo, luego la cola, sin juntarlo en memoria.

    El zip de una base migrada ronda los cientos de MB; construir el cuerpo
    multipart como un solo bytes lo duplicaría en RAM.
    """

    def __init__(self, cabecera, fh, cola):
        self._partes = [cabecera, fh, cola]
        self._i = 0

    def read(self, n=-1):
        while self._i < len(self._partes):
            parte = self._partes[self._i]
            if hasattr(parte, "read"):
                trozo = parte.read(n)
            else:
                trozo = parte
                self._partes[self._i] = b""
            if trozo:
                return trozo
            self._i += 1
        return b""


def _post(path, campos, archivo=None, timeout=TIMEOUT):
    """POST al gestor de bases. Con `archivo` va como multipart/form-data."""
    url = f"{ODOO['url']}{path}"
    if archivo is None:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(campos).encode())
        return urllib.request.urlopen(req, timeout=timeout)

    frontera = uuid.uuid4().hex
    nombre, ruta = archivo
    cabecera = b""
    for clave, valor in campos.items():
        cabecera += (
            f"--{frontera}\r\n"
            f'Content-Disposition: form-data; name="{clave}"\r\n\r\n'
            f"{valor}\r\n"
        ).encode()
    cabecera += (
        f"--{frontera}\r\n"
        f'Content-Disposition: form-data; name="{nombre}"; '
        f'filename="{os.path.basename(ruta)}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    cola = f"\r\n--{frontera}--\r\n".encode()

    tam = os.path.getsize(ruta)
    with open(ruta, "rb") as fh:
        req = urllib.request.Request(url, data=_Encadenado(cabecera, fh, cola))
        req.add_header("Content-Type", f"multipart/form-data; boundary={frontera}")
        req.add_header("Content-Length", str(len(cabecera) + tam + len(cola)))
        return urllib.request.urlopen(req, timeout=timeout)


def backup(destino):
    print(f"Respaldando {ODOO['db']} desde {ODOO['url']}...")
    t0 = time.time()
    try:
        resp = _post("/web/database/backup", {
            "master_pwd": MASTER_PWD,
            "name": ODOO["db"],
            "backup_format": "zip",
        })
    except urllib.error.HTTPError as exc:
        sys.exit(f"ERROR: el gestor rechazó la petición ({exc.code}). "
                 f"¿Es correcta la contraseña maestra (admin_passwd)?")

    # Si la contraseña falla, Odoo responde 200 con una página HTML de error.
    if "html" in resp.headers.get("Content-Type", ""):
        sys.exit("ERROR: el gestor devolvió HTML en vez del zip. Suele ser "
                 "contraseña maestra incorrecta o base inexistente.")

    os.makedirs(os.path.dirname(destino), exist_ok=True)
    escritos = 0
    with open(destino, "wb") as fh:
        while True:
            trozo = resp.read(1024 * 1024)
            if not trozo:
                break
            fh.write(trozo)
            escritos += len(trozo)
            if escritos % (100 * 1024 * 1024) < 1024 * 1024:
                print(f"  {escritos / (1024 * 1024):,.0f} MB...")

    print(f"Escrito {destino} ({escritos / (1024 * 1024):,.1f} MB) "
          f"en {time.time() - t0:.0f}s")

    # Un zip truncado se detecta ahora, no el día que haga falta restaurarlo.
    with zipfile.ZipFile(destino) as z:
        nombres = z.namelist()
        corrupta = z.testzip()
    if corrupta:
        sys.exit(f"ERROR: el zip está corrupto (entrada {corrupta}).")
    for esperado in ("dump.sql", "manifest.json"):
        if esperado not in nombres:
            sys.exit(f"ERROR: el respaldo no contiene {esperado}; no es "
                     f"restaurable desde el gestor de Odoo.")
    print(f"  contenido verificado: {len(nombres)} entradas, "
          f"dump.sql y manifest.json presentes")
    return destino


def drop(nombre, silencioso=False):
    try:
        _post("/web/database/drop", {"master_pwd": MASTER_PWD, "name": nombre},
              timeout=300)
        if not silencioso:
            print(f"  base de prueba '{nombre}' eliminada")
    except urllib.error.HTTPError:
        if not silencioso:
            print(f"  [AVISO] no se pudo eliminar '{nombre}'; bórrala a mano "
                  f"desde el gestor de bases.")


def existe_db(nombre):
    """¿Aparece `nombre` en la lista del gestor de bases?

    /web/database/list habla JSON-RPC, no form-urlencoded: mandarlo como
    formulario devuelve 415, la excepción se tragaba y la comprobación daba
    siempre "no existe", convirtiendo una restauración correcta en un fallo.
    """
    try:
        req = urllib.request.Request(
            f"{ODOO['url']}/web/database/list",
            data=json.dumps({"jsonrpc": "2.0", "method": "call",
                             "params": {}}).encode(),
            headers={"Content-Type": "application/json"})
        cuerpo = urllib.request.urlopen(req, timeout=60).read()
        return f'"{nombre}"' in cuerpo.decode("utf-8", "replace")
    except Exception:                                 # noqa: BLE001
        return False


def restore_test(origen):
    """Restaura el zip como TEST_DB y la borra: prueba de que sirve de verdad."""
    print(f"\nProbando la restauración como '{TEST_DB}'...")
    drop(TEST_DB, silencioso=True)
    t0 = time.time()
    try:
        _post("/web/database/restore", {
            "master_pwd": MASTER_PWD,
            "name": TEST_DB,
            # copy=true genera un uuid nuevo: la copia no compite con la
            # original si alguna vez ambas apuntan al mismo Odoo Online.
            "copy": "true",
        }, archivo=("backup_file", origen))
    except urllib.error.HTTPError as exc:
        sys.exit(f"ERROR: la restauración de prueba falló ({exc.code}).")
    except (http.client.RemoteDisconnected, urllib.error.URLError) as exc:
        # Odoo corta la conexión HTTP durante el restore (recicla los workers),
        # pero el trabajo SIGUE en el servidor: la base tarda todavía un par de
        # minutos en aparecer. Comprobarlo una sola vez al recibir el corte da
        # un falso negativo —la base aún no existe—, así que se espera.
        print(f"  (Odoo cerró la conexión: {type(exc).__name__}; "
              f"esperando a que termine en el servidor)")
        for i in range(60):
            time.sleep(10)
            if existe_db(TEST_DB):
                break
            if i and i % 6 == 0:
                print(f"    ...{(i + 1) * 10}s")
        else:
            sys.exit(f"ERROR: la restauración de prueba no apareció "
                     f"tras 10 minutos ({exc}).")
    print(f"  restaurada en {time.time() - t0:.0f}s")
    drop(TEST_DB)
    print("Respaldo verificado: se restaura correctamente.")


def main():
    destino = os.path.join(EXPORT_DIR, f"{ODOO['db']}.zip")
    backup(destino)
    if "--verify" in sys.argv:
        restore_test(destino)
    print("\nPara restaurarlo: http://localhost:10020/web/database/manager "
          "-> Restore Database")


if __name__ == "__main__":
    main()
