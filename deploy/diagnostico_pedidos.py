"""Diagnóstico SOMENTE LEITURA: pedidos do Mercado Livre x pedidos no CompeTIDOR.

Para uma conta e um mês, busca os pedidos exatamente como o app busca, compara
com os que o CompeTIDOR guardou e explica pedidos específicos (status, data,
valor, carrinho) para mostrar por que ficaram de fora. Nada é gravado e o token
salvo não é renovado; se estiver vencido, rode de novo daqui a alguns minutos.

Uso no servidor (mês no formato AAAA-MM; os códigos de pedido são opcionais):

  sudo -u www-data bash -c 'set -a; . /etc/competidor.env; cd /opt/competidor; \
    .venv/bin/python deploy/diagnostico_pedidos.py "AGUITARRADEPRATA" 2026-11 2000016101789289'
"""
import collections
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402  (importar não inicia o servidor nem as rotinas)


def describe(order):
    return (f"status={order.get('status')} criado={order.get('date_created')} fechado={order.get('date_closed')} "
            f"total={order.get('total_amount')} pago={order.get('paid_amount')} carrinho(pack)={order.get('pack_id')} "
            f"tags={order.get('tags')} seller={(order.get('seller') or {}).get('id')}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    wanted = sys.argv[1].strip().lower()
    period = sys.argv[2] if len(sys.argv) > 2 else server.current_month_period()
    order_ids = [value.strip().lstrip("#") for value in sys.argv[3:] if value.strip()]
    payload = server.read_payload(include_catalog=False)
    account = next((a for a in payload.get("accounts") or [] if a.get("official") and wanted in {
        str(a.get("nickname") or "").lower(), str(a.get("id")), str(a.get("seller_id"))}), None)
    if not account:
        print("Conta não encontrada:", ", ".join(str(a.get("nickname")) for a in payload.get("accounts") or []))
        return 1
    client = server.MercadoLivreClient(account["access_token"])
    period, start, end = server.sales_goal_month(period)
    upper = min(end, server.datetime.now(server.APP_TZ)) - timedelta(milliseconds=1)
    print(f"Conta {account.get('nickname')} (seller {account.get('seller_id')}) · mês {period} · "
          f"{start.isoformat()} até {upper.isoformat()} (fuso do app: {server.APP_TZ})")

    print("\n=== 1. Pedidos que a busca do Mercado Livre devolve (mesma busca do app) ===")
    try:
        orders = server.fetch_seller_orders_window(client, account.get("seller_id"),
                                                   start.isoformat(timespec="milliseconds"), upper.isoformat(timespec="milliseconds"))
    except Exception as exc:
        print("A busca falhou:", exc)
        orders = []
    by_id = {str(o.get("id")): o for o in orders if o.get("id")}
    statuses = collections.Counter(str(o.get("status")) for o in orders)
    revenue = [o for o in orders if server.official_order_is_revenue(o)]
    packs = {str(o.get("pack_id")) for o in orders if o.get("pack_id")}
    print(f"Total devolvido: {len(orders)} · contados como venda: {len(revenue)} · por status: {dict(statuses)}")
    print(f"Pedidos dentro de carrinho (pack): {sum(1 for o in orders if o.get('pack_id'))} em {len(packs)} carrinhos")
    print(f"Faturamento contado: R$ {sum(server.official_order_revenue_amount(o) for o in revenue):,.2f}")

    print("\n=== 2. O que o CompeTIDOR guardou para este mês ===")
    monthly = payload.get("monthly_revenue") or {}
    record = server.account_record(((monthly.get("history") or {}).get(period) or {}).get("accounts"), account) or {}
    if not record and monthly.get("period") == period:
        record = server.account_record(monthly.get("accounts"), account) or {}
    stored = set(record.get("order_ids") or [])
    print(f"Pedidos guardados: {record.get('orders_count')} · faturamento: {record.get('amount')} · "
          f"atualizado em: {record.get('updated_at')} · situação: {record.get('sync_status')}")
    only_ml = sorted(set(by_id) - stored)
    only_app = sorted(stored - set(by_id))
    print(f"Na busca do Mercado Livre e não guardados no app ({len(only_ml)}):")
    for identity in only_ml[:40]:
        print("  -", identity, describe(by_id[identity]))
    print(f"Guardados no app e que a busca não devolveu agora ({len(only_app)}): {', '.join(only_app[:40]) or 'nenhum'}")

    if order_ids:
        print("\n=== 3. Pedidos específicos ===")
    for identity in order_ids:
        print(f"\nPedido {identity}:")
        try:
            order = client.order(identity)
        except Exception as exc:
            print("  Leitura direta falhou:", exc, "(se for o número do carrinho/pack, informe o número do pedido)")
            continue
        print(" ", describe(order))
        print("  Itens:", [(line.get("item") or {}).get("id") for line in order.get("order_items") or []])
        print("  Volta na busca do mês:", identity in by_id, "· guardado no app:", identity in stored,
              "· conta como venda:", server.official_order_is_revenue(order))
        created = server.parse_meli_datetime(order.get("date_created"))
        if created and not (start <= created < end):
            print("  Atenção: pela data de criação ele pertence a outro mês no fuso do app.")
        if created and identity not in by_id:
            day_from = (created - timedelta(hours=12)).isoformat(timespec="milliseconds")
            day_to = (created + timedelta(hours=12)).isoformat(timespec="milliseconds")
            try:
                near = client.seller_orders(account.get("seller_id"), limit=50, date_from=day_from, date_to=day_to)
                ids = [str(o.get("id")) for o in near.get("results") or []]
                print(f"  Busca só em volta da data ({day_from} a {day_to}): {len(ids)} pedidos; contém este? {identity in ids}")
            except Exception as exc:
                print("  Busca em volta da data falhou:", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
