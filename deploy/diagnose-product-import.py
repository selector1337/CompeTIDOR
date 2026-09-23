"""Read-only product import diagnosis. Run with the service's environment/user.

Prints no credentials, cookies, raw API bodies, or page HTML.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    server.product_import_host(args.url)
    item_id = server.extract_meli_item_id(args.url)
    up_id = server.extract_meli_user_product_id(args.url)
    report = {"item_id": item_id, "user_product_id": up_id, "checks": []}

    def emit(row):
        report["checks"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    payload = server.read_json(server.APP_DATA_FILE, {})
    paths = []
    if item_id:
        paths.append(f"/items/{item_id}?include_attributes=all")
    if up_id:
        paths.append(f"/user-products/{up_id}")
    for index, client in enumerate(server.official_reader_clients(payload), 1):
        for path in paths:
            try:
                result = client.get(path, retries=0, timeout=10)
                emit({"reader": f"account_{index}", "path": path, "ok": True,
                      "has_title": bool(result.get("title") or result.get("name")),
                      "pictures": len(result.get("pictures") or [])})
            except Exception as exc:
                emit({"reader": f"account_{index}", "path": path, "ok": False,
                      "status": server.meli_error_status(exc), "exception": type(exc).__name__})

    urls = [args.url]
    if item_id:
        urls.append(f"https://produto.mercadolivre.com.br/MLB-{item_id[3:]}-_JM")
    for url in urls:
        try:
            page, final_url = server.product_browser_download(url)
            product = server.parse_external_product_page(page, final_url)
            emit({"reader": "chromium", "path": server.urlparse(url).path,
                  "ok": True, "title": product.get("title"),
                  "pictures": len(product.get("pictures") or []),
                  "has_description": bool(product.get("source_description"))})
        except Exception as exc:
            # Only include our own bounded Chromium diagnostic, never API response bodies.
            message = str(exc)
            emit({"reader": "chromium", "path": server.urlparse(url).path,
                  "ok": False, "exception": type(exc).__name__,
                  "diagnosis": message[:500] if message.startswith("Chromium: HTTP") else "Falha ao renderizar ou extrair produto"})
    executor = server.PRODUCT_BROWSER_EXECUTOR
    if executor:
        executor.submit(server.reset_product_browser_worker).result(timeout=10)
        executor.shutdown(wait=False)


if __name__ == "__main__":
    main()
