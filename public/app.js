const state = {
  data: null,
  meta: null,
  route: "dashboard",
  filter: "all",
  catalogAccount: "all",
  catalogSearch: "",
  catalogMlStatus: "all",
  catalogStock: "all",
  adsAccount: "all",
  adsProduct: "",
  adsSku: "",
  adsCode: "",
  adsStatus: "all",
  adsFlex: "all",
  stockPeriod: "today",
  stockCustomDate: "",
  alertType: "all",
  alertPeriod: "today",
  alertCustomDate: "",
  catalogPage: 1,
  adsPage: 1,
  copyPage: 1,
  copyPageSize: Number(localStorage.getItem("competidor-copy-page-size") || 20),
  copySearch: "",
  copySku: "",
  cloneSelectedIds: new Set(),
  alertsPage: 1,
  scanPage: 1,
  competitorsPage: 1,
  theme: localStorage.getItem("competidor-theme") || "dark",
  currentUser: null,
  catalogLoaded: false,
  catalogLoading: null,
};

const PAGE_SIZE = 100;
const renderTimers = {};

const pageTitles = {
  dashboard: ["Painel unificado", "Dashboard"],
  contas: ["OAuth oficial", "Contas Mercado Livre"],
  catalogo: ["Catálogo Mercado Livre", "Disputa de catálogo"],
  anuncios: ["Gestão operacional", "Anúncios"],
  copiar: ["Multiplicar anúncios", "Copiar anúncios específicos"],
  concorrentes: ["Monitoramento", "Concorrentes"],
  scan: ["Scan", "Acompanhar preços"],
  alertas: ["Alertas oficiais", "Canais e prioridades"],
  usuarios: ["SaaS e equipe", "Usuários"],
};

const labels = {
  winning: "Ganhando",
  losing: "Perdendo",
  sharing: "Compartilhando",
  paused: "Pausado",
};

const colors = {
  winning: "#0f9f6e",
  losing: "#dc2626",
  sharing: "#d97706",
  paused: "#64748b",
};

const money = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
});

function applyTheme() {
  document.documentElement.dataset.theme = state.theme;
  const icon = document.querySelector("#theme-icon");
  if (icon) icon.innerHTML = state.theme === "dark" ? moonIcon() : sunIcon();
}

function moonIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.2 15.4A8.6 8.6 0 0 1 8.6 3.8 9 9 0 1 0 20.2 15.4Z"/></svg>`;
}

function sunIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM11 1h2v4h-2V1Zm0 18h2v4h-2v-4ZM1 11h4v2H1v-2Zm18 0h4v2h-4v-2ZM4.2 2.8 7 5.6 5.6 7 2.8 4.2l1.4-1.4Zm14.2 14.2 2.8 2.8-1.4 1.4-2.8-2.8 1.4-1.4Zm2.8-12.8L18.4 7 17 5.6l2.8-2.8 1.4 1.4ZM7 18.4l-2.8 2.8-1.4-1.4L5.6 17 7 18.4Z"/></svg>`;
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !["/api/auth/me", "/api/auth/login", "/api/auth/setup-master"].includes(path)) {
      showLogin();
    }
    const error = new Error(payload.error || payload.message || `Erro ${response.status}`);
    Object.assign(error, payload);
    throw error;
  }
  return payload;
}

function showToast(message, type = "success") {
  const stack = document.querySelector("#toast-stack");
  if (!stack) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `<strong>${type === "error" ? "Ação não concluída" : "Tudo certo"}</strong><span>${escapeText(message)}</span>`;
  stack.appendChild(toast);
  window.setTimeout(() => toast.classList.add("visible"), 20);
  window.setTimeout(() => {
    toast.classList.remove("visible");
    window.setTimeout(() => toast.remove(), 220);
  }, 4200);
}

async function load() {
  const [meta, dashboard] = await Promise.all([api("/api/meta"), api("/api/dashboard")]);
  let meliConfig;
  try {
    meliConfig = await api("/api/meli/config");
  } catch (error) {
    meliConfig = {
      client_id: "",
      client_secret_set: false,
      redirect_uri: meta.meli?.redirect_uri || "",
      suggested_redirect_uri: meta.meli?.suggested_redirect_uri || `${location.origin}/api/oauth/callback`,
      issues: meta.meli?.oauth_issues || ["Configuração Mercado Livre indisponível nesta instância"],
      locked: true,
      locked_message: error.message || "Apenas o usuário master pode editar as credenciais OAuth.",
    };
  }
  state.meta = meta;
  state.meliConfig = meliConfig;
  state.data = dashboard;
  state.catalogLoaded = Boolean((dashboard.catalog || []).length);
  const apiStatus = document.querySelector("#api-status");
  const redirectUri = document.querySelector("#redirect-uri");
  if (apiStatus) apiStatus.textContent = meta.meli.client_configured ? "OAuth pronto" : "Credenciais pendentes";
  if (redirectUri) redirectUri.textContent = meta.meli.redirect_uri || "Configure MELI_REDIRECT_URI";
  document.querySelector("#api-pill").textContent = meta.meli.client_configured ? "OAuth pronto" : "OAuth pendente";
  document.querySelector("#tenant-pill").textContent = tenantLabel(meta, dashboard);
  renderCurrentUser();
  render();
}

async function loadCatalogInBackground(force = false) {
  if (!state.data || (state.catalogLoaded && !force)) return;
  if (state.catalogLoading) return state.catalogLoading;
  state.catalogLoading = api("/api/catalog")
    .then((result) => {
      state.data.catalog = result.catalog || [];
      state.data.item_logs = result.item_logs || [];
      state.data.catalog_counts = result.catalog_counts || state.data.catalog_counts || {};
      state.catalogLoaded = true;
      render();
    })
    .catch((error) => showToast(error.message || "Não foi possível carregar os anúncios.", "error"))
    .finally(() => {
      state.catalogLoading = null;
    });
  return state.catalogLoading;
}

async function checkSession() {
  const result = await api("/api/auth/me");
  if (result.setup_required) {
    showSetup();
    return;
  }
  if (!result.authenticated) {
    showLogin();
    return;
  }
  state.currentUser = result.user;
  showApp();
  await load();
}

function showLogin() {
  document.body.classList.remove("is-authenticated", "auth-loading", "needs-setup");
  document.body.classList.add("needs-auth");
}

function showSetup() {
  document.body.classList.remove("is-authenticated", "auth-loading", "needs-auth");
  document.body.classList.add("needs-setup");
}

function showApp() {
  document.body.classList.remove("needs-auth", "needs-setup", "auth-loading");
  document.body.classList.add("is-authenticated");
}

function paginate(items, page, pageSize = PAGE_SIZE) {
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(1, Number(page) || 1), pages);
  const start = (current - 1) * pageSize;
  return {
    items: items.slice(start, start + pageSize),
    current,
    pages,
    total,
    start,
    pageSize,
  };
}

function paginationHtml(key, pageInfo) {
  const pageSize = pageInfo?.pageSize || PAGE_SIZE;
  if (!pageInfo || pageInfo.total <= pageSize) {
    return pageInfo?.total ? `<div class="pagination-info">${pageInfo.total} resultado(s)</div>` : "";
  }
  const buttons = [];
  const add = (page, label = page) => {
    buttons.push(`<button class="${page === pageInfo.current ? "active" : ""}" type="button" data-page-key="${key}" data-page="${page}">${label}</button>`);
  };
  add(Math.max(1, pageInfo.current - 1), "Anterior");
  const from = Math.max(1, pageInfo.current - 2);
  const to = Math.min(pageInfo.pages, pageInfo.current + 2);
  if (from > 1) add(1);
  for (let page = from; page <= to; page += 1) add(page);
  if (to < pageInfo.pages) add(pageInfo.pages);
  add(Math.min(pageInfo.pages, pageInfo.current + 1), "Próxima");
  return `
    <div class="pagination">
      <span>${pageInfo.total} resultado(s) · página ${pageInfo.current} de ${pageInfo.pages}</span>
      <div>${buttons.join("")}</div>
    </div>
  `;
}

function renderCurrentUser() {
  const user = state.currentUser;
  if (!user) return;
  const name = document.querySelector("#current-user-name");
  const avatar = document.querySelector("#current-user-avatar");
  if (name) name.textContent = user.name || user.email;
  if (avatar) avatar.textContent = (user.name || user.email || "U").slice(0, 1).toUpperCase();
  document.body.dataset.role = user.role || "viewer";
  renderPermissionUi();
}

function canManageOAuth() {
  return state.currentUser?.role === "master";
}

function canManageUsers() {
  return ["master", "admin"].includes(state.currentUser?.role);
}

function renderPermissionUi() {
  document.querySelectorAll("[data-master-only]").forEach((node) => {
    node.hidden = !canManageOAuth();
  });
  document.querySelectorAll("[data-non-master-only]").forEach((node) => {
    node.hidden = canManageOAuth();
  });
  document.querySelectorAll("[data-users-admin-only]").forEach((node) => {
    node.hidden = !canManageUsers();
  });
  document.querySelectorAll('#users-form select[name="role"] option[value="master"]').forEach((node) => {
    node.hidden = !canManageOAuth();
    node.disabled = !canManageOAuth();
  });
  const createRole = document.querySelector('#users-form select[name="role"]');
  if (createRole && !canManageOAuth() && createRole.value === "master") {
    createRole.value = "admin";
  }
}

function tenantLabel(meta, data) {
  const official = data.accounts.filter((account) => account.official).length;
  const total = data.accounts.length;
  return `${official}/${total} contas conectadas`;
}

