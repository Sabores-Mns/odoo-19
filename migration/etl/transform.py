"""Transforma los CSVs crudos de Profit en CSVs de importación Odoo + planes JSON.

Entrada:  /export/raw/*.csv      (generados por extract.py)
Salida:   /export/odoo_csv/*.csv (formato de importación estándar de Odoo)
          /export/plan/*.json    (plan de conciliación y mapa de diarios para load_xmlrpc.py)
"""
import csv
import json
import os
import re
from collections import defaultdict

from config import RAW_DIR, ODOO_CSV_DIR

PLAN_DIR = os.path.join(os.path.dirname(ODOO_CSV_DIR.rstrip("/")), "plan")

MONEDAS_USD = {"US$", "USD"}  # ambos códigos de Profit son dólares


def read(name):
    with open(os.path.join(RAW_DIR, f"{name}.csv"), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(name, header, rows):
    path = os.path.join(ODOO_CSV_DIR, name)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    print(f"{name}: {len(rows)} filas")


def ext(prefix, code):
    return prefix + re.sub(r"[^A-Za-z0-9]", "_", code.strip())


def d(value):
    """'2024-12-26 00:00:00' -> '2024-12-26'"""
    return value.split(" ")[0] if value else ""


def f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_true(value):
    return str(value).strip().lower() in ("true", "1")


def main():
    os.makedirs(ODOO_CSV_DIR, exist_ok=True)
    os.makedirs(PLAN_DIR, exist_ok=True)

    clientes = read("clientes")
    prov = read("prov")
    vendedores = read("vendedor")
    arts = read("art")
    lin_art = {r["co_lin"]: r["lin_des"] for r in read("lin_art")}
    sub_lin = {r["co_subl"]: r["subl_des"] for r in read("sub_lin")}
    cat_art = {r["co_cat"]: r["cat_des"] for r in read("cat_art")}
    unidades = read("unidades")
    almacenes = read("almacen")
    bancos = read("bancos")
    zonas = read("zona")
    segmentos = read("segmento")
    tasas = read("tasas")
    st_almac = read("st_almac")
    facturas = read("factura")
    reng_fac = read("reng_fac")
    docum_cc = read("docum_cc")
    cobros = read("cobros")
    reng_cob = read("reng_cob")
    mov_ban = read("mov_ban")
    mov_caj = read("mov_caj")

    ven_codes = {v["co_ven"] for v in vendedores}

    # ------------------------------------------------------------------ UoM
    uni_usadas = sorted({a["uni_venta"].strip() for a in arts if a["uni_venta"].strip()})
    write_csv(
        "01_uom_category.csv",
        ["id", "name"],
        [[ext("profit_uomcat_", u), f"Profit {u}"] for u in uni_usadas],
    )
    uni_des = {u["co_uni"]: u["des_uni"] for u in unidades}
    write_csv(
        "02_uom_uom.csv",
        ["id", "name", "category_id/id", "uom_type", "rounding"],
        [
            [ext("profit_uom_", u), uni_des.get(u, u).title() or u,
             ext("profit_uomcat_", u), "reference", 0.0001]
            for u in uni_usadas
        ],
    )

    # --------------------------------------------------- Etiquetas de partner
    tag_rows = [["profit_tag_root_zona", "Zona Profit", ""],
                ["profit_tag_root_seg", "Segmento Profit", ""],
                ["profit_tag_root_tipo", "Tipo Cliente Profit", ""]]
    for z in zonas:
        tag_rows.append([ext("profit_zona_", z["co_zon"]),
                         z["zon_des"].strip() or z["co_zon"], "profit_tag_root_zona"])
    for s in segmentos:
        tag_rows.append([ext("profit_seg_", s["co_seg"]),
                         s["seg_des"].strip() or s["co_seg"], "profit_tag_root_seg"])
    tipos_cli = sorted({c["tipo"].strip() for c in clientes if c["tipo"].strip()})
    for t in tipos_cli:
        tag_rows.append([ext("profit_tipo_", t), t, "profit_tag_root_tipo"])
    write_csv("03_res_partner_category.csv",
              ["id", "name", "parent_id/id"], tag_rows)

    # ------------------------------------------------------------ Vendedores
    write_csv(
        "04_res_users.csv",
        ["id", "login", "name", "groups_id/id"],
        [
            [ext("profit_ven_", v["co_ven"]),
             f"ven.{v['co_ven'].strip().lower()}@profit.local",
             v["ven_des"].strip().title(), "base.group_user"]
            for v in vendedores
        ],
    )

    # ----------------------------------------------------- Términos de pago
    dias = sorted({int(f(c["plaz_pag"])) for c in clientes} | {0, 7, 15, 30, 45})
    term_rows = []
    for n in dias:
        term_rows.append([
            f"profit_term_{n}",
            "Contado (Profit)" if n == 0 else f"{n} días (Profit)",
            "percent", 100, n, "days_after",
        ])
    write_csv(
        "05_account_payment_term.csv",
        ["id", "name", "line_ids/value", "line_ids/value_amount",
         "line_ids/nb_days", "line_ids/delay_type"],
        term_rows,
    )

    # ------------------------------------------------- Categorías de producto
    cat_rows = [["profit_cat_BEB", lin_art.get("BEB", "BEBIDAS").strip().title(), ""]]
    for code, name in sub_lin.items():
        cat_rows.append([ext("profit_cat_sub_", code), name.strip().title(), "profit_cat_BEB"])
    for code, name in cat_art.items():
        if code.strip() != "PT":
            cat_rows.append([ext("profit_cat_", code), name.strip().title(), ""])
    write_csv("06_product_category.csv", ["id", "name", "parent_id/id"], cat_rows)

    # ---------------------------------------------------------------- Clientes
    cli_rows = []
    for c in clientes:
        code = c["co_cli"].strip()
        tags = []
        if c["co_zon"].strip():
            tags.append(ext("profit_zona_", c["co_zon"]))
        if c["co_seg"].strip():
            tags.append(ext("profit_seg_", c["co_seg"]))
        if c["tipo"].strip():
            tags.append(ext("profit_tipo_", c["tipo"]))
        cli_rows.append([
            ext("profit_cli_", code),
            c["cli_des"].strip(),
            code,
            c["rif"].strip(),
            (c["direc1"] or "").strip()[:250],
            (c["direc2"] or "").strip()[:250],
            (c["ciudad"] or "").strip(),
            (c["zip"] or "").strip(),
            (c["telefonos"] or "").strip(),
            (c["email"] or "").strip().lower(),
            "base.ve" if c["co_pais"].strip().upper() == "VE" else "",
            1,
            "0" if is_true(c["inactivo"]) else "1",
            ",".join(tags),
            ext("profit_ven_", c["co_ven"]) if c["co_ven"].strip() in ven_codes else "",
            f"profit_term_{int(f(c['plaz_pag']))}",
            "1" if is_true(c["juridico"]) else "0",
        ])
    write_csv(
        "07_res_partner_clientes.csv",
        ["id", "name", "ref", "vat", "street", "street2", "city", "zip", "phone",
         "email", "country_id/id", "customer_rank", "active", "category_id/id",
         "user_id/id", "property_payment_term_id/id", "is_company"],
        cli_rows,
    )

    # ------------------------------------------------------------- Proveedores
    prov_rows = []
    for p in prov:
        code = p["co_prov"].strip()
        prov_rows.append([
            ext("profit_prov_", code),
            p["prov_des"].strip(),
            code,
            p["rif"].strip(),
            (p["direc1"] or "").strip()[:250],
            (p["ciudad"] or "").strip(),
            (p["telefonos"] or "").strip(),
            (p["email"] or "").strip().lower(),
            "base.ve" if is_true(p["nacional"]) else "",
            1,
            "0" if is_true(p["inactivo"]) else "1",
            "1",
        ])
    write_csv(
        "08_res_partner_prov.csv",
        ["id", "name", "ref", "vat", "street", "city", "phone", "email",
         "country_id/id", "supplier_rank", "active", "is_company"],
        prov_rows,
    )

    # ---------------------------------------------------------------- Productos
    art_uom = {}
    prod_rows = []
    for a in arts:
        code = a["co_art"].strip()
        uni = a["uni_venta"].strip() or "UND"
        art_uom[code] = uni
        co_cat = a["co_cat"].strip()
        if co_cat == "PT":
            categ = (ext("profit_cat_sub_", a["co_subl"])
                     if a["co_subl"].strip() else "profit_cat_BEB")
            ptype, storable = "consu", "1"
        else:
            categ = ext("profit_cat_", co_cat) if co_cat else "profit_cat_BEB"
            ptype, storable = "service", "0"
        cost = f(a["cos_pro_un"]) or f(a["ult_cos_un"])
        prod_rows.append([
            ext("profit_art_", code),
            a["art_des"].strip(),
            code,
            categ,
            ptype,
            storable,
            ext("profit_uom_", uni),
            ext("profit_uom_", uni),
            round(f(a["prec_vta1"]), 4),
            round(cost, 4),
            (a["ref"] or "").strip(),
            "0" if is_true(a["anulado"]) else "1",
            round(f(a["peso"]), 4),
        ])
    write_csv(
        "09_product_template.csv",
        ["id", "name", "default_code", "categ_id/id", "type", "is_storable",
         "uom_id/id", "uom_po_id/id", "list_price", "standard_price",
         "barcode", "active", "weight"],
        prod_rows,
    )

    # --------------------------------------------------------- Listas de precio
    write_csv(
        "10_product_pricelist.csv",
        ["id", "name", "currency_id/id"],
        [[f"profit_pricelist_{n}", f"Precio {n} (Profit)", "base.USD"] for n in (2, 3, 4)],
    )
    item_rows = []
    for a in arts:
        code = a["co_art"].strip()
        for n in (2, 3, 4):
            price = f(a[f"prec_vta{n}"])
            if price > 0:
                item_rows.append([
                    f"profit_plitem_{n}_{ext('', code)}",
                    f"profit_pricelist_{n}",
                    "1_product",
                    ext("profit_art_", code),
                    round(price, 4),
                ])
    write_csv(
        "11_product_pricelist_item.csv",
        ["id", "pricelist_id/id", "applied_on", "product_tmpl_id/id", "fixed_price"],
        item_rows,
    )

    # -------------------------------------------------------- Tasas de cambio
    rate_rows = []
    for t in tasas:
        if t["co_mone"].strip() == "USD" and f(t["tasa_v"]) > 0:
            rate_rows.append([
                ext("profit_rate_", d(t["fecha"])),
                d(t["fecha"]),
                "base.USD",
                "base.main_company",
                round(1.0 / f(t["tasa_v"]), 12),
            ])
    write_csv(
        "12_res_currency_rate.csv",
        ["id", "name", "currency_id/id", "company_id/id", "rate"],
        rate_rows,
    )

    # ------------------------------------------------------------- Facturas
    lines_by_fact = defaultdict(list)
    for r in reng_fac:
        if not is_true(r["anulado"]):
            lines_by_fact[r["fact_num"]].append(r)

    fact_header = [
        "id", "move_type", "partner_id/id", "invoice_date", "invoice_date_due",
        "date", "name", "ref", "payment_reference", "currency_id/id",
        "invoice_user_id/id", "narration",
        "invoice_line_ids/product_id/id", "invoice_line_ids/name",
        "invoice_line_ids/quantity", "invoice_line_ids/price_unit",
        "invoice_line_ids/tax_ids/id",
    ]
    fact_rows = []
    skipped_curr = 0
    for fa in facturas:
        if is_true(fa["anulada"]):
            continue
        if fa["moneda"].strip() not in MONEDAS_USD:
            skipped_curr += 1
            continue
        num = fa["fact_num"]
        fid = f"profit_fac_{num}"
        emis, venc = d(fa["fec_emis"]), d(fa["fec_venc"]) or d(fa["fec_emis"])
        ven = (ext("profit_ven_", fa["co_ven"])
               if fa["co_ven"].strip() in ven_codes else "")
        narr = " ".join(x for x in [fa["comentario"], fa["descrip"]] if x and x.strip())
        empty_head = ["", "", "", "", "", "", "", "", "", "", "", ""]
        first = True
        sum_neto = 0.0
        for r in lines_by_fact.get(num, []):
            head = ([fid, "out_invoice", ext("profit_cli_", fa["co_cli"]),
                     emis, venc, emis, f"FAC/{num}",
                     fa["num_control"].strip() if fa["num_control"] else "",
                     f"CTRL-{fa['num_control']}" if fa["num_control"] else "",
                     "base.USD", ven, narr.strip()[:500]]
                    if first else empty_head)
            first = False
            desc = (r["des_art"] or "").strip()
            qty = f(r["total_art"])
            neto = f(r["reng_neto"])  # neto del renglón CON descuentos aplicados
            if qty:
                price = neto / qty
            else:
                qty, price = 1, neto
            sum_neto += neto
            fact_rows.append(head + [
                ext("profit_artvar_", r["co_art"]),
                desc[:500] if desc else r["co_art"].strip(),
                qty,
                repr(price),
                "",
            ])
        # descuento global / recargo / flete de cabecera: línea de ajuste para
        # que el total del asiento sea EXACTO al tot_neto de Profit
        if not first:
            ajuste = round(f(fa["tot_neto"]) - sum_neto, 2)
            if abs(ajuste) >= 0.01:
                fact_rows.append(empty_head + [
                    "", "Ajuste global Profit (descuento/recargo/flete)",
                    1, repr(ajuste), "",
                ])
    write_csv("13_account_move_facturas.csv", fact_header, fact_rows)
    if skipped_curr:
        print(f"  [AVISO] {skipped_curr} facturas en moneda no-USD omitidas")

    # ------------------------------------------- Notas de crédito y débito CxC
    simple_header = [
        "id", "move_type", "partner_id/id", "invoice_date", "invoice_date_due",
        "date", "name", "ref", "currency_id/id", "narration",
        "invoice_line_ids/name", "invoice_line_ids/quantity",
        "invoice_line_ids/price_unit", "invoice_line_ids/tax_ids/id",
    ]
    nc_rows, nd_rows = [], []
    for r in docum_cc:
        tipo = r["tipo_doc"].strip()
        if tipo not in ("N/CR", "N/DB") or is_true(r["anulado"]):
            continue
        num = r["nro_doc"]
        emis, venc = d(r["fec_emis"]), d(r["fec_venc"]) or d(r["fec_emis"])
        monto = f(r["monto_net"])
        if monto <= 0:
            continue
        obs = (r["observa"] or "").strip()
        orig = f"{r['doc_orig'].strip()} {r['nro_orig']}".strip()
        if tipo == "N/CR":
            nc_rows.append([
                f"profit_nc_{num}", "out_refund", ext("profit_cli_", r["co_cli"]),
                emis, venc, emis, f"NC/{num}", orig, "base.USD", obs[:500],
                f"Nota de crédito Profit {num}" + (f" ({obs[:80]})" if obs else ""),
                1, round(monto, 4), "",
            ])
        else:
            nd_rows.append([
                f"profit_nd_{num}", "out_invoice", ext("profit_cli_", r["co_cli"]),
                emis, venc, emis, f"ND/{num}", orig, "base.USD", obs[:500],
                f"Nota de débito Profit {num}" + (f" ({obs[:80]})" if obs else ""),
                1, round(monto, 4), "",
            ])
    write_csv("14_account_move_nc.csv", simple_header, nc_rows)
    write_csv("15_account_move_nd.csv", simple_header, nd_rows)

    # ------------------------------------------------------ Diario por cobro
    journal_by_cobro = {}
    for m in mov_ban:
        if m["origen"].strip() == "COB" and not is_true(m["anulado"]):
            cob = m["cob_pag"].strip()
            if cob and cob not in journal_by_cobro:
                journal_by_cobro[cob] = m["codigo"].strip()
    for m in mov_caj:
        if m["origen"].strip() == "COB" and not is_true(m["anulado"]):
            cob = m["cob_pag"].strip()
            if cob and cob not in journal_by_cobro:
                journal_by_cobro[cob] = "CAJA"

    # ---------------------------------------------------------------- Cobros
    pay_rows = []
    for c in cobros:
        if is_true(c["anulado"]):
            continue
        monto = f(c["monto"])
        if monto <= 0:
            continue
        pay_rows.append([
            f"profit_cob_{c['cob_num']}",
            "inbound", "customer",
            ext("profit_cli_", c["co_cli"]),
            round(monto, 2),
            "base.USD",
            d(c["fec_cob"]),
            (c["recibo"] or "").strip() or f"Cobro Profit {c['cob_num']}",
            journal_by_cobro.get(c["cob_num"], ""),
        ])
    write_csv(
        "16_account_payment_cobros.csv",
        ["id", "payment_type", "partner_type", "partner_id/id", "amount",
         "currency_id/id", "date", "memo", "x_journal_profit"],
        pay_rows,
    )

    # ------------------------------------------------------------- Inventario
    stock_rows = []
    for s in st_almac:
        qty = f(s["stock_act"])
        art_code = s["co_art"].strip()
        if art_code and qty != 0:
            stock_rows.append([
                ext("profit_loc_", s["co_alma"]),
                ext("profit_artvar_", art_code),
                qty,
            ])
    write_csv(
        "17_stock_quant.csv",
        ["location_id/id", "product_id/id", "inventory_quantity"],
        stock_rows,
    )

    # ------------------------------------------------------------ Planes JSON
    adel_source = {}   # nro ADEL -> cobro que lo creó
    for r in docum_cc:
        if r["tipo_doc"].strip() == "ADEL" and r["doc_orig"].strip() == "COBR":
            adel_source[r["nro_doc"]] = r["nro_orig"]

    valid_fact = {fa["fact_num"] for fa in facturas
                  if not is_true(fa["anulada"]) and fa["moneda"].strip() in MONEDAS_USD}
    valid_nc = {r[0].split("_")[-1] for r in nc_rows}
    valid_nd = {r[0].split("_")[-1] for r in nd_rows}
    valid_cob = {r[0].split("_")[-1] for r in pay_rows}
    # el plan incluye también cobros sin efectivo (monto=0: aplicación pura de
    # anticipos/NC); esos no generan account.payment pero sí concilian
    plan_cob = {c["cob_num"] for c in cobros if not is_true(c["anulado"])}

    # Cada renglón lleva su importe (`neto`), no sólo el documento. Sin él,
    # load19 dejaba que Odoo repartiera a su criterio —la conciliación
    # automática aplica el MÁXIMO posible— y una factura se cancelaba entera
    # aunque Profit dijera que sólo se le abonó una parte. Ejemplo real: el
    # cobro 5448 abona 8.572,65 a la FAC 11564 de 13.392,00, y Odoo la daba
    # por pagada del todo, borrando 4.819,35 de deuda viva del cliente.
    plan = defaultdict(lambda: {"docs": [], "fuentes": []})
    for r in reng_cob:
        cob = r["cob_num"]
        if cob not in plan_cob:
            continue
        tp, doc = r["tp_doc_cob"].strip(), r["doc_num"].strip()
        monto = round(abs(f(r["neto"])), 2)
        entry = plan[cob]
        if tp == "FACT" and doc in valid_fact:
            entry["docs"].append({"id": f"profit_fac_{doc}", "monto": monto})
        elif tp == "N/DB" and doc in valid_nd:
            entry["docs"].append({"id": f"profit_nd_{doc}", "monto": monto})
        elif tp == "N/CR" and doc in valid_nc:
            entry["fuentes"].append({"id": f"profit_nc_{doc}", "monto": monto})
        elif tp == "ADEL":
            src = adel_source.get(doc)
            if src and src != cob and src in valid_cob:
                entry["fuentes"].append({"id": f"profit_cob_{src}",
                                         "monto": monto})

    with open(os.path.join(PLAN_DIR, "reconcile_plan.json"), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=1)
    print(f"reconcile_plan.json: {len(plan)} cobros")

    # saldos objetivo por documento (para el cuadre exacto post-conciliación)
    objetivo = {}
    for r in docum_cc:
        tipo = r["tipo_doc"].strip()
        num = r["nro_doc"]
        if is_true(r["anulado"]):
            continue
        if tipo == "FACT" and num in valid_fact:
            objetivo[f"profit_fac_{num}"] = round(f(r["saldo"]), 2)
        elif tipo == "N/CR" and num in valid_nc:
            objetivo[f"profit_nc_{num}"] = round(f(r["saldo"]), 2)
        elif tipo == "N/DB" and num in valid_nd:
            objetivo[f"profit_nd_{num}"] = round(f(r["saldo"]), 2)
    with open(os.path.join(PLAN_DIR, "saldo_objetivo.json"), "w", encoding="utf-8") as fh:
        json.dump(objetivo, fh, indent=1)
    print(f"saldo_objetivo.json: {len(objetivo)} documentos")

    bancos_map = {b["co_ban"].strip(): b["des_ban"].strip() for b in bancos}
    with open(os.path.join(PLAN_DIR, "journal_map.json"), "w", encoding="utf-8") as fh:
        json.dump({"bancos": bancos_map,
                   "almacenes": {a["co_alma"].strip(): a["alma_des"].strip()
                                 for a in almacenes}}, fh, indent=1)
    print("journal_map.json listo")

    # avisos de calidad
    uni_reng = {r["uni_venta"].strip() for r in reng_fac if r["uni_venta"].strip()}
    extra = uni_reng - set(uni_usadas)
    if extra:
        print(f"  [AVISO] unidades en renglones sin producto asociado: {extra}")


if __name__ == "__main__":
    main()
