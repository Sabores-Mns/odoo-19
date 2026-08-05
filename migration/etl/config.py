"""Conexiones y rutas de la migración Profit -> Odoo 19.

Todo es sobreescribible por variable de entorno para poder correr los scripts
tanto dentro del contenedor `migration_runner` (rutas /migration/...) como
desde el host durante depuración.

Dentro del contenedor el proyecto se monta completo en /migration, así que:
    /migration/db                 <- el .bak de Profit que deja el usuario
    /migration/export/raw         <- salida de extract.py
    /migration/export/odoo_csv    <- salida de transform.py   (formato v18)
    /migration/export/odoo_csv19  <- salida de transform19.py (formato v19)
    /migration/export/plan        <- planes JSON de conciliación
"""
import os

# Raíz del proyecto: /migration en el contenedor, el padre de etl/ en el host.
BASE_DIR = os.getenv(
    "MIGRATION_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)


def _p(*parts):
    return os.path.join(BASE_DIR, *parts)


# --------------------------------------------------------------- SQL Server
# Origen: backup nativo de Profit Plus restaurado como base MULTI_A.
MSSQL = {
    "server": os.getenv("MSSQL_HOST", "mssql"),
    "port": int(os.getenv("MSSQL_PORT", "1433")),
    "database": os.getenv("MSSQL_DB", "MULTI_A"),
    "user": os.getenv("MSSQL_USER", "sa"),
    "password": os.getenv("MSSQL_PASSWORD", "Profit2Odoo!2026"),
}

# Nombres lógicos dentro del .bak de Profit. restore_mssql.py los detecta en
# tiempo de ejecución con RESTORE FILELISTONLY y usa estos solo como respaldo.
MSSQL_LOGICAL_DATA = os.getenv("MSSQL_LOGICAL_DATA", "GLOBAL_A")
MSSQL_LOGICAL_LOG = os.getenv("MSSQL_LOGICAL_LOG", "GLOBAL_A_log")

# -------------------------------------------------------------------- Odoo
# Destino: el stack odoo-19 existente (contenedor odoo-19-mns, host :10020).
ODOO = {
    "url": os.getenv("ODOO_URL", "http://odoo-19-mns:8069"),
    "db": os.getenv("ODOO_DB", "profit_migrado19"),
    "user": os.getenv("ODOO_USER", "admin"),
    "password": os.getenv("ODOO_PASSWORD", "admin"),
}

# ------------------------------------------------------------------ Rutas
DB_DIR = os.getenv("DB_DIR", _p("db"))
RAW_DIR = os.getenv("RAW_DIR", _p("export", "raw"))
ODOO_CSV_DIR = os.getenv("ODOO_CSV_DIR", _p("export", "odoo_csv"))
ODOO_CSV19_DIR = os.getenv("ODOO_CSV19_DIR", _p("export", "odoo_csv19"))
PLAN_DIR = os.getenv("PLAN_DIR", _p("export", "plan"))
EXPORT_DIR = os.getenv("EXPORT_DIR", _p("export"))

# Ruta del .bak tal como la ve el contenedor de SQL Server (bind mount ./db).
MSSQL_BACKUP_DIR = os.getenv("MSSQL_BACKUP_DIR", "/backups")

# ------------------------------------------------------------------ Fechas
# Fecha de corte del backup de Profit. Los asientos de ajuste de migración
# (diario MIGAJ) se registran en esta fecha; en load17.py estaba hardcodeada
# como '2026-07-25', lo que rompía el barrido final al correr en otro mes.
CUTOFF_DATE = os.getenv("CUTOFF_DATE", "2026-07-08")
ADJ_DATE = os.getenv("ADJ_DATE", CUTOFF_DATE)

# ------------------------------------------------- Diario/cuenta de ajustes
ADJ_JOURNAL_CODE = "MIGAJ"
ADJ_JOURNAL_NAME = "Ajustes migración Profit"
ADJ_ACCOUNT_CODE = os.getenv("ADJ_ACCOUNT_CODE", "899999")
ADJ_ACCOUNT_NAME = "Ajustes migración Profit"
# Patrimonio, no ingresos. El cuadre de migración es un asiento de APERTURA:
# llevarlo a `income_other` metía el descuadre (~2,16 M) en la cuenta de
# resultados como ingreso inventado. Con `equity` queda donde corresponde y no
# distorsiona el P&L del ejercicio.
ADJ_ACCOUNT_TYPE = os.getenv("ADJ_ACCOUNT_TYPE", "equity")

# Umbral de aviso del cuadre final. Un ajuste por debajo es redondeo cambiario
# y se absorbe sin ruido; por encima ya no lo es, y el paso `trueup` lo avisa
# en vez de dejar que MIGAJ se lo trague en silencio.
TRUEUP_AVISO = float(os.getenv("TRUEUP_AVISO", "20"))