function setRoute() {
  const route = (location.hash.replace("#/", "") || "dashboard").split("?")[0];
  state.route = pageTitles[route] ? route : "dashboard";
  document.querySelectorAll(".page").forEach((page) => page.classList.remove("active"));
  document.querySelector(`#page-${state.route}`).classList.add("active");
  document.querySelectorAll("nav a").forEach((link) => link.classList.toggle("active", link.dataset.route === state.route));
  document.querySelector("#page-eyebrow").textContent = pageTitles[state.route][0];
  document.querySelector("#page-title").textContent = pageTitles[state.route][1];
}

function routeParams() {
  const query = location.hash.split("?")[1] || "";
  return new URLSearchParams(query);
}

function render() {
  setRoute();
  renderSummary();
  renderRoute();
  renderPermissionUi();
}

function renderRoute() {
  if (!state.data) return;
  if (["catalogo", "anuncios", "copiar"].includes(state.route) && !state.catalogLoaded) {
    loadCatalogInBackground();
  }
  const routeRenderers = {
    dashboard: renderDashboard,
    contas: () => {
      renderAccounts();
      renderMeliConfig();
    },
    catalogo: renderCatalog,
    anuncios: renderAds,
    copiar: renderClone,
    concorrentes: renderCompetitors,
    scan: renderScan,
    alertas: () => {
      renderAlerts();
      renderNotificationForm();
    },
    usuarios: renderUsers,
  };
  (routeRenderers[state.route] || renderDashboard)();
}

function scheduleRender(key, callback, delay = 120) {
  window.clearTimeout(renderTimers[key]);
  renderTimers[key] = window.setTimeout(callback, delay);
}

function connectedAccounts() {
  return state.data.accounts.filter((account) => account.status !== "needs_auth" && account.status !== "oauth_error");
}

function renderSummary() {
  const { catalog, alerts, accounts } = state.data;
  const counts = state.data.catalog_counts || {};
  document.querySelector("#winning-count").textContent = counts.winning ?? catalog.filter((item) => item.status === "winning").length;
  document.querySelector("#losing-count").textContent = counts.losing ?? catalog.filter((item) => item.status === "losing").length;
  document.querySelector("#critical-count").textContent = alerts.filter((alert) => !alert.read && alert.severity === "critical").length;
  document.querySelector("#official-count").textContent = accounts.filter((account) => account.official).length;
  const officialLabel = document.querySelector("#official-accounts-label");
  if (officialLabel) officialLabel.textContent = `${accounts.filter((account) => account.official).length} oficiais`;
}

function renderDashboard() {
  const ops = state.data.operations || {};
  const stockRows = filterByStockPeriod(ops.attention_stock || []);
  document.querySelector("#dashboard-revenue").innerHTML = `
    <article class="revenue-total">
      <span>Faturamento real mensal</span>
      <strong>${money.format(ops.total_monthly_revenue || 0)}</strong>
      <small>Pedidos oficiais sincronizados no Mercado Livre</small>
    </article>
    ${(ops.revenue || []).map((item) => `
      <article class="revenue-account">
        <strong>${item.account}</strong>
        <span>${money.format(item.monthly_revenue || 0)}</span>
        <small>${item.orders_count || 0} pedidos · ${item.period || ""}${item.updated_at ? ` · ${formatDateBR(item.updated_at)}` : ""}</small>
        <small>${item.sync_status || item.source || ""}</small>
      </article>
    `).join("")}
  `;

  document.querySelector("#dashboard-stock").innerHTML = opsRows(stockRows, "Sem produtos sem estoque neste período.", "danger");
  document.querySelector("#dashboard-catalog-loss").innerHTML = opsRows(ops.attention_catalog || [], "Sem perdas de catálogo detectadas.", "danger");
  document.querySelector("#dashboard-claims").innerHTML = (ops.claims || []).map((item) => `
    <details class="ops-item claim-card">
      <summary>
        <strong>${item.account}</strong>
        <span>${Number(item.open || 0)} abertas · ${Number(item.mediations || 0)} em mediação</span>
      </summary>
      <div class="chip-row">${copyChip("Conta", item.account)}${copyChip("Abertas", item.open)}${copyChip("Mediação", item.mediations)}${copyChip("Data", formatDateBR(item.updated_at))}</div>
      <div class="claim-detail-list">
        ${(item.details || []).length ? item.details.map((claim) => `
          <article class="claim-detail">
            <strong>${claim.subject || claim.title || "Reclamação"}</strong>
            <p>${claim.description || claim.reason || "Detalhe ainda não sincronizado pela API."}</p>
            <div class="chip-row">${copyChip("ID", claim.id || "-")}${copyChip("Status", claim.status || "-")}${copyChip("Data", formatDateBR(claim.created_at || item.updated_at))}</div>
          </article>
        `).join("") : `<div class="notice">${escapeText(item.sync_status || "Nenhuma reclamação detalhada sincronizada para esta conta.")}</div>`}
      </div>
    </details>
  `).join("") || `<div class="notice">Nenhuma reclamação ativa sincronizada.</div>`;
  document.querySelector("#dashboard-shipments").innerHTML = (ops.pending_shipments || []).map((item) => `
    <article class="ops-item">
      <strong>${item.account}</strong>
      <p>Pedido ${item.order_id} · ${item.buyer}</p>
      <div class="chip-row">${copyChip("Conta", item.account)}${copyChip("Limite", item.deadline)}${copyChip("Falta", item.time_left)}</div>
      ${item.sync_status ? `<small>${escapeText(item.sync_status)}</small>` : ""}
    </article>
  `).join("") || `<div class="notice">Nenhum envio pendente sincronizado.</div>`;

  document.querySelector("#dashboard-sales").innerHTML = (state.data.recent_sales || []).slice(0, 12).map((sale) => `
    <article class="sale-item">
      ${sale.thumbnail ? `<img class="sale-thumb" src="${sale.thumbnail}" alt="${escapeAttr(sale.product || sale.item_id)}" loading="lazy" />` : `<span class="sale-thumb sale-thumb-empty"></span>`}
      <div>
        <strong>${escapeText(sale.product || "Produto vendido")}</strong>
        <div class="chip-row">
          ${copyChip("Conta", sale.account || "-")}
          ${copyChip("MLB", sale.item_id || "-")}
          ${copyChip("SKU", sale.sku || "-")}
          ${copyChip("Qtd.", sale.quantity || 1)}
          ${copyChip("Canal", sale.channel || "Mercado Livre")}
          ${copyChip("Data", formatDateBR(sale.date || "-"))}
        </div>
      </div>
      <div class="sale-total">
        <span>Valor vendido</span>
        <strong>${money.format(sale.total || 0)}</strong>
        <small>Pedido ${sale.order_id || "-"}</small>
      </div>
    </article>
  `).join("") || `<div class="notice">Nenhuma venda recente sincronizada. Sincronize uma conta oficial com permissão de vendas/pedidos para preencher este bloco.</div>`;

  document.querySelector("#dashboard-metrics").innerHTML = state.data.metrics
    .map(
      (metric) => `
        <article class="metric-item">
          <div class="metric-header">
            <strong>${metric.account}</strong>
            <small>${metric.period}</small>
          </div>
          <div class="metric-bars">
            ${metricLine("Reclamações", metric.claims, 8, true)}
            ${metricLine("Envios em atraso", metric.late_shipments, 10, true)}
            ${metricLine("Agências ML", metric.agency_score, 100, false)}
            ${metricLine("Flex", metric.flex_score, 100, false)}
          </div>
        </article>
      `
    )
    .join("");
}

function filterByStockPeriod(rows) {
  return rows.filter((item) => isDateInPeriod(item.occurred_at, state.stockPeriod, state.stockCustomDate));
}

