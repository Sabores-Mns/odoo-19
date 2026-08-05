"""Extrae las tablas de Profit (MULTI_A) a CSVs crudos en export/raw/."""
import csv
import os

import pytds

from config import MSSQL, RAW_DIR

# tabla -> SELECT. Solo columnas que usa la migración.
QUERIES = {
    "clientes": """
        SELECT co_cli, cli_des, tipo, direc1, direc2, ciudad, zip, telefonos, fax,
               email, rif, nit, inactivo, fecha_reg, saldo, mont_cre, plaz_pag,
               co_zon, co_seg, co_ven, tipo_iva, contribu, juridico, tipo_per, co_pais
        FROM clientes""",
    "prov": """
        SELECT co_prov, prov_des, direc1, direc2, ciudad, zip, telefonos, email,
               rif, nit, inactivo, fecha_reg, saldo, plaz_pag, nacional
        FROM prov""",
    "vendedor": """
        SELECT co_ven, ven_des, tipo, cedula, telefonos, email, comision, comisionv
        FROM vendedor""",
    "art": """
        SELECT co_art, art_des, co_lin, co_cat, co_subl, co_color, ref, modelo,
               co_prov, uni_venta, uni_compra, stock_act, anulado, tipo, tipo_imp,
               co_imp, prec_vta1, prec_vta2, prec_vta3, prec_vta4, prec_vta5,
               ult_cos_un, cos_pro_un, peso, alm_prin, fecha_reg
        FROM art""",
    "lin_art": "SELECT co_lin, lin_des FROM lin_art",
    "cat_art": "SELECT co_cat, cat_des FROM cat_art",
    "sub_lin": "SELECT co_subl, subl_des FROM sub_lin",
    "unidades": "SELECT co_uni, des_uni FROM unidades",
    "condicio": "SELECT co_cond, cond_des, dias_cred FROM condicio",
    "almacen": "SELECT co_alma, alma_des FROM almacen",
    "bancos": "SELECT co_ban, des_ban FROM bancos",
    "zona": "SELECT co_zon, zon_des FROM zona",
    "segmento": "SELECT co_seg, seg_des FROM segmento",
    "tipo_cli": "SELECT tipo_cli, tip_cli_des FROM tipo_cli",
    "moneda": "SELECT co_mone, mone_des, cambio FROM moneda",
    "tasas": "SELECT co_mone, fecha, tasa_c, tasa_v FROM tasas",
    "st_almac": "SELECT co_alma, co_art, stock_act FROM st_almac",
    "factura": """
        SELECT fact_num, fec_emis, fec_venc, fec_reg, co_cli, co_ven, moneda, tasa,
               tot_bruto, glob_desc, tot_reca, tot_flete, iva, tot_neto, saldo,
               anulada, status, num_control, nombre, rif, comentario, descrip
        FROM factura""",
    "reng_fac": """
        SELECT fact_num, reng_num, co_art, co_alma, total_art, uni_venta, prec_vta,
               porc_desc, tipo_imp, isv, reng_neto, des_art, anulado
        FROM reng_fac""",
    "docum_cc": """
        SELECT tipo_doc, nro_doc, co_cli, co_ven, fec_emis, fec_venc, fec_reg,
               moneda, tasa, monto_bru, monto_imp, monto_net, saldo, anulado,
               doc_orig, nro_orig, observa, num_control, origen
        FROM docum_cc""",
    "cobros": """
        SELECT cob_num, recibo, co_cli, co_ven, fec_cob, anulado, monto, dppago,
               mont_ncr, ncr, tasa, moneda, descrip, adel_num, origen
        FROM cobros""",
    "reng_cob": """
        SELECT cob_num, reng_num, tp_doc_cob, doc_num, neto, dppago, reng_ncr,
               moneda, tasa, monto_reten, ret_iva
        FROM reng_cob""",
    "mov_ban": "SELECT * FROM mov_ban",
    "mov_caj": "SELECT * FROM mov_caj",
}


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    with pytds.connect(
        MSSQL["server"], MSSQL["database"], MSSQL["user"], MSSQL["password"],
        port=MSSQL["port"], as_dict=False,
    ) as conn:
        for name, sql in QUERIES.items():
            cur = conn.cursor()
            try:
                cur.execute(sql)
            except pytds.tds_base.Error as exc:
                print(f"[WARN] {name}: {exc}")
                continue
            cols = [d[0] for d in cur.description]
            path = os.path.join(RAW_DIR, f"{name}.csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(cols)
                rows = 0
                for row in cur:
                    writer.writerow(
                        [v.strip() if isinstance(v, str) else v for v in row]
                    )
                    rows += 1
            print(f"{name}: {rows} filas -> {path}")


if __name__ == "__main__":
    main()
