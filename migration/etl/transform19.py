"""Adapta los CSVs de importación (generados para Odoo 18) al modelo de Odoo 19.

Lee  export/odoo_csv/   (salida de etl/transform.py — NO se modifica)
Deja export/odoo_csv19/ (variante para Odoo 19)

Diferencias que cubre (todas confirmadas en la corrida v19 del 2026-07-11):
- Odoo 19 eliminó las categorías de UoM: se omite 01_uom_category.csv y
  02_uom_uom.csv queda solo con id,name (unidad raíz independiente).
- res.users: el campo m2m de grupos pasó de groups_id a group_ids.
- account.payment.term: sin xmlid por línea, re-importar duplica las líneas
  y Odoo rechaza el término con "la suma debe ser 100%".
- product.template: uom_po_id desapareció (unidades de compra/venta unificadas).
El resto de archivos se copia sin cambios.
"""
import csv
import os
import shutil

from config import ODOO_CSV_DIR, ODOO_CSV19_DIR

SRC = ODOO_CSV_DIR
DST = ODOO_CSV19_DIR


def rewrite(path_in, path_out, fn):
    with open(path_in, encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    rows = fn(rows)
    with open(path_out, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def main():
    os.makedirs(DST, exist_ok=True)
    for name in sorted(os.listdir(SRC)):
        src = os.path.join(SRC, name)
        dst = os.path.join(DST, name)
        if name == "01_uom_category.csv":
            print(f"{name}: omitido (Odoo 19 no tiene uom.category)")
            continue
        if name == "02_uom_uom.csv":
            rewrite(src, dst, lambda rows: [["id", "name"]] +
                    [[r[0], r[1]] for r in rows[1:]])
            print(f"{name}: reescrito para Odoo 19 (solo id,name)")
            continue
        if name == "04_res_users.csv":
            def fix_groups(rows):
                header = list(rows[0])
                if "groups_id/id" in header:
                    header[header.index("groups_id/id")] = "group_ids/id"
                return [header] + rows[1:]
            rewrite(src, dst, fix_groups)
            print(f"{name}: groups_id -> group_ids")
            continue
        if name == "05_account_payment_term.csv":
            def add_line_xmlids(rows):
                header = list(rows[0])
                if "line_ids/id" in header:
                    return rows
                header.append("line_ids/id")
                out = [header]
                for r in rows[1:]:
                    out.append(list(r) + [f"{r[0]}_l1" if r[0] else ""])
                return out
            rewrite(src, dst, add_line_xmlids)
            print(f"{name}: xmlid por línea (re-import idempotente)")
            continue
        if name == "09_product_template.csv":
            def drop_uom_po(rows):
                header = rows[0]
                if "uom_po_id/id" not in header:
                    return rows
                col = header.index("uom_po_id/id")
                return [r[:col] + r[col + 1:] for r in rows]
            rewrite(src, dst, drop_uom_po)
            print(f"{name}: columna uom_po_id eliminada (no existe en 19)")
            continue
        shutil.copyfile(src, dst)
        print(f"{name}: copiado")
    print(f"\nCSVs para Odoo 19 en {DST}")


if __name__ == "__main__":
    main()