function isDateInPeriod(value, period, customDate) {
  const today = new Date();
  const target = customDate ? new Date(`${customDate}T00:00:00`) : today;
  const date = new Date((value || "").replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return period === "week";
  if (period === "today") return localDateKey(date) === localDateKey(today);
  if (period === "yesterday") {
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    return localDateKey(date) === localDateKey(yesterday);
  }
  if (period === "custom") return localDateKey(date) === localDateKey(target);
  const weekAgo = new Date(today);
  weekAgo.setDate(today.getDate() - 7);
  return date >= weekAgo && date <= today;
}

function localDateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function opsRows(rows, empty, tone = "") {
  return rows
    .map((item) => `
      <article class="ops-item ${tone}">
        <div class="ops-media-row">
          ${item.thumbnail ? `<img src="${item.thumbnail}" alt="${escapeAttr(item.title || item.id)}" loading="lazy" />` : ""}
          <div>
            <strong>${item.title}</strong>
            <div class="chip-row">
              ${copyChip("Loja", item.account)}
              ${copyChip("MLB", item.id)}
              ${copyChip("SKU", item.sku || "-")}
              ${copyChip("Data", formatDateBR(item.occurred_at))}
            </div>
          </div>
        </div>
      </article>
    `)
    .join("") || `<div class="notice">${empty}</div>`;
}

function renderCatalog() {
  const list = document.querySelector("#catalog-list");
  if (!state.catalogLoaded) {
    list.innerHTML = `<div class="notice">Carregando anúncios e disputa de catálogo em segundo plano...</div>`;
    loadCatalogInBackground();
    return;
  }
  renderFilterOptions();
  const term = state.catalogSearch.toLowerCase();
  const filtered = state.data.catalog.filter((item) => {
    const text = `${item.title} ${item.sku} ${item.id}`.toLowerCase();
    return isCatalogListing(item)
      && (state.filter === "all" || item.status === state.filter)
      && (state.catalogAccount === "all" || item.account === state.catalogAccount)
      && (state.catalogMlStatus === "all" || item.meli_status === state.catalogMlStatus)
      && (state.catalogStock === "all" || (state.catalogStock === "zero" ? Number(item.stock) === 0 : Number(item.stock) > 0))
      && (!term || text.includes(term));
  });
  const pageInfo = paginate(filtered, state.catalogPage);
  state.catalogPage = pageInfo.current;
  list.innerHTML = filtered.length ? paginationHtml("catalogPage", pageInfo) + pageInfo.items
    .map(
      (item) => `
        <article class="catalog-item ${item.status}">
          <a class="product-media" href="${catalogPublicUrl(item)}" target="_blank" rel="noreferrer" aria-label="Abrir catálogo ${item.catalog_product_id || item.id}">
            ${item.thumbnail ? `<img src="${item.thumbnail}" alt="${item.title}" loading="lazy" />` : `<span>${(item.title || item.id).slice(0, 2).toUpperCase()}</span>`}
          </a>
          <div>
            <div class="catalog-title">
              <span class="status-dot status-${item.status}"></span>
              <strong>${item.title}</strong>
              <span class="badge ${item.status}">${labels[item.status]}</span>
            </div>
            <div class="item-facts">
              ${fact("Loja", item.account)}
              ${fact("SKU", item.sku)}
              ${fact("Anúncio ML", item.id)}
              ${fact("Tipo", listingTypeLabel(item.listing_type_id))}
              ${fact("Catálogo", item.catalog_product_id)}
              ${fact("Estoque", item.stock)}
              ${fact("Preço", money.format(item.price))}
              ${item.meli_status ? fact("Status ML", item.meli_status === "paused" ? "Pausado" : item.meli_status) : ""}
              ${fact("Vencedor", catalogWinnerName(item))}
              ${fact("Preço vencedor", catalogWinnerPrice(item))}
              ${catalogWinnerSourceValue(item) ? fact("Fonte vencedor", catalogWinnerSourceValue(item)) : ""}
              ${fact("Preço p/ ganhar", item.price_to_win ? money.format(item.price_to_win) : "Sem sugestão ML")}
            </div>
            <div class="progress"><span style="width:${item.share}%; background:${colors[item.status]}; color:${colors[item.status]}"></span></div>
            <p class="catalog-action">${item.action}</p>
            ${item.competition_reason ? `<p class="catalog-action subtle-action">Competição: ${competitionReasonLabel(item.competition_reason)}</p>` : ""}
            ${item.official_source ? `
              <div class="price-tools">
                <label>Novo preço
                  <span class="money-field"><input type="number" min="0" step="0.01" value="${item.price || 0}" data-catalog-price-input="${item.id}" /></span>
                </label>
                <button class="mini-button" data-save-catalog-price="${item.id}">Salvar preço</button>
                ${item.price_to_win
                  ? `<button class="mini-button win-button" data-win-catalog="${item.id}">Ganhar catálogo por ${money.format(item.price_to_win)}</button>`
                  : `<button class="mini-button" disabled title="O Mercado Livre não retornou preço sugerido para este anúncio.">Sem sugestão ML</button>`}
              </div>
            ` : ""}
          </div>
          <div class="score">
            <small>Participação estimada</small>
            <strong>${item.share}%</strong>
            <small>Vencedor: ${catalogWinnerName(item)}</small>
          </div>
        </article>
      `
    )
    .join("") + paginationHtml("catalogPage", pageInfo) : `<div class="notice">Nenhum anúncio de catálogo encontrado.</div>`;
}

function catalogWinnerName(item) {
  if (item.winner_confirmed && item.winner_name) return item.winner_name;
  return item.winner_name && !/não confirmado|aguardando/i.test(item.winner_name) ? item.winner_name : "Não confirmado";
}

function catalogPublicUrl(item) {
  return item.catalog_product_id && item.catalog_product_id !== "-"
    ? `https://www.mercadolivre.com.br/p/${item.catalog_product_id}`
    : item.permalink || "#";
}

function catalogWinnerPrice(item) {
  const value = item.winner_price;
  return value ? money.format(value) : "-";
}

function catalogWinnerSourceValue(item) {
  if (item.winner_confirmed && item.winner_source) return catalogWinnerSource(item.winner_source);
  return "";
}

function catalogWinnerSource(source) {
  const sources = {
    price_to_win: "API oficial",
    products_items_winner_marker: "API catálogo",
    public_product_page: "Página pública ML",
    catalog_reference: "Catálogo ML",
    catalog_lowest_active_offer: "Menor oferta ativa ML",
    public_purchase_options: "Página pública ML",
  };
  return sources[source] || "Mercado Livre";
}

function renderFilterOptions() {
  const accounts = ["all", ...new Set(state.data.catalog.map((item) => item.account).filter(Boolean))];
  const mlStatuses = ["all", ...new Set(["active", "paused", "under_review", ...state.data.catalog.map((item) => item.meli_status).filter(Boolean)])];
  setOptions("#catalog-account-filter", accounts, state.catalogAccount, "Todas");
  setOptions("#catalog-ml-status-filter", mlStatuses, state.catalogMlStatus, "Todos", statusLabel);
  setOptions("#ads-account-filter", accounts, state.adsAccount, "Todas");
  setOptions("#ads-status-filter", mlStatuses, state.adsStatus, "Todos", statusLabel);
}

function setOptions(selector, values, current, allLabel, formatter = (value) => value) {
  const el = document.querySelector(selector);
  if (!el) return;
  const old = el.value || current;
  const key = values.join("|");
  if (el.dataset.optionsKey !== key) {
    el.innerHTML = values.map((value) => `<option value="${value}">${value === "all" ? allLabel : formatter(value)}</option>`).join("");
    el.dataset.optionsKey = key;
  }
  el.value = values.includes(old) ? old : current;
}

function renderAds() {
  const list = document.querySelector("#ads-list");
  if (!state.catalogLoaded) {
    list.innerHTML = `<div class="notice">Carregando anúncios em segundo plano...</div>`;
    loadCatalogInBackground();
    return;
  }
  if (!list || !state.data) return;
  renderFilterOptions();
  const productTerm = state.adsProduct.toLowerCase();
  const skuTerm = state.adsSku.toLowerCase();
  const codeTerm = state.adsCode.toLowerCase();
  const filtered = state.data.catalog.filter((item) => {
    const title = `${item.title || ""}`.toLowerCase();
    const sku = `${item.sku || ""}`.toLowerCase();
    const code = `${item.id || ""}`.toLowerCase();
    return (state.adsAccount === "all" || item.account === state.adsAccount)
      && (state.adsStatus === "all" || item.meli_status === state.adsStatus)
      && (state.adsFlex === "all" || (state.adsFlex === "active" ? item.shipping_logistic_type === "self_service" : item.shipping_logistic_type !== "self_service"))
      && (!productTerm || title.includes(productTerm))
      && (!skuTerm || sku.includes(skuTerm))
      && (!codeTerm || code.includes(codeTerm));
  });
  const pageInfo = paginate(filtered, state.adsPage);
  state.adsPage = pageInfo.current;
  list.innerHTML = filtered.length ? paginationHtml("adsPage", pageInfo) + pageInfo.items.map((item) => `
    <article class="ad-item">
      <a class="product-media" href="${item.permalink || "#"}" target="_blank" rel="noreferrer">
        ${item.thumbnail ? `<img src="${item.thumbnail}" alt="${item.title}" loading="lazy" />` : `<span>${(item.title || item.id).slice(0, 2).toUpperCase()}</span>`}
      </a>
      <div class="ad-main">
        <div class="ad-title-row">
          <strong>${item.title}</strong>
          <span class="badge ${item.meli_status === "paused" ? "paused" : "winning"}">${statusLabel(item.meli_status || item.status)}</span>
        </div>
        <div class="ad-facts">
          ${fact("Conta", item.account)}
          ${fact("Código do anúncio", item.id)}
          ${fact("SKU", item.sku || "-")}
          ${fact("Tipo", listingTypeLabel(item.listing_type_id))}
          ${flexStatusBadge(item.shipping_logistic_type)}
          ${fact("Status do anúncio", statusLabel(item.meli_status || item.status))}
        </div>
        <div class="inline-edit">
          <label>Preço <span class="money-field"><input type="number" min="0" step="0.01" value="${item.price || 0}" data-price-input="${item.id}" /></span></label>
          <label>Estoque <input type="number" min="0" step="1" value="${item.stock || 0}" data-stock-input="${item.id}" /></label>
          <label>Peso <input type="text" value="${escapeAttr(item.package_weight || "")}" placeholder="Ex: 500 g" data-weight-input="${item.id}" /></label>
          <label>Altura <input type="text" value="${escapeAttr(item.package_height || "")}" placeholder="Ex: 10 cm" data-height-input="${item.id}" /></label>
          <label>Largura <input type="text" value="${escapeAttr(item.package_width || "")}" placeholder="Ex: 20 cm" data-width-input="${item.id}" /></label>
          <label>Comprimento <input type="text" value="${escapeAttr(item.package_length || "")}" placeholder="Ex: 30 cm" data-length-input="${item.id}" /></label>
          <div class="ad-actions">
            <button class="mini-button" data-save-ad="${item.id}">Salvar</button>
            ${item.meli_status === "paused" || item.status === "paused"
              ? `<button class="mini-button success-button" data-activate-ad="${item.id}">Ativar</button>`
              : `<button class="mini-button danger-button" data-pause-ad="${item.id}">Pausar</button>`}
            ${item.official_source && item.shipping_logistic_type === "self_service"
              ? `<button class="mini-button warning-button" data-remove-flex="${item.id}">Remover Flex</button>`
              : item.official_source
                ? `<button class="mini-button success-button" data-activate-flex="${item.id}">Ativar Flex</button>`
                : ""}
          </div>
        </div>
        ${renderItemLog(item)}
      </div>
    </article>
  `).join("") + paginationHtml("adsPage", pageInfo) : `<div class="notice">Nenhum anúncio encontrado.</div>`;
}

function isCatalogListing(item) {
  return item.catalog_listing === true;
}

function renderItemLog(item) {
  const logs = (state.data.item_logs || []).filter((log) => log.item_id === item.id).slice(0, 6);
  return `
    <details class="item-log">
      <summary>Log do anúncio</summary>
      <div class="item-log-list">
        ${logs.length ? logs.map((log) => `
          <article>
            <strong>${log.action}</strong>
            <span>${formatDateBR(log.created_at)} · ${log.user || "Sistema"}</span>
            <p>${formatLogChanges(log)}</p>
          </article>
        `).join("") : `<div class="notice">Nenhuma alteração registrada ainda. Quando houver venda sincronizada, o log poderá registrar a baixa de estoque com o ID da venda.</div>`}
      </div>
    </details>
  `;
}

function formatLogChanges(log) {
  if (log.sale_id) return `Estoque reduzido por venda ${log.sale_id}.`;
  const changes = log.changes || {};
  const parts = [];
  if (changes.price) parts.push(`Preço: ${money.format(Number(changes.price.from || 0))} -> ${money.format(Number(changes.price.to || 0))}`);
  if (changes.stock) parts.push(`Estoque: ${changes.stock.from} -> ${changes.stock.to}`);
  if (changes.status) parts.push(`Status: ${changes.status.from || "-"} -> ${changes.status.to || "-"}`);
  if (changes.title) parts.push("Título alterado");
  return parts.join(" · ") || "Alteração registrada.";
}

function renderAlerts() {
  const list = document.querySelector("#alerts-list");
  const alerts = filterAlerts(state.data.alerts || []);
  const pageInfo = paginate(alerts, state.alertsPage);
  state.alertsPage = pageInfo.current;
  list.innerHTML = alerts.length ? paginationHtml("alertsPage", pageInfo) + pageInfo.items
    .map(
      (alert) => `
        <article class="alert-item ${alert.severity} ${alert.read ? "read" : ""}">
          <div class="alert-top">
            <strong>${alert.title}</strong>
            <button class="mini-button" data-alert="${alert.id}">${alert.read ? "Lido" : "Marcar lido"}</button>
          </div>
          <p>${alert.message}</p>
          <div class="chip-row alert-chips">
            ${copyChip("Conta", alert.account || "-")}
            ${alert.item_id ? copyChip("MLB", alert.item_id) : ""}
            ${alert.sku ? copyChip("SKU", alert.sku) : ""}
            ${copyChip("Data", formatDateBR(alert.created_at))}
          </div>
          <small>${(alert.channel || []).join(", ")}</small>
        </article>
      `
    )
    .join("") + paginationHtml("alertsPage", pageInfo) : `<div class="notice">Nenhum alerta encontrado neste filtro.</div>`;
}

function filterAlerts(alerts) {
  return alerts.filter((alert) => {
    const typeOk = state.alertType === "all" || alert.type === state.alertType;
    const periodOk = isDateInPeriod(alert.created_at, state.alertPeriod, state.alertCustomDate);
    return typeOk && periodOk;
  });
}

function renderAccounts() {
  const list = document.querySelector("#accounts-list");
  const feedback = document.querySelector("#account-feedback");
  const issues = state.meta?.meli?.oauth_issues || [];
  const oauthStatus = routeParams().get("oauth");
  const oauthEditable = canManageOAuth();
  const diagnostics = oauthEditable
    ? (issues.length
      ? `<div class="notice danger-notice"><strong>OAuth ainda não está pronto</strong><p>${issues.join(", ")}. Salve as credenciais abaixo. O Redirect URI precisa ser exatamente igual ao cadastrado no Mercado Livre.</p></div>`
      : `<div class="notice success-notice"><strong>OAuth configurado</strong><p>O botão de conexão usa o Login oficial. Se o Mercado Livre não mostrar a tela de login, ele está aproveitando a sessão já aberta no navegador; saia da conta atual no Mercado Livre ou abra uma janela anônima com a outra loja.</p><code>${state.meta.meli.redirect_uri}</code></div>`)
    : "";
  if (feedback && !feedback.dataset.locked) {
    if (oauthStatus === "updated") {
      feedback.innerHTML = `<div class="notice"><strong>Mesma conta reconectada</strong><p>O Mercado Livre aproveitou a sessão já aberta neste navegador e renovou a autorização da conta atual. Para conectar outra loja, saia da conta atual no Mercado Livre ou use uma janela anônima logada na outra conta.</p></div>`;
    } else if (oauthStatus === "connected") {
      feedback.innerHTML = `<div class="notice success-notice"><strong>Nova conta conectada</strong><p>A conta foi adicionada ao CompeTIDOR por OAuth oficial.</p></div>`;
    } else if (oauthStatus === "error") {
      feedback.innerHTML = `<div class="notice danger-notice"><strong>OAuth não concluiu</strong><p>O Mercado Livre não retornou uma autorização válida para esta tentativa.</p></div>`;
    } else {
      feedback.innerHTML = "";
    }
  }
  list.innerHTML = diagnostics + state.data.accounts
    .map(
      (account) => `
        <article class="account-item">
          <span class="account-color" style="background:${account.color}"></span>
          <div class="account-main">
            <strong>${account.nickname}</strong>
            <div class="meta-row">
              <span>Seller ${account.seller_id}</span>
              <span>${account.site_id}</span>
              <span>${formatDateBR(account.last_sync)}</span>
              ${account.permalink ? `<a href="${account.permalink}" target="_blank" rel="noreferrer">Perfil ML</a>` : ""}
            </div>
            ${account.error ? `<p class="error-text">${account.error}</p>` : ""}
          </div>
          <div class="account-actions">
            <span class="badge ${account.official ? "winning" : account.status === "oauth_error" ? "losing" : "sharing"}">
              ${account.official ? "Oficial OAuth" : account.status === "oauth_error" ? "Erro OAuth" : "Demo"}
            </span>
            ${account.official ? `<a class="mini-button button-link" href="/api/oauth/start?switch_account=1">Reautenticar</a>` : ""}
            ${account.official ? `<button class="mini-button" data-sync-account="${account.id}">Sincronizar anúncios</button>` : ""}
            ${account.official ? `<button class="mini-button danger-button" data-unlink-account="${account.id}" data-account-name="${account.nickname}">Desvincular</button>` : ""}
            ${account.sync_status ? `<small>${account.sync_status}</small>` : ""}
          </div>
        </article>
      `
    )
    .join("");
}

function metricLine(label, value, max, inverse) {
  const percent = Math.min(100, Math.round((value / max) * 100));
  const color = inverse
    ? percent > 60
      ? "#dc2626"
      : percent > 35
        ? "#d97706"
        : "#0f9f6e"
    : percent > 88
      ? "#0f9f6e"
      : percent > 75
        ? "#d97706"
        : "#dc2626";
  return `
    <div class="metric-line">
      <span>${label}</span>
      <div class="progress"><span style="width:${percent}%; background:${color}; color:${color}"></span></div>
      <strong>${value}%</strong>
    </div>
  `;
}

function renderCompetitors() {
  const list = document.querySelector("#competitors-list");
  const competitors = state.data.competitors || [];
  const pageInfo = paginate(competitors, state.competitorsPage);
  state.competitorsPage = pageInfo.current;
  list.innerHTML = competitors.length ? paginationHtml("competitorsPage", pageInfo) + pageInfo.items
    .map(
      (competitor) => `
        <article class="competitor-item">
          <div>
            <strong>${competitor.name}</strong>
            <div class="meta-row">
              <span>${competitor.items_total ?? competitor.watched_products ?? 0} anúncios</span>
              <span>${competitor.seller_id ? `Seller ${competitor.seller_id}` : `${competitor.price_moves || 0} mudanças de preço`}</span>
              ${competitor.analysis_limit ? `<span>${competitor.items_loaded || 0}/${competitor.analysis_limit} analisados</span>` : ""}
              ${competitor.source ? `<span>${competitor.source === "sites_search_public" ? "Busca pública" : "OAuth"}</span>` : ""}
              ${competitor.reputation ? `<span>Reputação ${competitor.reputation}</span>` : ""}
              ${competitor.updated_at ? `<span>${formatDateBR(competitor.updated_at)}</span>` : ""}
            </div>
            ${competitor.sync_error ? `<div class="notice danger-notice competitor-error">${escapeText(competitor.sync_error)}</div>` : ""}
            ${competitor.items ? `
              <div class="item-facts competitor-facts">
                ${fact("Menor preço", competitor.price_min ? money.format(competitor.price_min) : "-")}
                ${fact("Maior preço", competitor.price_max ? money.format(competitor.price_max) : "-")}
                ${fact("Preço médio", competitor.price_avg ? money.format(competitor.price_avg) : "-")}
                ${fact("Leitura pública", competitor.estimated_revenue ? money.format(competitor.estimated_revenue) : "-")}
              </div>
              <details class="competitor-products">
                <summary>Ver anúncios analisados</summary>
                <div class="competitor-product-list">
                  ${competitor.items.slice(0, 12).map((item) => `
                    <a href="${item.permalink || "#"}" target="_blank" rel="noreferrer">
                      <span>${escapeText(item.title)}</span>
                      <strong>${money.format(item.price || 0)}</strong>
                      <small>${item.sold_quantity || 0} vendas públicas · ${listingTypeLabel(item.listing_type_id)}</small>
                    </a>
                  `).join("")}
                </div>
              </details>
              <p class="catalog-action subtle-action">${competitor.note || ""}</p>
            ` : ""}
          </div>
          <span class="badge sharing">${competitor.catalog_wins ? `${competitor.catalog_wins} vitórias catálogo` : `${competitor.items_loaded || 0} carregados`}</span>
        </article>
      `
    )
    .join("") + paginationHtml("competitorsPage", pageInfo) : `<div class="notice">Nenhum concorrente acompanhado ainda.</div>`;
}

function renderScan() {
  const list = document.querySelector("#scan-list");
  if (!list || !state.data) return;
  const scans = state.data.scan_items || [];
  const pageInfo = paginate(scans, state.scanPage);
  state.scanPage = pageInfo.current;
  list.innerHTML = scans.length ? paginationHtml("scanPage", pageInfo) + pageInfo.items.map((scan) => {
    const history = scan.history || [];
    const minimum = Number(scan.minimum_price || 0);
    const belowOffers = scan.below_minimum_offers || [];
    const belowMinimum = belowOffers.length || (minimum && Number(scan.last_price || 0) <= minimum);
    return `
      <article class="scan-item ${belowMinimum ? "danger" : ""}">
        <div class="scan-head">
          <a class="product-media scan-media" href="${scan.last_permalink || scan.url || "#"}" target="_blank" rel="noreferrer">
            ${scan.last_thumbnail ? `<img src="${scan.last_thumbnail}" alt="${escapeAttr(scan.name)}" loading="lazy" />` : `<span>${(scan.name || scan.item_id || "SC").slice(0, 2).toUpperCase()}</span>`}
          </a>
          <div>
            <strong>${escapeText(scan.name)}</strong>
            <div class="item-facts">
              ${fact("Anúncio ML", scan.item_id || "-")}
              ${fact("Vendedor atual", scan.last_seller_name || "-")}
              ${fact("Preço atual", scan.last_price ? money.format(scan.last_price) : "Sem scan")}
              ${fact("Tipo de scan", scan.target_type === "catalog_product" ? `Catálogo (${scan.offer_count || 0} ofertas ativas)` : "Anúncio")}
              ${fact("Último scan", formatDateBR(scan.last_scan_at || "-"))}
              ${fact("Automático", scan.auto_scan_error ? "Com erro" : "Ativo")}
              ${fact("Último auto", formatDateBR(scan.last_auto_scan_at || "-"))}
            </div>
            <form class="scan-min-form" data-scan-update="${scan.id}">
              <label>Preço mínimo para alerta <span class="money-field"><input name="minimum_price" type="number" min="0" step="0.01" value="${minimum || 0}" /></span></label>
              <button class="mini-button success-button" type="submit">Salvar mínimo</button>
            </form>
            ${scan.auto_scan_error ? `<p class="catalog-action subtle-action">Último erro automático: ${escapeText(scan.auto_scan_error)}</p>` : ""}
          </div>
          <button class="mini-button scan-run-button" data-run-scan="${scan.id}">Rodar scan</button>
        </div>
        <div class="scan-history">
          ${belowOffers.length ? `
            <div class="scan-under-list">
              <strong>Ofertas abaixo do mínimo</strong>
              ${belowOffers.map((offer) => `
                <a href="${offer.permalink || "#"}" target="_blank" rel="noreferrer" class="scan-under-row">
                  <span>${escapeText(offer.seller_name || "-")}</span>
                  <small>${offer.item_id || "-"}</small>
                  <strong>${money.format(offer.price || 0)}</strong>
                </a>
              `).join("")}
            </div>
          ` : ""}
          ${history.length ? history.slice(0, 8).map((entry) => `
            <div class="scan-history-row ${Number(entry.price || 0) <= minimum && minimum ? "under-minimum" : ""}">
              <span>${formatDateBR(entry.created_at)}</span>
              <strong>${money.format(entry.price || 0)}</strong>
              <span>${escapeText(entry.seller_name || "-")}</span>
              <small>${entry.changed ? "Preço alterado" : "Primeiro scan"}</small>
            </div>
          `).join("") : `<div class="notice">Nenhum scan rodado ainda para este produto.</div>`}
        </div>
      </article>
    `;
  }).join("") + paginationHtml("scanPage", pageInfo) : `<div class="notice">Nenhum produto em Scan ainda. Cadastre um produto padrão e cole o link do anúncio para começar.</div>`;
}

function renderClone() {
  const source = document.querySelector('select[name="source"]');
  const target = document.querySelector('select[name="target"]');
  const currentSource = source.value;
  const currentTarget = target.value;
  const hadTarget = Boolean(currentTarget);
  const options = connectedAccounts()
    .map((account) => `<option value="${account.nickname}">${account.nickname}</option>`)
    .join("");
  source.innerHTML = options;
  target.innerHTML = options;
  if ([...source.options].some((option) => option.value === currentSource)) source.value = currentSource;
  if ([...target.options].some((option) => option.value === currentTarget)) target.value = currentTarget;
  if (!hadTarget && target.options.length) target.selectedIndex = 0;
  const copyProduct = document.querySelector("#copy-product-filter");
  const copySku = document.querySelector("#copy-sku-filter");
  const copyPageSize = document.querySelector("#copy-page-size");
  if (copyProduct && copyProduct.value !== state.copySearch) copyProduct.value = state.copySearch;
  if (copySku && copySku.value !== state.copySku) copySku.value = state.copySku;
  if (copyPageSize && Number(copyPageSize.value) !== state.copyPageSize) copyPageSize.value = String(state.copyPageSize);
  renderCopyItems();

  document.querySelector("#clone-jobs").innerHTML = state.data.clone_jobs
    .map(
      (job) => `
        <article class="clone-job" data-clone-job-card="${job.id}">
          <div>
            <strong>${job.source} -> ${job.target}</strong>
            <div class="meta-row"><span>${job.items} anúncios</span><span>${(job.item_ids || []).join(", ")}</span></div>
            <p>${job.note}</p>
            ${job.created_details?.length ? cloneCreatedHtml(job.created_details) : ""}
            ${job.errors?.length ? cloneErrorsHtml(job.errors) : ""}
            ${["preview_ready", "review_required", "partial_error", "error"].includes(job.status) ? `<button class="mini-button" data-execute-clone="${job.id}">${job.status === "review_required" ? "Copiar com ajustes" : "Copiar agora"}</button>` : ""}
          </div>
          <span class="badge winning">${job.status}</span>
        </article>
      `
    )
    .join("");
}

function cloneCreatedHtml(items) {
  return `
    <div class="clone-created-grid">
      ${items.map((item) => `
        <a class="copy-chip clone-created-chip" href="${item.permalink || "#"}" target="_blank" rel="noreferrer">
          <small>Criado ${escapeText(item.status || "-")}</small>
          <strong>${escapeText(item.item_id || "-")}</strong>
          <span>${escapeText(item.sku || "")}</span>
          ${item.verification_warning ? `<em>${escapeText(item.verification_warning)}</em>` : ""}
        </a>
      `).join("")}
    </div>
  `;
}

function cloneErrorsHtml(errors) {
  return `
    <div class="notice danger-notice">
      ${errors.map((row) => `
        <div class="clone-error-block">
          <strong>${escapeText(row.item_id)}</strong>
          <p>${escapeText(row.error)}</p>
          ${row.pending_fields?.length ? `
            <div class="clone-pending-grid">
              ${row.pending_fields.map((field) => `
                <label>
                  ${escapeText(field.label || field.id)}
                  <input
                    data-clone-answer-item="${escapeAttr(row.item_id)}"
                    data-clone-answer-field="${escapeAttr(field.id)}"
                    placeholder="${escapeAttr(field.message || "Informe o valor exigido pelo Mercado Livre")}"
                  />
                </label>
              `).join("")}
            </div>
          ` : ""}
        </div>
      `).join("")}
    </div>
  `;
}

function renderUsers() {
  const list = document.querySelector("#users-list");
  if (!list || !state.data) return;
  const roleLabels = {
    master: "Master",
    admin: "Administrador",
    manager: "Gestor",
    operator: "Operador",
    viewer: "Leitura",
  };
  const users = state.data.users || [];
  const editableRoles = canManageOAuth()
    ? ["master", "admin", "manager", "operator", "viewer"]
    : ["admin", "manager", "operator", "viewer"];
  list.innerHTML = users.map((user) => `
    <details class="user-card">
      <summary>
        <span class="user-avatar">${(user.name || user.email || "U").slice(0, 1).toUpperCase()}</span>
        <span>
          <strong>${user.name}</strong>
          <span class="meta-row"><span>${user.email}</span><span>${roleLabels[user.role] || user.role}</span><span>${user.status || "ativo"}</span></span>
        </span>
        <span class="edit-user-button">Editar</span>
      </summary>
      <form class="user-edit-form" data-user-update="${user.id}">
        <label>Nome <input name="name" value="${escapeAttr(user.name)}" required /></label>
        <label>E-mail <input name="email" type="email" value="${escapeAttr(user.email)}" required /></label>
        <label>Papel
          <select name="role">
            ${editableRoles.map((role) => `<option value="${role}" ${user.role === role ? "selected" : ""}>${roleLabels[role]}</option>`).join("")}
          </select>
        </label>
        <label>Status
          <select name="status">
            <option value="ativo" ${user.status === "ativo" || user.status === "active" ? "selected" : ""}>Ativo</option>
            <option value="inativo" ${user.status === "inativo" || user.status === "inactive" ? "selected" : ""}>Inativo</option>
          </select>
        </label>
        <label>Nova senha <input name="password" type="password" minlength="6" autocomplete="new-password" placeholder="Deixe vazio para manter" /></label>
        <button class="mini-button success-button" type="submit">Salvar usuário</button>
      </form>
    </details>
  `).join("") || `<div class="notice">Nenhum usuário criado neste workspace.</div>`;
}

function renderCopyItems() {
  const source = document.querySelector('select[name="source"]').value;
  const list = document.querySelector("#copy-items-list");
  if (!state.catalogLoaded) {
    list.innerHTML = `<div class="notice">Carregando anúncios da origem em segundo plano...</div>`;
    loadCatalogInBackground();
    return;
  }
  const productTerm = state.copySearch.toLowerCase();
  const skuTerm = state.copySku.toLowerCase();
  const filtered = state.data.catalog.filter((item) => {
    const title = `${item.title || ""}`.toLowerCase();
    const sku = `${item.sku || ""}`.toLowerCase();
    const code = `${item.id || ""}`.toLowerCase();
    return item.account === source
      && (!productTerm || title.includes(productTerm))
      && (!skuTerm || sku.includes(skuTerm) || code.includes(skuTerm));
  });
  const pageInfo = paginate(filtered, state.copyPage, state.copyPageSize);
  state.copyPage = pageInfo.current;
  const selectedCount = state.cloneSelectedIds.size;
  list.innerHTML = filtered.length
    ? `<div class="pagination-info">${selectedCount} selecionado(s)</div>`
        + paginationHtml("copyPage", pageInfo)
        + pageInfo.items
        .map(
          (item) => `
            <label class="copy-item">
              <input type="checkbox" name="item_ids" value="${item.id}" ${state.cloneSelectedIds.has(item.id) ? "checked" : ""} />
              ${item.thumbnail
                ? `<img class="copy-thumb" src="${item.thumbnail}" alt="${escapeAttr(item.title || item.id)}" loading="lazy" />`
                : `<span class="copy-thumb copy-thumb-empty"></span>`}
              <span>
                <strong>${item.title}</strong>
                <small>${item.id} - SKU ${item.sku} - ${listingTypeLabel(item.listing_type_id)} - ${money.format(item.price)} - estoque ${item.stock}</small>
              </span>
            </label>
          `
        )
        .join("")
        + paginationHtml("copyPage", pageInfo)
    : `<div class="notice">Nenhum anúncio carregado para esta origem. Em conta oficial, a sincronização vem de /users/{seller_id}/items/search.</div>`;
}

function fact(label, value) {
  return `
    <button class="fact copy-neon" type="button" data-copy="${escapeAttr(value)}" title="Copiar ${label}">
      <small>${label}</small>
      <strong>${value}</strong>
    </button>
  `;
}

function formatDateBR(value) {
  if (!value || value === "Pendente" || value === "-") return value || "-";
  const text = String(value);
  const isoLike = text.match(/^\d{4}-\d{2}-\d{2}T/);
  if (isoLike) {
    const date = new Date(text);
    if (!Number.isNaN(date.getTime())) {
      const day = String(date.getDate()).padStart(2, "0");
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const year = date.getFullYear();
      const hour = String(date.getHours()).padStart(2, "0");
      const minute = String(date.getMinutes()).padStart(2, "0");
      return `${day}-${month}-${year} ${hour}:${minute}`;
    }
  }
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})(.*)$/);
  if (match) return `${match[3]}-${match[2]}-${match[1]}${(match[4] || "").replace("T", " ").slice(0, 6)}`;
  return text;
}

