"""Carga la migración Profit en Odoo 19 vía XML-RPC.

Motor consolidado. Toma como base load17.py (el más avanzado de los dos
proyectos previos, único que trae `crossapply`) y le incorpora lo que sólo
existía en load_xmlrpc.py (`fix_payments`), adaptado al modelo de Odoo 19.

Qué se conserva de load17.py
  - step_crossapply: las compensaciones documento-contra-documento sin caja
    (824 cobros, ~$30,1M) que reconcile_plan.json excluye y que, sin este
    paso, dejan los documentos colgados con su saldo completo.
  - step_payments con account.payment nativo, para que los anticipos ADEL
    queden como crédito aplicable desde la factura.
  - _rates_to_secondary_currency: la corrección USD-compañía / VES-secundaria.

Qué se incorpora de load_xmlrpc.py
  - step_fix_payments: desde Odoo 18 un pago sin cuenta transitoria en su
    método de pago queda `in_process` SIN asiento. Se asigna la cuenta del
    diario y se re-publica.

Qué se corrigió respecto de load17.py
  - Las fechas '2026-07-25' y 'MIGAJ/2026/07/' estaban hardcodeadas en
    step_trueup; ahora vienen de config.ADJ_DATE y el barrido busca por
    `ref like 'MIGAJ_PAY_%'`, que no depende del mes de ejecución.
  - La ruta '/export/raw/docum_cc.csv' hardcodeada -> RAW_DIR.
  - El `except Exception: pass` del primer reconcile ahora sólo tolera el
    Fault de marshalling y cuenta el resto en vez de tragárselo.
  - Se eliminó _product_compat()/_adapt_product_columns(): el sondeo v17<->v18
    sobra porque transform.py ya emite type=consu + is_storable.

Odoo 19 (diferencias confirmadas en la corrida del 2026-07-11)
  - uom.category no existe: 01_uom_category.csv no se carga.
  - res.users.groups_id -> group_ids   (lo resuelve transform19.py)
  - product.template.uom_po_id eliminado (idem)
  - account.payment.term necesita xmlid por línea o se duplican (idem)
  - Los Fault "cannot marshal None" siguen apareciendo en action_post y
    reconcile aunque el servidor SÍ hace commit: se toleran explícitamente.

Uso:
  python load19.py                       # todos los pasos, en orden
  python load19.py reconcile crossapply  # sólo algunos

Re-ejecutable: la idempotencia se apoya en los External ID __import__.profit_*
"""
import csv
import json
import os
import sys
import time
import xmlrpc.client

from config import (
    ADJ_ACCOUNT_CODE,
    ADJ_ACCOUNT_NAME,
    ADJ_ACCOUNT_TYPE,
    ADJ_DATE,
    ADJ_JOURNAL_CODE,
    ADJ_JOURNAL_NAME,
    EXPORT_DIR,
    ODOO,
    ODOO_CSV19_DIR,
    PLAN_DIR,
    RAW_DIR,
    TRUEUP_AVISO,
)

common = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/common")
UID = common.authenticate(ODOO["db"], ODOO["user"], ODOO["password"], {})
if not UID:
    sys.exit(f"ERROR: no se pudo autenticar en {ODOO['url']} "
             f"(base {ODOO['db']}, usuario {ODOO['user']}).")
models = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/object", allow_none=True)


def call(model, method, *args, **kw):
    return models.execute_kw(ODOO["db"], UID, ODOO["password"],
                             model, method, list(args), kw)


def is_marshal_fault(exc):
    """El Fault que XML-RPC lanza cuando el método devuelve algo no serializable.

    Ocurre en action_post y en reconcile: el servidor ya hizo commit, sólo
    falla al devolver el resultado. Tratarlo como éxito es correcto; tratar
    CUALQUIER excepción como éxito (lo que hacía load17.py) no lo es.
    """
    return "cannot marshal" in str(exc)


