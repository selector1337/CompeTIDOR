"""Persistent, opt-in SKU goal pricing. External prices are always reconciled before writes."""
from contextlib import contextmanager
import copy
import math
import threading
import time
import uuid
from urllib.parse import urlencode

FILE = 'goal_pricing.json'
HISTORY_LIMIT = 1000
SCOPES = {'both': {'gold_special', 'gold_pro'}, 'classic': {'gold_special'}, 'premium': {'gold_pro'}}

def rule_types(rule):
    return set(rule.get('types') or SCOPES['both'])

def rule_scope(rule):
    return next((key for key, types in SCOPES.items() if types == rule_types(rule)), 'both')

@contextmanager
def bounded_locks(*locks):
    acquired = []
    try:
        for lock in locks:
            if not lock.acquire(timeout=1):
                raise RuntimeError('Fila de preços ocupada; nova tentativa no próximo ciclo.')
            acquired.append(lock)
        yield
    finally:
        for lock in reversed(acquired):
            lock.release()


def item_due(rule, item):
    if item.get('blocked'):
        return None
    if item.get('pending') is not None or not item.get('initialized') or rule.get('restoring'):
        return item.get('retry_after', 0)
    return max(item.get('retry_after', 0), item.get('last_action', 0)+rule.get('interval_hours', 0)*3600,
               rule.get('last_sale', 0)+rule.get('sale_pause_hours', 0)*3600)


def rule_summary(rule):
    items = list((rule.get('items') or {}).values())
    initialized = [i for i in items if i.get('initialized') and i.get('original', 0) > 0]
    original = sum(i['original'] for i in initialized)
    current = sum(i.get('observed_price', i['last']) for i in initialized)
    due = [value for i in items if (value := item_due(rule, i)) is not None]
    now = time.time()
    pending_count = sum(i.get('pending') is not None for i in items)
    blocked_count = sum(bool(i.get('blocked')) for i in items)
    retry_count = sum(bool(i.get('retry_after')) for i in items)
    historical = {}
    for event in rule.get('history', []):
        if event.get('outcome') == 'success' and event.get('from') is not None and event.get('to') is not None and event['to'] < event['from']:
            identity = event.get('item_id')
            historical[identity] = historical.get(identity, 0) + 1
    return {**{k:rule.get(k) for k in ('sku','enabled','status')}, 'listing_scope':rule_scope(rule),
            'next_change_at': max(now, min(due)) if due and rule.get('enabled') else None,
            'due_count':sum(value <= now for value in due), 'pending_count':pending_count,
            'blocked_count':blocked_count, 'retry_count':retry_count,
            'checked_at':rule.get('checked_at'), 'sales_error':rule.get('sales_error'),
            'confirmed_changes': sum(i.get('confirmed_changes', historical.get(identity, 0)) for identity, i in (rule.get('items') or {}).items()),
            'reduction_percent': round((original-current)/original*100, 2) if original else None,
            'initialized_count':len(initialized), 'item_count':len(items)}


