"""Restaura el respaldo .bak de Profit como base MULTI_A en SQL Server.

En los proyectos anteriores este paso estaba sólo documentado en MIGRACION.md y
se ejecutaba a mano con sqlcmd. Aquí se automatiza para que run-migration lo
haga sin intervención.

Qué hace:
  1. Localiza el único *.bak dentro de migration/db/ (cualquier nombre sirve).
  2. Lee los nombres lógicos reales con RESTORE FILELISTONLY, en vez de asumir
     GLOBAL_A / GLOBAL_A_log. Así un respaldo de otra empresa Profit funciona
     sin tocar configuración.
  3. Ejecuta RESTORE DATABASE ... WITH MOVE ..., REPLACE.
  4. Comprueba que la base quedó accesible y con las tablas esperadas.

Uso:
  python /migration/etl/restore_mssql.py            # no hace nada si ya existe
  python /migration/etl/restore_mssql.py --force    # restaura otra vez
"""
import glob
import os
import sys
import time

import pytds

from config import (
    DB_DIR,
    MSSQL,
    MSSQL_BACKUP_DIR,
    MSSQL_LOGICAL_DATA,
    MSSQL_LOGICAL_LOG,
)

# Tablas que la migración necesita sí o sí. Sirven de comprobación de que el
# respaldo restaurado es realmente una base de Profit y no otra cosa.
TABLAS_CLAVE = ["clientes", "factura", "reng_fac", "docum_cc", "cobros", "reng_cob"]

DATA_DIR = "/var/opt/mssql/data"


def connect(database="master", timeout=10):
    """Conexión con autocommit: RESTORE no puede correr dentro de transacción."""
    return pytds.connect(
        MSSQL["server"], database, MSSQL["user"], MSSQL["password"],
        port=MSSQL["port"], autocommit=True, login_timeout=timeout,
    )


def wait_for_sqlserver(intentos=40, espera=5):
    """SQL Server tarda en aceptar conexiones tras arrancar el contenedor."""
    for i in range(1, intentos + 1):
        try:
            with connect() as conn:
                conn.cursor().execute("SELECT 1")
            print(f"SQL Server responde (intento {i})")
            return
        except Exception as exc:                      # noqa: BLE001
            if i == intentos:
                sys.exit(f"ERROR: SQL Server no respondió tras "
                         f"{intentos * espera}s: {exc}")
            if i == 1 or i % 5 == 0:
                print(f"  esperando a SQL Server... ({i}/{intentos})")
            time.sleep(espera)


def find_backup():
    """El .bak que el usuario dejó en migration/db/."""
    baks = sorted(glob.glob(os.path.join(DB_DIR, "*.bak")))
    if not baks:
        sys.exit(
            f"ERROR: no hay ningún archivo .bak en {DB_DIR}\n"
            f"       Copia ahí el respaldo de Profit. Ver {DB_DIR}/README.md"
        )
    if len(baks) > 1:
        nombres = "\n         ".join(os.path.basename(b) for b in baks)
        sys.exit(
            f"ERROR: hay {len(baks)} archivos .bak en {DB_DIR}:\n"
            f"         {nombres}\n"
            f"       Deja sólo el que quieras migrar."
        )
    local = baks[0]
    # Ruta tal como la ve el contenedor de SQL Server (bind mount ./db:/backups).
    remoto = f"{MSSQL_BACKUP_DIR}/{os.path.basename(local)}"
    size_mb = os.path.getsize(local) / (1024 * 1024)
    print(f"Respaldo: {os.path.basename(local)} ({size_mb:,.0f} MB) -> {remoto}")
    return remoto


def database_exists(nombre):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DB_ID(%s)", (nombre,))
        return cur.fetchone()[0] is not None


def logical_names(backup):
    """Nombres lógicos reales dentro del .bak (Type: D=datos, L=log)."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"RESTORE FILELISTONLY FROM DISK = '{backup}'")
        cols = [d[0] for d in cur.description]
        i_name, i_type = cols.index("LogicalName"), cols.index("Type")
        data = log = None
        for row in cur:
            tipo = str(row[i_type]).strip().upper()
            if tipo == "D" and data is None:
                data = row[i_name]
            elif tipo == "L" and log is None:
                log = row[i_name]
    if not data or not log:
        print(f"[WARN] FILELISTONLY incompleto; uso los valores de config: "
              f"{MSSQL_LOGICAL_DATA} / {MSSQL_LOGICAL_LOG}")
        return MSSQL_LOGICAL_DATA, MSSQL_LOGICAL_LOG
    print(f"Archivos lógicos detectados: {data} (datos), {log} (log)")
    return data, log


def restore(backup, destino):
    data, log = logical_names(backup)
    sql = (
        f"RESTORE DATABASE [{destino}] FROM DISK = '{backup}' WITH "
        f"MOVE '{data}' TO '{DATA_DIR}/{destino}.mdf', "
        f"MOVE '{log}' TO '{DATA_DIR}/{destino}_log.ldf', "
        f"REPLACE, STATS = 10"
    )
    print(f"Restaurando {destino}... (puede tardar varios minutos)")
    t0 = time.time()
    with connect(timeout=30) as conn:
        conn.cursor().execute(sql)
    print(f"RESTORE completado en {time.time() - t0:.0f}s")


def verify(destino):
    """La base existe, tiene las tablas de Profit y filas dentro."""
    with connect(destino) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sys.tables WHERE name IN "
            "(" + ",".join(f"'{t}'" for t in TABLAS_CLAVE) + ")"
        )
        encontradas = {r[0].lower() for r in cur}
        faltan = [t for t in TABLAS_CLAVE if t.lower() not in encontradas]
        if faltan:
            sys.exit(f"ERROR: la base {destino} no parece de Profit; "
                     f"faltan las tablas: {', '.join(faltan)}")

        print(f"\nBase {destino} verificada:")
        for tabla in TABLAS_CLAVE:
            cur.execute(f"SELECT COUNT(*) FROM [{tabla}]")
            print(f"  {tabla:<12} {cur.fetchone()[0]:>8,} filas")


def main():
    force = "--force" in sys.argv
    destino = MSSQL["database"]

    wait_for_sqlserver()

    if database_exists(destino) and not force:
        print(f"La base {destino} ya existe; no se restaura. "
              f"Usa --force para rehacerla.")
        verify(destino)
        return

    restore(find_backup(), destino)
    verify(destino)
    print("\nrestore_mssql OK")


if __name__ == "__main__":
    main()
