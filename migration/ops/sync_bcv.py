import xmlrpc.client
import urllib.request
import ssl
from bs4 import BeautifulSoup
import datetime

import os

# --- CONFIGURACION ODOO ---
url = os.getenv('ODOO_URL', 'http://localhost:8069')
db = os.getenv('ODOO_DB', 'profit_migrado17')
username = 'admin'
password = 'admin'
# ---------------------------

def get_bcv_rate():
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request('https://www.bcv.org.ve/', headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, context=ctx).read()
        soup = BeautifulSoup(html, 'html.parser')
        
        dolar_div = soup.find('div', id='dolar')
        if dolar_div:
            return float(dolar_div.find('strong').text.strip().replace(',', '.'))
    except Exception as e:
        print(f"Error obteniendo tasa del BCV: {e}")
    return None

def sync_rate_odoo(rate):
    try:
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        uid = common.authenticate(db, username, password, {})
        models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
        
        # Buscar el ID de la moneda VES (Bolívares)
        ves_ids = models.execute_kw(db, uid, password, 'res.currency', 'search', [[['name', '=', 'VES']]])
        if not ves_ids:
            print("No se encontró la moneda VES en Odoo.")
            return

        currency_id = ves_ids[0]
        today = datetime.date.today().strftime('%Y-%m-%d')
        
        # La tasa a inyectar es directa
        odoo_rate = rate

        # Verificar si la tasa de hoy ya existe
        existing = models.execute_kw(db, uid, password, 'res.currency.rate', 'search', [[['currency_id', '=', currency_id], ['name', '=', today]]])
        
        if existing:
            # Actualizar
            models.execute_kw(db, uid, password, 'res.currency.rate', 'write', [existing, {'rate': odoo_rate}])
            print(f"Tasa actualizada para {today}: {odoo_rate}")
        else:
            # Crear
            models.execute_kw(db, uid, password, 'res.currency.rate', 'create', [{'currency_id': currency_id, 'name': today, 'rate': odoo_rate}])
            print(f"Nueva tasa creada para {today}: {odoo_rate} (equivale a {rate} Bs/$)")

    except Exception as e:
        print(f"Error conectando con Odoo: {e}")

if __name__ == '__main__':
    print("Obteniendo tasa del BCV...")
    bcv_rate = get_bcv_rate()
    if bcv_rate:
        print(f"Tasa obtenida: {bcv_rate}")
        sync_rate_odoo(bcv_rate)
