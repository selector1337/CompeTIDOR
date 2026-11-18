"""Diagnóstico SOMENTE LEITURA das medidas de embalagem de um produto de Catálogo.

Mostra de onde o Mercado Livre deixa a API ler as medidas que ele exige para
um anúncio de Catálogo, para decidir como exibi-las no Anunciar Produtos.

Nada é criado nem alterado: só leituras (/products, /items) e validações
(/items/validate, que não cria anúncio). O token salvo não é renovado nem
regravado; se estiver vencido, rode de novo depois que o serviço renovar.

Uso no servidor (troque MLB12345678 pelo código do produto de Catálogo, o que
aparece no link /p/MLB...; a conta é opcional):

  sudo -u www-data bash -c 'set -a; . /etc/competidor.env; cd /opt/competidor; \
    .venv/bin/python deploy/diagnostico_medidas_catalogo.py MLB12345678 "NOME DA CONTA"'
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402  (importar não inicia o servidor nem as rotinas)

DIMENSION_WORDS = ("HEIGHT", "WIDTH", "LENGTH", "DEPTH", "WEIGHT", "DIMENSION", "PACKAGE", "ALTURA", "LARGURA", "COMPRIMENTO", "PESO")
SELLER_PACKAGE = list(server.SELLER_PACKAGE_ATTRIBUTE_FIELDS)


def title(text):
    print(f"\n=== {text} ===")


def attribute_text(attribute):
    value = attribute.get("value_name")
    if not value and attribute.get("values"):
        value = (attribute.get("values") or [{}])[0].get("name")
    struct = attribute.get("value_struct") or ((attribute.get("values") or [{}])[0] or {}).get("struct")
    return f"{attribute.get('id')} ({attribute.get('name')}): {value}" + (f"  struct={struct}" if struct else "")


def dimension_like(node, path=""):
    """Every key or attribute anywhere in a response that looks like a measure."""
    found = []
    if isinstance(node, dict):
        attr_id = str(node.get("id") or "").upper()
        if attr_id and any(word in attr_id for word in DIMENSION_WORDS) and ("value_name" in node or "values" in node):
            found.append(f"{path}: {attribute_text(node)}")
        for key, value in node.items():
            if any(word.lower() in str(key).lower() for word in DIMENSION_WORDS) and not isinstance(value, (dict, list)):
                found.append(f"{path}.{key} = {value}")
            found.extend(dimension_like(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(dimension_like(value, f"{path}[{index}]"))
    return found


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    product_id = sys.argv[1].strip().upper()
    wanted_account = sys.argv[2].strip().lower() if len(sys.argv) > 2 else ""
    payload = server.read_payload(include_catalog=False)
    accounts = [a for a in payload.get("accounts") or [] if a.get("official") and a.get("access_token")]
    account = next((a for a in accounts if wanted_account in {str(a.get("nickname") or "").lower(), str(a.get("id")), str(a.get("seller_id"))}), None) \
        if wanted_account else (accounts[0] if accounts else None)
    if not account:
        print("Conta oficial não encontrada. Contas disponíveis:", ", ".join(str(a.get("nickname")) for a in accounts))
        return 1
    # Token salvo, sem renovar: este script nunca grava nos dados do app.
    client = server.MercadoLivreClient(account["access_token"])
    print(f"Produto {product_id} · conta {account.get('nickname')} (seller {account.get('seller_id')})")

    title("1. Ficha do produto (/products)")
    try:
        product = client.product(product_id) or {}
    except Exception as exc:
        print("Falhou:", exc)
        product = {}
    print("Nome:", product.get("name"), "| domínio:", product.get("domain_id"), "| categoria:", product.get("category_id"))
    rows = dimension_like(product, "product")
    print("\n".join(rows) if rows else "Nenhuma medida encontrada na ficha.")
    print("Mínimos que o app já extrai daqui:", server.catalog_product_package_minimums(product) or "nenhum")

    title("2. Ofertas já aceitas neste mesmo produto (/products/{id}/items)")
    try:
        offers = client.product_winners(product_id) or {}
        offer_rows = offers.get("results") if isinstance(offers, dict) else offers
        ids = [str(r.get("item_id") or r.get("id")) for r in offer_rows or [] if isinstance(r, dict)][:10]
        print("Ofertas:", ", ".join(ids) or "nenhuma")
        for item in client.items_bulk(ids) if ids else []:
            package = server.package_values_from_item(item)
            shipping = (item.get("shipping") or {}).get("dimensions")
            print(f"- {item.get('id')} seller {item.get('seller_id')}: SELLER_PACKAGE={package or 'não visível'}; shipping.dimensions={shipping}")
    except Exception as exc:
        print("Falhou:", exc)
    print("Mínimos que o app já extrai daqui:", server.catalog_offer_package_minimums(client, product_id, product) or "nenhum")

    title("3. Validação oficial com medidas mínimas (/items/validate, não cria anúncio)")
    category_id = product.get("category_id") or (sys.argv[3] if len(sys.argv) > 3 else "")
    if not category_id:
        print("A ficha não trouxe a categoria. Rode de novo informando a categoria como 3º argumento (ex.: MLB49484).")
        return 0
    pictures = [{"source": p.get("url") or p.get("secure_url")} for p in product.get("pictures") or [] if p.get("url") or p.get("secure_url")][:6]
    probe = {
        "title": str(product.get("name") or "Produto")[:60], "category_id": category_id, "price": 100,
        "currency_id": "BRL", "available_quantity": 1, "buying_mode": "buy_it_now",
        "listing_type_id": "gold_special", "condition": "new", "catalog_listing": True,
        "catalog_product_id": product_id, "pictures": pictures or [{"source": "https://http2.mlstatic.com/D_NQ_NP_000000-MLA0000000000_000000-O.jpg"}],
        "attributes": [{"id": attr_id, "value_name": "1 g" if "WEIGHT" in attr_id else "1 cm"} for attr_id in SELLER_PACKAGE],
    }
    try:
        client.validate_item(probe)
        print("O Mercado Livre aceitou 1 cm / 1 g: este produto não tem medida mínima própria.")
        return 0
    except Exception as exc:
        print("Resposta completa da validação (procure números de medidas aqui):")
        print(json.dumps(server.meli_error_detail(exc), ensure_ascii=False, indent=2)[:4000] or str(exc))
        too_small = server.catalog_too_small_package_fields(exc)
        print("Campos que ele diz estarem pequenos:", too_small or "nenhum (o erro é outro; veja acima)")
    if too_small:
        print("\nProcurando a menor embalagem aceita (só validações, cerca de 10 a 15 chamadas)...")
        try:
            _payload, _source, adjustments = server.catalog_package_validated_payload(client, probe, {})
            accepted = server.package_payload_numbers(_payload)
            print("Menor embalagem aceita pelo validador:", accepted)
            print("Ajustes:", adjustments)
        except Exception as exc:
            print("Não encontrou:", server.friendly_clone_error(exc))
    title("Compare com o que o Mercado Livre mostra na tela de anunciar este produto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
