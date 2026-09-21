(() => {
  const $ = id => document.getElementById(id);
  const esc = escapeText;
  const attr = escapeAttr;
  const kinds = ['gold_special', 'gold_pro'];
  const labels = {gold_special: 'Clássico', gold_pro: 'Premium', pending: 'A publicar', existing: 'Já existente', excluded: 'Excluído pela marca', created: 'Criado', error: 'Precisa de ajuste', uncertain: 'Conferência necessária', submitting: 'Resultado a conferir'};
  let data = null, selected = new Set(), targets = new Set(), prices = {}, page = 0, batch = null, busy = false, batchPage = 0;
  async function operation(action, body = {}) {
    const queued = await api(`/api/reports/official-stores/${action}`, {method: 'POST', body: JSON.stringify(body)});
    return waitForAsyncOperation(queued, message => { $('stores-progress').textContent = message || 'Processando…'; }, 24 * 60 * 60 * 1000);
  }
  async function run(work) {
    if (busy) return;
    busy = true;
    $('official-stores-report').setAttribute('aria-busy', 'true');
    $('official-stores-report').querySelectorAll('button').forEach(b => b.disabled = true);
    try { await work(); $('stores-progress').textContent = 'Operação concluída.'; }
    catch (error) { $('stores-progress').textContent = error.message; showToast(error.message, 'error'); }
    finally { busy = false; $('official-stores-report').removeAttribute('aria-busy'); $('official-stores-report').querySelectorAll('button').forEach(b => b.disabled = false); }
  }
  function filtered() {
    if (!data) return [];
    const text = $('stores-search').value.toLocaleLowerCase();
    const brand = $('stores-brand').value;
    const ref = $('stores-reference').value;
    const status = $('stores-status').value;
    return data.rows.filter(r => (!text || `${r.sku} ${r.title}`.toLocaleLowerCase().includes(text))
      && (!brand || r.brands.includes(brand))
      && (!ref || kinds.some(k => r.cells[ref]?.[k]?.status === 'existing'))
      && (!status || (r.sources || []).some(s => s.status === status && (!ref || `${s.account_id}:${s.official_store_id}` === ref)))
      && (!$('stores-missing').checked || [...targets].some(t => kinds.some(k => r.cells[t]?.[k]?.status === 'missing'))));
  }
  function renderRows() {
    if (!data) return;
    const rows = filtered(), columns = data.destinations.filter(t => targets.has(t.id));
    page = Math.max(0, Math.min(page, Math.ceil(rows.length / 50) - 1));
    $('stores-head').innerHTML = `<tr><th>Selecionar</th><th>SKU / produto</th><th>Preço Clássico</th><th>Preço Premium</th>${columns.map(t => `<th>${esc(t.account)}<br>${esc(t.name)}</th>`).join('')}</tr>`;
    $('stores-rows').innerHTML = rows.slice(page * 50, page * 50 + 50).map(r => `<tr>
      <td><input type="checkbox" aria-label="Selecionar ${attr(r.sku)}" data-store-sku="${attr(r.sku)}" ${selected.has(r.sku) ? 'checked' : ''}></td>
      <td><strong>${esc(r.sku)}</strong><br>${esc(r.title)}<br><small>${esc(r.brands.join(', ') || 'Marca não identificada')}</small></td>
      ${kinds.map(k => `<td><input type="number" min="0.01" step="0.01" aria-label="Preço ${labels[k]} ${attr(r.sku)}" data-price-sku="${attr(r.sku)}" data-kind="${k}" value="${attr(prices[r.sku]?.[k] ?? r.prices[k] ?? '')}"></td>`).join('')}
      ${columns.map(t => `<td>${kinds.map(k => {
        const cell = r.cells[t.id][k];
        return `<div><b>${labels[k]}:</b> ${cell.status === 'missing' ? 'Faltando' : cell.status === 'excluded' ? 'Marca não permitida' : cell.items.map(i => `<a target="_blank" rel="noreferrer" href="${attr(i.permalink || '#')}">${esc(i.id)}</a> (${esc(i.status || 'status desconhecido')}${Number(i.stock) === 0 ? ', sem estoque' : ''})`).join(', ')}</div>`;
      }).join('')}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${4 + columns.length}">Nenhum produto neste filtro. Selecione destinos ou ajuste os filtros.</td></tr>`;
    $('stores-selection').textContent = `${selected.size} SKU(s) selecionado(s) · ${rows.length} no filtro · ${targets.size} destino(s). Os preços informados valem para todos os destinos.`;
    $('stores-page').textContent = `${page + 1} / ${Math.max(1, Math.ceil(rows.length / 50))}`;
  }
  function renderBatches() {
    $('stores-batches').innerHTML = '<option value="">Selecione</option>' + [...data.batches].reverse().map(b => `<option value="${attr(b.id)}">${esc(b.created_at)} · ${esc(b.status)} · ${b.tasks.length} combinações</option>`).join('');
    if (batch) $('stores-batches').value = batch.id;
  }
  function renderBatch() {
    if (!batch) { $('stores-batch').innerHTML = ''; return; }
    const counts = {};
    batch.tasks.forEach(t => counts[t.status] = (counts[t.status] || 0) + 1);
    $('stores-batch').innerHTML = `<p>${Object.entries(counts).map(([s, n]) => `${n} ${labels[s] || s}`).join(' · ')}</p>
      <p>Revise os destinos e preços abaixo. A publicação preserva os anúncios existentes. Solicitações com resposta incerta serão conferidas, sem repetição automática.</p>
      <button type="button" class="primary" data-store-execute> ${batch.status === 'preview' ? 'Publicar anúncios faltantes' : 'Retomar / conferir pendências'}</button>
      <div class="store-matrix-scroll"><table class="store-matrix"><thead><tr><th>SKU</th><th>Conta / loja</th><th>Tipo / preço</th><th>Resultado</th></tr></thead><tbody>${batch.tasks.slice(batchPage * 100, batchPage * 100 + 100).map(t => `<tr><td>${esc(t.sku)}</td><td>${esc(t.target.account)} / ${esc(t.target.name)}</td><td>${labels[t.kind]} · ${Number(t.price || 0).toLocaleString('pt-BR', {style:'currency',currency:'BRL'})}</td><td>${labels[t.status] || esc(t.status)} ${esc(t.item_id || '')}<br>${esc(t.error || t.warning || '')}</td></tr>`).join('')}</tbody></table></div>
      <button type="button" data-store-batch-prev>Anterior</button> ${batchPage + 1} / ${Math.max(1, Math.ceil(batch.tasks.length / 100))} <button type="button" data-store-batch-next>Próxima</button>`;
  }
  async function load() {
    data = await operation('query');
    targets = new Set([...targets].filter(id => data.destinations.some(t => t.id === id)));
    selected = new Set([...selected].filter(s => data.rows.some(r => r.sku === s)));
    $('stores-content').hidden = false;
    $('stores-targets').innerHTML = data.destinations.map(t => `<label><input type="checkbox" data-store-target="${attr(t.id)}" ${targets.has(t.id) ? 'checked' : ''}>${esc(t.account)} — ${esc(t.name)}</label>`).join('') || 'Nenhuma loja autorizada encontrada nas contas conectadas.';
    const stores = [...new Map(data.destinations.map(t => [t.store_id, t])).values()];
    $('stores-rules').innerHTML = stores.map(t => `<label>${esc(t.name)}${t.fixed_brands ? ' (marca obrigatória)' : ''}<input data-store-rule="${attr(t.store_id)}" value="${attr(t.brands.join(', '))}" placeholder="Todas as marcas" ${t.fixed_brands ? 'disabled' : ''}></label>`).join('');
    $('stores-brand').innerHTML = '<option value="">Todas</option>' + [...new Set(data.rows.flatMap(r => r.brands))].sort().map(b => `<option>${esc(b)}</option>`).join('');
    $('stores-reference').innerHTML = '<option value="">Todas</option>' + data.destinations.map(t => `<option value="${attr(t.id)}">${esc(t.account)} / ${esc(t.name)}</option>`).join('');
    $('stores-errors').hidden = !data.errors.length;
    $('stores-errors').textContent = data.errors.length ? `Contas não consultadas: ${data.errors.map(e => `${e.account}: ${e.error}`).join(' | ')}` : '';
    renderRows(); renderBatches();
  }
  $('stores-load').onclick = () => run(load);
  $('stores-save-rules').onclick = () => run(async () => {
    const rules = {};
    document.querySelectorAll('[data-store-rule]:not(:disabled)').forEach(i => rules[i.dataset.storeRule] = i.value.split(',').map(v => v.trim()).filter(Boolean));
    await operation('rules', {rules}); await load(); batch = null; renderBatch();
  });
  function setTargets(all) { if (busy || !data) return; targets = new Set(all ? data.destinations.map(t => t.id) : []); document.querySelectorAll('[data-store-target]').forEach(i => i.checked = all); page = 0; renderRows(); }
  $('stores-all-targets').onclick = () => setTargets(true);
  $('stores-no-targets').onclick = () => setTargets(false);
  $('stores-targets').onchange = e => { const id = e.target.dataset.storeTarget; if (!id || busy) return; e.target.checked ? targets.add(id) : targets.delete(id); page = 0; renderRows(); };
  ['stores-search', 'stores-status', 'stores-brand', 'stores-reference', 'stores-missing'].forEach(id => $(id).addEventListener('input', () => {
    page = 0;
    const visible = new Set(filtered().map(r => r.sku));
    selected = new Set([...selected].filter(sku => visible.has(sku)));
    renderRows();
  }));
  $('stores-select-filtered').onclick = () => { filtered().forEach(r => selected.add(r.sku)); renderRows(); };
  $('stores-clear-selected').onclick = () => { selected.clear(); renderRows(); };
  $('stores-rows').onchange = e => {
    const sku = e.target.dataset.storeSku;
    if (sku) { e.target.checked ? selected.add(sku) : selected.delete(sku); renderRows(); }
    const priceSku = e.target.dataset.priceSku;
    if (priceSku) { prices[priceSku] ||= {...data.rows.find(r => r.sku === priceSku).prices}; prices[priceSku][e.target.dataset.kind] = e.target.value; }
  };
  $('stores-prev').onclick = () => { page--; renderRows(); };
  $('stores-next').onclick = () => { page++; renderRows(); };
  $('stores-preview').onclick = () => run(async () => {
    batch = await operation('preview', {skus: [...selected], destinations: [...targets], prices});
    data.batches.push(batch); batchPage = 0; renderBatches(); renderBatch(); $('stores-batch').scrollIntoView({behavior:'smooth',block:'start'});
  });
  $('stores-batches').onchange = () => { batch = data.batches.find(b => b.id === $('stores-batches').value); batchPage = 0; renderBatch(); };
  $('stores-batch').onclick = e => {
    if (e.target.hasAttribute('data-store-execute')) run(async () => {
      batch = await operation('execute', {batch_id: batch.id});
      await load(); renderBatch();
    });
    if (e.target.hasAttribute('data-store-batch-prev')) { batchPage = Math.max(0, batchPage - 1); renderBatch(); }
    if (e.target.hasAttribute('data-store-batch-next')) { batchPage = Math.min(Math.max(0, Math.ceil(batch.tasks.length / 100) - 1), batchPage + 1); renderBatch(); }
  };
})();