function copyChip(label, value) {
  return `<button class="copy-chip" type="button" data-copy="${escapeAttr(value)}" title="Copiar ${label}"><small>${label}</small><strong>${value}</strong></button>`;
}

function escapeAttr(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function escapeText(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function statusLabel(status) {
  const map = {
    active: "Ativo",
    paused: "Pausado",
    closed: "Encerrado",
    under_review: "Aguardando Revisão",
    winning: "Ganhando",
    losing: "Perdendo",
    sharing: "Compartilhando",
  };
  return map[status] || status || "-";
}

function shippingLabel(value) {
  const map = {
    self_service: "Mercado Envios Flex",
    drop_off: "Mercado Envios",
    fulfillment: "Mercado Envios Full",
    cross_docking: "Coleta",
    xd_drop_off: "Drop off",
  };
  return map[value] || value || "Não informado";
}

function flexStatusBadge(value) {
  const active = value === "self_service";
  return `
    <div class="fact flex-fact ${active ? "flex-active" : "flex-inactive"}" title="${shippingLabel(value)}">
      <small>Mercado Envios Flex</small>
      <strong>${active ? "ME Flex Ativo" : "ME Flex Inativo"}</strong>
    </div>
  `;
}

function listingTypeLabel(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("gold_pro") || text.includes("premium")) return "Premium";
  if (text.includes("gold_special") || text.includes("classic") || text.includes("clássico")) return "Clássico";
  return value ? value : "Não informado";
}

function competitionReasonLabel(reason) {
  const labels = {
    item_not_opted_in: "anúncio não está disputando catálogo no momento",
    "HTTP 404: {\"message\": \"No winners found\"": "sem vencedor encontrado para este catálogo",
  };
  return labels[reason] || reason;
}

function renderMeliConfig() {
  const form = document.querySelector("#meli-config-form");
  if (!form || !state.meliConfig) return;
  form.client_id.value = state.meliConfig.client_id || "";
  form.client_secret.value = state.meliConfig.client_secret_set ? "********" : "";
  form.redirect_uri.value = state.meliConfig.redirect_uri || state.meliConfig.suggested_redirect_uri || "";
}

function renderNotificationForm() {
  const cfg = state.data.notifications || {};
  const form = document.querySelector("#notifications-form");
  if (!form) return;
  form.telegram_enabled.checked = Boolean(cfg.telegram?.enabled);
  form.telegram_chat_id.value = cfg.telegram?.chat_id || "";
  const selectedTypes = cfg.telegram?.alert_types || ["stock", "catalog", "shipping", "scan"];
  form.querySelectorAll('input[name="alert_types"]').forEach((input) => {
    input.checked = selectedTypes.includes(input.value);
  });
  document.querySelector("#notification-status").textContent =
    `${cfg.telegram?.status || "Telegram pendente"}`;
}

document.querySelector(".segmented").addEventListener("click", (event) => {
  if (!event.target.matches("button")) return;
  document.querySelectorAll(".segmented button").forEach((button) => button.classList.remove("active"));
  event.target.classList.add("active");
  state.filter = event.target.dataset.filter;
  state.catalogPage = 1;
  renderCatalog();
});

document.querySelector("#stock-period-filter").addEventListener("click", (event) => {
  const button = event.target.closest("[data-stock-period]");
  if (!button) return;
  document.querySelectorAll("[data-stock-period]").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  state.stockPeriod = button.dataset.stockPeriod;
  state.stockCustomDate = "";
  document.querySelector("#stock-custom-date").value = "";
  renderDashboard();
});

document.querySelector("#stock-custom-date").addEventListener("change", (event) => {
  state.stockPeriod = "custom";
  state.stockCustomDate = event.target.value;
  document.querySelectorAll("[data-stock-period]").forEach((item) => item.classList.remove("active"));
  renderDashboard();
});

document.querySelector("#alert-type-filter").addEventListener("click", (event) => {
  const button = event.target.closest("[data-alert-type]");
  if (!button) return;
  document.querySelectorAll("[data-alert-type]").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  state.alertType = button.dataset.alertType;
  state.alertsPage = 1;
  renderAlerts();
});

document.querySelector("#alert-period-filter").addEventListener("click", (event) => {
  const button = event.target.closest("[data-alert-period]");
  if (!button) return;
  document.querySelectorAll("[data-alert-period]").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  state.alertPeriod = button.dataset.alertPeriod;
  state.alertCustomDate = "";
  state.alertsPage = 1;
  document.querySelector("#alert-custom-date").value = "";
  renderAlerts();
});

document.querySelector("#alert-custom-date").addEventListener("change", (event) => {
  state.alertPeriod = "custom";
  state.alertCustomDate = event.target.value;
  state.alertsPage = 1;
  document.querySelectorAll("[data-alert-period]").forEach((item) => item.classList.remove("active"));
  renderAlerts();
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const value = button.dataset.copy || "";
  try {
    await navigator.clipboard.writeText(value);
    button.classList.add("copied");
    setTimeout(() => button.classList.remove("copied"), 900);
  } catch (error) {
    button.classList.add("copied");
    setTimeout(() => button.classList.remove("copied"), 900);
  }
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-page-key][data-page]");
  if (!button) return;
  const key = button.dataset.pageKey;
  const page = Number(button.dataset.page || 1);
  if (!(key in state)) return;
  state[key] = page;
  if (key === "catalogPage") renderCatalog();
  if (key === "adsPage") renderAds();
  if (key === "copyPage") renderCopyItems();
  if (key === "alertsPage") renderAlerts();
  if (key === "scanPage") renderScan();
  if (key === "competitorsPage") renderCompetitors();
});