class GoalPricing:
    def __init__(self, app):
        self.a = app
        self.lock = threading.RLock()
        self.worker_lock = threading.RLock()
        self.rule_locks = {}
        self.last_refresh = 0
        self.independent_refresh = False
        self.last_heartbeat = 0

    def read(self):
        return self.a.read_json(FILE, {'rules': {}})

    def rule_lock(self, sku):
        with self.lock:
            return self.rule_locks.setdefault(sku, threading.RLock())

    def commit_rule(self, store, sku=None):
        # The worker snapshot may predate a user save for another SKU. Merge only
        # the rule whose lock we own; never replace the whole stale snapshot.
        with self.lock:
            latest = self.read()
            if sku is not None:
                latest['rules'][sku] = copy.deepcopy(store['rules'][sku])
            latest['cursor'] = store.get('cursor', 0)
            self.a.write_json(FILE, latest)

    def listing(self, sku=None):
        rules = self.read()['rules']
        if sku:
            return {'rules': [{**r, 'rule_id':key, 'listing_scope':rule_scope(r)} for key,r in rules.items()
                              if r.get('sku') == self.a.normalized_sku_key(sku)]}
        # The goals list only needs badges; fetch detailed logs on demand.
        return {'rules': [{**rule_summary(r),'rule_id':key} for key,r in rules.items()]}

    def check_overlap(self, store, sku, key, types):
        for other_key, other in store['rules'].items():
            if other_key != key and other.get('sku') == sku and (
                other.get('enabled') or any(i.get('pending') is not None for i in other.get('items',{}).values())
            ) and rule_types(other) & types:
                raise RuntimeError('Já existe uma regra ativa ou com confirmação pendente para esta modalidade. Pause a regra sobreposta antes de ativar outra.')

    def event(self, rule, identity, outcome, message, before=None, target=None):
        """Persist with the surrounding state commit; never call an uncertain write success."""
        tracking = rule.get('items', {}).get(identity, {})
        if outcome == 'success' and before is not None and target is not None and target < before:
            tracking['confirmed_changes'] = tracking.get('confirmed_changes', sum(1 for h in rule.get('history', []) if h.get('item_id') == identity and h.get('outcome') == 'success' and h.get('from') is not None and h.get('to') is not None and h['to'] < h['from'])) + 1
        fingerprint = [outcome, message, before, target]
        key = 'last_event' if identity else 'last_rule_event'
        holder = tracking if identity else rule
        duplicate = holder.get(key) == fingerprint and outcome != 'success'
        holder[key] = fingerprint
        if identity:
            tracking.update(outcome=outcome, result_at=self.a.now_label())
        if duplicate: return
        if outcome in {'error', 'blocked'}:
            print('Metas automáticas:', rule.get('sku'), identity or 'regra', outcome, message, flush=True)
        original = tracking.get('original') if tracking.get('initialized') else None
        current = tracking.get('observed_price', tracking.get('last')) if original is not None else None
        reduction = round(original-current, 2) if original is not None and current is not None else None
        rule.setdefault('history', []).append({
            'at': self.a.now_label(), 'sku': rule['sku'], 'item_id': identity,
            'account_id': tracking.get('account_id'), 'account': tracking.get('account_name'),
            'type': tracking.get('type'), 'outcome': outcome, 'action': message,
            'from': before, 'to': target,
            'price_attempts': tracking.get('price_attempts', 0),
            'retry_after': tracking.get('retry_after', 0),
            'original': original, 'current': current,
            'reduction_amount': reduction,
            'reduction_percent': round(reduction/original*100, 2) if original and reduction is not None else None,
        })
        rule['history'] = rule['history'][-HISTORY_LIMIT:]

    def managed(self, item_id):
        return any(r.get('enabled') and item_id in r.get('items', {}) for r in self.read()['rules'].values())

    def save(self, req, actor):
        if not self.a.can_manage_users(actor) or actor.get('status') not in {'ativo','active'}:
            raise RuntimeError('Somente Master e Administradores podem configurar preços automáticos.')
        sku = self.a.normalized_sku_key(req.get('sku'))
        # Serialize edits of this SKU with its in-flight price confirmation,
        # without holding the shared storage lock while waiting for the API.
        with self.rule_lock(sku):
            return self._save(req, actor)

    def _save(self, req, actor):
        if not self.a.can_manage_users(actor) or actor.get('status') not in {'ativo','active'}:
            raise RuntimeError('Somente Master e Administradores podem configurar preços automáticos.')
        sku = self.a.normalized_sku_key(req.get('sku'))
        def number(key, low, high):
            try: value = float(req.get(key))
            except (ValueError, TypeError): raise RuntimeError('Preencha os campos numéricos da regra.')
            if not math.isfinite(value) or not low <= value <= high:
                raise RuntimeError('Valor inválido: ' + key)
            return value
        with self.lock:
            store = self.read()
            key = str(req.get('rule_id') or sku)
            if req.get('new_rule'):
                key = sku if sku not in store['rules'] else 'rule-' + uuid.uuid4().hex
            previous = store['rules'].get(key)
            if req.get('rule_id') and (not previous or previous.get('sku') != sku):
                raise RuntimeError('Regra não encontrada para este SKU.')
            action = req.get('action', 'save')
            if action not in {'save','pause','restore','resume'}: raise RuntimeError('Ação inválida.')
            if action in {'pause', 'restore', 'resume'}:
                if not previous: raise RuntimeError('Regra não encontrada.')
                if action != 'pause': self.check_overlap(store,sku,key,rule_types(previous))
                previous.update(enabled=action != 'pause', restoring=action == 'restore', status='Restauração solicitada' if action == 'restore' else 'Ativa' if action == 'resume' else 'Pausada; preços atuais mantidos')
                if action == 'restore':
                    for item in previous['items'].values(): item.update(restore_done=False,retry_after=0)
                if action == 'resume':
                    for item in previous['items'].values(): item['last_action']=time.time()
                previous['updated_by']=actor.get('name') or actor.get('id')
                previous['updated_at']=self.a.now_label()
                self.a.write_json(FILE, store)
                return {**previous, 'rule_id':key, 'listing_scope':rule_scope(previous)}
            if previous and (previous.get('enabled') or any(i.get('pending') is not None or i.get('last') != i.get('original') for i in previous.get('items', {}).values())):
                raise RuntimeError('Restaure os preços da regra anterior antes de alterar sua configuração.')
            payload = self.a.read_payload()
            targets = self.a.sales_goal_targets(self.a.read_json(self.a.SALES_GOALS_FILE, {'months':{}}))
            if not targets.get(sku): raise RuntimeError('Cadastre uma meta para este SKU primeiro.')
            scope = req.get('listing_scope', rule_scope(previous or {}))
            if scope not in SCOPES: raise RuntimeError('Selecione Clássico, Premium ou ambos.')
            types = SCOPES[scope]
            if req.get('enabled') is True: self.check_overlap(store,sku,key,types)
            allowed = {a['id'] for a in payload.get('accounts',[]) if a.get('official') and a.get('status') == 'connected'}
            accounts = allowed
            if not accounts:
                raise RuntimeError('Conecte uma conta do Mercado Livre antes de configurar a regra.')
            mode = req.get('mode')
            if mode not in {'amount','percent'}: raise RuntimeError('Selecione redução em R$ ou %.')
            interval = number('interval_hours', 1, 720)
            cooldown = number('sale_pause_hours', 1, 720)
            step = number('step', .01, 99 if mode == 'percent' else 1000000)
            floor = round(number('floor_price', 0, 10000000), 2)
            margin = None if req.get('floor_margin') in (None,'') else number('floor_margin', 0, 99)
            if not floor and margin is None: raise RuntimeError('Defina um preço mínimo ou uma margem mínima.')
            items = {}
            for item in payload.get('catalog',[]):
                if self.a.normalized_sku_key(item.get('sku')) != sku or item.get('account_id') not in accounts or item.get('listing_type_id') not in types: continue
                if self.a.normalized_meli_status(item.get('meli_status')) not in {'active','paused'}: continue
                if any(s.get('enabled') and s.get('item_id') == item['id'] for s in payload.get('price_schedules',[])):
                    raise RuntimeError('Há um agendamento de preço neste SKU. Desative-o antes de ativar a regra.')
                price = float(item.get('price') or 0)
                if not math.isfinite(price) or price <= 0 or floor > price: raise RuntimeError('Preço mínimo superior ao preço de um dos anúncios selecionados.')
                items[item['id']] = {'account_id':item['account_id'], 'account_name':next((a.get('nickname',a['id']) for a in payload['accounts'] if a['id']==item['account_id']),item['account_id']), 'type':item['listing_type_id'], 'original':price, 'last':price, 'initialized':False, 'last_action':time.time(), 'status':'Aguardando validação do preço original'}
            if not items: raise RuntimeError('Nenhum anúncio ativo ou pausado deste SKU foi encontrado.')
            rule = dict(sku=sku, accounts=sorted(accounts), types=sorted(types), mode=mode, step=step,
                        interval_hours=interval, sale_pause_hours=cooldown, floor_price=floor, floor_margin=margin,
                        enabled=req.get('enabled') is True, restoring=False, items=items, period=self.a.current_month_period(),
                        created_at=self.a.now_label(), updated_by=actor.get('name') or actor.get('id'), status='Ativa' if req.get('enabled') is True else 'Desativada', history=copy.deepcopy((previous or {}).get('history', []))[-HISTORY_LIMIT:])
            store['rules'][key] = rule
            self.a.write_json(FILE, store)
            return {**copy.deepcopy(rule), 'rule_id':key, 'listing_scope':scope}

    def sales(self, payload, sku, now, store=None):
        if store is None: store = self.a.read_json(self.a.SALES_GOALS_FILE, {'months':{}})
        target = self.a.sales_goal_targets(store).get(sku)
        records = store.get('months',{}).get(self.a.current_month_period(),{}).get('accounts',{})
        sold, last = 0, 0
        accounts = [a for a in payload.get('accounts',[]) if a.get('official') and a.get('status') == 'connected']
        if not accounts: raise RuntimeError('Sem contas conectadas para conferir vendas.')
        seen = set()
        for account in accounts:
            seller = str(account.get('seller_id') or account['id'])
            if seller in seen: continue
            seen.add(seller)
            record = records.get(seller, {})
            if now - record.get('updated_at_ns',0)/1e9 > 900 or 'last_sales' not in record:
                raise RuntimeError('Aguardando conferência recente das vendas da conta ' + str(account.get('nickname') or seller) + '.')
            if record.get('unknown_units'): raise RuntimeError('Existem vendas sem SKU identificado; confira os cadastros antes de reduzir preços.')
            sold += record.get('units',{}).get(sku,0)
            date = self.a.parse_meli_datetime(record.get('last_sales',{}).get(sku))
            if date: last = max(last, date.timestamp())
        return target, sold, last

    def tick(self, now=None):
        now = now or time.time()
        if not any(r.get('enabled') for r in self.read()['rules'].values()): return
        rules = list(self.read()['rules'].values())
        if not self.independent_refresh and now - self.last_refresh > 600 and any(r.get('enabled') and not r.get('restoring') for r in rules):
            self.last_refresh = now
            try:
                result = self.a.reconcile_month_sales({'month':self.a.current_month_period()})
                if isinstance(result, dict) and any(r.get('status') == 'error' for r in result.get('results', [])):
                    self.last_refresh = now - 540  # retry failed sales verification in one minute
            except Exception as exc:
                self.last_refresh = now - 540
                print('Conferência de vendas das metas:', str(exc), flush=True)
        with bounded_locks(self.a.PRICE_SCHEDULE_LOCK, self.worker_lock):
            store = self.read()
            payload = self.a.read_payload()
            catalog = {i['id']:i for i in payload.get('catalog',[]) if i.get('id')}
            by_sku = {}
            for item in catalog.values():
                by_sku.setdefault(self.a.normalized_sku_key(item.get('sku')), []).append(item)
            sales_store = self.a.read_json(self.a.SALES_GOALS_FILE, {'months':{}})
            accounts = {a['id']:a for a in payload.get('accounts',[])}
            budget = 20
            deadline = time.monotonic()+20
            payload_changed = False
            rule_keys = list(store['rules'])
            cursor = int(store.get('cursor', 0)) % max(1, len(rule_keys))
            for rule_index in list(range(cursor, len(rule_keys))) + list(range(cursor)):
                if budget <= 0 or time.monotonic() > deadline:
                    break
                store['cursor'] = (rule_index + 1) % max(1, len(rule_keys))
                sku = rule_keys[rule_index]
                rule_lock = self.rule_lock(store['rules'][sku]['sku'])
                if not rule_lock.acquire(blocking=False): continue
                # A pause/edit can have arrived since the cycle snapshot.
                rule = None
                try:
                    rule = self.read()['rules'].get(sku)
                    if rule is None or not rule.get('enabled'): continue
                    store['rules'][sku] = rule
                    # Include new matching listings/accounts without changing existing baselines.
                    if not rule.get('restoring'):
                        for item in by_sku.get(rule['sku'], []):
                            account = accounts.get(item.get('account_id'),{})
                            if self.a.normalized_sku_key(item.get('sku')) == rule['sku'] and account.get('official') and account.get('status') == 'connected' and item.get('listing_type_id') in rule_types(rule) and self.a.normalized_meli_status(item.get('meli_status')) in {'active','paused'} and item['id'] not in rule['items']:
                                price=float(item.get('price') or 0)
                                if price > 0:
                                    rule['items'][item['id']]={'account_id':item['account_id'],'account_name':account.get('nickname',item['account_id']),'type':item['listing_type_id'],'original':price,'last':price,'initialized':False,'last_action':now,'status':'Novo anúncio; aguardando validação'}
                    sales_error = ''
                    try:
                        target, sold, last_sale = self.sales(payload, rule['sku'], now, sales_store) if not rule.get('restoring') else (None,0,0)
                    except Exception as exc:
                        sales_error = str(exc)
                        target, sold, last_sale = None, 0, rule.get('last_sale', 0)
                    rule.update(sales_error=sales_error, checked_at=self.a.now_label())
                    if sales_error:
                        self.event(rule, None, 'blocked', sales_error)
                    last_sale = max(last_sale, rule.get('last_sale',0))
                    rule['last_sale'] = last_sale
                    # Goals and calendar boundaries never reset the pricing flow.
                    # Only an explicit user request restores the baseline.
                    rule['period'] = self.a.current_month_period()
                    restore = bool(rule.get('restoring'))
                    rule['status'] = 'Restaurando preços originais' if restore else 'Aguardando intervalo sem vendas'
                    for identity, tracking in sorted(rule['items'].items(), key=lambda pair: pair[1].get('checked_at', 0)):
                        if budget <= 0 or time.monotonic() > deadline: break
                        if tracking.get('retry_after',0) > now: continue
                        if sales_error and tracking.get('initialized') and tracking.get('pending') is None and not tracking.get('blocked') and not rule.get('restoring'): continue
                        desired = tracking['original'] if restore else None
                        if restore and tracking.get('pending') is None and abs(tracking['last']-tracking['original']) < .009 and (not rule.get('restoring') or tracking.get('restore_done')): continue
                        if not restore and not tracking.get('blocked') and tracking.get('pending') is None and tracking.get('initialized') and now < max(tracking['last_action']+rule['interval_hours']*3600,last_sale+rule['sale_pause_hours']*3600): continue
                        budget -= 1
                        tracking['checked_at'] = now
                        try:
                            item = catalog.get(identity)
                            account = accounts.get(tracking['account_id'])
                            if not item or not account or account.get('status') != 'connected': raise RuntimeError('Anúncio ou conta indisponível.')
                            if any(s.get('enabled') and s.get('item_id') == identity for s in payload.get('price_schedules',[])): raise RuntimeError('Conflito com agendamento de preço.')
                            client = self.a.account_client(account)
                            live = client.get('/items/'+identity, retries=1, timeout=12)
                            price = self.a.response_item_price(live)
                            if price is None or not math.isfinite(price) or price <= 0: raise RuntimeError('Preço atual não confirmado pelo Mercado Livre.')
                            tracking.update(observed_price=price, observed_at=self.a.now_label())
                            if tracking.get('blocked') and not restore:
                                expected = [tracking['last']]
                                if tracking.get('pending') is not None: expected.append(tracking['pending'])
                                if not any(abs(price-value) < .009 for value in expected):
                                    tracking.update(retry_after=now+300, status='Divergência de preço persiste; nova conferência automática em 5 minutos')
                                    self.event(rule,identity,'blocked',tracking['status'],tracking['last'],price)
                                    continue
                                tracking.update(blocked=False,retry_after=0,status='Preço esperado confirmado; bloqueio removido automaticamente')
                                self.event(rule,identity,'waiting',tracking['status'],price,price)
                                if sales_error and tracking.get('pending') is None: continue
                            if tracking.get('pending') is not None:
                                pending = tracking['pending']
                                if abs(price-pending) < .009:
                                    before = tracking.get('pending_from', tracking['last'])
                                    tracking.update(last=price, pending=None, last_action=now, retry_after=0, status='Preço confirmado após reconciliação')
                                    self.event(rule,identity,'success',tracking['status'],before,price)
                                    self.a.append_item_log(payload,item,{'name':'Metas de venda'},tracking['status'],{'price':{'from':before,'to':price}})
                                    item.update(price=price,price_source='goal_rule',sale_fee_status='pending',sale_fee_basis='',sale_fee_updated_at='',item_data_checked_at=self.a.now_label())
                                    payload_changed = True
                                elif abs(price-tracking['last']) < .009:
                                    # The five-minute wait already elapsed. Recheck the
                                    # rule next cycle before resending, without another
                                    # unnecessary wait or a second decrement.
                                    tracking.update(pending=None, retry_after=now+15, status='Preço ainda não aplicado; nova tentativa após revalidar a regra')
                                    self.event(rule,identity,'error',tracking['status'],price,pending)
                                elif rule.get('restoring'): tracking.update(last=price,pending=None,blocked=False)
                                else:
                                    tracking.update(blocked=True,retry_after=now+300,status='Preço divergente do esperado; nova conferência automática em 5 minutos')
                                    self.event(rule,identity,'blocked',tracking['status'],price,pending)
                                self.commit_rule(store, sku)
                                continue
                            if rule.get('restoring') and abs(price-tracking['original']) < .009:
                                tracking.update(last=price,restore_done=True,blocked=False,retry_after=0,status='Original confirmado')
                                self.event(rule,identity,'waiting','Preço original já estava aplicado; nenhuma alteração enviada',price,price)
                                continue
                            if not tracking.get('initialized'):
                                tracking.update(original=price,last=price,initialized=True,status='Preço original registrado; aguardando intervalo')
                                self.commit_rule(store, sku)
                                continue
                            if abs(price-tracking['last']) > .009:
                                if rule.get('restoring'):
                                    tracking.update(last=price,blocked=False)
                                else:
                                    tracking.update(blocked=True,retry_after=now+300,status='Preço divergente do esperado; nova conferência automática em 5 minutos')
                                    self.event(rule,identity,'blocked',tracking['status'],tracking['last'],price)
                                    continue
                            if not restore and live.get('status') not in {'active','paused'}:
                                tracking.update(status='Anúncio encerrado ou indisponível; nenhuma redução',retry_after=now+3600); continue
                            if not restore:
                                desired = max(rule['floor_price'], round(price-rule['step'] if rule['mode']=='amount' else price*(1-rule['step']/100),2))
                                if desired <= 0 or desired >= price:
                                    tracking.update(status='Preço mínimo atingido',last_action=now)
                                    self.event(rule,identity,'waiting',tracking['status'],price,None)
                                    continue
                                if rule['floor_margin'] is not None:
                                    cost=(payload.get('sku_costs',{}).get(rule['sku']) or {}).get('cost')
                                    shipping=self.a.shipping_quote_values(client.item_shipping_cost(account.get('seller_id'),identity))['cost']
                                    params={'price':f'{desired:.2f}','listing_type_id':tracking['type'],'category_id':live.get('category_id') or ''}
                                    shipping_mode=(live.get('shipping') or {}).get('mode')
                                    if shipping_mode: params['shipping_mode']=shipping_mode
                                    logistic=(live.get('shipping') or {}).get('logistic_type')
                                    if logistic: params['logistic_type']=logistic
                                    fees=client.get('/sites/'+(account.get('site_id') or 'MLB')+'/listing_prices?'+urlencode(params),retries=1,timeout=12)
                                    fee,_=self.a.sale_fee_values(fees,tracking['type'])
                                    if any(v is None or not math.isfinite(float(v)) or float(v)<0 for v in (cost,shipping,fee)): raise RuntimeError('Custo, tarifa ou frete incompleto; redução bloqueada.')
                                    net=desired-fee-shipping
                                    if net <= 0 or (net-float(cost))/net*100 < rule['floor_margin']:
                                        tracking.update(status='Próxima redução ultrapassaria a margem mínima',last_action=now)
                                        self.event(rule,identity,'waiting',tracking['status'],price,desired)
                                        continue
                            # Related catalogue/traditional offers may be synchronized by ML.
                            # Persist their expected outcome too, so a synchronized price
                            # cannot be mistaken for a manual edit or reduced a second time.
                            for relation in live.get('item_relations') or []:
                                peer=rule['items'].get(str(relation.get('id')))
                                if peer and peer.get('initialized') and not peer.get('blocked') and peer.get('pending') is None and peer['account_id']==tracking['account_id'] and peer['type']==tracking['type'] and abs(peer['last']-price)<.009:
                                    peer['pending']=desired
                                    peer['pending_from']=peer['last']
                            tracking['pending']=desired
                            tracking['pending_from']=price
                            tracking['price_attempts']=tracking.get('price_attempts',0)+1
                            tracking['retry_after']=0
                            self.event(rule,identity,'pending','Envio preparado; aguardando confirmação do Mercado Livre',price,desired)
                            self.commit_rule(store, sku)  # durable intent BEFORE remote mutation
                            response=client.put('/items/'+identity,{'price':desired},retries=1,timeout=12)
                            confirmed=self.a.response_item_price(response)
                            if confirmed is None or not math.isfinite(confirmed) or abs(confirmed-desired)>.009:
                                confirmed=self.a.response_item_price(client.get('/items/'+identity, retries=1, timeout=12))
                            if confirmed is None or not math.isfinite(confirmed) or abs(confirmed-desired)>.009: raise RuntimeError('Aguardando confirmação do preço enviado.')
                            tracking.update(last=confirmed,pending=None,last_action=now,retry_after=0,restore_done=bool(restore),status='Original restaurado' if restore else 'Preço reduzido')
                            tracking.update(observed_price=confirmed, observed_at=self.a.now_label())
                            self.event(rule,identity,'success',tracking['status'],price,confirmed)
                            self.commit_rule(store, sku)
                            item.update(price=confirmed,price_source='goal_rule',sale_fee_status='pending',sale_fee_basis='',sale_fee_updated_at='',item_data_checked_at=self.a.now_label())
                            self.a.append_item_log(payload,item,{'name':'Metas de venda'},tracking['status'],{'price':{'from':price,'to':confirmed}})
                            payload_changed = True
                        except Exception as exc:
                            tracking.update(status=str(exc),retry_after=now+300)
                            uncertain = tracking.get('pending') is not None
                            self.event(rule,identity,'pending' if uncertain else 'error',str(exc),tracking.get('pending_from',tracking['last']) if uncertain else tracking['last'],tracking['pending'] if uncertain else desired)
                    complete = all(not i.get('blocked') and i.get('pending') is None and abs(i['last']-i['original'])<.009 and (not rule.get('restoring') or i.get('restore_done')) for i in rule['items'].values())
                    if restore and complete:
                        rule['status']='Preços originais restaurados por solicitação do usuário'
                        rule.update(enabled=False,restoring=False)
                    if not restore and any(i.get('blocked') or i.get('retry_after',0)>now for i in rule['items'].values()):
                        rule['status']='Há anúncios aguardando conferência. Consulte o acompanhamento.'
                    if sales_error:
                        rule['status'] = sales_error
                    elif not restore:
                        if any(not i.get('initialized') for i in rule['items'].values()):
                            rule['status'] = 'Confirmando preços originais; anúncios pendentes na fila'
                        elif any(i.get('pending') is not None for i in rule['items'].values()):
                            rule['status'] = 'Conferindo alterações enviadas; retentativa automática'
                        elif any(i.get('blocked') for i in rule['items'].values()):
                            rule['status'] = 'Preço divergente do esperado; conferência automática programada'
                        elif any(i.get('retry_after',0)>now for i in rule['items'].values()):
                            rule['status'] = 'Falha temporária; retentativa automática programada'
                        elif any((item_due(rule,i) or 0)<=now for i in rule['items'].values()):
                            rule['status'] = 'Janela atingida; aguardando atendimento na fila'

                except Exception as exc:
                    if rule is None: raise
                    rule['status']=str(exc)
                    self.event(rule,None,'blocked',str(exc))
                finally:
                    try:
                        if rule is not None and rule.get('enabled') is not None:
                            store['rules'][sku] = rule
                            self.commit_rule(store, sku)
                    finally:
                        rule_lock.release()
            self.commit_rule(store)
            if payload_changed: self.a.write_payload(payload)
            if now - self.last_heartbeat >= 300:
                self.last_heartbeat = now
                enabled = [r for r in store['rules'].values() if r.get('enabled')]
                tracked = [i for r in enabled for i in r.get('items', {}).values()]
                print('Metas automáticas: ciclo concluído;', len(enabled), 'regras;',
                      sum(bool(i.get('initialized')) for i in tracked), '/', len(tracked), 'originais confirmados;',
                      sum(i.get('pending') is not None for i in tracked), 'confirmações pendentes;',
                      sum(bool(i.get('blocked')) for i in tracked), 'bloqueios externos;',
                      sum(bool(r.get('sales_error')) for r in enabled), 'regras aguardando vendas', flush=True)


    def refresh_sales_loop(self):
        self.a.MELI_WORK_CONTEXT.priority = 'background'
        while True:
            delay = 60
            try:
                if any(r.get('enabled') and not r.get('restoring') for r in self.read()['rules'].values()):
                    result = self.a.reconcile_month_sales({'month':self.a.current_month_period()})
                    for row in (result or {}).get('results', []):
                        if row.get('status') == 'error':
                            print('Conferência de vendas das metas:', row.get('account'), row.get('error'), 'Nova tentativa após 60 segundos.', flush=True)
                    delay = 60 if any(r.get('status') == 'error' for r in (result or {}).get('results', [])) else 300
            except Exception as exc:
                print('Conferência de vendas das metas:', str(exc), flush=True)
            time.sleep(delay)

    def loop(self):
        self.independent_refresh = True
        threading.Thread(target=self.refresh_sales_loop, name='goal-pricing-sales', daemon=True).start()
        self.a.MELI_WORK_CONTEXT.priority = 'background'
        while True:
            try: self.tick()
            except Exception as exc: print('Metas automáticas:', str(exc), flush=True)
            time.sleep(15)
