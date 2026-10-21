(() => {
  const $ = id => document.getElementById(id), esc = value => escapeText(String(value ?? ''));
  let data = null, busy = false, page = 0, edits = {}, loadedAt = 0;
  function currentMonth() {
    const parts = new Intl.DateTimeFormat('en', {timeZone:'America/Sao_Paulo', year:'numeric', month:'2-digit'}).formatToParts(new Date());
    return `${parts.find(p=>p.type==='year').value}-${parts.find(p=>p.type==='month').value}`;
  }
  let lastCurrentMonth = currentMonth();
  $('goals-month').value = lastCurrentMonth;
  function editable() { return data?.editable && document.body.dataset.role !== 'viewer'; }
  function filters() { return {month:$('goals-month').value, search:$('goals-search').value, status:$('goals-status').value, registered:$('goals-registered').value, achievement:$('goals-achievement').value}; }
  async function operation(action, body) {
    const queued = await api('/api/sales-goals/' + action, {method:'POST', body:JSON.stringify(body), manualProgress:action !== 'query'});
    return waitForAsyncOperation(queued, message => $('goals-feedback').textContent = message || 'Processando…');
  }
  async function run(work) {
    if (busy) return;
    busy = true;
    document.querySelectorAll('#page-metas button, #page-metas input, #page-metas select').forEach(n=>n.disabled=true);
    try { await work(); } catch (error) { $('goals-feedback').textContent = error.message; }
    finally { busy = false; document.querySelectorAll('#page-metas button, #page-metas input, #page-metas select').forEach(n=>n.disabled=false); render(); }
  }
  function goalRevenueEstimate() {
    let total = 0, missing = 0, count = 0;
    for (const row of data?.rows || []) {
      const target = Object.hasOwn(edits, row.sku) ? Number(edits[row.sku]) : row.target;
      if (!(target > 0)) continue;
      count++;
      const prices = [row.classic_price, row.premium_price].filter(p => p != null && Number.isFinite(Number(p)) && Number(p) > 0).map(Number);
      if (!prices.length) { missing++; continue; }
      total += target * prices.reduce((sum, p) => sum + p, 0) / prices.length;
    }
    return {total, missing, count};
  }
  function rows() {
    if (!data) return [];
    const f = filters();
    return data.rows.map(r=>({...r, target:Object.hasOwn(edits,r.sku) ? Number(edits[r.sku]) || null : r.target})).filter(r =>
      (!f.status || r.statuses.includes(f.status)) && (!f.search || `${r.sku} ${r.product}`.toLocaleLowerCase().includes(f.search.toLocaleLowerCase())) &&
      (f.registered !== 'yes' || r.target) && (f.registered !== 'no' || !r.target) &&
      (!f.achievement || (r.target && (f.achievement === 'reached' ? r.sold >= r.target : f.achievement === 'not_reached' ? r.sold < r.target : r.sold === 0))));
  }
  function render() {
    $('goals-search-clear').disabled = busy || !$('goals-search').value;
    if (!data) {
      $('goals-rows').innerHTML = '<tr><td colspan="5">Consulte o mês para carregar as metas.</td></tr>';
      $('goals-summary').innerHTML = ''; $('goals-accounts').innerHTML = '';
      $('goals-save').disabled = true;
      document.querySelectorAll('#page-metas [data-export-report]').forEach(b=>b.disabled=true);
      return;
    }
    const all = rows(), targeted = all.filter(r=>r.target), total = targeted.reduce((n,r)=>n+r.target,0), sold = targeted.reduce((n,r)=>n+r.sold,0);
    page = Math.max(0, Math.min(page, Math.ceil(all.length/50)-1));
    $('goals-summary').innerHTML = [['SKUs com meta',targeted.length],['Meta em unidades',total],['Unidades vendidas',sold],['Metas atingidas',targeted.filter(r=>r.sold>=r.target).length]].map(([label,n])=>`<article><small>${label}</small><strong>${n.toLocaleString('pt-BR')}</strong></article>`).join('');
    const estimate = goalRevenueEstimate();
    $('goals-summary').insertAdjacentHTML('beforeend', `<article class="goal-revenue-estimate"><div><small>Faturamento bruto ao atingir todas as metas${estimate.missing ? ' · estimativa parcial' : ''}</small><strong>${estimate.count > estimate.missing ? money.format(estimate.total) : '—'}</strong></div><p>${estimate.count} SKU(s) com meta, independentemente dos filtros. Média dos preços atuais de Clássico e Premium; se houver só um preço, ele é utilizado. Sem descontar tarifas, frete ou custos.${estimate.missing ? ` ${estimate.missing} SKU(s) sem preço foram excluídos do valor.` : ''}${Object.keys(edits).length ? ' Inclui alterações de metas ainda não salvas.' : ''}</p></article>`);
    $('goals-rows').innerHTML = all.slice(page*50,page*50+50).map(r=> {
      const percent = r.target ? r.sold/r.target*100 : 0;
      return `<tr><td><strong>${esc(r.sku)}</strong><small>${esc(r.product)}</small>${skuCommercialHtml(r)}${window.goalPricingPage?.button(r.sku) || ''}</td><td><input type="number" min="1" step="1" aria-label="Meta mensal ${escapeAttr(r.sku)}" data-goal-sku="${escapeAttr(r.sku)}" value="${r.target || ''}" placeholder="Sem meta" ${!editable() || busy ? 'disabled' : ''}></td><td>${r.sold.toLocaleString('pt-BR')}</td><td><div class="goal-progress" role="progressbar" aria-label="Progresso ${escapeAttr(r.sku)}" aria-valuenow="${Math.min(100,percent)}" aria-valuemin="0" aria-valuemax="100"><span style="width:${Math.min(100,percent)}%"></span></div><small>${r.target ? `${percent.toFixed(1)}% · ${r.sold >= r.target ? 'Meta atingida' : 'Em andamento'}` : 'Cadastre uma meta'}</small></td><td>${r.target ? Math.max(0,r.target-r.sold).toLocaleString('pt-BR') : '—'}</td></tr>`;
    }).join('') || '<tr><td colspan="5">Nenhum SKU neste filtro.</td></tr>';
    $('goals-page').textContent = `${page+1} / ${Math.max(1,Math.ceil(all.length/50))} · ${all.length} SKU(s)`;
    $('goals-save').disabled = !editable() || !Object.keys(edits).length || busy;
    $('goals-prev').disabled = busy || page === 0;
    $('goals-next').disabled = busy || (page+1)*50 >= all.length;
    $('goals-accounts').innerHTML = data.accounts.map(a=>`<p><strong>${esc(a.account)}</strong> · ${a.orders_count} pedidos · ${money.format(a.amount)} · Atualizado em ${esc(a.updated_at)}</p>`).join('') || '<p>Ainda não há vendas sincronizadas para este mês.</p>';
    document.querySelectorAll('#page-metas [data-export-report]').forEach(b=>b.disabled=busy || Object.keys(edits).length>0);
  }
  async function load() {
    await window.goalPricingPage?.load();
    if (data && data.month !== $('goals-month').value) { data=null; render(); }
    data = await operation('query', {month:$('goals-month').value});
    loadedAt = Date.now();
    $('goals-feedback').textContent = data.warnings.join(' ') || 'Metas válidas para todos os meses. Vendas atualizadas automaticamente pela sincronização das contas.';
  }
  $('goals-refresh').onclick = () => run(async()=> {
    const result = await operation('refresh', {month:$('goals-month').value});
    await load();
    const errors = result.results.filter(r=>r.status==='error');
    if (errors.length) $('goals-feedback').textContent = errors.map(r=>`${r.account}: ${r.error}`).join(' · ');
  });
  $('goals-save').onclick = () => run(async()=> {
    await operation('save', {month:$('goals-month').value, targets:edits}); edits={}; await load(); $('goals-feedback').textContent='Metas salvas para todos os meses.';
  });
  $('goals-rows').onchange = e => {
    if (!e.target.dataset.goalSku) return;
    if (!e.target.validity.valid) { $('goals-feedback').textContent='Informe uma quantidade inteira maior que zero, ou deixe vazio para remover a meta.'; return; }
    edits[e.target.dataset.goalSku]=e.target.value;
    // Wait until blur finishes before replacing the focused input's table row.
    setTimeout(()=>{if(!busy)render();},0);
  };
  $('goals-search-clear').onclick = () => { $('goals-search').value=''; page=0; render(); $('goals-search').focus(); };
  ['goals-search','goals-status','goals-registered','goals-achievement'].forEach(id=>$(id).oninput=()=>{page=0;render();});
  $('goals-month').onchange = () => {
    if (Object.keys(edits).length && !confirm('Trocar de mês e descartar as metas ainda não salvas?')) { $('goals-month').value=data.month; return; }
    edits={};page=0;run(load);
  };
  $('goals-prev').onclick=()=>{page--;render();}; $('goals-next').onclick=()=>{page++;render();};
  function refreshView() {
    if (Object.keys(edits).length || busy) return;
    const now = currentMonth();
    if ($('goals-month').value === lastCurrentMonth && now !== lastCurrentMonth) $('goals-month').value = now;
    lastCurrentMonth = now;
    if (data && data.month === $('goals-month').value && Date.now()-loadedAt < 60000) return;
    run(load);
  }
  window.salesGoalsPage={filters,open:refreshView};
  setInterval(()=>{if(location.hash.startsWith('#/metas'))refreshView();},60000);
})();
