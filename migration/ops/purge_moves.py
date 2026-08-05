"""Elimina TODOS los asientos migrados de facturas/NC/ND (y sus xmlids)
para recargarlos desde los CSVs corregidos. No toca pagos ni maestros.
"""
import xmlrpc.client

from config import ODOO

common = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/common")
UID = common.authenticate(ODOO["db"], ODOO["user"], ODOO["password"], {})
models = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/object", allow_none=True)


def call(model, method, *args, **kw):
    return models.execute_kw(ODOO["db"], UID, ODOO["password"], model, method,
                             list(args), kw)


def tolerant(model, method, ids):
    try:
        call(model, method, ids)
    except xmlrpc.client.Fault as exc:
        if "marshal" not in str(exc):
            raise


imd = []
for pref in ("profit_fac_%", "profit_nc_%", "profit_nd_%"):
    imd += call("ir.model.data", "search_read",
                [["module", "=", "__import__"], ["model", "=", "account.move"],
                 ["name", "like", pref]], fields=["id", "res_id"])
move_ids = [r["res_id"] for r in imd]
imd_ids = [r["id"] for r in imd]
print(f"asientos migrados a borrar: {len(move_ids)}")

existing = []
for i in range(0, len(move_ids), 5000):
    existing += call("account.move", "search",
                     [["id", "in", move_ids[i:i + 5000]]])
print(f"existentes en BD: {len(existing)}")

for i in range(0, len(existing), 100):
    chunk = existing[i:i + 100]
    tolerant("account.move", "button_draft", chunk)
    call("account.move", "unlink", chunk)
    if i % 1000 == 0:
        print(f"  borrados {i + len(chunk)}/{len(existing)}")

for i in range(0, len(imd_ids), 5000):
    call("ir.model.data", "unlink", imd_ids[i:i + 5000])
print("xmlids purgados; purga completa")
