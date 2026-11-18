"""Diagnóstico SOMENTE LEITURA: pedidos do Mercado Livre x pedidos no CompeTIDOR.

Para uma conta (ou "todas") e um mês, busca os pedidos exatamente como o app
busca, compara com os que o CompeTIDOR guardou e mostra as formas de contagem
mais comuns em outros integradores (com cancelados, por data de fechamento,
por carrinho, por item). Também explica números específicos, aceitando tanto
o número do pedido quanto o do carrinho (pack) que o Mercado Livre mostra.
Nada é gravado e o token salvo não é renovado.

Uso no servidor (mês no formato AAAA-MM; os números são opcionais).
Roda com o mesmo usuário e a mesma configuração do serviço:

  sudo systemd-run --quiet --pty --wait --collect -p User=www-data \
    -p WorkingDirectory=/opt/competidor -p EnvironmentFile=/etc/competidor.env \
    -p EnvironmentFile=-/etc/competidor-dedicated.env \
    /opt/competidor/.venv/bin/python deploy/diagnostico_pedidos.py todas 2026-11 2000016101789289
"""
import collections
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402  (importar não inicia o servidor nem as rotinas)


def describe(order):
    return (f"pedido={order.get('id')} status={order.get('status')} criado={order.get('date_created')} "
            f"fechado={order.get('date_closed')} total={order.get('total_amount')} carrinho={order.get('pack_id')} "
            f"tags={order.get('tags')}")


def stored_ids(payload, account, period):
    monthly = payload.get("monthly_revenue") or {}
    record = server.account_record(((monthly.get("history") or {}).get(period) or {}).get("accounts"), account) or {}
    if not record and monthly.get("period") == period:
        record = server.account_record(monthly.get("accounts"), account) or {}
    return record, set(record.get("order_ids") or [])


def analyse(client, account, payload, period, start, end):
    upper = min(end, server.datetime.now(server.APP_TZ)) - timedelta(milliseconds=1)
    # A wider window (7 days before) is only used for the "by closing date" count.
    wide = server.fetch_seller_orders_window(client, account.get("seller_id"),
                                            (start - timedelta(days=7)).isoformat(timespec="milliseconds"),
                                            upper.isoformat(timespec="milliseconds"))
    in_month = [o for o in wide if (d := server.parse_meli_datetime(o.get("date_created"))) and start <= d < end]
    paid = [o for o in in_month if server.official_order_is_revenue(o)]
    closed = [o for o in wide if server.official_order_is_revenue(o)
              and (d := server.parse_meli_datetime(o.get("date_closed") or o.get("date_created"))) and start <= d < end]
    carts = {str(o.get("pack_id") or f"o{o.get('id')}") for o in paid}
    lines = sum(len(o.get("order_items") or []) or 1 for o in paid)
    units = sum(int(line.get("quantity") or 1) for o in paid for line in o.get("order_items") or [{}])
    record, stored = stored_ids(payload, account, period)
    found = {str(o.get("id")) for o in in_month}
    counts = {
        "app (pagos, por data de criação)": len(paid),
        "com cancelados": len(in_month),
        "pagos por data de fechamento": len(closed),
        "por carrinho (venda)": len(carts),
        "por item vendido": lines,
        "unidades": units,
    }
    return counts, in_month, record, sorted(found - stored), sorted(stored - found)


def explain(client, identity, months_orders):
    print(f"\nNúmero {identity}:")
    try:
        order = client.order(identity)
        print("  É um pedido:", describe(order))
        matches = [order]
    except Exception:
        matches = [o for o in months_orders if str(o.get("pack_id")) == identity]
        if not matches:
            print("  Não é um pedido e não foi encontrado como carrinho nos pedidos deste mês.")
            return
        print(f"  É um carrinho (pack) com {len(matches)} pedido(s):")
        for order in matches:
            print("   -", describe(order))
    for order in matches:
        print(f"  Pedido {order.get('id')}: conta como venda? {server.official_order_is_revenue(order)}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    wanted = sys.argv[1].strip().lower()
    period, start, end = server.sales_goal_month(sys.argv[2] if len(sys.argv) > 2 else None)
    numbers = [value.strip().lstrip("#") for value in sys.argv[3:] if value.strip()]
    payload = server.read_payload(include_catalog=False)
    accounts = [a for a in payload.get("accounts") or [] if a.get("official") and a.get("access_token")]
    if wanted != "todas":
        accounts = [a for a in accounts if wanted in {str(a.get("nickname") or "").lower(), str(a.get("id")), str(a.get("seller_id"))}]
    if not accounts:
        print("Conta não encontrada.")
        return 1
    print(f"Mês {period} · fuso do app: {server.APP_TZ}")
    totals = collections.Counter()
    all_orders = []
    for account in accounts:
        client = server.MercadoLivreClient(account["access_token"])
        try:
            counts, orders, record, only_ml, only_app = analyse(client, account, payload, period, start, end)
        except Exception as exc:
            print(f"\n{account.get('nickname')}: a busca falhou: {exc}")
            continue
        all_orders.extend((client, o) for o in orders)
        totals.update(counts)
        print(f"\n{account.get('nickname')} · guardado no app: {record.get('orders_count')} pedidos (atualizado {record.get('updated_at')})")
        print("  " + " · ".join(f"{name}: {value}" for name, value in counts.items()))
        if only_ml:
            print(f"  Na busca e não guardados no app ({len(only_ml)}): {', '.join(only_ml[:30])}")
        if only_app:
            print(f"  Guardados no app e fora da busca ({len(only_app)}): {', '.join(only_app[:30])}")
    if len(accounts) > 1:
        print("\nTOTAL de todas as contas · " + " · ".join(f"{name}: {value}" for name, value in totals.items()))
        print("Compare com o número do outro integrador para ver qual forma de contagem ele usa.")
    for identity in numbers:
        client = all_orders[0][0] if all_orders else server.MercadoLivreClient(accounts[0]["access_token"])
        owner = next((c for c, o in all_orders if identity in {str(o.get("id")), str(o.get("pack_id"))}), client)
        explain(owner, identity, [o for _, o in all_orders])
    return 0


if __name__ == "__main__":
    sys.exit(main())
