"""Compatibility exports; implementation ships inside server.py."""
from server import official_store_report as _service

LOCK = _service.LOCK
KINDS = _service.KINDS
key = _service.key
destinations = _service.destinations
allowed = _service.allowed
coverage = _service.coverage
report = _service.report
save_rules = _service.save_rules
preview = _service.preview
live_inventory = _service.live_inventory
persist = _service.persist
execute = _service.execute
