"""Internal EAN-13 codes (GS1 restricted-circulation range 040-049).

GS1 never assigns the 040-049 prefixes to a company: they are reserved for
each company's internal use. Codes here are therefore safe for labels, stock
and internal control, and are never confused with a real product's GTIN.
They are not globally unique (other companies may use the same internal
numbers) and Mercado Livre does not accept them as a listing's GTIN.

Uniqueness that can be guaranteed and is checked on every generation:
 - never generated before by this app (registry with the last serial per prefix);
 - not used as GTIN by any listing in the synced catalog;
 - optionally, not found in Mercado Livre's product catalog.
"""
import re
import threading
import time

REGISTRY_FILE = "internal_codes.json"
DEFAULT_PREFIX = "04003"
MAX_BATCH = 500
REGISTRY_LOCK = threading.RLock()

GTIN_LENGTHS = {8: "EAN-8", 12: "UPC-A (GTIN-12)", 13: "EAN-13 (GTIN-13)", 14: "GTIN-14"}


def check_digit(body):
    """GS1 mod-10 check digit for the digits before it (any GTIN length)."""
    digits = [int(char) for char in str(body)]
    total = sum(digit * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits)))
    return str((10 - total % 10) % 10)


def clean_code(value):
    return re.sub(r"\D", "", str(value or ""))


def is_valid(code):
    code = clean_code(code)
    return len(code) in GTIN_LENGTHS and check_digit(code[:-1]) == code[-1]


def as_gtin13(code):
    """Comparable form: GTIN-12 gains a leading zero; GTIN-14 keeps its last 13 when it starts with 0."""
    code = clean_code(code)
    if len(code) == 12:
        return "0" + code
    if len(code) == 14 and code.startswith("0"):
        return code[1:]
    return code


def classify(code):
    """What the code is, by GS1 prefix rules (EAN-13 / UPC-A forms)."""
    gtin13 = as_gtin13(code)
    if len(gtin13) != 13:
        return {"type": GTIN_LENGTHS.get(len(clean_code(code)), "desconhecido"), "range": "", "internal": False}
    prefix = int(gtin13[:3])
    if 40 <= prefix <= 49:
        return {"type": "EAN-13", "range": "Uso interno da empresa (GS1 040-049, circulação restrita)", "internal": True}
    if 50 <= prefix <= 59:
        return {"type": "EAN-13", "range": "Cupons (GS1 050-059)", "internal": True}
    if 20 <= prefix <= 29 or 200 <= prefix <= 299:
        return {"type": "EAN-13", "range": "Circulação restrita (peso variável / uso regional)", "internal": True}
    if 789 <= prefix <= 790:
        return {"type": "EAN-13", "range": "GS1 Brasil (código oficial de empresa)", "internal": False}
    if prefix <= 19 or 30 <= prefix <= 39 or 60 <= prefix <= 139:
        return {"type": "EAN-13", "range": "GS1 EUA/Canadá (UPC, código oficial de empresa)", "internal": False}
    return {"type": "EAN-13", "range": "GS1 (código oficial de empresa de outro país)", "internal": False}


def validate_prefix(prefix):
    prefix = clean_code(prefix) or DEFAULT_PREFIX
    if not 3 <= len(prefix) <= 9:
        raise ValueError("Use um prefixo de 3 a 9 dígitos.")
    if not 40 <= int(prefix[:3]) <= 49:
        raise ValueError("O prefixo precisa começar entre 040 e 049, a faixa que a GS1 reserva para uso interno.")
    return prefix


def empty_registry():
    return {"version": 1, "last_serial": {}, "codes": []}


def catalog_codes(catalog):
    """Every GTIN used by a synced listing, in comparable EAN-13 form."""
    codes = {}
    for item in catalog or []:
        for part in re.split(r"[,;\s]+", str(item.get("gtin") or "")):
            code = as_gtin13(part)
            if code:
                codes.setdefault(code, []).append(str(item.get("id") or ""))
    return codes