[
  ["#catalog-account-filter", "catalogAccount"],
  ["#catalog-search", "catalogSearch"],
  ["#catalog-ml-status-filter", "catalogMlStatus"],
  ["#catalog-stock-filter", "catalogStock"],
  ["#ads-account-filter", "adsAccount"],
  ["#ads-product-filter", "adsProduct"],
  ["#ads-sku-filter", "adsSku"],
  ["#ads-code-filter", "adsCode"],
  ["#ads-status-filter", "adsStatus"],
  ["#ads-flex-filter", "adsFlex"],
  ["#copy-product-filter", "copySearch"],
  ["#copy-sku-filter", "copySku"],
  ["#copy-page-size", "copyPageSize"],
].forEach(([selector, key]) => {
  document.addEventListener("input", (event) => {
    if (!event.target.matches(selector)) return;
    state[key] = key === "copyPageSize" ? Number(event.target.value || 20) : event.target.value;
    if (key === "copyPageSize") localStorage.setItem("competidor-copy-page-size", String(state.copyPageSize));
    if (key.startsWith("catalog")) {
      state.catalogPage = 1;
      scheduleRender("catalog", renderCatalog);
    }
    if (key.startsWith("ads")) {
      state.adsPage = 1;
      scheduleRender("ads", renderAds);
    }
    if (key.startsWith("copy")) {
      state.copyPage = 1;
      scheduleRender("copy", renderCopyItems);
    }
  });
  document.addEventListener("change", (event) => {
    if (!event.target.matches(selector)) return;
    state[key] = key === "copyPageSize" ? Number(event.target.value || 20) : event.target.value;
    if (key === "copyPageSize") localStorage.setItem("competidor-copy-page-size", String(state.copyPageSize));
    if (key.startsWith("catalog")) {
      state.catalogPage = 1;
      renderCatalog();
    }
    if (key.startsWith("ads")) {
      state.adsPage = 1;
      renderAds();
    }
    if (key.startsWith("copy")) {
      state.copyPage = 1;
      renderCopyItems();
    }
  });
});

