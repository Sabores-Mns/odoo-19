"""Resetea la capa de conciliación: borra MIGADJ, desconcilia todo el
receivable y elimina los asientos de diferencia cambiaria huérfanos.
Deja facturas/NC/ND/pagos publicados intactos, listos para reconciliar de cero.
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


# 1) asientos de ajuste de migración
ids = call("account.move", "search", [["ref", "like", "MIGADJ-"]])
print(f"MIGADJ a borrar: {len(ids)}")
for i in range(0, len(ids), 100):
    chunk = ids[i:i + 100]
    tolerant("account.move", "button_draft", chunk)
    call("account.move", "unlink", chunk)

# 2) desconciliar todas las líneas receivable
line_ids = call("account.move.line", "search",
                [["account_id.account_type", "=", "asset_receivable"],
                 ["matched_debit_ids", "!=", False]])
line_ids += call("account.move.line", "search",
                 [["account_id.account_type", "=", "asset_receivable"],
                  ["matched_credit_ids", "!=", False]])
line_ids = list(set(line_ids))
print(f"líneas receivable con matching: {len(line_ids)}")
for i in range(0, len(line_ids), 500):
    tolerant("account.move.line", "remove_move_reconcile", line_ids[i:i + 500])
    if i % 2000 == 0:
        print(f"  desconciliadas {i + 500}")

# 3) asientos de diferencia cambiaria (quedaron huérfanos al desconciliar)
exch = call("account.move", "search", [["journal_id.code", "in", ["CAMBI", "EXCH"]]])
print(f"asientos de diferencia cambiaria a borrar: {len(exch)}")
for i in range(0, len(exch), 100):
    chunk = exch[i:i + 100]
    tolerant("account.move", "button_draft", chunk)
    call("account.move", "unlink", chunk)

rest = call("account.partial.reconcile", "search_count", [])
print(f"partials restantes: {rest}")
print("reset completo")