def generate(app, quantity, prefix=DEFAULT_PREFIX, note="", actor=None, meli_check=None, progress=None):
    """Reserve `quantity` new valid codes.

    meli_check(codes) -> set of the codes Mercado Livre's catalog already has
    (checked in blocks, so a batch of hundreds stays fast)."""
    prefix = validate_prefix(prefix)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("Informe uma quantidade válida.")
    if not 1 <= quantity <= MAX_BATCH:
        raise ValueError(f"Gere de 1 a {MAX_BATCH} códigos por vez.")
    width = 12 - len(prefix)
    capacity = 10 ** width
    used_in_catalog = catalog_codes(app.read_json(app.CATALOG_DATA_FILE, []))
    with REGISTRY_LOCK:
        registry = app.read_json(REGISTRY_FILE, empty_registry())
        registry.setdefault("codes", [])
        registry.setdefault("last_serial", {})
        known = {row.get("code") for row in registry["codes"]}
        serial = int(registry["last_serial"].get(prefix, -1))
        created, skipped = [], []
        while len(created) < quantity:
            if len(skipped) > 100 + quantity * 10:
                raise ValueError("Muitos números deste prefixo já estão em uso. Use outro prefixo (ex.: 04004).")
            # Next block of free candidates, in serial order.
            block = []
            while len(block) < quantity - len(created):
                serial += 1
                if serial >= capacity:
                    raise ValueError(f"O prefixo {prefix} não tem mais códigos livres. Use um prefixo mais curto ou outro final (ex.: 04004).")
                body = prefix + str(serial).zfill(width)
                code = body + check_digit(body)
                if code in known or code in used_in_catalog:
                    skipped.append({"code": code, "reason": "já usado nos seus anúncios ou já gerado"})
                    continue
                block.append(code)
            found = set()
            if meli_check is not None:
                try:
                    found = set(meli_check(block))
                except Exception as exc:  # never hand out an unchecked code as checked
                    raise ValueError(f"Não foi possível consultar o Mercado Livre agora ({exc}). Tente de novo ou gere sem a consulta.")
            for code in block:
                if code in found:
                    skipped.append({"code": code, "reason": "existe no catálogo do Mercado Livre"})
                    continue
                created.append({
                    "code": code, "prefix": prefix, "created_at": time.strftime("%Y-%m-%d %H:%M"),
                    "created_by": str((actor or {}).get("name") or (actor or {}).get("email") or ""),
                    "note": str(note or "")[:120], "meli_checked": meli_check is not None,
                })
            if progress:
                progress(len(created), quantity)
        registry["last_serial"][prefix] = serial
        registry["codes"].extend(created)
        app.write_json(REGISTRY_FILE, registry)
    return {"codes": created, "skipped": skipped, "prefix": prefix, "remaining": capacity - serial - 1}


def check(app, code, meli_check=None):
    """Explain a code: valid?, which GS1 range, used here?, known to Mercado Livre?"""
    raw = clean_code(code)
    result = {"code": raw, "valid": is_valid(raw), **classify(raw)}
    if not result["valid"]:
        expected = check_digit(raw[:-1]) if len(raw) in GTIN_LENGTHS else ""
        result["message"] = (f"Dígito verificador incorreto: o último dígito deveria ser {expected}."
                             if expected else "Um GTIN tem 8, 12, 13 ou 14 dígitos.")
        return result
    gtin13 = as_gtin13(raw)
    registry = app.read_json(REGISTRY_FILE, empty_registry())
    generated = next((row for row in registry.get("codes") or [] if row.get("code") == gtin13), None)
    listings = catalog_codes(app.read_json(app.CATALOG_DATA_FILE, [])).get(gtin13, [])
    result.update(generated_here=bool(generated), generated=generated or {}, listings=listings)
    if meli_check is not None:
        try:
            result["meli_found"] = raw in set(meli_check([raw]))
        except Exception as exc:
            result["meli_error"] = str(exc)
    return result