document.querySelector("#catalog-list").addEventListener("click", async (event) => {
  const win = event.target.closest("[data-win-catalog]");
  const price = event.target.closest("[data-save-catalog-price]");
  if (!win && !price) return;
  const id = (win || price).dataset.winCatalog || (win || price).dataset.saveCatalogPrice;
  const item = state.data.catalog.find((row) => row.id === id);
  try {
    if (win) {
      await api("/api/meli/item/win_catalog", { method: "POST", body: JSON.stringify({ item_id: id }) });
      showToast("Preço ajustado para tentar ganhar o catálogo.");
    } else {
      const input = document.querySelector(`[data-catalog-price-input="${id}"]`);
      const value = Number(input?.value);
      if (!Number.isFinite(value) || value <= 0) {
        alert("Informe um preço válido para atualizar o anúncio.");
        return;
      }
      await api("/api/meli/item/update", {
        method: "POST",
        body: JSON.stringify({ item_id: id, account_id: item.account_id, price: value }),
      });
      showToast("Preço do anúncio alterado com sucesso.");
    }
    await load();
  } catch (error) {
    showToast(error.message || "Não foi possível atualizar o anúncio.", "error");
    alert(error.message || "Não foi possível atualizar o anúncio.");
  }
});

document.querySelector("#ads-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-save-ad], [data-pause-ad], [data-activate-ad], [data-remove-flex], [data-activate-flex]");
  if (!button) return;
  const id = button.dataset.saveAd || button.dataset.pauseAd || button.dataset.activateAd || button.dataset.removeFlex || button.dataset.activateFlex;
  const item = state.data.catalog.find((row) => row.id === id);
  if (button.dataset.removeFlex) {
    try {
      await api("/api/meli/item/remove_flex", {
        method: "POST",
        body: JSON.stringify({ item_id: id, account_id: item.account_id }),
      });
      showToast("Mercado Envios Flex desativado com sucesso.");
      await load();
    } catch (error) {
      showToast(error.message || "Não foi possível remover o anúncio do Mercado Envios Flex.", "error");
      alert(error.message || "Não foi possível remover o anúncio do Mercado Envios Flex.");
    }
    return;
  }
  if (button.dataset.activateFlex) {
    try {
      await api("/api/meli/item/activate_flex", {
        method: "POST",
        body: JSON.stringify({ item_id: id, account_id: item.account_id }),
      });
      showToast("Mercado Envios Flex ativado com sucesso.");
      await load();
    } catch (error) {
      showToast(error.message || "Não foi possível ativar o anúncio no Mercado Envios Flex.", "error");
      alert(error.message || "Não foi possível ativar o anúncio no Mercado Envios Flex.");
    }
    return;
  }
  const payload = { item_id: id, account_id: item.account_id };
  if (button.dataset.saveAd) {
    payload.price = Number(document.querySelector(`[data-price-input="${id}"]`).value);
    payload.available_quantity = Number(document.querySelector(`[data-stock-input="${id}"]`).value);
    payload.package_weight = document.querySelector(`[data-weight-input="${id}"]`).value;
    payload.package_height = document.querySelector(`[data-height-input="${id}"]`).value;
    payload.package_width = document.querySelector(`[data-width-input="${id}"]`).value;
    payload.package_length = document.querySelector(`[data-length-input="${id}"]`).value;
  }
  if (button.dataset.pauseAd) payload.status_action = "pause";
  if (button.dataset.activateAd) payload.status_action = "activate";
  try {
    await api("/api/meli/item/update", { method: "POST", body: JSON.stringify(payload) });
    if (button.dataset.pauseAd) showToast("Anúncio pausado com sucesso.");
    else if (button.dataset.activateAd) showToast("Anúncio ativado com sucesso.");
    else showToast("Anúncio atualizado com sucesso.");
    await load();
  } catch (error) {
    showToast(error.message || "Não foi possível atualizar o anúncio.", "error");
    alert(error.message || "Não foi possível atualizar o anúncio.");
  }
});

