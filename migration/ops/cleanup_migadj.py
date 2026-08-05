"""Elimina los asientos de ajuste MIGADJ-* creados con la cuenta equivocada."""
import xmlrpc.client

from config import ODOO

common = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/common")
UID = common.authenticate(ODOO["db"], ODOO["user"], ODOO["password"], {})
models = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/object", allow_none=True)


def call(model, method, *args, **kw):
    return models.execute_kw(ODOO["db"], UID, ODOO["password"], model, method,
                             list(args), kw)


ids = call("account.move", "search", [["ref", "like", "MIGADJ-%"]])
print(f"asientos MIGADJ a eliminar: {len(ids)}")
for i in range(0, len(ids), 100):
    chunk = ids[i:i + 100]
    try:
        call("account.move", "button_draft", chunk)
    except xmlrpc.client.Fault as exc:
        if "marshal" not in str(exc):
            raise
    call("account.move", "unlink", chunk)
    if i % 1000 == 0:
        print(f"  eliminados {i + len(chunk)}/{len(ids)}")
print("limpieza completa")
