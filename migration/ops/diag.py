"""Diagnóstico del estado de conciliación en Odoo (solo lectura)."""
import xmlrpc.client

from config import ODOO

common = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/common")
UID = common.authenticate(ODOO["db"], ODOO["user"], ODOO["password"], {})
models = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/object", allow_none=True)


def call(model, method, *args, **kw):
    return models.execute_kw(ODOO["db"], UID, ODOO["password"], model, method,
                             list(args), kw)


n_part = call("account.partial.reconcile", "search_count", [])
amt = call("account.partial.reconcile", "read_group", [],
           ["amount:sum"], [])
print(f"partials: {n_part}  monto_conciliado_VES: {amt[0]['amount']:,.2f}")

# líneas receivable sin conciliar del todo, separadas por signo
for label, dom in [
    ("DEUDA abierta (facturas/ND, débito)", [["balance", ">", 0]]),
    ("CRÉDITO colgando (pagos/NC, crédito)", [["balance", "<", 0]]),
]:
    g = call("account.move.line", "read_group",
             [["account_id.account_type", "=", "asset_receivable"],
              ["reconciled", "=", False], ["parent_state", "=", "posted"]] + dom,
             ["amount_residual_currency:sum"], [])
    row = g[0] if g else {}
    print(f"{label}: n={row.get('__count')} "
          f"residual USD={row.get('amount_residual_currency') or 0:,.2f}")

migadj = call("account.move", "search_count", [["ref", "like", "MIGADJ-"]])
print(f"asientos MIGADJ existentes: {migadj}")

# estado de los pagos
for st in call("account.payment", "read_group", [], ["amount:sum"], ["state"],
               lazy=False):
    print(f"pagos state={st.get('state')}: n={st['__count']} "
          f"USD={st['amount']:,.2f}")

# líneas receivable de asientos de pago (diarios P*)
pl = call("account.move.line", "read_group",
          [["account_id.account_type", "=", "asset_receivable"],
           ["journal_id.code", "like", "P"]],
          ["amount_currency:sum", "balance:sum"], ["parent_state"], lazy=False)
for g in pl:
    print(f"lineas recv de pagos parent_state={g.get('parent_state')}: "
          f"n={g['__count']} USD={g['amount_currency']:,.2f} "
          f"VES={g['balance']:,.2f}")

# muestra de un pago con su método y cuenta transitoria
for p in call("account.payment", "search_read", [["move_id", "=", False]],
              limit=2, order="id"):
    print(f"\nPAGO {p['name']} state={p['state']} amount={p['amount']} "
          f"journal={p['journal_id']} move={p.get('move_id')} "
          f"method_line={p.get('payment_method_line_id')} "
          f"outstanding={p.get('outstanding_account_id')}")

# líneas de método de pago inbound y sus cuentas
for ml in call("account.payment.method.line", "search_read",
               [["payment_type", "=", "inbound"]],
               fields=["id", "name", "journal_id", "payment_account_id"]):
    print(f"ML {ml['id']} {ml['name']} journal={ml['journal_id']} "
          f"account={ml['payment_account_id']}")