document.querySelector("#alerts-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-alert]");
  if (!button) return;
  const result = await api("/api/alerts/read", {
    method: "POST",
    body: JSON.stringify({ id: button.dataset.alert }),
  });
  state.data.alerts = result.alerts;
  showToast("Alerta marcado como lido.");
  render();
});

document.querySelector("#accounts-list").addEventListener("click", async (event) => {
  const unlinkButton = event.target.closest("[data-unlink-account]");
  if (unlinkButton) {
    const name = unlinkButton.dataset.accountName || "esta conta";
    if (!confirm(`Desvincular ${name} do CompeTIDOR? Os tokens locais e dados importados dessa conta serão removidos.`)) return;
    unlinkButton.disabled = true;
    unlinkButton.textContent = "Desvinculando...";
    const feedback = document.querySelector("#account-feedback");
    if (feedback) {
      feedback.dataset.locked = "1";
      feedback.innerHTML = "";
    }
    try {
      const result = await api("/api/meli/unlink", {
        method: "POST",
        body: JSON.stringify({ account_id: unlinkButton.dataset.unlinkAccount }),
      });
      await load();
      if (feedback) {
        feedback.dataset.locked = "1";
        feedback.innerHTML = `<div class="notice"><strong>Conta desvinculada</strong><p>${result.account.nickname} foi removida do CompeTIDOR local. Para revogar totalmente o acesso, remova também o aplicativo nas configurações da sua conta Mercado Livre.</p></div>`;
      }
      showToast("Conta desvinculada com sucesso.");
    } catch (error) {
      showToast(error.message || "Não foi possível desvincular a conta.", "error");
      alert(error.message || "Não foi possível desvincular a conta.");
      unlinkButton.disabled = false;
      unlinkButton.textContent = "Desvincular";
    }
    return;
  }

  const button = event.target.closest("[data-sync-account]");
  if (!button) return;
  const feedback = document.querySelector("#account-feedback");
  if (feedback) {
    feedback.dataset.locked = "1";
    feedback.innerHTML = "";
  }
  button.disabled = true;
  button.textContent = "Sincronizando...";
  try {
    const result = await api("/api/meli/sync", {
      method: "POST",
      body: JSON.stringify({ account_id: button.dataset.syncAccount, limit: "all" }),
    });
    await load();
    showToast(result.queued ? "Sincronização iniciada em segundo plano." : "Anúncios sincronizados com sucesso.");
    if (!result.queued) location.hash = "#/catalogo";
  } catch (error) {
    showToast(error.message || "Não foi possível sincronizar a conta.", "error");
    if (feedback) {
      feedback.innerHTML = `<div class="notice danger-notice"><strong>Sincronização bloqueada pelo Mercado Livre</strong><p>${error.message || "Não foi possível sincronizar a conta."}</p><p>Depois de habilitar as permissões de anúncios/vendas no painel de desenvolvedores, refaça o login OAuth desta conta.</p></div>`;
    }
    button.disabled = false;
    button.textContent = "Sincronizar anúncios";
  }
});

document.querySelector('select[name="source"]').addEventListener("change", () => {
  state.cloneSelectedIds.clear();
  state.copyPage = 1;
  renderCopyItems();
});

document.querySelector("#copy-items-list").addEventListener("change", (event) => {
  const input = event.target.closest('input[name="item_ids"]');
  if (!input) return;
  if (input.checked) state.cloneSelectedIds.add(input.value);
  else state.cloneSelectedIds.delete(input.value);
  if (input.checked && state.cloneSelectedIds.size === 1) fillCloneFieldsFromItem(input.value);
  renderCopyItems();
});

async function fillCloneFieldsFromItem(itemId) {
  const item = state.data.catalog.find((row) => row.id === itemId);
  if (!item) return;
  const form = document.querySelector("#clone-form");
  form.elements.title_override.value = item.title || "";
  form.elements.price_override.value = item.price || "";
  form.elements.stock_override.value = item.stock || "";
  form.elements.listing_type_override.value = item.listing_type_id || "";
  form.elements.sku_suffix.value = item.sku && item.sku !== "-" ? item.sku : "";
  form.elements.description_override.value = "Carregando descrição...";
  try {
    const result = await api("/api/meli/item/description", {
      method: "POST",
      body: JSON.stringify({ item_id: item.id, account_id: item.account_id }),
    });
    form.elements.description_override.value = result.description || "";
  } catch (_) {
    form.elements.description_override.value = "";
  }
}

document.querySelector("#clone-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  form.getAll("item_ids").forEach((itemId) => state.cloneSelectedIds.add(itemId));
  const itemIds = [...state.cloneSelectedIds];
  if (!itemIds.length) {
    alert("Selecione pelo menos um anúncio específico para copiar.");
    return;
  }
  try {
    const job = await api("/api/clone/preview", {
      method: "POST",
      body: JSON.stringify({
        source: form.get("source"),
        target: form.get("target"),
        item_ids: itemIds,
        edits: {
          title: form.get("title_override"),
          sku: form.get("sku_suffix"),
          listing_type_id: form.get("listing_type_override"),
          price: form.get("price_override"),
          stock: form.get("stock_override"),
          description: form.get("description_override"),
        },
      }),
    });
    state.data.clone_jobs.unshift(job);
    state.cloneSelectedIds.clear();
    showToast("Preview de cópia criado com sucesso.");
    renderClone();
  } catch (error) {
    showToast(error.message || "Não foi possível gerar o preview.", "error");
    alert(error.message || "Não foi possível gerar o preview.");
  }
});

