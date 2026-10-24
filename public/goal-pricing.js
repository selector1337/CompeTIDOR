(() => {
  const formatCurrency = v => new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(v);
  const esc = v => escapeText(String(v ?? ''));
  let rules = [], sku = '', busy = false, auditPage = 0, auditFilter = '', commercialRows = [];
  const labels={success:'Confirmado',error:'Falha',pending:'Aguardando confirmação',blocked:'Bloqueado',waiting:'Sem alteração'};
  const money=v=>v==null?'—':formatCurrency(v);
  const retryDate=v=>new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',dateStyle:'short',timeStyle:'medium'}).format(new Date(v*1000));
  const dialog = document.createElement('dialog');
  dialog.className = 'goal-rule-dialog';
  document.body.append(dialog);
  const admin = () => ['master','admin'].includes(document.body.dataset.role);
  async function request(action, body={}) {
    return api('/api/sales-goals/pricing/'+action, {method:'POST',body:JSON.stringify(body),manualProgress:false,retryPrompt:false});
  }
  async function load() {
    try { rules = ((await request('query')).rules || []).map(r=>({...rules.find(old=>old.sku===r.sku),...r})); }
    catch (_) { /* Opening a rule reports connection errors explicitly. */ }
  }
  async function loadDetail() {
    const result=await request('query',{sku});
    rules=[...rules.filter(r=>r.sku.toUpperCase()!==sku.toUpperCase()),...(result.rules||[])];
  }
  function summary(r) {
    if(!r)return '';
    const next=r.due_count ? 'Janela atingida · na fila de conferência' : r.next_change_at ? retryDate(r.next_change_at) : 'Sem previsão';
    return `<div class="goal-rule-summary"><span>${esc(r.status || 'Aguardando processamento')}</span><span>Próxima janela: <b>${esc(next)}</b></span><span>${r.pending_count||0} confirmações pendentes · ${r.retry_count||0} retentativas · ${r.blocked_count||0} bloqueios para revisão</span><span>${r.confirmed_changes || 0} reduções confirmadas por anúncio · ${r.reduction_percent==null?'Redução ainda não confirmada':esc(r.reduction_percent)+'% de redução média ponderada'}</span><small>${r.initialized_count||0}/${r.item_count||0} preços originais confirmados. Horários sujeitos a vendas, fila e limites.</small></div>`;
  }
  function simulation(form) {
    const row=commercialRows.find(r=>r.sku.toUpperCase()===sku.toUpperCase());
    if(!row)return '<p>Dados comerciais ainda não carregados. Abra pela lista de metas.</p>';
    const val=name=>Number(form.elements[name].value), step=val('step'), interval=val('interval_hours'), floor=val('floor_price');
    const margin=form.elements.floor_margin.value===''?null:val('floor_margin'), mode=form.elements.mode.value;
    return ['classic','premium'].map(kind=>{
      const price=Number(row[kind+'_price']),fee=row[kind+'_fee'],ship=row[kind+'_shipping'],cost=row.sku_cost;
      const known=[fee,ship,cost].every(v=>v!=null && Number.isFinite(Number(v)) && Number(v)>=0) && price>0;
      let minimum=floor,steps=0,current=price;
      const rate=known?Number(fee)/price:null;
      if(margin!==null && known && rate<1 && margin>=0 && margin<100)minimum=Math.max(minimum,(Number(cost)/(1-margin/100)+Number(ship))/(1-rate));
      let valid=price>0 && step>0 && interval>=1 && minimum>0 && (mode!=='percent'||step<100) && (margin===null || (known && margin>=0 && margin<100 && rate<1));
      if(valid)while(steps<100000){const next=Math.max(floor,Math.round((mode==='amount'?current-step:current*(1-step/100))*100)/100);if(next<=0||next>=current||next+0.000001<minimum)break;current=next;steps++;}
      const net=known?price-Number(fee)-Number(ship):null,profit=net==null?null:net-Number(cost);
      const hours=steps*interval;
      return `<article class="goal-simulation-card"><strong>${kind==='classic'?'Clássico':'Premium'}</strong><small>${esc(row[kind+'_reference']||'Sem anúncio base')}</small><div class="goal-simulation-values">${[['Preço',price>0?price:null],['Tarifa',fee],['Frete',ship],['Custo',cost],['Líquido',net],['Lucro',profit]].map(([label,value])=>`<span>${label}<b>${money(value)}</b></span>`).join('')}</div><p>Margem atual: <b>${known&&net>0?((profit/net)*100).toLocaleString('pt-BR',{maximumFractionDigits:2})+'%':'Indisponível'}</b></p><p>${valid&&steps<100000?`${steps} reduções · aproximadamente ${Math.floor(hours/24)} dia(s) e ${hours%24} hora(s)<br>Preço final estimado: <b>${money(current)}</b>`:'Preencha a regra e confira custo, tarifa e frete para estimar.'}</p></article>`;
    }).join('')+'<p class="muted">Estimativa a partir dos anúncios base e preços atuais, sem novas vendas. A tarifa proporcional e o frete ficam constantes nesta simulação. Vendas, pausas, fila, falhas e mudanças de tarifas podem ampliar o prazo; cada redução é validada pela API antes do envio.</p>';
  }
  function button(value) {
    if (!admin()) return '';
    const rule=rules.find(r=>r.sku.toUpperCase()===value.toUpperCase());
    const status=rule?.enabled ? 'active' : rule ? 'paused' : 'new';
    return `<button type="button" class="goal-rule-open is-${status}" data-goal-rule="${escapeAttr(value)}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M13 3 4 14h7l-1 7 10-12h-7l1-6Z"/></svg><span>Preços automáticos</span><span class="goal-rule-status">${rule ? (rule.enabled?'Ativos':'Pausados') : 'Configurar'}</span><span aria-hidden="true">›</span></button>`;
  }
  function draw() {
    const r = rules.find(r=>r.sku.toUpperCase()===sku.toUpperCase()) || {};
    const locked = r.enabled || Object.values(r.items || {}).some(i=>i.pending!=null || i.last!==i.original);
    const field = (name,label,value,min,step='any') => `<label>${label}<input name="${name}" type="number" min="${min}" step="${step}" value="${esc(value)}" ${locked?'disabled':''}></label>`;
    dialog.innerHTML = `<form><header><div><small>METAS DE VENDA · ${esc(sku)}</small><h2>Preços automáticos</h2></div><button type="button" data-close aria-label="Fechar">×</button></header>
      <p>Todos os anúncios Clássicos e Premium deste SKU, ativos ou pausados, em todas as contas conectadas. Novos anúncios também serão incluídos.</p>
      <div class="notice">${esc(r.status || 'Desativada — configure e ative quando desejar.')}</div>
      ${r.sku?'<section id="goal-rule-audit"></section>':''}
      <div class="goal-rule-grid">
      ${field('interval_hours','Intervalo sem vendas (horas)',r.interval_hours??24,1)}
      ${field('sale_pause_hours','Pausa após cada venda (horas)',r.sale_pause_hours??24,1)}
      <label>Reduzir em<select name="mode" ${locked?'disabled':''}><option value="amount" ${r.mode==='amount'?'selected':''}>R$ por alteração</option><option value="percent" ${r.mode==='percent'?'selected':''}>% do preço atual</option></select></label>
      ${field('step','Valor da redução',r.step??1,.01)}
      ${field('floor_price','Preço mínimo (R$; 0 para usar só margem)',r.floor_price??0,0)}
      ${field('floor_margin','Margem mínima (%; opcional)',r.floor_margin??'',0)}
      </div><section class="goal-simulation"><h3>Rentabilidade e duração estimada</h3><div data-simulation></div></section><p class="muted">O primeiro ciclo confirma e registra o preço original de cada anúncio. Atingir a meta ou mudar de mês não restaura preços nem reinicia o intervalo. A regra continua reduzindo até os limites cadastrados, respeitando a pausa após vendas. A margem considera custo, tarifa e frete sobre a receita líquida; dados incompletos bloqueiam a redução.</p>
      <p class="muted">Pausar mantém os preços atuais. Para editar uma regra existente, pause e restaure os preços primeiro. Alterações manuais de preço interrompem a automação do anúncio.</p>
      <div class="goal-rule-actions"><button type="submit" name="save" ${locked?'disabled':''}>Salvar desativada</button><button type="submit" name="activate" ${locked?'disabled':''}>Salvar e ativar</button>
      ${r.sku?`<button type="button" data-action="${r.enabled?'pause':'resume'}">${r.enabled?'Pausar':'Retomar'}</button><button type="button" data-action="restore">Restaurar preços e desativar</button>`:''}</div>
      <p role="status" id="goal-rule-feedback"></p>

    </form>`;
    renderAudit();
    const form=dialog.querySelector('form');
    const updateSimulation=()=>{dialog.querySelector('[data-simulation]').innerHTML=simulation(form);};
    form.addEventListener('input',updateSimulation);updateSimulation();
    dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.querySelector('form').onsubmit=e=>{e.preventDefault();const body=Object.fromEntries(new FormData(e.currentTarget));body.enabled=e.submitter?.name==='activate';change(body);};
    dialog.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>change({action:b.dataset.action}));
  }
  function priceEvolution(i) {
    const original=i.initialized ? i.original : null, current=i.observed_price ?? i.last;
    if(!(original>0) || current==null)return '<small>Redução acumulada: aguardando confirmação</small>';
    const delta=original-current, percent=Math.abs(delta)/original*100;
    return `<small>${delta>=0?'Redução acumulada':'Aumento desde o original'}: ${esc(money(Math.abs(delta)))} (${percent.toLocaleString('pt-BR',{maximumFractionDigits:2})}%)</small>`;
  }
  function renderAudit() {
    const panel=dialog.querySelector('#goal-rule-audit');if(!panel)return;
    const r=rules.find(r=>r.sku.toUpperCase()===sku.toUpperCase()) || {};
    const status=i=>i.blocked?'blocked':i.pending!=null?'pending':i.outcome || 'waiting';
    const items=Object.entries(r.items||{}),counts={success:0,error:0,pending:0,blocked:0,waiting:0};
    items.forEach(([,i])=>counts[status(i)]++);
    const history=(r.history||[]).slice().reverse().filter(h=>!auditFilter || (h.outcome||'success')===auditFilter);
    const pages=Math.max(1,Math.ceil(history.length/20));auditPage=Math.min(auditPage,pages-1);
    panel.innerHTML=`<h3>Acompanhamento dos preços</h3><p>${esc(r.status||'')}</p><p class="muted">Último resultado registrado por anúncio. Confirmações anteriores não significam que um novo ciclo já terminou. Atualize para consultar o processamento.</p>
      <div class="goal-audit-counts">${Object.entries(counts).map(([key,n])=>`<span class="goal-audit-${key}">${labels[key]}: <strong>${n}</strong></span>`).join('')}</div>
      <button type="button" data-audit-refresh>Atualizar acompanhamento</button><p role="status" data-audit-message></p>
      <details open><summary>Resultado por anúncio (${items.length})</summary><div class="goal-audit-scroll"><table><thead><tr><th>Conta / anúncio</th><th>Preço consultado / evolução</th><th>Preço pendente</th><th>Resultado</th></tr></thead><tbody>${items.map(([id,i])=>`<tr><td>${esc(i.account_name||i.account_id)}<br><strong>${esc(id)}</strong><br>${i.type==='gold_pro'?'Premium':'Clássico'}</td><td>${esc(money(i.observed_price ?? (i.initialized ? i.last : null)))}<small>Original: ${esc(money(i.initialized ? i.original : null))}</small>${priceEvolution(i)}<small>${i.observed_at?'Consultado/confirmado: '+esc(i.observed_at):'Último valor registrado; sem consulta em tempo real'}</small></td><td>${esc(money(i.pending))}</td><td><strong class="goal-audit-${status(i)}">${labels[status(i)]}</strong><small>${esc(i.status)}</small><small>${esc(i.result_at||'')}</small><small>Envios de preço: ${esc(i.price_attempts||0)}</small>${r.enabled&&i.retry_after>0?`<small>Próxima conferência prevista: ${esc(retryDate(i.retry_after))}</small>`:''}</td></tr>`).join('')}</tbody></table></div></details>
      <details open><summary>Histórico de eventos</summary><p class="muted">Até 1.000 eventos recentes por SKU, preservados ao editar a regra. O preço solicitado só é considerado aplicado quando o resultado é Confirmado.</p>
      <label>Filtrar resultado <select data-audit-filter><option value="">Todos</option>${Object.entries(labels).map(([k,v])=>`<option value="${k}" ${auditFilter===k?'selected':''}>${v}</option>`).join('')}</select></label>
      <div class="goal-audit-scroll"><table><thead><tr><th>Data / anúncio</th><th>Anterior</th><th>Solicitado / confirmado</th><th>Resultado</th></tr></thead><tbody>${history.slice(auditPage*20,(auditPage+1)*20).map(h=>`<tr><td>${esc(h.at)}<small>${esc(h.account||r.items?.[h.item_id]?.account_name||'')} · ${esc(h.item_id||'Regra do SKU')}</small><small>${h.type?(h.type==='gold_pro'?'Premium':'Clássico'):''}</small></td><td>${esc(money(h.from))}</td><td>${esc(money(h.to))}</td><td><strong class="goal-audit-${h.outcome||'success'}">${labels[h.outcome||'success']||'Registro'}</strong><small>${esc(h.action)}</small>${h.original!=null?`<small>Original: ${esc(money(h.original))} · Preço consultado/confirmado: ${esc(money(h.current))}</small><small>Variação desde o original: ${esc(money(h.reduction_amount))} (${esc(h.reduction_percent)}% de redução)</small>`:''}${h.price_attempts?`<small>Envio nº ${esc(h.price_attempts)}</small>`:''}${h.retry_after>0?`<small>Conferência programada: ${esc(retryDate(h.retry_after))}</small>`:''}</td></tr>`).join('') || '<tr><td colspan="4">Nenhum evento neste filtro.</td></tr>'}</tbody></table></div>
      <div class="goal-rule-actions"><button type="button" data-audit-prev ${auditPage===0?'disabled':''}>Anterior</button><span>${auditPage+1} / ${pages} · ${history.length} eventos</span><button type="button" data-audit-next ${auditPage+1>=pages?'disabled':''}>Próxima</button></div></details>`;
    panel.querySelector('[data-audit-filter]').onchange=e=>{auditFilter=e.target.value;auditPage=0;renderAudit();};
    panel.querySelector('[data-audit-prev]').onclick=()=>{auditPage--;renderAudit();};
    panel.querySelector('[data-audit-next]').onclick=()=>{auditPage++;renderAudit();};
    panel.querySelector('[data-audit-refresh]').onclick=async e=>{
      e.target.disabled=true;
      try {await loadDetail();renderAudit();}
      catch(err){panel.querySelector('[data-audit-message]').textContent=err.message;e.target.disabled=false;}
    };
  }
  async function change(body) {
    if(busy || !admin())return;
    if((body.enabled || body.action==='resume') && !confirm(`Ativar reduções para ${sku} em TODAS as contas e nas duas modalidades?`))return;
    if(body.action==='restore' && !confirm('Restaurar os preços originais registrados, inclusive se houve alteração manual, e desativar a regra?'))return;
    busy=true;
    dialog.querySelector('#goal-rule-feedback').textContent='Salvando solicitação… Se houver uma alteração deste SKU em andamento, aguardaremos sua conferência.';
    const buttons=Array.from(dialog.querySelectorAll('button'),b=>[b,b.disabled]);
    buttons.forEach(([b])=>b.disabled=true);
    try { await request('save',{...body,sku});await loadDetail();window.salesGoalsPage?.pricingChanged(sku,Boolean(rules.find(r=>r.sku.toUpperCase()===sku.toUpperCase())?.enabled));draw();dialog.querySelector('#goal-rule-feedback').textContent='Solicitação salva. O processamento ocorre em segundo plano.'; }
    catch(e){dialog.querySelector('#goal-rule-feedback').textContent=e.message;}
    finally{busy=false;buttons.forEach(([b,disabled])=>{if(b.isConnected)b.disabled=disabled;});}
  }
  document.addEventListener('click',async e=>{
    const b=e.target.closest('[data-goal-rule]');if(!b || !admin())return;
    sku=b.dataset.goalRule;auditPage=0;auditFilter='';
    try{await loadDetail();draw();dialog.showModal();}
    catch(err){document.getElementById('goals-feedback').textContent=err.message;}
  });
  window.goalPricingPage={button,load,summary,setRows:rows=>{commercialRows=rows;rules=rows.filter(r=>r.pricing_summary).map(r=>({...rules.find(old=>old.sku===r.sku),...r.pricing_summary}));}};
})();
