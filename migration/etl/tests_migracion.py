"""Suite de pruebas de la migración: compara Profit (CSVs crudos) vs Odoo.

Uso:
  python tests_migracion.py 18    # contra profit_migrado  (odoo:8069)
  python tests_migracion.py 19    # contra profit_migrado19 (odoo19:8069)

Solo lectura. Escribe /export/test_odoo<ver>.md y termina con exit code 1
si algún test crítico falla.
"""
import csv
import json
import os
import sys
import xmlrpc.client
from collections import defaultdict

from config import EXPORT_DIR, ODOO, RAW_DIR

TARGETS = {
    # El destino v18 era el del proyecto anterior; se conserva sólo para poder
    # comparar corridas viejas. El de v19 sale de config.py —igual que el resto
    # del ETL— porque el contenedor se llama odoo-19-mns, no "odoo19": con el
    # nombre antiguo la suite ni siquiera resolvía el host.
    "18": {"url": "http://odoo:8069", "db": "profit_migrado",
           "user": "admin", "password": "admin"},
    "19": {"url": ODOO["url"], "db": ODOO["db"],
           "user": ODOO["user"], "password": ODOO["password"]},
}
VER = sys.argv[1] if len(sys.argv) > 1 else "18"
T = TARGETS[VER]
USER, PWD = T["user"], T["password"]

common = xmlrpc.client.ServerProxy(f"{T['url']}/xmlrpc/2/common")
UID = common.authenticate(T["db"], USER, PWD, {})
models = xmlrpc.client.ServerProxy(f"{T['url']}/xmlrpc/2/object", allow_none=True)


def call(model, method, *args, **kw):
    return models.execute_kw(T["db"], UID, PWD, model, method, list(args), kw)