def read_csv(name):
    with open(os.path.join(ODOO_CSV19_DIR, name), encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    header, data = rows[0], rows[1:]
    # `memo` se deja tal cual. Comprobado con fields_get contra el contenedor
    # vivo (2026-08-04): account.payment en Odoo 19 tiene `memo` (char, store)
    # y NO tiene `ref`. Aquí se renombraba memo -> ref por la suposición
    # contraria, lo que hacía fallar la carga de 16_account_payment_cobros.csv
    # con "Invalid field 'ref' on model 'account.payment'".
    # Los `ref` que este loader sí usa son de account.move, no de pagos.
    return header, data


def load_model(model, header, rows, chunk=400, skip_existing=False):
    """Equivalente a la importación CSV de la UI (crea/actualiza por external id).

    Los registros con one2many ocupan varias filas: la primera trae el `id` y
    las siguientes lo dejan vacío. Se agrupan para que un lote nunca parta un
    registro por la mitad.
    """
    records, current = [], []
    for row in rows:
        if row[0].strip() and current:
            records.append(current)
            current = []
        current.append(row)
    if current:
        records.append(current)

    if skip_existing:
        existing = xmlid_map([r[0][0] for r in records if r and r[0][0]], model)
        records = [r for r in records if r and r[0][0] not in existing]
        if not records:
            print(f"{model}: todos los registros ya existían (omitidos)")
            return

    parts, buf, count = [], [], 0
    for rec in records:
        if count and count + len(rec) > chunk:
            parts.append(buf)
            buf, count = [], 0
        buf.extend(rec)
        count += len(rec)
    if buf:
        parts.append(buf)

    total = 0
    for i, part in enumerate(parts):
        res = call(model, "load", header, part)
        if res.get("messages"):
            errors = [m for m in res["messages"] if m.get("type") in ("error", None)]
            if errors:
                for m in errors[:5]:
                    print(f"  [ERROR] {model} fila {m.get('record', '?')}: "
                          f"{m.get('message', '')[:300]}")
                raise SystemExit(f"Carga de {model} abortada (lote {i}).")
        total += len(res.get("ids") or [])
    print(f"{model}: {total} registros cargados")


def xmlid_map(names, model):
    out = {}
    names = list(names)
    for i in range(0, len(names), 2000):
        for r in call("ir.model.data", "search_read",
                      [["module", "=", "__import__"], ["model", "=", model],
                       ["name", "in", names[i:i + 2000]]],
                      fields=["name", "res_id"]):
            out[r["name"]] = r["res_id"]
    return out


def ensure_xmlid(name, model, res_id):
    if not call("ir.model.data", "search_count",
                [["module", "=", "__import__"], ["name", "=", name]]):
        call("ir.model.data", "create",
             [{"module": "__import__", "name": name, "model": model,
               "res_id": res_id, "noupdate": False}])


def post_safe(model, ids):
    try:
        call(model, "action_post", ids)
    except xmlrpc.client.Fault as exc:
        if not is_marshal_fault(exc):
            raise


def reconcile_lines(line_ids):
    """Concilia y devuelve True si quedó hecho.

    Centraliza el manejo del Fault de marshalling, que en load17.py estaba
    repetido con anidamientos inconsistentes ([[a,b]] en un sitio, [a,b] en
    otro). La firma correcta es reconcile(<lista de ids>).
    """
    try:
        call("account.move.line", "reconcile", list(line_ids))
        return True
    except xmlrpc.client.Fault as exc:
        if is_marshal_fault(exc):
            return True
        raise


def plan_json(name):
    with open(os.path.join(PLAN_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def raw_rows(name):
    with open(os.path.join(RAW_DIR, f"{name}.csv"), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────
def step_setup():
    ves = call("res.currency", "search", [["name", "=", "VES"]],
               context={"active_test": False})
    usd = call("res.currency", "search", [["name", "=", "USD"]],
               context={"active_test": False})
    call("res.currency", "write", ves + usd, {"active": True})

    company = call("res.company", "search_read", [],
                   fields=["id", "currency_id", "name"])[0]
    if company["currency_id"][1] != "USD":
        # Odoo bloquea el cambio de moneda si ya hay asientos; en una base
        # recién creada funciona, y si no, el resto de la migración sigue.
        try:
            call("res.company", "write", [company["id"]], {"currency_id": usd[0]})
            print(f"Moneda compañía: {company['currency_id'][1]} -> USD")
        except xmlrpc.client.Fault as exc:
            print(f"[AVISO] no se pudo cambiar la moneda de la compañía: "
                  f"{str(exc)[:200]}")
    ve = call("res.country", "search", [["code", "=", "VE"]])
    call("res.company", "write", [company["id"]],
         {"name": "MULTI_A (migrada de Profit)", "country_id": ve[0]})

    jm = plan_json("journal_map.json")
    for code, name in list(jm["bancos"].items()) + [("CAJA", "Caja Principal")]:
        jtype = "cash" if code == "CAJA" else "bank"
        if not call("account.journal", "search_count", [["code", "=", f"P{code[:4]}"]]):
            jid = call("account.journal", "create",
                       [{"name": f"{name} (Profit)", "code": f"P{code[:4]}",
                         "type": jtype}])[0]
            ensure_xmlid(f"profit_journal_{code}", "account.journal", jid)

    # Diario y cuenta donde aterrizan los ajustes de migración (diferencial
    # cambiario). Ver docs/EXCHANGE_DIFFERENCES.md.
    if not call("account.journal", "search_count",
                [["code", "=", ADJ_JOURNAL_CODE]]):
        jid = call("account.journal", "create",
                   [{"name": ADJ_JOURNAL_NAME, "code": ADJ_JOURNAL_CODE,
                     "type": "general"}])[0]
        ensure_xmlid(f"profit_journal_{ADJ_JOURNAL_CODE}", "account.journal", jid)
    if not call("account.account", "search_count",
                [["code", "=", ADJ_ACCOUNT_CODE]]):
        aid = call("account.account", "create",
                   [{"code": ADJ_ACCOUNT_CODE, "name": ADJ_ACCOUNT_NAME,
                     "account_type": ADJ_ACCOUNT_TYPE}])[0]
        ensure_xmlid("profit_account_MIGAJ", "account.account", aid)
    print("setup OK")


# ─────────────────────────────────────────────────────────────────────────────
# MASTERS
# ─────────────────────────────────────────────────────────────────────────────
# Sin 01_uom_category.csv: Odoo 19 eliminó el modelo uom.category.
MASTER_FILES = [
    ("02_uom_uom.csv",               "uom.uom"),
    ("03_res_partner_category.csv",  "res.partner.category"),
    ("04_res_users.csv",             "res.users"),
    ("05_account_payment_term.csv",  "account.payment.term"),
    ("06_product_category.csv",      "product.category"),
    ("07_res_partner_clientes.csv",  "res.partner"),
    ("08_res_partner_prov.csv",      "res.partner"),
    ("09_product_template.csv",      "product.template"),
    ("10_product_pricelist.csv",     "product.pricelist"),
    ("11_product_pricelist_item.csv", "product.pricelist.item"),
    ("12_res_currency_rate.csv",     "res.currency.rate"),
]

# Re-importar la cabecera de un registro con one2many vuelve a añadir sus
# líneas. En los términos de pago eso rompe la regla de "la suma debe ser
# 100%", así que ese archivo sólo se carga si faltan registros.
SKIP_IF_EXISTS = {"05_account_payment_term.csv"}


def _rates_to_secondary_currency(header, rows):
    """Reasigna a VES las tasas que el CSV pone sobre la moneda de la compañía.

    Profit guarda los importes en US$ y usa `tasa` sólo como referencia en
    bolívares, así que esos importes YA son los definitivos. Como la moneda
    base de Odoo es USD, su tasa vale 1 por definición: cargarle tasas hace
    que Odoo divida cada importe y grabe el libro mayor en magnitudes de
    bolívares (una CxC de $12,9M aparecía como 4.724 millones).

    El CSV trae rate = 1/tasa sobre base.USD; aquí pasa a base.VES con
    rate = tasa (bolívares por dólar), que es lo que Odoo espera de una
    moneda secundaria.
    """
    cur_idx, rate_idx = header.index("currency_id/id"), header.index("rate")
    out = []
    for row in rows:
        if row[cur_idx].strip() != "base.USD":
            out.append(row)
            continue
        try:
            rate = float(row[rate_idx])
        except ValueError:
            continue
        if rate <= 0:
            continue
        row = list(row)
        row[cur_idx] = "base.VES"
        row[rate_idx] = f"{1.0 / rate:.12f}"
        out.append(row)
    return out


def step_masters():
    for fname, model in MASTER_FILES:
        if not os.path.exists(os.path.join(ODOO_CSV19_DIR, fname)):
            print(f"[AVISO] {fname} no existe, se omite")
            continue
        header, rows = read_csv(fname)
        if fname == "12_res_currency_rate.csv":
            ves = call("res.currency", "search", [["name", "=", "VES"]],
                       context={"active_test": False})
            if ves:
                call("res.currency", "write", ves, {"active": True})
            rows = _rates_to_secondary_currency(header, rows)
        load_model(model, header, rows,
                   skip_existing=(fname in SKIP_IF_EXISTS))

        if fname == "09_product_template.csv":
            # Todo el histórico de Profit es exento (IVA 0): los productos no
            # deben arrastrar los impuestos por defecto del plan contable.
            ids = call("product.template", "search",
                       [["default_code", "!=", False]],
                       context={"active_test": False})
            call("product.template", "write", ids,
                 {"taxes_id": [(5,)], "supplier_taxes_id": [(5,)],
                  "type": "consu", "is_storable": True})
            print("  impuestos por defecto limpiados; productos almacenables")


# ─────────────────────────────────────────────────────────────────────────────
# INVOICES
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_variant_xmlids():
    """Los CSV de facturas referencian variantes (profit_artvar_*), no plantillas."""
    tmpl = {r["name"]: r["res_id"] for r in call(
        "ir.model.data", "search_read",
        [["module", "=", "__import__"], ["model", "=", "product.template"],
         ["name", "like", "profit_art_%"]], fields=["name", "res_id"])}
    existing = {r["name"] for r in call(
        "ir.model.data", "search_read",
        [["module", "=", "__import__"], ["model", "=", "product.product"]],
        fields=["name"])}
    variants = call("product.product", "search_read",
                    [["product_tmpl_id", "in", list(tmpl.values())]],
                    fields=["id", "product_tmpl_id"],
                    context={"active_test": False})
    by_tmpl = {v["product_tmpl_id"][0]: v["id"] for v in variants}
    vals = []
    for name, tid in tmpl.items():
        var_name = name.replace("profit_art_", "profit_artvar_")
        if var_name not in existing and tid in by_tmpl:
            vals.append({"module": "__import__", "name": var_name,
                         "model": "product.product", "res_id": by_tmpl[tid],
                         "noupdate": False})
    if vals:
        call("ir.model.data", "create", vals)
    print(f"  xmlids de variantes: {len(vals)} creados")


def _post_moves(xml_names, batch=80):
    id_map = xmlid_map(xml_names, "account.move")
    ids = list(id_map.values())
    if not ids:
        return
    draft = call("account.move", "search",
                 [["id", "in", ids], ["state", "=", "draft"]])
    print(f"  a publicar: {len(draft)} de {len(ids)}")
    done = 0
    for i in range(0, len(draft), batch):
        post_safe("account.move", draft[i:i + batch])
        done += len(draft[i:i + batch])
        if done % 800 < batch:
            print(f"  publicadas {done}/{len(draft)}")


def step_invoices():
    _ensure_variant_xmlids()
    for fname in ("13_account_move_facturas.csv", "14_account_move_nc.csv",
                  "15_account_move_nd.csv"):
        header, rows = read_csv(fname)
        t0 = time.time()
        load_model("account.move", header, rows, chunk=600)
        print(f"  {fname} cargado en {time.time() - t0:.0f}s")
        names = sorted({r[0] for r in rows if r[0]})
        _post_moves(names)


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENTS
# ─────────────────────────────────────────────────────────────────────────────
def _journal_map():
    jmap = {}
    for r in call("ir.model.data", "search_read",
                  [["module", "=", "__import__"],
                   ["model", "=", "account.journal"]],
                  fields=["name", "res_id"]):
        jmap[r["name"].replace("profit_journal_", "")] = r["res_id"]
    return jmap


def step_payments():
    """Crea un account.payment por cada cobro real de Profit.

    Se eligió account.payment nativo (en vez de asientos manuales, que era el
    camino de load_xmlrpc.py) para que los anticipos ADEL queden visibles como
    crédito del cliente y se puedan aplicar desde la propia factura.
    """
    header, rows = read_csv("16_account_payment_cobros.csv")
    jmap = _journal_map()
    fallback = jmap.get("CAJA")
    if not fallback:
        raise SystemExit("No existe el diario CAJA; ejecuta antes el paso `setup`.")

    # La columna x_journal_profit trae el código de banco de Profit; se
    # sustituye por el id real del diario (/.id = id de base de datos).
    jcol = header.index("x_journal_profit")
    new_header = header[:jcol] + ["journal_id/.id"]
    new_rows = [r[:jcol] + [str(jmap.get(r[jcol]) or fallback)] for r in rows]

    # En re-ejecuciones, no tocar pagos ya publicados.
    existing = xmlid_map([r[0] for r in new_rows], "account.payment")
    posted = set()
    if existing:
        states = {r["id"]: r["state"] for r in call(
            "account.payment", "search_read",
            [["id", "in", list(existing.values())]], fields=["id", "state"])}
        posted = {n for n, pid in existing.items()
                  if states.get(pid) and states[pid] != "draft"}
    if posted:
        print(f"  {len(posted)} pagos ya publicados, se omiten")

    load_model("account.payment", new_header,
               [r for r in new_rows if r[0] not in posted], chunk=400)

    ids = list(xmlid_map([r[0] for r in new_rows], "account.payment").values())
    todo = call("account.payment", "search",
                [["id", "in", ids], ["state", "=", "draft"]])
    print(f"  pagos a publicar: {len(todo)}")
    done = 0
    for i in range(0, len(todo), 200):
        post_safe("account.payment", todo[i:i + 200])
        done += len(todo[i:i + 200])
        if done % 1000 < 200:
            print(f"  publicados {done}/{len(todo)}")

    sin_asiento = call("account.payment", "search_count",
                       [["state", "!=", "draft"], ["move_id", "=", False]])
    if sin_asiento:
        print(f"  [AVISO] {sin_asiento} pagos publicados SIN asiento: "
              f"lo corrige el paso `fix_payments`")
    print("payments OK")


def step_fix_payments():
    """Da asiento contable a los pagos que quedaron `in_process`.

    Desde Odoo 18, si la línea de método de pago del diario no tiene cuenta
    transitoria, el pago se publica pero NO genera asiento. Se asigna la
    cuenta del propio diario (el dinero aterriza directo en banco/caja) y se
    re-publican.
    """
    journals = call("account.journal", "search_read",
                    [["type", "in", ["bank", "cash"]]],
                    fields=["id", "default_account_id", "name"])
    acc_by_journal = {j["id"]: j["default_account_id"][0] for j in journals
                      if j["default_account_id"]}
    lines = call("account.payment.method.line", "search_read",
                 [["journal_id", "in", [j["id"] for j in journals]],
                  ["payment_type", "=", "inbound"]],
                 fields=["id", "journal_id", "payment_account_id"])
    todo = [l for l in lines if not l["payment_account_id"]]
    for l in todo:
        acc = acc_by_journal.get(l["journal_id"][0])
        if acc:
            call("account.payment.method.line", "write", [l["id"]],
                 {"payment_account_id": acc})
    print(f"  cuentas de pago configuradas en {len(todo)} métodos")

    pays = call("account.payment", "search",
                [["state", "!=", "draft"], ["move_id", "=", False]])
    print(f"  pagos sin asiento a re-publicar: {len(pays)}")
    done = 0
    for i in range(0, len(pays), 100):
        chunk = pays[i:i + 100]
        try:
            call("account.payment", "action_draft", chunk)
        except xmlrpc.client.Fault as exc:
            if not is_marshal_fault(exc):
                raise
        post_safe("account.payment", chunk)
        done += len(chunk)
        if done % 1000 < 100:
            print(f"  re-publicados {done}/{len(pays)}")

    quedan = call("account.payment", "search_count",
                  [["state", "!=", "draft"], ["move_id", "=", False]])
    print(f"fix_payments OK (quedan sin asiento: {quedan})")


# ─────────────────────────────────────────────────────────────────────────────
# CONCILIACIÓN — utilidades comunes a reconcile / crossapply / trueup
# ─────────────────────────────────────────────────────────────────────────────
def _receivable_lines(move_ids):
    """move_id -> [líneas de cuentas por cobrar de ese asiento]."""
    out = {}
    ids = list(move_ids)
    for i in range(0, len(ids), 5000):
        for r in call("account.move.line", "search_read",
                      [["move_id", "in", ids[i:i + 5000]],
                       ["account_id.account_type", "=", "asset_receivable"]],
                      # El residual es imprescindible para step_reconcile, que
                      # reparte importes exactos: sin él daba 0 en todas las
                      # líneas y no aplicaba ni una sola conciliación.
                      fields=["id", "move_id", "reconciled",
                              "amount_residual", "amount_residual_currency"]):
            out.setdefault(r["move_id"][0], []).append(r)
    return out


def _payment_moves(names):
    """xmlid de account.payment -> id del account.move que generó.

    load17.py resolvía el mapa inverso con un `next(k for k, v in ...)` dentro
    del bucle, O(n²) sobre 5.002 pagos. Aquí se invierte el diccionario una vez.
    """
    pay_map = xmlid_map(names, "account.payment")
    if not pay_map:
        return {}
    por_id = {v: k for k, v in pay_map.items()}
    out = {}
    ids = list(pay_map.values())
    for i in range(0, len(ids), 1000):
        for p in call("account.payment", "search_read",
                      [["id", "in", ids[i:i + 1000]], ["move_id", "!=", False]],
                      fields=["id", "move_id"]):
            name = por_id.get(p["id"])
            if name:
                out[name] = p["move_id"][0]
    return out


def _apply_group(cand):
    """Concilia un grupo de líneas candidatas.

    Devuelve ("done", None), ("skipped", None) o ("failed", mensaje).

    Relee el estado antes de conciliar en vez de fiarse del que se cargó al
    principio del paso: una línea puede haber quedado conciliada por un grupo
    anterior. Y exige que haya ambos signos, porque conciliar sólo débitos (o
    sólo créditos) no compensa nada y Odoo responde con un error opaco.
    """
    cand = list(dict.fromkeys(cand))
    if len(cand) < 2:
        return "skipped", None
    libres = call("account.move.line", "search_read",
                  [["id", "in", cand], ["reconciled", "=", False]],
                  fields=["id", "balance"])
    if (len(libres) < 2
            or not any(l["balance"] > 0 for l in libres)
            or not any(l["balance"] < 0 for l in libres)):
        return "skipped", None
    try:
        reconcile_lines([l["id"] for l in libres])
        return "done", None
    except xmlrpc.client.Fault as exc:
        return "failed", str(exc)[:300]


# ─────────────────────────────────────────────────────────────────────────────
# RECONCILE — pagos contra facturas, según el plan normalizado
# ─────────────────────────────────────────────────────────────────────────────
def _entry_items(seq):
    """Renglones del plan como (xmlid, monto).

    transform.py emite {"id": ..., "monto": ...}; el formato antiguo era una
    lista de cadenas sin importe. Se aceptan los dos para que un plan viejo en
    export/plan/ no reviente la carga: sin importe, monto = None y se aplica el
    máximo posible, que es el comportamiento de antes.
    """
    for it in seq:
        if isinstance(it, dict):
            yield it["id"], it.get("monto")
        else:
            yield it, None


def _apply_exact(creditos, docs):
    """Aplica a cada documento EXACTAMENTE el importe que Profit le asignó.

    `creditos` es [[line_id, disponible], ...] con el saldo a favor (anticipos,
    notas de crédito, el propio cobro). `docs` es [(line_id, debe, monto), ...].

    No se usa reconcile() —la conciliación automática de Odoo reparte a su
    criterio y aplica el máximo posible, que es justo lo que descuadraba la
    cartera— sino account.partial.reconcile con el importe explícito.

    Devuelve (parciales, sin_credito, consumido_por_linea).
    """
    hechos = falta_credito = 0
    consumido = {}
    for line_id, debe, monto in docs:
        # Nunca aplicar más de lo que el documento debe ni más de lo que dice
        # Profit; si el plan no trae importe, se cubre el documento entero.
        pendiente = debe if monto is None else min(monto, debe)
        for c in creditos:
            if pendiente <= 0.005:
                break
            if c[1] <= 0.005:
                continue
            usar = round(min(pendiente, c[1]), 2)
            if usar <= 0.005:
                continue
            call("account.partial.reconcile", "create", [{
                "debit_move_id": line_id,
                "credit_move_id": c[0],
                "amount": usar,
                "debit_amount_currency": usar,
                "credit_amount_currency": usar,
            }])
            c[1] = round(c[1] - usar, 2)
            pendiente = round(pendiente - usar, 2)
            consumido[line_id] = round(consumido.get(line_id, 0.0) + usar, 2)
            consumido[c[0]] = round(consumido.get(c[0], 0.0) + usar, 2)
            hechos += 1
        if pendiente > 0.005:
            falta_credito += 1
    return hechos, falta_credito, consumido


def step_reconcile():
    """Aplica reconcile_plan.json: a cada documento, el importe que Profit dice.

    El plan ya viene normalizado por transform.py: las facturas de los cobros
    que sólo consumen anticipo están asignadas al cobro de origen, y los cobros
    mixtos traen sus facturas más las fuentes. No hay referencias circulares a
    través de ADEL intermedios.

    Lo que cambió: antes se pasaba el grupo entero a reconcile() y Odoo repartía
    aplicando el máximo posible, de modo que una factura quedaba saldada aunque
    Profit sólo le hubiera abonado una parte —y esa deuda viva del cliente
    desaparecía—. Ahora cada renglón se aplica por su importe exacto.
    """
    plan = plan_json("reconcile_plan.json")

    doc_names, cob_names = set(), set()
    for entry in plan.values():
        for name, _ in _entry_items(entry["docs"]):
            doc_names.add(name)
        for name, _ in _entry_items(entry["fuentes"]):
            (cob_names if name.startswith("profit_cob_") else doc_names).add(name)
    cob_names.update(f"profit_cob_{cob}" for cob in plan)

    move_map = xmlid_map(doc_names, "account.move")
    cob_move = _payment_moves(cob_names)
    rec_lines = _receivable_lines(set(move_map.values()) | set(cob_move.values()))

    def lines_of(name):
        mid = (cob_move if name.startswith("profit_cob_") else move_map).get(name)
        return rec_lines.get(mid, [])

    # Residual vivo por línea: se lleva en memoria para no releer el estado en
    # cada cobro. Positivo = debe (factura), negativo = haber (pago o NC).
    saldo = {}
    for lst in rec_lines.values():
        for l in lst:
            saldo[l["id"]] = (l.get("amount_residual_currency")
                              or l.get("amount_residual") or 0.0)

    hechos = sin_credito = fallidos = 0
    for n, (cob, entry) in enumerate(
            sorted(plan.items(), key=lambda kv: int(kv[0])), start=1):
        docs = []
        for name, monto in _entry_items(entry["docs"]):
            for l in lines_of(name):
                debe = saldo.get(l["id"], 0.0)
                if debe > 0.005:
                    docs.append((l["id"], debe, monto))
        if not docs:
            continue

        # Las fuentes van PRIMERO, cada una limitada a lo que Profit dice que
        # aporta; el efectivo del cobro va al final y absorbe el resto.
        # Con el pago primero, un cobro con caja suficiente cubría él solo los
        # documentos y las notas de crédito y anticipos no se consumían nunca:
        # quedaban flotando como crédito del cliente aunque Profit los diera
        # por aplicados (la NC 331 aporta 70.233,23 a un cobro que además trae
        # 78.395,61 en caja; sin este orden se quedaba entera sin usar).
        creditos = []
        for name, monto in _entry_items(entry["fuentes"]):
            for l in lines_of(name):
                disp = -saldo.get(l["id"], 0.0)
                if monto is not None:
                    disp = min(disp, monto)
                if disp > 0.005:
                    creditos.append([l["id"], disp])
        for l in lines_of(f"profit_cob_{cob}"):
            disp = -saldo.get(l["id"], 0.0)
            if disp > 0.005:
                creditos.append([l["id"], disp])
        if not creditos:
            continue

        try:
            ok, sc, consumido = _apply_exact(creditos, docs)
        except xmlrpc.client.Fault as exc:
            if not is_marshal_fault(exc):
                fallidos += 1
                if fallidos <= 10:
                    print(f"  [WARN] cobro {cob}: {str(exc)[:200]}")
                continue
            ok, sc, consumido = 0, 0, {}
        hechos += ok
        sin_credito += sc

        # Reflejar lo realmente consumido —no lo previsto—, para que el
        # siguiente cobro parta del estado correcto sin releerlo del servidor.
        # Un débito baja su residual; un crédito sube hacia cero.
        for lid, usado in consumido.items():
            s = saldo.get(lid, 0.0)
            saldo[lid] = round(s - usado, 2) if s > 0 else round(s + usado, 2)

        if n % 500 == 0:
            print(f"  {n}/{len(plan)} — parciales {hechos}, "
                  f"sin crédito {sin_credito}, fallidos {fallidos}")

    print(f"reconcile: {hechos} aplicaciones parciales, "
          f"{sin_credito} documentos sin crédito suficiente, "
          f"{fallidos} fallidos")


# ─────────────────────────────────────────────────────────────────────────────
# CROSSAPPLY — compensaciones documento contra documento, sin caja
# ─────────────────────────────────────────────────────────────────────────────
def step_crossapply():
    """Aplica los cruces que Profit registra como cobros de monto cero.

    Por ejemplo el cobro 2909, que cancela la N/CR 447 contra la N/DB 132 por
    $343.098,91 sin que entre un centavo. reconcile_plan.json los excluye (824
    cobros, 3.485 renglones, ~$30,1M), así que en Odoo esos documentos quedaban
    colgados con su saldo completo.

    Debe correr ANTES de trueup, para que el ajuste MIGAJ sólo absorba lo que
    de verdad es diferencial cambiario y no castigue documentos de negocio.
    """
    # ADEL y AJPA no tienen asiento propio: cuelgan del cobro que los originó.
    anticipos = {}
    for r in raw_rows("docum_cc"):
        if r["tipo_doc"] in ("ADEL", "AJPA"):
            anticipos[(r["tipo_doc"], r["nro_doc"].strip())] = \
                "profit_cob_" + r["nro_orig"].strip()

    prefijos = {"FACT": "profit_fac_", "N/DB": "profit_nd_", "N/CR": "profit_nc_"}

    def doc_xmlid(tipo, nro):
        nro = nro.strip()
        if tipo in prefijos:
            return prefijos[tipo] + nro
        return anticipos.get((tipo, nro))

    sin_efectivo = {
        c["cob_num"].strip() for c in raw_rows("cobros")
        if str(c["anulado"]).strip().lower() in ("0", "false", "")
        and abs(f(c["monto"])) < 0.0001
    }

    grupos = {}
    for r in raw_rows("reng_cob"):
        cob = r["cob_num"].strip()
        if cob not in sin_efectivo or abs(f(r["neto"])) < 0.0001:
            continue
        xml = doc_xmlid(r["tp_doc_cob"].strip(), r["doc_num"])
        if xml:
            grupos.setdefault(cob, set()).add(xml)
    # Un solo documento no cruza contra nada.
    grupos = {c: n for c, n in grupos.items() if len(n) > 1}
    print(f"  cruces sin efectivo detectados: {len(grupos)}")
    if not grupos:
        return

    todos = set().union(*grupos.values())
    move_map = xmlid_map({n for n in todos if not n.startswith("profit_cob_")},
                         "account.move")
    cob_move = _payment_moves({n for n in todos if n.startswith("profit_cob_")})
    rec_lines = _receivable_lines(set(move_map.values()) | set(cob_move.values()))

    done = failed = skipped = 0
    for cob, nombres in sorted(grupos.items(), key=lambda kv: int(kv[0])):
        cand = []
        for name in nombres:
            mid = (cob_move if name.startswith("profit_cob_") else move_map).get(name)
            cand += [l["id"] for l in rec_lines.get(mid, [])]

        status, msg = _apply_group(cand)
        if status == "done":
            done += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
            if failed <= 10:
                print(f"  [WARN] cruce {cob}: {msg}")

    print(f"crossapply: {done} aplicados, {failed} fallidos, {skipped} sin pareja")


# ─────────────────────────────────────────────────────────────────────────────
# TRUEUP — cuadre final contra los saldos de Profit
# ─────────────────────────────────────────────────────────────────────────────
def _saldos_profit():
    """xmlid del documento en Odoo -> saldo pendiente según Profit."""
    out = {}
    for r in raw_rows("docum_cc"):
        tipo = r["tipo_doc"]
        if tipo in ("ADEL", "AJPA"):
            # Sin asiento propio: el saldo del anticipo vive en su cobro.
            name = "profit_cob_" + r["nro_orig"].strip()
        elif tipo in ("FACT", "GIRO", "CHDV"):
            name = "profit_fac_" + r["nro_doc"].strip()
        elif tipo == "N/DB":
            # Las N/DB se cargan como profit_nd_ (15_account_move_nd.csv). Con
            # el prefijo profit_fac_ la clave nunca coincidía y el trueup no
            # llegaba a procesar ni una sola nota de débito.
            name = "profit_nd_" + r["nro_doc"].strip()
        elif tipo == "N/CR":
            name = "profit_nc_" + r["nro_doc"].strip()
        else:
            continue
        out[name] = f(r["saldo"])
    return out


def step_trueup():
    """Absorbe en el diario MIGAJ el residual que Profit da por saldado.

    Tras reconcile y crossapply quedan documentos con saldo en Odoo que en
    Profit están en cero. La diferencia es esencialmente cambiaria: Profit
    guarda el equivalente en Bs de cada cobro al cambio del día, y al reexpresar
    en USD no cierra al céntimo (ver docs/EXCHANGE_DIFFERENCES.md). Se crea un
    asiento por documento contra la cuenta de ajustes y se concilia.

    Dos fases: (A) crear y conciliar cada ajuste, (B) barrido de los ajustes
    que quedaron sueltos porque su contraparte cambió de estado por el camino.
    """
    saldo_profit = _saldos_profit()

    lines = call("account.move.line", "search_read",
                 [["account_id.account_type", "=", "asset_receivable"],
                  ["reconciled", "=", False]],
                 fields=["id", "move_id", "amount_residual",
                         "amount_residual_currency", "account_id", "currency_id"])
    print(f"  líneas por cobrar sin conciliar: {len(lines)}")

    move_ids = list({l["move_id"][0] for l in lines})
    xml_map = {}
    for i in range(0, len(move_ids), 2000):
        for r in call("ir.model.data", "search_read",
                      [["model", "=", "account.move"],
                       ["res_id", "in", move_ids[i:i + 2000]]],
                      fields=["name", "res_id"]):
            # En v17/v18 Odoo añadía _account_move al xmlid del asiento de pago.
            xml_map[r["res_id"]] = r["name"].replace("_account_move", "")

    # En Odoo 19 el asiento de un account.payment se crea al publicar y NO
    # recibe xmlid de importación: comprobado, 0 de 5.002 asientos de pago lo
    # tienen, frente a 5.273 de 5.273 asientos de factura. El bucle de arriba
    # no los encontraba, `xml` quedaba en None y el `continue` de abajo saltaba
    # TODOS los pagos: el trueup cuadraba los documentos pero nunca barría el
    # lado del pago, y el sobrante quedaba como "crédito pendiente" visible al
    # facturar (2.183 líneas, ~$2,18 M).
    # Se resuelven vía account.payment, que sí conserva su xmlid profit_cob_*.
    faltan = [mid for mid in move_ids if mid not in xml_map]
    if faltan:
        pay_by_move = {}
        for i in range(0, len(faltan), 2000):
            for p in call("account.payment", "search_read",
                          [["move_id", "in", faltan[i:i + 2000]]],
                          fields=["id", "move_id"]):
                pay_by_move[p["move_id"][0]] = p["id"]
        name_by_pay = {}
        pay_ids = list(pay_by_move.values())
        for i in range(0, len(pay_ids), 2000):
            for r in call("ir.model.data", "search_read",
                          [["module", "=", "__import__"],
                           ["model", "=", "account.payment"],
                           ["res_id", "in", pay_ids[i:i + 2000]]],
                          fields=["name", "res_id"]):
                name_by_pay[r["res_id"]] = r["name"]
        for mid, pid in pay_by_move.items():
            if pid in name_by_pay:
                xml_map[mid] = name_by_pay[pid]
        print(f"  asientos de pago resueltos por account.payment: "
              f"{len(pay_by_move)}")

    to_adjust = []
    for l in lines:
        xml = xml_map.get(l["move_id"][0])
        if not xml:
            continue
        esperado = saldo_profit.get(xml)
        if esperado is None:
            # Un cobro que no generó anticipo, o una NC sin fila en docum_cc,
            # está totalmente aplicado en Profit: su saldo esperado es cero.
            if not (xml.startswith("profit_cob_") or xml.startswith("profit_nc_")):
                continue
            esperado = 0.0
        residual = abs(l["amount_residual_currency"] or 0.0)
        if residual < 0.001:
            residual = abs(l["amount_residual"] or 0.0)
        if esperado == 0 and residual > 0.001:
            to_adjust.append(l)

    print(f"  documentos a ajustar: {len(to_adjust)}")
    if not to_adjust:
        return

    adj_account = xmlid_map(["profit_account_MIGAJ"], "account.account")
    adj_journal = xmlid_map([f"profit_journal_{ADJ_JOURNAL_CODE}"], "account.journal")
    if not adj_account or not adj_journal:
        raise SystemExit("Falta el diario o la cuenta de ajustes; "
                         "ejecuta primero el paso `setup`.")
    adj_account = next(iter(adj_account.values()))
    adj_journal = next(iter(adj_journal.values()))

    # Silenciar el chatter: 6.600 asientos generarían otros tantos mensajes.
    ctx = {"tracking_disable": True, "mail_notrack": True}

    creados = rec_ok = rec_fail = 0
    grandes = []
    for l in to_adjust:
        amt = -(l["amount_residual"] or 0.0)
        amt_curr = -(l["amount_residual_currency"] or 0.0)
        if abs(amt) < 0.001 and abs(amt_curr) < 0.001:
            continue
        # Con reconcile aplicando los importes exactos de Profit, aquí sólo
        # deberían caer céntimos de diferencia cambiaria. Un ajuste grande ya
        # no es redondeo: es un dato que no cuadra, y hay que verlo en vez de
        # que se lo trague MIGAJ en silencio. Se registra igual —Profit manda
        # sobre el saldo— pero se avisa.
        if max(abs(amt), abs(amt_curr)) > TRUEUP_AVISO:
            grandes.append((xml_map.get(l["move_id"][0]),
                            max(abs(amt), abs(amt_curr))))
        curr_id = l["currency_id"][0] if l.get("currency_id") else False
        xml = xml_map.get(l["move_id"][0])

        move_vals = {
            "move_type": "entry",
            "journal_id": adj_journal,
            "date": ADJ_DATE,
            # La referencia es la que usa la fase B para reencontrar el par.
            "ref": f"MIGAJ_PAY_{xml}",
            "line_ids": [
                (0, 0, {"account_id": l["account_id"][0],
                        "name": "Ajuste residual migración",
                        "debit": amt if amt > 0 else 0.0,
                        "credit": -amt if amt < 0 else 0.0,
                        "amount_currency": amt_curr,
                        "currency_id": curr_id}),
                (0, 0, {"account_id": adj_account,
                        "name": "Diferencia migración",
                        "debit": -amt if amt < 0 else 0.0,
                        "credit": amt if amt > 0 else 0.0,
                        "amount_currency": -amt_curr,
                        "currency_id": curr_id}),
            ],
        }
        new_id = call("account.move", "create", [move_vals], context=ctx)[0]
        post_safe("account.move", [new_id])

        contra = call("account.move.line", "search",
                      [["move_id", "=", new_id],
                       ["account_id", "=", l["account_id"][0]]], limit=1)
        if contra:
            # load17.py hacía `except Exception: pass` aquí y se tragaba
            # cualquier fallo real. Sólo se tolera el Fault de marshalling;
            # el resto se cuenta y la fase B vuelve a intentarlo.
            try:
                reconcile_lines([l["id"], contra[0]])
                rec_ok += 1
            except xmlrpc.client.Fault as exc:
                rec_fail += 1
                if rec_fail <= 10:
                    print(f"  [WARN] ajuste {xml}: {str(exc)[:200]}")
        creados += 1
        if creados % 500 == 0:
            print(f"  {creados}/{len(to_adjust)} ajustes creados "
                  f"({rec_ok} conciliados, {rec_fail} fallidos)")

    print(f"  ajustes creados: {creados} "
          f"({rec_ok} conciliados, {rec_fail} fallidos)")
    if grandes:
        total = sum(g[1] for g in grandes)
        print(f"  [AVISO] {len(grandes)} ajustes superan {TRUEUP_AVISO:,.2f} USD "
              f"(total {total:,.2f}). No son redondeo cambiario; revísalos:")
        for xml, imp in sorted(grandes, key=lambda g: -g[1])[:10]:
            print(f"      {xml}: {imp:,.2f}")

    # --- Fase B: barrido de los ajustes que quedaron sin conciliar -----------
    # Se buscan por `ref`, no por nombre de asiento: load17.py filtraba por
    # move_name like 'MIGAJ/2026/07/' y el barrido dejaba de funcionar en
    # cuanto la migración se ejecutaba en otro mes.
    print("  --- barrido de ajustes MIGAJ sueltos ---")
    ref_by_move = {
        m["id"]: m["ref"] for m in
        call("account.move", "search_read",
             [["ref", "like", "MIGAJ_PAY_"], ["journal_id", "=", adj_journal]],
             fields=["id", "ref"])
        if (m["ref"] or "").startswith("MIGAJ_PAY_")
    }
    if not ref_by_move:
        print("trueup OK (sin ajustes que barrer)")
        return

    pendientes = []
    ids = list(ref_by_move)
    for i in range(0, len(ids), 2000):
        pendientes += call("account.move.line", "search_read",
                           [["move_id", "in", ids[i:i + 2000]],
                            ["account_id.account_type", "=", "asset_receivable"],
                            ["reconciled", "=", False]],
                           fields=["id", "move_id"])
    print(f"  ajustes sin conciliar: {len(pendientes)}")
    if not pendientes:
        print(f"trueup OK ({creados} ajustes, todos conciliados)")
        return

    xmls = {ref_by_move[l["move_id"][0]][len("MIGAJ_PAY_"):] for l in pendientes}
    buscar = list(xmls) + [x + "_account_move" for x in xmls]
    orig = {}
    for i in range(0, len(buscar), 2000):
        for r in call("ir.model.data", "search_read",
                      [["model", "=", "account.move"],
                       ["name", "in", buscar[i:i + 2000]]],
                      fields=["name", "res_id"]):
            orig[r["name"].replace("_account_move", "")] = r["res_id"]

    barridos = huerfanos = fallidos = 0
    for l in pendientes:
        xml = ref_by_move[l["move_id"][0]][len("MIGAJ_PAY_"):]
        orig_id = orig.get(xml)
        if not orig_id:
            huerfanos += 1
            continue
        contra = call("account.move.line", "search",
                      [["move_id", "=", orig_id],
                       ["account_id.account_type", "=", "asset_receivable"],
                       ["reconciled", "=", False]], limit=1)
        if not contra:
            huerfanos += 1
            continue
        try:
            reconcile_lines([l["id"], contra[0]])
            barridos += 1
        except xmlrpc.client.Fault as exc:
            fallidos += 1
            if fallidos <= 10:
                print(f"  [WARN] barrido {xml}: {str(exc)[:200]}")

    print(f"trueup: {creados} ajustes creados, {rec_ok + barridos} conciliados, "
          f"{fallidos} fallidos, {huerfanos} sin contraparte")


# ─────────────────────────────────────────────────────────────────────────────
# STOCK
# ─────────────────────────────────────────────────────────────────────────────
def step_stock():
    """Crea un almacén por almacén de Profit y aplica las existencias.

    Las existencias negativas del origen se cargan tal cual: Profit arrastra
    stock negativo porque la empresa vende sin registrar todas las compras.
    Es un hallazgo de calidad de datos, no un error de la migración.
    """
    almacenes = plan_json("journal_map.json")["almacenes"]
    whs = call("stock.warehouse", "search_read", [],
               fields=["id", "code", "lot_stock_id"])
    default_wh = whs[0]
    for idx, code in enumerate(sorted(almacenes)):
        name = almacenes[code].title()
        if idx == 0:
            # El almacén que Odoo crea con la compañía se reutiliza.
            call("stock.warehouse", "write", [default_wh["id"]],
                 {"name": name, "code": f"A{code[:4]}"})
            loc = default_wh["lot_stock_id"][0]
        else:
            existing = [w for w in whs if w["code"] == f"A{code[:4]}"]
            if existing:
                loc = existing[0]["lot_stock_id"][0]
            else:
                wid = call("stock.warehouse", "create",
                           [{"name": name, "code": f"A{code[:4]}"}])[0]
                loc = call("stock.warehouse", "search_read", [["id", "=", wid]],
                           fields=["lot_stock_id"])[0]["lot_stock_id"][0]
        ensure_xmlid(f"profit_loc_{code}", "stock.location", loc)

    header, rows = read_csv("17_stock_quant.csv")
    load_model("stock.quant", header, rows)

    quant_ids = call("stock.quant", "search", [["inventory_quantity", "!=", 0]])
    if quant_ids:
        try:
            call("stock.quant", "action_apply_inventory", quant_ids)
        except xmlrpc.client.Fault as exc:
            if not is_marshal_fault(exc):
                raise
    print(f"stock: {len(quant_ids)} quants aplicados")


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY
# ─────────────────────────────────────────────────────────────────────────────
def step_verify():
    """Contrasta lo cargado en Odoo contra los saldos de Profit.

    Escribe export/verificacion19.md. No falla por sí mismo: el veredicto
    PASS/FAIL lo da tests_migracion.py, que compara contra los CSV crudos.
    """
    doc = ["# Verificación migración Profit → Odoo 19",
           "",
           f"Base `{ODOO['db']}` en {ODOO['url']}",
           ""]

    def add(label, value):
        doc.append(f"- **{label}**: {value}")
        print(f"  {label}: {value}")

    doc.append("## Volúmenes")
    add("Clientes", call("res.partner", "search_count", [["customer_rank", ">", 0]],
                         context={"active_test": False}))
    add("Productos", call("product.template", "search_count", [],
                          context={"active_test": False}))
    add("Facturas publicadas", call("account.move", "search_count",
                                    [["move_type", "=", "out_invoice"],
                                     ["state", "=", "posted"]]))
    add("Notas de crédito", call("account.move", "search_count",
                                 [["move_type", "=", "out_refund"],
                                  ["state", "=", "posted"]]))

    doc.append("")
    doc.append("## Pagos y anticipos")
    add("Pagos de cliente", call("account.payment", "search_count",
                                 [["partner_type", "=", "customer"]]))
    sin_asiento = call("account.payment", "search_count",
                       [["partner_type", "=", "customer"],
                        ["state", "!=", "draft"], ["move_id", "=", False]])
    add("Pagos sin asiento (debe ser 0)", sin_asiento)

    # Anticipos abiertos: el crédito que le queda al cliente a favor. Se compara
    # contra los ADEL de Profit que aún tienen saldo.
    adel_profit = sum(abs(f(r["saldo"])) for r in raw_rows("docum_cc")
                      if r["tipo_doc"] == "ADEL" and abs(f(r["saldo"])) > 0.001)
    pay_move_ids = [p["move_id"][0] for p in call(
        "account.payment", "search_read",
        [["partner_type", "=", "customer"], ["move_id", "!=", False]],
        fields=["move_id"])]
    credito = 0.0
    abiertos = 0
    for i in range(0, len(pay_move_ids), 5000):
        for l in call("account.move.line", "search_read",
                      [["move_id", "in", pay_move_ids[i:i + 5000]],
                       ["account_id.account_type", "=", "asset_receivable"],
                       ["reconciled", "=", False]],
                      fields=["amount_residual"]):
            if abs(l["amount_residual"]) > 0.001:
                credito += abs(l["amount_residual"])
                abiertos += 1
    add("Anticipos abiertos en Profit (USD)", f"{adel_profit:,.2f}")
    add("Crédito de pagos sin aplicar en Odoo (USD)", f"{credito:,.2f}")
    add("Líneas de pago con saldo abierto", abiertos)

    doc.append("")
    doc.append("## Integridad de la conciliación")
    # Una línea marcada como conciliada no puede conservar residual.
    incoherentes = call("account.move.line", "search_count",
                        [["reconciled", "=", True], ["amount_residual", "!=", 0]])
    add("Líneas conciliadas con residual ≠ 0 (debe ser 0)", incoherentes)
    migaj_sueltos = call("account.move.line", "search_count",
                         [["move_id.ref", "like", "MIGAJ_PAY_"],
                          ["account_id.account_type", "=", "asset_receivable"],
                          ["reconciled", "=", False]])
    add("Ajustes MIGAJ sin conciliar (debe ser 0)", migaj_sueltos)

    doc.append("")
    doc.append("## Cartera")
    objetivo = plan_json("saldo_objetivo.json")

    def _con_signo(name, val):
        """Las NC restan cartera; saldo_objetivo.json las guarda en positivo.

        Sin esta corrección el objetivo sumaba las notas de crédito abiertas en
        vez de restarlas —inflándolo en 2x su saldo, 46.734,62— y además las
        contaba a todas como discrepancia: `verify` reportaba 48 documentos
        distintos donde el test T8b, que sí aplica el signo, encuentra los que
        de verdad no cuadran.
        """
        return -abs(val) if name.startswith("profit_nc_") else val

    target = round(sum(_con_signo(k, v) for k, v in objetivo.items() if v), 2)
    move_map = xmlid_map(objetivo.keys(), "account.move")
    inv_map = {v: k for k, v in move_map.items()}
    total_res, mismatch, filas = 0.0, 0, []
    ids = list(move_map.values())
    for i in range(0, len(ids), 5000):
        for r in call("account.move", "search_read", [["id", "in", ids[i:i + 5000]]],
                      fields=["id", "name", "amount_residual", "move_type"]):
            # Las NC restan cartera: su residual viene positivo desde Odoo.
            sign = -1 if r["move_type"] == "out_refund" else 1
            res = round(sign * r["amount_residual"], 2)
            total_res += res
            esperado = _con_signo(inv_map[r["id"]],
                                  objetivo.get(inv_map[r["id"]], 0.0))
            if abs(res - esperado) >= 0.02:
                mismatch += 1
                filas.append((inv_map[r["id"]], r["name"], esperado, res))

    add("Saldo objetivo Profit (USD)", f"{target:,.2f}")
    add("Saldo residual Odoo (USD)", f"{round(total_res, 2):,.2f}")
    add("Diferencia (USD)", f"{round(total_res - target, 2):,.2f}")
    add("Documentos con saldo distinto a Profit", mismatch)

    qty = call("stock.quant", "read_group", [["location_id.usage", "=", "internal"]],
               ["quantity:sum"], [])
    add("Existencias totales (unid.)", f"{qty[0]['quantity']:,.0f}" if qty else 0)

    if filas:
        ruta = os.path.join(EXPORT_DIR, "mismatch19.csv")
        with open(ruta, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["xmlid", "asiento", "saldo_profit", "saldo_odoo"])
            w.writerows(sorted(filas, key=lambda r: -abs(r[3] - r[2])))
        doc.append("")
        doc.append(f"Detalle de las diferencias en `mismatch19.csv` "
                   f"({len(filas)} documentos).")
        print(f"  detalle escrito en {ruta}")

    ruta = os.path.join(EXPORT_DIR, "verificacion19.md")
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("\n".join(doc) + "\n")
    print(f"verificacion19.md escrito en {ruta}")


# ─────────────────────────────────────────────────────────────────────────────
# Orden de ejecución.
#
# crossapply va DESPUÉS de reconcile (necesita que los pagos normales ya estén
# aplicados) y ANTES de trueup (si no, MIGAJ absorbería como diferencial
# cambiario las compensaciones documento-contra-documento, que son negocio).
# ─────────────────────────────────────────────────────────────────────────────
STEPS = {
    "setup": step_setup,
    "masters": step_masters,
    "invoices": step_invoices,
    "payments": step_payments,
    "fix_payments": step_fix_payments,
    "reconcile": step_reconcile,
    "crossapply": step_crossapply,
    "trueup": step_trueup,
    "stock": step_stock,
    "verify": step_verify,
}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(STEPS)
    desconocidos = [s for s in wanted if s not in STEPS]
    if desconocidos:
        sys.exit(f"Paso(s) desconocido(s): {', '.join(desconocidos)}\n"
                 f"Disponibles: {', '.join(STEPS)}")

    t_total = time.time()
    for step in wanted:
        print(f"\n===== {step} =====")
        t0 = time.time()
        STEPS[step]()
        print(f"----- {step} en {time.time() - t0:.0f}s -----")
    print(f"\nTotal: {(time.time() - t_total) / 60:.1f} min")
