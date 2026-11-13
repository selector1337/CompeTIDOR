(() => {
  const formatCurrency = v => new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(v);
  const esc = v => escapeText(String(v ?? ''));
  let rules = [], sku = '', busy = false, auditPage = 0, auditFilter = '', commercialRows = [];
  let selectedRuleId = null, creatingRule = false;
  const pendingSaves = new Map();
  const scopeLabel = scope => ({classic:'Clássico',premium:'Premium',both:'Clássico e Premium'}[scope] || 'Clássico e Premium');
  const skuRules = () => rules.filter(r=>r.sku.toUpperCase()===sku.toUpperCase());
  const selectedRule = () => creatingRule ? {} : skuRules().find(r=>(r.rule_id||r.sku)===(selectedRuleId||sku)) || skuRules()[0] || {};
  const labels={success:'Confirmado',error:'Falha',pending:'Aguardando confirmação',blocked:'Bloqueado',waiting:'Sem alteração'};
  const money=v=>v==null?'—':formatCurrency(v);
  const retryDate=v=>new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',dateStyle:'short',timeStyle:'medium'}).format(new Date(v*1000));
  const dialog = document.createElement('dialog');
  dialog.className = 'goal-rule-dialog';
  document.body.append(dialog);
  const admin = () => ['master','admin'].includes(document.body.dataset.role);
  async function request(action, body={}, options={}) {
    return api('/api/sales-goals/pricing/'+action, {method:'POST',body:JSON.stringify(body),manualProgress:false,retryPrompt:false,...options});
  }
  async function load() {
    try { rules = ((await request('query')).rules || []).map(r=>({...rules.find(old=>(old.rule_id||old.sku)===(r.rule_id||r.sku)),...r})); }
    catch (_) { /* Opening a rule reports connection errors explicitly. */ }
  }
  async function loadDetail() {
    const result=await request('query',{sku});
    rules=[...rules.filter(r=>r.sku.toUpperCase()!==sku.toUpperCase()),...(result.rules||[])];
  }
  function summary(r) {
    if(!r)return '';
    if(r.rules)return r.rules.map(entry=>`<div><strong>${scopeLabel(entry.listing_scope)}</strong>${summary(entry)}</div>`).join('');
    const next=r.due_count ? 'Janela atingida · na fila de conferência' : r.next_change_at ? retryDate(r.next_change_at) : 'Sem previsão';
    return `<div class="goal-rule-summary"><span>${esc(r.status || 'Aguardando processamento')}</span><span>Próxima janela: <b>${esc(next)}</b></span><span>${r.pending_count||0} confirmações pendentes · ${r.retry_count||0} retentativas · ${r.blocked_count||0} bloqueios para revisão</span><span>${r.confirmed_changes || 0} reduções confirmadas por anúncio · ${r.reduction_percent==null?'Redução ainda não confirmada':esc(r.reduction_percent)+'% de redução média ponderada'}</span><small>${r.initialized_count||0}/${r.item_count||0} preços originais confirmados. Horários sujeitos a vendas, fila e limites.</small></div>`;
  }
  function simulation(form) {
    const row=commercialRows.find(r=>r.sku.toUpperCase()===sku.toUpperCase());
    if(!row)return '<p>Dados comerciais ainda não carregados. Abra pela lista de metas.</p>';
    const val=name=>Number(form.elements[name].value), step=val('step'), interval=val('interval_hours'), floor=val('floor_price');
    const margin=form.elements.floor_margin.value===''?null:val('floor_margin'), mode=form.elements.mode.value;
    const scope=form.elements.listing_scope.value;
    return (scope==='both'?['classic','premium']:[scope]).map(kind=>{
      const price=Number(row[kind+'_price']),fee=row[kind+'_fee'],ship=row[kind+'_shipping'],cost=row.sku_cost;
      const known=[fee,ship,cost].every(v=>v!=null && Number.isFinite(Number(v)) && Number(v)>=0) && price>0;
      let minimum=floor,steps=0,current=price;
      const rate=known?Number(fee)/price:null;
      if(margin!==null && known && rate<1 && margin>=0 && margin<100)minimum=Math.max(minimum,(Number(cost)/(1-margin/100)+Number(ship))/(1-rate));
      let valid=price>0 && step>0 && interval>=1 && minimum>0 && (mode!=='percent'||step<100) && (margin===null || (known && margin>=0 && margin<100 && rate<1));
      if(valid)while(steps<100000){const next=Math.max(floor,Math.round((mode==='amount'?current-step:current*(1-step/100))*100)/100);if(next<=0||next>=current||next+0.000001<minimum)break;current=next;steps++;}
      const net=known?price-Number(fee)-Number(ship):null,profit=net==null?null:net-Number(cost);
      const hours=steps*interval;
      const marginValue=known&&net>0?(profit/net)*100:null;
      const ok=valid&&steps<100000;
      return `<article class="goal-simulation-card"><header class="goal-sim-head"><strong>${kind==='classic'?'Clássico':'Premium'}</strong><small>${esc(row[kind+'_reference']||'Sem anúncio base')}</small></header><div class="goal-simulation-values">${[['Preço',price>0?price:null],['Tarifa',fee],['Frete',ship],['Custo',cost],['Líquido',net],['Lucro',profit]].map(([label,value])=>`<span class="${label==='Lucro'&&value!=null?(value>=0?'is-positive':'is-negative'):''}">${label}<b>${money(value)}</b></span>`).join('')}</div><p class="goal-sim-margin ${marginValue==null?'':marginValue>=0?'is-positive':'is-negative'}">Margem atual: <b>${marginValue==null?'Indisponível':marginValue.toLocaleString('pt-BR',{maximumFractionDigits:2})+'%'}</b></p><div class="goal-sim-result ${ok?'':'is-empty'}">${ok?`<span class="goal-sim-steps">${steps} reduções · aproximadamente ${Math.floor(hours/24)} dia(s) e ${hours%24} hora(s)</span><span class="goal-sim-track" aria-hidden="true"><i style="width:${price>0?Math.max(2,Math.min(100,current/price*100)).toFixed(1):0}%"></i></span><span class="goal-sim-final"><small>${money(price>0?price:null)}</small><span aria-hidden="true">→</span>Preço final estimado: <b>${money(current)}</b></span>`:'Preencha a regra e confira custo, tarifa e frete para estimar.'}</div></article>`;
    }).join('')+'<p class="muted goal-sim-note">Estimativa a partir dos anúncios base e preços atuais, sem novas vendas. A tarifa proporcional e o frete ficam constantes nesta simulação. Vendas, pausas, fila, falhas e mudanças de tarifas podem ampliar o prazo; cada redução é validada pela API antes do envio.</p>';
  }
  function button(value) {
    if (!admin()) return '';
    const matching=rules.filter(r=>r.sku.toUpperCase()===value.toUpperCase());
    const rule=matching.find(r=>r.enabled)||matching[0];
    const status=rule?.enabled ? 'active' : rule ? 'paused' : 'new';
    return `<button type="button" class="goal-rule-open is-${status}" data-goal-rule="${escapeAttr(value)}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M13 3 4 14h7l-1 7 10-12h-7l1-6Z"/></svg><span>Preços automáticos</span><span class="goal-rule-status">${rule ? (rule.enabled?'Ativos':'Pausados') : 'Configurar'}</span><span aria-hidden="true">›</span></button>`;
  }
  function draw() {
    const r = selectedRule();
    const locked = Boolean(r.restoring);
    const field = (name,label,value,min,hint='',unit='',step='any') => `<label class="gp-field"><span>${label}</span><span class="gp-input${unit?' has-unit':''}"><input name="${name}" type="number" min="${min}" step="${step}" value="${esc(value)}" ${locked?'disabled':''}>${unit?`<em data-unit-for="${name}">${unit}</em>`:''}</span>${hint?`<small>${hint}</small>`:''}</label>`;
    const state = r.restoring ? 'restoring' : r.enabled ? 'active' : r.sku ? 'paused' : 'new';
    const stateLabel = {restoring:'Restaurando',active:'Ativa',paused:'Desativada',new:'Nova regra'}[state];
    const section = (n,title,sub) => `<header class="gp-section-head"><b>${n}</b><div><h3>${title}</h3><small>${sub}</small></div></header>`;
    dialog.innerHTML = `<form class="gp-form"><header class="gp-head"><span class="gp-head-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M13 3 4 14h7l-1 7 10-12h-7l1-6Z"/></svg></span><div class="gp-head-title"><small>Metas de venda · <b>${esc(sku)}</b></small><h2>Preços automáticos</h2></div><span class="gp-state is-${state}">${stateLabel}</span><button type="button" data-close aria-label="Fechar">×</button></header>
      <div class="gp-body">
      <p class="gp-intro">Anúncios ativos ou pausados deste SKU em todas as contas conectadas, nas modalidades escolhidas. Novos anúncios dessas modalidades também serão incluídos.</p>
      <div class="gp-rulebar"><div class="goal-rule-picker"><label>Regra do SKU<select data-rule-picker>${skuRules().map(entry=>`<option value="${esc(entry.rule_id||entry.sku)}" ${(entry.rule_id||entry.sku)===(r.rule_id||r.sku)?'selected':''}>${scopeLabel(entry.listing_scope)} · ${entry.enabled?'Ativa':'Pausada'}</option>`).join('')}<option value="__new__" ${creatingRule||!r.sku?'selected':''}>+ Nova regra</option></select></label></div>
      <div class="notice gp-status is-${state}">${esc(r.status || 'Desativada — configure e ative quando desejar.')}</div></div>
      ${r.sku?'<section id="goal-rule-audit" class="gp-card"></section>':''}
      <section class="gp-card">${section(1,'Regra de redução','Onde aplicar, quando reduzir, quanto e até onde')}
      <div class="goal-rule-grid gp-groups">
      <div class="gp-group"><h4>Onde aplicar</h4><label class="gp-field"><span>Aplicar aos anúncios</span><select name="listing_scope" ${r.sku||locked?'disabled':''}><option value="both" ${!r.listing_scope||r.listing_scope==='both'?'selected':''}>Clássico e Premium</option><option value="classic" ${r.listing_scope==='classic'?'selected':''}>Somente Clássico</option><option value="premium" ${r.listing_scope==='premium'?'selected':''}>Somente Premium</option></select>${r.sku?'<small>Para outra modalidade, cadastre uma nova regra.</small>':''}</label></div>
      <div class="gp-group"><h4>Quando reduzir</h4>${field('interval_hours','Intervalo sem vendas',r.interval_hours??24,1,'Tempo sem vendas antes de cada redução.','horas')}${field('sale_pause_hours','Pausa após cada venda',r.sale_pause_hours??24,1,'Reinicia a contagem a cada venda.','horas')}</div>
      <div class="gp-group"><h4>Quanto reduzir</h4><label class="gp-field"><span>Reduzir em</span><select name="mode" ${locked?'disabled':''}><option value="amount" ${r.mode==='amount'?'selected':''}>R$ por alteração</option><option value="percent" ${r.mode==='percent'?'selected':''}>% do preço atual</option></select></label>${field('step','Valor da redução',r.step??1,.01,'',r.mode==='percent'?'%':'R$')}</div>
      <div class="gp-group"><h4>Até onde</h4>${field('floor_price','Preço mínimo',r.floor_price??0,0,'Use 0 para limitar só pela margem.','R$')}${field('floor_margin','Margem mínima',r.floor_margin??'',0,'Opcional.','%')}</div>
      </div><p class="muted gp-note">O primeiro ciclo confirma e registra o preço original de cada anúncio. Atingir a meta ou mudar de mês não restaura preços nem reinicia o intervalo. A regra continua reduzindo até os limites cadastrados, respeitando a pausa após vendas. A margem considera custo, tarifa e frete sobre a receita líquida; dados incompletos bloqueiam a redução.</p></section>
      <section class="gp-card">${section(2,'Programação','Opcional · horário de Brasília')}
      <fieldset class="goal-calendar"><legend>Programação opcional · horário de Brasília</legend><div class="gp-weekdays">${['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'].map((day,index)=>`<label><input type="checkbox" name="weekday" value="${index}" ${(r.weekdays||[]).includes(index)?'checked':''}><span>${day}</span></label>`).join('')}</div><div class="gp-times"><label>Data específica<input type="date" name="specific_date" value="${esc(r.specific_date||'')}"></label><label>Das<input type="time" name="start_time" value="${esc(r.start_time||'')}"></label><label>Até<input type="time" name="end_time" value="${esc(r.end_time||'')}"></label></div><small>Em branco: todos os dias e horários. Os filtros se combinam. Se o fim for anterior ao início, o intervalo termina no dia seguinte. Vendas e confirmações continuam sendo conferidas fora do horário.</small></fieldset></section>
      <section class="goal-simulation gp-card">${section(3,'Rentabilidade e duração estimada','Simulação com os preços atuais dos anúncios base')}<div data-simulation></div></section>
      <p class="muted gp-note">Edite os campos e salve para atualizar a regra sem restaurar preços. Histórico e preços originais são preservados. Para outra modalidade, cadastre uma nova regra. A duração acima considera tempo contínuo sem restrições de calendário.</p>
      </div>
      <footer class="gp-foot"><p role="status" id="goal-rule-feedback"></p><div class="goal-rule-actions">${r.sku?`<button type="button" class="gp-danger" data-action="restore">Restaurar preços e desativar</button><button type="button" data-action="${r.enabled?'pause':'resume'}">${r.enabled?'Pausar':'Retomar'}</button>`:''}<span class="gp-spacer"></span><button type="submit" name="save" ${locked?'disabled':''}>Salvar desativada</button><button type="submit" name="activate" ${locked?'disabled':''}>${r.enabled?'Salvar alterações e manter ativa':'Salvar e ativar'}</button></div></footer>
    </form>`;
    renderAudit();
    const form=dialog.querySelector('form');
    const updateSimulation=()=>{if(!form.isConnected)return;const unit=dialog.querySelector('[data-unit-for="step"]');if(unit)unit.textContent=form.elements.mode.value==='percent'?'%':'R$';dialog.querySelector('[data-simulation]').innerHTML=simulation(form);};
    form.addEventListener('change',updateSimulation);
    form.addEventListener('input',updateSimulation);updateSimulation();
    dialog.querySelector('[data-rule-picker]').onchange=e=>{creatingRule=e.target.value==='__new__';selectedRuleId=creatingRule?null:e.target.value;auditPage=0;draw();};
    dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.querySelector('form').onsubmit=e=>{e.preventDefault();const data=new FormData(e.currentTarget);const body=Object.fromEntries(data);body.weekdays=data.getAll('weekday').map(Number);body.enabled=e.submitter?.name==='activate';change(body);};
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
    const r=selectedRule();
    const status=i=>i.blocked?'blocked':i.pending!=null?'pending':i.outcome || 'waiting';
    const items=Object.entries(r.items||{}),counts={success:0,error:0,pending:0,blocked:0,waiting:0};
    items.forEach(([,i])=>counts[status(i)]++);
    const history=(r.history||[]).slice().reverse().filter(h=>!auditFilter || (h.outcome||'success')===auditFilter);
    const pages=Math.max(1,Math.ceil(history.length/20));auditPage=Math.min(auditPage,pages-1);
    const type=t=>t==='gold_pro'?'Premium':'Clássico';
    const next=r.due_count ? 'Na fila de conferência' : r.next_change_at ? retryDate(r.next_change_at) : 'Sem previsão';
    const overview=[
      ['Próxima janela',next,'clock'],
      ['Reduções confirmadas',String(r.confirmed_changes||0),'check'],
      ['Redução média',r.reduction_percent==null?'—':`${r.reduction_percent}%`,'down'],
      ['Preços originais',`${r.initialized_count||0}/${r.item_count||items.length}`,'tag'],
    ];
    const itemCard=([id,i])=>{
      const st=status(i), original=i.initialized?i.original:null, current=i.observed_price ?? (i.initialized ? i.last : null);
      const ratio=original>0&&current!=null?Math.max(2,Math.min(100,current/original*100)):null;
      return `<article class="gp-item is-${st}"><div class="gp-item-id"><strong>${esc(id)}</strong><small>${esc(i.account_name||i.account_id)}<span class="gp-chip">${type(i.type)}</span></small></div>
        <div class="gp-item-price"><b>${esc(money(current))}</b><small>Original: ${esc(money(original))}</small>${priceEvolution(i)}${ratio==null?'':`<span class="gp-bar" aria-hidden="true"><i style="width:${ratio.toFixed(1)}%"></i></span>`}<small class="gp-faint">${i.observed_at?'Consultado/confirmado: '+esc(i.observed_at):'Último valor registrado; sem consulta em tempo real'}</small></div>
        <div class="gp-item-pending"><small>Preço pendente</small><b>${esc(money(i.pending))}</b></div>
        <div class="gp-item-result"><strong class="gp-pill goal-audit-${st}">${labels[st]}</strong><small>${esc(i.status)}</small><small>${esc(i.result_at||'')}</small><small>Envios de preço: ${esc(i.price_attempts||0)}</small>${r.enabled&&i.retry_after>0?`<small>Próxima conferência prevista: ${esc(retryDate(i.retry_after))}</small>`:''}</div></article>`;
    };
    const eventRow=h=>{const outcome=h.outcome||'success';return `<li class="gp-event is-${outcome}"><div class="gp-event-main"><strong class="gp-pill goal-audit-${outcome}">${labels[outcome]||'Registro'}</strong><span>${esc(h.action)}</span><small>${esc(h.at)} · ${esc(h.account||r.items?.[h.item_id]?.account_name||'')} · ${esc(h.item_id||'Regra do SKU')}${h.type?' · '+type(h.type):''}</small>${h.original!=null?`<small>Original: ${esc(money(h.original))} · Preço consultado/confirmado: ${esc(money(h.current))}</small><small>Variação desde o original: ${esc(money(h.reduction_amount))} (${esc(h.reduction_percent)}% de redução)</small>`:''}${h.price_attempts?`<small>Envio nº ${esc(h.price_attempts)}</small>`:''}${h.retry_after>0?`<small>Conferência programada: ${esc(retryDate(h.retry_after))}</small>`:''}</div><div class="gp-event-price"><s>${esc(money(h.from))}</s><span aria-hidden="true">→</span><b>${esc(money(h.to))}</b></div></li>`;};
    panel.innerHTML=`<header class="gp-section-head gp-audit-head"><span class="gp-live ${r.enabled?'is-on':''}" aria-hidden="true"></span><div><h3>Acompanhamento dos preços</h3><small>${esc(r.status||'')}</small></div><button type="button" data-audit-refresh>Atualizar acompanhamento</button></header><p role="status" data-audit-message></p>
      <div class="gp-overview">${overview.map(([label,value,icon])=>`<div class="gp-stat gp-stat-${icon}"><span>${label}</span><strong>${esc(value)}</strong></div>`).join('')}</div>
      <div class="goal-audit-counts">${Object.entries(counts).map(([key,n])=>`<span class="goal-audit-${key}">${labels[key]}: <strong>${n}</strong></span>`).join('')}</div>
      <p class="muted gp-note">Último resultado registrado por anúncio. Confirmações anteriores não significam que um novo ciclo já terminou. Atualize para consultar o processamento.</p>
      <details open class="gp-details"><summary>Resultado por anúncio <span>${items.length}</span></summary><div class="gp-items">${items.map(itemCard).join('') || '<p class="gp-empty">Nenhum anúncio processado ainda.</p>'}</div></details>
      <details open class="gp-details"><summary>Histórico de eventos <span>${history.length}</span></summary><div class="gp-history-toolbar"><p class="muted">Até 1.000 eventos recentes por SKU, preservados ao editar a regra. O preço solicitado só é considerado aplicado quando o resultado é Confirmado.</p>
      <label>Filtrar resultado <select data-audit-filter><option value="">Todos</option>${Object.entries(labels).map(([k,v])=>`<option value="${k}" ${auditFilter===k?'selected':''}>${v}</option>`).join('')}</select></label></div>
      <ol class="gp-timeline">${history.slice(auditPage*20,(auditPage+1)*20).map(eventRow).join('') || '<li class="gp-empty">Nenhum evento neste filtro.</li>'}</ol>
      <div class="goal-rule-actions gp-pager"><button type="button" data-audit-prev ${auditPage===0?'disabled':''}>Anterior</button><span>${auditPage+1} / ${pages} · ${history.length} eventos</span><button type="button" data-audit-next ${auditPage+1>=pages?'disabled':''}>Próxima</button></div></details>`;
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
    if(!pendingSaves.has(sku) && (body.enabled || body.action==='resume') && !confirm(`Ativar reduções para ${sku} em TODAS as contas: ${scopeLabel(body.listing_scope||selectedRule().listing_scope)}?`))return;
    if(body.action==='restore' && !confirm('Restaurar os preços originais registrados, inclusive se houve alteração manual, e desativar a regra?'))return;
    busy=true;
    dialog.querySelector('#goal-rule-feedback').textContent='Salvando solicitação… Se houver uma alteração deste SKU em andamento, aguardaremos sua conferência.';
    const buttons=Array.from(dialog.querySelectorAll('button,input,select,textarea'),b=>[b,b.disabled]);
    buttons.forEach(([b])=>b.disabled=true);
    try {
      const current=selectedRule();
      let attempt=pendingSaves.get(sku);
      if(!attempt){attempt={body:{...body,sku,rule_id:current.rule_id||undefined,new_rule:creatingRule},key:window.crypto?.randomUUID?.() || `goal-${Date.now()}-${Math.random().toString(36).slice(2)}`};pendingSaves.set(sku,attempt);}
      else dialog.querySelector('#goal-rule-feedback').textContent='Conferindo o salvamento anterior. Alterações novas devem ser feitas após sua confirmação.';
      if(!attempt.job)attempt.job=await request('save',attempt.body,{headers:{'X-Idempotency-Key':attempt.key},timeoutMs:20000});
      const result=attempt.job.job_id ? await waitForAsyncOperation(attempt.job,message=>{dialog.querySelector('#goal-rule-feedback').textContent=message || 'Salvamento em andamento…';}) : attempt.job;
      pendingSaves.delete(sku);
      selectedRuleId=result.rule?.rule_id||current.rule_id||sku;creatingRule=false;
      if(result.rule){rules=rules.filter(r=>(r.rule_id||r.sku)!==selectedRuleId);rules.push(result.rule);}
      else {try{await loadDetail();}catch(_){/* The save response already confirmed success. */}}
      draw();
      dialog.querySelector('#goal-rule-feedback').textContent='Regra salva e confirmada. A automação seguirá a configuração cadastrada.';
      try{window.salesGoalsPage?.pricingChanged(sku,skuRules().some(r=>r.enabled));}catch(_){}
    }
    catch(e){
      if(e.status==='error' || (Number(e.status)>=400 && Number(e.status)<500))pendingSaves.delete(sku);
      const feedback=dialog.querySelector('#goal-rule-feedback');feedback.textContent=e.message || 'Não foi possível confirmar o salvamento.';
      if(pendingSaves.has(sku)){
        feedback.append(document.createTextNode(' O salvamento pode continuar no servidor. '));
        const retry=document.createElement('button');retry.type='button';retry.textContent='Conferir o mesmo salvamento';retry.onclick=()=>change({});feedback.append(retry);
      }
    }
    finally{busy=false;buttons.forEach(([b,disabled])=>{if(b.isConnected)b.disabled=disabled;});}
  }
  document.addEventListener('click',async e=>{
    const b=e.target.closest('[data-goal-rule]');if(!b || !admin() || busy)return;
    sku=b.dataset.goalRule;auditPage=0;auditFilter='';selectedRuleId=null;creatingRule=false;
    try{await loadDetail();draw();dialog.showModal();}
    catch(err){document.getElementById('goals-feedback').textContent=err.message;}
  });
  window.goalPricingPage={button,load,summary,setRows:rows=>{commercialRows=rows;rules=rows.flatMap(r=>r.pricing_summaries|| (r.pricing_summary?[r.pricing_summary]:[])).map(r=>({...rules.find(old=>(old.rule_id||old.sku)===(r.rule_id||r.sku)),...r}));}};
})();