def read_raw(name):
    # RAW_DIR, no "/export/raw": dentro del runner el proyecto se monta en
    # /migration, así que la ruta absoluta antigua no existía.
    with open(os.path.join(RAW_DIR, f"{name}.csv"), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


results = []


def check(nombre, esperado, obtenido, tol=0.0):
    if isinstance(esperado, (int, float)) and isinstance(obtenido, (int, float)):
        ok = abs(esperado - obtenido) <= tol
    else:
        ok = esperado == obtenido
    results.append((ok, nombre, esperado, obtenido))
    print(f"{'PASS' if ok else 'FAIL'}  {nombre}: esperado={esperado} obtenido={obtenido}")
    return ok


print(f"===== Tests de migración contra Odoo {VER} ({T['db']}) =====\n")

# ---------------------------------------------------------------- origen
clientes = read_raw("clientes")
prov = read_raw("prov")
arts = read_raw("art")
facturas = read_raw("factura")
docum_cc = read_raw("docum_cc")
cobros = read_raw("cobros")
tasas = read_raw("tasas")
st_almac = read_raw("st_almac")
reng_fac = read_raw("reng_fac")

fact_ok = [r for r in facturas if r["anulada"].strip() == "False"]
con_lineas = {r["fact_num"] for r in reng_fac if r["anulado"].strip() == "False"}
fact_ok = [r for r in fact_ok if r["fact_num"] in con_lineas]
nc_src = [r for r in docum_cc if r["tipo_doc"].strip() == "N/CR"
          and r["anulado"].strip() == "False" and f(r["monto_net"]) > 0]
nd_src = [r for r in docum_cc if r["tipo_doc"].strip() == "N/DB"
          and r["anulado"].strip() == "False" and f(r["monto_net"]) > 0]
cob_ok = [c for c in cobros if c["anulado"].strip() == "False" and f(c["monto"]) > 0]

# ------------------------------------------------------------- T1-T3 maestros
check("T1 clientes", len(clientes),
      call("res.partner", "search_count", [["customer_rank", ">", 0]],
           context={"active_test": False}))
check("T2 proveedores", len(prov),
      call("res.partner", "search_count", [["supplier_rank", ">", 0]],
           context={"active_test": False}))
check("T3 productos", len(arts),
      call("product.template", "search_count", [["default_code", "!=", False]],
           context={"active_test": False}))

# ------------------------------------------------------- T4-T5 documentos
check("T4a facturas FAC publicadas", len(fact_ok),
      call("account.move", "search_count",
           [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
            ["name", "like", "FAC/%"]]))
check("T4b notas de débito ND", len(nd_src),
      call("account.move", "search_count",
           [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
            ["name", "like", "ND/%"]]))
check("T5 notas de crédito NC", len(nc_src),
      call("account.move", "search_count",
           [["move_type", "=", "out_refund"], ["state", "=", "posted"]]))

# ------------------------------------------------- T6 totales facturados
src_fac_total = round(sum(f(r["tot_neto"]) for r in fact_ok), 2)
g = call("account.move", "read_group",
         [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
          ["name", "like", "FAC/%"]], ["amount_total:sum"], [])
check("T6a total facturado USD", src_fac_total,
      round(g[0]["amount_total"], 2), tol=1.0)
src_nc_total = round(sum(f(r["monto_net"]) for r in nc_src), 2)
g = call("account.move", "read_group",
         [["move_type", "=", "out_refund"], ["state", "=", "posted"]],
         ["amount_total:sum"], [])
check("T6b total NC USD", src_nc_total, round(g[0]["amount_total"], 2), tol=1.0)

# ----------------------------------------------------------- T7 cobros
# Este test contaba xmlids `profit_cobmove_*`, que pertenecen a la ruta
# `payment_moves` (asientos sueltos) que quedó como *fallback*. load19.py migra
# los cobros como `account.payment` nativo —decisión #1 de ESTADO.md—, cuyo
# xmlid es `profit_cob_*` y cuyo asiento lo genera Odoo sin xmlid de importación
# propio. Contra la ruta implementada el conteo daba 0 por construcción.
n_cobmove = call("account.payment", "search_count",
                 [["partner_type", "=", "customer"], ["move_id", "!=", False]])
check("T7a cobros con asiento contable", len(cob_ok), n_cobmove)
src_cob_total = round(sum(f(c["monto"]) for c in cob_ok), 2)
g = call("account.move.line", "read_group",
         [["journal_id.code", "like", "P%"], ["amount_currency", ">", 0],
          ["parent_state", "=", "posted"]],
         ["amount_currency:sum"], [])
check("T7b total cobrado USD", src_cob_total,
      round(g[0]["amount_currency"], 2), tol=1.0)

# ------------------------------------- T8 cartera por documento (con signo)
objetivo_signed = {}
fact_nums = {x["fact_num"] for x in fact_ok}
nd_nums = {x["nro_doc"] for x in nd_src}
nc_nums = {x["nro_doc"] for x in nc_src}
for r in docum_cc:
    if r["anulado"].strip() == "True":
        continue
    tipo, num, saldo = r["tipo_doc"].strip(), r["nro_doc"], f(r["saldo"])
    if tipo == "FACT" and num in fact_nums:
        objetivo_signed[f"profit_fac_{num}"] = round(saldo, 2)
    elif tipo == "N/DB" and num in nd_nums:
        objetivo_signed[f"profit_nd_{num}"] = round(saldo, 2)
    elif tipo == "N/CR" and num in nc_nums:
        objetivo_signed[f"profit_nc_{num}"] = round(-saldo, 2)  # NC resta cartera

imd = {}
names = list(objetivo_signed)
for i in range(0, len(names), 5000):
    for r in call("ir.model.data", "search_read",
                  [["module", "=", "__import__"], ["model", "=", "account.move"],
                   ["name", "in", names[i:i + 5000]]],
                  fields=["name", "res_id"]):
        imd[r["res_id"]] = r["name"]

res_signed, mismatches = {}, []
ids = list(imd)
for i in range(0, len(ids), 5000):
    for r in call("account.move", "search_read",
                  [["id", "in", ids[i:i + 5000]], ["state", "=", "posted"]],
                  fields=["id", "amount_residual", "move_type"]):
        sign = -1 if r["move_type"] == "out_refund" else 1
        res_signed[imd[r["id"]]] = round(sign * r["amount_residual"], 2)

for name, target in objetivo_signed.items():
    got = res_signed.get(name, 0.0)
    if abs(got - target) >= 0.02:
        mismatches.append((name, target, got))

check("T8a documentos de cartera comparados", len(objetivo_signed), len(res_signed))
check("T8b docs con saldo distinto a Profit", 0, len(mismatches))
src_net = round(sum(objetivo_signed.values()), 2)
odoo_net = round(sum(res_signed.values()), 2)
check("T8c cartera neta USD (FACT+ND-NC)", src_net, odoo_net, tol=50.0)

# --------------------------------------------- T9 saldo por cliente
src_cli = defaultdict(float)
for r in docum_cc:
    if r["anulado"].strip() == "True":
        continue
    tipo, saldo = r["tipo_doc"].strip(), f(r["saldo"])
    if saldo == 0:
        continue
    sign = {"FACT": 1, "N/DB": 1, "N/CR": -1, "ADEL": -1}.get(tipo)
    if sign:
        src_cli[r["co_cli"].strip()] += sign * saldo

odoo_cli = {}
g = call("account.move.line", "read_group",
         [["account_id.account_type", "=", "asset_receivable"],
          ["parent_state", "=", "posted"]],
         ["amount_residual_currency:sum"], ["partner_id"], lazy=False)
pids = [x["partner_id"][0] for x in g if x["partner_id"]]
refs = {}
for i in range(0, len(pids), 2000):
    for p in call("res.partner", "search_read",
                  [["id", "in", pids[i:i + 2000]]], fields=["id", "ref"],
                  context={"active_test": False}):
        refs[p["id"]] = (p["ref"] or "").strip()
for x in g:
    if x["partner_id"]:
        ref = refs.get(x["partner_id"][0], "")
        if ref:
            odoo_cli[ref] = round(x["amount_residual_currency"], 2)

cli_diff = []
for cli, saldo in src_cli.items():
    got = odoo_cli.get(cli, 0.0)
    if abs(got - round(saldo, 2)) >= 0.05:
        cli_diff.append((cli, round(saldo, 2), got))
check("T9 clientes con saldo distinto (|dif|>=0.05)", 0, len(cli_diff))

# ----------------------------------------------------------- T10 stock
src_stock = round(sum(f(s["stock_act"]) for s in st_almac), 2)
g = call("stock.quant", "read_group",
         [["location_id.usage", "=", "internal"]], ["quantity:sum"], [])
check("T10 existencias totales", src_stock,
      round(g[0]["quantity"] or 0, 2), tol=0.01)

# ------------------------------------------------------ T11-T12 varios
# Las tasas quedan sobre VES, no sobre USD: la compañía lleva su contabilidad
# en dólares (tasa 1 por definición) y load19._rates_to_secondary_currency
# reasigna cada fila de base.USD a base.VES. Contar sobre USD daba 0 siempre,
# así que este test fallaba por construcción, no por un fallo de la migración.
src_tasas = [t for t in tasas
             if t["co_mone"].strip() == "USD" and f(t["tasa_v"]) > 0]
check("T11 tasas de cambio (cargadas sobre VES)", len(src_tasas),
      call("res.currency.rate", "search_count", [["currency_id.name", "=", "VES"]]))

# T11b — dirección de la tasa, que es lo que decide la convención de
# ops/sync_bcv.py. Odoo debe guardar bolívares por dólar (tasa_v tal cual).
# transform.py escribe 1/tasa_v en el CSV y load19 lo vuelve a invertir, así
# que el valor final tiene que coincidir con el de Profit.
if src_tasas:
    ult = max(src_tasas, key=lambda t: t["fecha"][:10])
    fecha, esperada = ult["fecha"][:10], round(f(ult["tasa_v"]), 4)
    got = call("res.currency.rate", "search_read",
               [["currency_id.name", "=", "VES"], ["name", "=", fecha]],
               fields=["rate"])
    check(f"T11b tasa VES del {fecha} en Bs/US$", esperada,
          round(got[0]["rate"], 4) if got else 0.0,
          tol=max(0.01, esperada * 0.001))
src_rif = len([c for c in clientes if c["rif"].strip()])
check("T12 clientes con RIF migrado", src_rif,
      call("res.partner", "search_count",
           [["customer_rank", ">", 0], ["vat", "!=", False]],
           context={"active_test": False}))

# ---------------------------------------------------------------- reporte
passed = sum(1 for ok, *_ in results if ok)
lines = [f"# Tests migración — Odoo {VER} ({T['db']})", "",
         f"**{passed}/{len(results)} PASS**", ""]
for ok, nombre, esp, obt in results:
    lines.append(f"- {'✅' if ok else '❌'} {nombre}: esperado `{esp}` → obtenido `{obt}`")
if mismatches:
    lines += ["", f"## Documentos con saldo distinto ({len(mismatches)})", ""]
    for name, target, got in sorted(mismatches, key=lambda x: -abs(x[1] - x[2]))[:15]:
        lines.append(f"- {name}: Profit {target:,.2f} vs Odoo {got:,.2f}")
if cli_diff:
    lines += ["", f"## Clientes con saldo distinto ({len(cli_diff)})", ""]
    for cli, esp, obt in sorted(cli_diff, key=lambda x: -abs(x[1] - x[2]))[:15]:
        lines.append(f"- {cli}: Profit {esp:,.2f} vs Odoo {obt:,.2f}")

report = os.path.join(EXPORT_DIR, f"test_odoo{VER}.md")
with open(report, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
print(f"\n{passed}/{len(results)} PASS -> {report}")
sys.exit(0 if passed == len(results) else 1)