document.querySelector("#clone-jobs").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-execute-clone]");
  if (!button) return;
  const card = button.closest("[data-clone-job-card]");
  const fieldAnswers = {};
  card?.querySelectorAll("[data-clone-answer-item][data-clone-answer-field]").forEach((input) => {
    if (!input.value.trim()) return;
    const itemId = input.dataset.cloneAnswerItem;
    fieldAnswers[itemId] ||= {};
    fieldAnswers[itemId][input.dataset.cloneAnswerField] = input.value.trim();
  });
  button.disabled = true;
  button.textContent = "Copiando...";
  try {
    const result = await api("/api/clone/execute", {
      method: "POST",
      body: JSON.stringify({ job_id: button.dataset.executeClone, field_answers: fieldAnswers }),
    });
    state.data.catalog.push(...(result.copied || []));
    state.data.clone_jobs = state.data.clone_jobs.map((job) => (job.id === result.job.id ? result.job : job));
    showToast("Anúncios copiados com sucesso.");
    render();
  } catch (error) {
    showToast(error.message || "Não foi possível copiar os anúncios.", "error");
    alert(error.message || "Não foi possível copiar os anúncios.");
    button.disabled = false;
    button.textContent = "Copiar agora";
  }
});

document.querySelector("#scan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formEl = event.currentTarget;
  const form = new FormData(formEl);
  try {
    const result = await api("/api/scan/items", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    state.data.scan_items = result.scan_items;
    formEl.reset();
    showToast("Produto adicionado ao Scan.");
    renderScan();
  } catch (error) {
    showToast(error.message || "Não foi possível criar o scan.", "error");
  }
});

document.querySelector("#scan-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-run-scan]");
  if (!button) return;
  button.disabled = true;
  button.textContent = "Escaneando...";
  try {
    const result = await api("/api/scan/run", {
      method: "POST",
      body: JSON.stringify({ id: button.dataset.runScan }),
    });
    state.data.scan_items = result.scan_items;
    showToast("Scan concluído e histórico atualizado.");
    renderScan();
  } catch (error) {
    showToast(error.message || "Não foi possível rodar o scan.", "error");
  }
});

document.querySelector("#scan-list").addEventListener("submit", async (event) => {
  const formEl = event.target.closest("[data-scan-update]");
  if (!formEl) return;
  event.preventDefault();
  const form = new FormData(formEl);
  try {
    const result = await api("/api/scan/update", {
      method: "POST",
      body: JSON.stringify({
        id: formEl.dataset.scanUpdate,
        minimum_price: form.get("minimum_price"),
      }),
    });
    state.data.scan_items = result.scan_items;
    showToast("Preço mínimo do Scan atualizado.");
    renderScan();
  } catch (error) {
    showToast(error.message || "Não foi possível atualizar o preço mínimo.", "error");
  }
});

document.querySelector("#competitor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formEl = event.currentTarget;
  const form = new FormData(formEl);
  try {
    const result = await api("/api/competitors/scan", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    state.data.competitors = result.competitors;
    showToast("Concorrente analisado com sucesso.");
    renderCompetitors();
  } catch (error) {
    showToast(error.message || "Não foi possível analisar o concorrente.", "error");
  }
});

document.querySelector("#users-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canManageUsers()) {
    showToast("Seu usuário não tem permissão para criar usuários.", "error");
    return;
  }
  const formEl = event.currentTarget;
  const form = new FormData(formEl);
  const result = await api("/api/users", {
    method: "POST",
    body: JSON.stringify(Object.fromEntries(form.entries())),
  });
  state.data.users = result.users;
  formEl.reset();
  showToast("Usuário criado com sucesso.");
  renderUsers();
});

document.querySelector("#users-list").addEventListener("submit", async (event) => {
  const formEl = event.target.closest("[data-user-update]");
  if (!formEl) return;
  event.preventDefault();
  if (!canManageUsers()) {
    showToast("Seu usuário não tem permissão para editar usuários.", "error");
    return;
  }
  const form = new FormData(formEl);
  const payload = Object.fromEntries(form.entries());
  payload.id = formEl.dataset.userUpdate;
  const result = await api("/api/users/update", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.data.users = result.users;
  showToast("Usuário atualizado com sucesso.");
  renderUsers();
});

document.querySelector("#meli-config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canManageOAuth()) {
    showToast("Apenas o usuário master pode alterar as credenciais OAuth.", "error");
    return;
  }
  const form = new FormData(event.currentTarget);
  const result = await api("/api/meli/config", {
    method: "POST",
    body: JSON.stringify(Object.fromEntries(form.entries())),
  });
  state.meliConfig = {
    ...state.meliConfig,
    ...result.config,
    suggested_redirect_uri: state.meliConfig.suggested_redirect_uri,
  };
  state.meta = await api("/api/meta");
  showToast("Configuração OAuth salva com sucesso.");
  render();
});

document.querySelector("#use-current-redirect").addEventListener("click", () => {
  if (!canManageOAuth()) {
    showToast("Apenas o usuário master pode alterar o Redirect URI.", "error");
    return;
  }
  const form = document.querySelector("#meli-config-form");
  form.redirect_uri.value = state.meliConfig?.suggested_redirect_uri || `${location.origin}/api/oauth/callback`;
});

document.querySelector("#notifications-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const result = await api("/api/notifications/config", {
    method: "POST",
    body: JSON.stringify({
      telegram: {
        enabled: form.get("telegram_enabled") === "on",
        bot_token: form.get("telegram_bot_token"),
        chat_id: form.get("telegram_chat_id"),
        alert_types: form.getAll("alert_types"),
      },
    }),
  });
  state.data.notifications = result.notifications;
  showToast("Configuração do Telegram salva com sucesso.");
  renderNotificationForm();
});

document.querySelector("#detect-telegram-chat").addEventListener("click", async () => {
  const box = document.querySelector("#telegram-chat-list");
  box.textContent = "Buscando chats recentes...";
  try {
    const result = await api("/api/notifications/telegram/updates", { method: "POST", body: "{}" });
    if (!result.chats?.length) {
      box.textContent = "Nenhum chat encontrado. Abra o bot no Telegram, envie /start e clique novamente.";
      return;
    }
    box.innerHTML = result.chats.map((chat) => `
      <button class="copy-chip telegram-chat-option" type="button" data-chat-id="${escapeAttr(chat.id)}">
        <small>${escapeText(chat.type || "chat")}</small>
        <strong>${escapeText(chat.title || chat.username || chat.id)} · ${escapeText(chat.id)}</strong>
      </button>
    `).join("");
  } catch (error) {
    box.textContent = error.message || "Não foi possível buscar os chats.";
  }
});

document.querySelector("#telegram-chat-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-chat-id]");
  if (!button) return;
  document.querySelector('#notifications-form input[name="telegram_chat_id"]').value = button.dataset.chatId;
  showToast("Chat ID preenchido.");
});

document.querySelector("#test-notifications").addEventListener("click", async () => {
  const result = await api("/api/notifications/test", {
    method: "POST",
    body: JSON.stringify({
      message: "Teste de alerta do CompeTIDOR: canal configurado.",
    }),
  });
  document.querySelector("#notification-status").textContent = JSON.stringify(result.results);
  if (result.results?.telegram?.ok === false) {
    showToast(result.results.telegram.error || result.results.telegram.status || "Teste do Telegram falhou.", "error");
  } else {
    showToast("Teste do Telegram enviado.");
  }
});

document.querySelector("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const feedback = document.querySelector("#setup-feedback");
  const form = new FormData(event.currentTarget);
  feedback.textContent = "";
  try {
    const result = await api("/api/auth/setup-master", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    state.currentUser = result.user;
    showApp();
    await load();
  } catch (error) {
    feedback.textContent = error.message || "Não foi possível criar o usuário master.";
  }
});

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const feedback = document.querySelector("#login-feedback");
  const form = new FormData(event.currentTarget);
  feedback.textContent = "";
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    state.currentUser = result.user;
    showApp();
    await load();
  } catch (error) {
    if (error.setup_required) {
      showSetup();
      return;
    }
    feedback.textContent = error.message || "Não foi possível entrar.";
  }
});

async function logout() {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  state.currentUser = null;
  showLogin();
}

document.addEventListener("click", (event) => {
  if (!event.target.closest("#logout")) return;
  logout();
});

document.querySelector("#theme-toggle").addEventListener("click", () => {
  state.theme = state.theme === "dark" ? "light" : "dark";
  localStorage.setItem("competidor-theme", state.theme);
  applyTheme();
});
window.addEventListener("hashchange", () => {
  if (state.data) render();
});

function fixStaticCopyLabels() {
  const descriptionInput = document.querySelector('input[name="description_override"]');
  if (descriptionInput) {
    const textarea = document.createElement("textarea");
    textarea.name = descriptionInput.name;
    textarea.placeholder = descriptionInput.placeholder;
    textarea.value = descriptionInput.value || "";
    textarea.rows = 7;
    descriptionInput.replaceWith(textarea);
  }
  const fieldLabels = {
    title_override: "Título padrão ",
    sku_suffix: "SKU ",
    price_override: "Preço ",
    stock_override: "Estoque ",
    description_override: "Descrição ",
  };
  Object.entries(fieldLabels).forEach(([name, text]) => {
    const input = document.querySelector(`[name="${name}"]`);
    const label = input?.closest("label");
    if (label?.firstChild) label.firstChild.textContent = text;
  });
  const listingType = document.querySelector('select[name="listing_type_override"]');
  if (listingType) {
    const labels = {
      "": "Mesmo tipo do anúncio",
      gold_special: "Clássico",
      gold_pro: "Premium",
    };
    [...listingType.options].forEach((option) => {
      option.textContent = labels[option.value] || option.textContent;
    });
    const label = listingType.closest("label");
    if (label?.firstChild) label.firstChild.textContent = "Tipo de anúncio ";
  }
}

if (!location.hash) location.hash = "#/dashboard";
applyTheme();
fixStaticCopyLabels();
checkSession().catch((error) => {
  document.body.innerHTML = `<main class="error"><h1>CompeTIDOR</h1><p>${error.message}</p></main>`;
});

