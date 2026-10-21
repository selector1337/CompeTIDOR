"""Diagnóstico pontual de frete: consulta somente leitura, sem exibir credenciais."""
import argparse
import json
import re
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('item_id')
    args = parser.parse_args()
    if not re.fullmatch(r'MLB\d+', args.item_id):
        parser.error('Informe um código MLB válido.')
    payload = server.read_json(server.APP_DATA_FILE, {})
    payload["catalog"] = server.read_json(server.CATALOG_DATA_FILE, payload.get("catalog", []))
    item = next((i for i in payload.get('catalog', []) if i.get('id') == args.item_id), None)
    if not item:
        print(json.dumps({'error':'Anúncio não encontrado na base configurada','data_dir':str(server.DATA)},ensure_ascii=False)); return
    account = next((a for a in payload.get('accounts', []) if a.get('id') == item.get('account_id')), {})
    output = {'item_id':args.item_id, 'account':account.get('nickname'), 'cached':{k:item.get(k) for k in ['shipping_cost','shipping_cost_status','shipping_cost_updated_at','shipping_mode','shipping_logistic_type','free_shipping']}}
    if not account.get('access_token') or not account.get('seller_id'):
        output['error']='Conta sem token ou seller_id'; print(json.dumps(output,ensure_ascii=False)); return
    output['cache_validation'] = {'with_account':server.shipping_cost_snapshot_valid(item,account), 'without_account':server.shipping_cost_snapshot_valid(item), 'listing_has_seller_id':bool(item.get('seller_id'))}
    start=time.monotonic()
    try:
        # Use existing token; no token refresh, local writes or listing changes.
        client=server.MercadoLivreClient(account['access_token'])
        response=client.item_shipping_cost(account['seller_id'],args.item_id)
        output['quote']=server.shipping_quote_values(response)
        output['response_fields']=list(response) if isinstance(response,dict) else []
        output['coverage_regions']=list(response.get('coverage') or {}) if isinstance(response,dict) else []
        output['ok']=True
    except Exception as exc:
        match=re.search(r'HTTP\s+(\d{3})',str(exc))
        output['ok']=False
        output['error_kind']=server.clip_error_kind(exc)
        output['http_status']=int(match[1]) if match else None
    output['elapsed_seconds']=round(time.monotonic()-start,2)
    print(json.dumps(output,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
