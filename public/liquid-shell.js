/* Liquid Glass shell: comportamento apenas visual do menu (folha "Mais" no celular,
   destaque do item ativo e rótulo do tema). Não altera rotas, dados ou ações da aplicação. */
(() => {
  const body = document.body;
  const sidebar = document.querySelector(".sidebar");
  const nav = sidebar?.querySelector("nav");
  const more = document.querySelector("#nav-more");
  const backdrop = document.querySelector(".nav-sheet-backdrop");
  if (!sidebar || !nav || !more) return;

  const mobile = window.matchMedia("(max-width: 780px)");

  function setSheet(open) {
    const next = Boolean(open) && mobile.matches;
    body.classList.toggle("nav-sheet-open", next);
    more.setAttribute("aria-expanded", String(next));
  }

  more.addEventListener("click", (event) => {
    event.stopPropagation();
    setSheet(!body.classList.contains("nav-sheet-open"));
  });
  backdrop?.addEventListener("click", () => setSheet(false));
  nav.addEventListener("click", (event) => {
    if (event.target.closest("a")) setSheet(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setSheet(false);
  });
  window.addEventListener("hashchange", () => setSheet(false));
  mobile.addEventListener?.("change", () => setSheet(false));

  // Quando a tela atual não está na barra inferior, o botão "Mais" fica destacado.
  function syncMore() {
    const active = nav.querySelector("a.active");
    more.classList.toggle("active", Boolean(active && !active.hasAttribute("data-tab")));
  }
  new MutationObserver(syncMore).observe(nav, { subtree: true, attributes: true, attributeFilter: ["class"] });
  syncMore();

  // Celular: barras com muitos filtros mostram os 3 primeiros e recolhem o resto
  // atrás de "Mais filtros". Nenhum campo é movido ou recriado; apenas ocultado.
  const filterBars = document.querySelectorAll([
    ".filter-bar", ".statistics-filters", ".customers-filter-grid", ".returns-filter-grid",
    ".goals-filters", ".purchase-filter-grid", ".analytics-filters",
  ].join(","));
  const isDefault = (field) => {
    if (field.type === "checkbox" || field.type === "radio") return field.checked === field.defaultChecked;
    if (field.tagName === "SELECT") {
      const initial = [...field.options].find((option) => option.defaultSelected) || field.options[0];
      return !initial || field.value === initial.value;
    }
    return field.value === field.defaultValue;
  };
  filterBars.forEach((bar) => {
    const labels = [...bar.querySelectorAll(":scope > label")];
    const candidates = labels.filter((label) => !label.hidden && !label.matches("[class*='custom'], [data-replenishment]"));
    if (labels.length < 6 || candidates.length < 5) return;
    // Período e busca principal ficam sempre visíveis.
    const essential = (label) => Boolean(label.querySelector('[name="period"], [type="search"][name="q"]'));
    const extras = candidates.slice(3).filter((label) => !essential(label));
    extras.forEach((label) => label.classList.add("lg-extra"));
    bar.classList.add("lg-collapsible", "lg-collapsed");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "lg-filter-toggle";
    toggle.setAttribute("aria-expanded", "false");
    bar.insertAdjacentElement("afterend", toggle);
    const update = () => {
      const open = !bar.classList.contains("lg-collapsed");
      const active = extras.filter((label) => [...label.querySelectorAll("input, select")].some((field) => !isDefault(field))).length;
      toggle.textContent = open ? "Menos filtros" : `Mais filtros (${extras.length})${active ? ` · ${active} ativo${active > 1 ? "s" : ""}` : ""}`;
      toggle.setAttribute("aria-expanded", String(open));
    };
    toggle.addEventListener("click", () => { bar.classList.toggle("lg-collapsed"); update(); });
    bar.addEventListener("input", update);
    bar.addEventListener("change", update);
    update();
  });

  // Central de relatórios: cartões de atalho que apenas escolhem a opção do seletor existente.
  const reportSelect = document.querySelector("#reports-section-select");
  const reportsHub = reportSelect?.closest(".reports-hub");
  if (reportSelect && reportsHub) {
    const details = {
      statistics: ["Desempenho, SKUs sem venda e histórico", "M4 20V10h3v10H4Zm6 0V4h3v16h-3Zm6 0v-7h3v7h-3Z"],
      sales: ["Pedidos, receita líquida e lucro", "M12 2v2.1c-3.4.4-5 2.3-5 4.7 0 2.9 2.5 4 5 4.7 2.2.6 3 1.1 3 2.2 0 1.2-1.1 2.1-3 2.1-2.2 0-3.8-.8-5.2-1.8l-1.5 2.3c1.6 1.2 3.8 2 6.7 2.2V22h2v-1.7c3.5-.5 5.2-2.5 5.2-5 0-3-2.4-4.1-5.2-4.9-2.1-.6-3-1.1-3-2.1 0-1 .9-1.8 2.8-1.8 1.8 0 3.2.6 4.4 1.4l1.4-2.3A10.7 10.7 0 0 0 14 4.1V2h-2Z"],
      brand: ["Rentabilidade e reposição por marca", "M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8ZM7.5 8.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z"],
      replenishment: ["O que repor e quanto comprar", "M7 4V2h2v2h6V2h2v2h3v18H4V4h3Zm11 6H6v10h12V10Z"],
      equalization: ["Lacunas e divergências entre contas", "M4 5h16v4H4V5Zm0 6h16v8H4v-8Z"],
      "official-stores": ["Cobertura por loja oficial e publicação", "M8 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8ZM3 21v-2a5 5 0 0 1 10 0v2H3Zm12-6h6v2h-6v-2Zm0 4h6v2h-6v-2Z"],
    };
    const grid = document.createElement("div");
    grid.className = "lg-report-cards";
    [...reportSelect.options].filter((option) => option.value).forEach((option) => {
      const [hint, icon] = details[option.value] || ["", "M5 3h10l4 4v14H5V3Z"];
      const card = document.createElement("button");
      card.type = "button";
      card.className = "lg-report-card";
      card.dataset.reportValue = option.value;
      card.innerHTML = `<span class="lg-report-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icon}"/></svg></span><strong></strong><small></small>`;
      card.querySelector("strong").textContent = option.textContent;
      card.querySelector("small").textContent = hint;
      card.addEventListener("click", () => {
        reportSelect.value = option.value;
        reportSelect.dispatchEvent(new Event("change", { bubbles: true }));
        reportSelect.scrollIntoView({ block: "start", behavior: "smooth" });
      });
      grid.append(card);
    });
    reportsHub.insertAdjacentElement("afterend", grid);
    const syncCards = () => {
      grid.hidden = Boolean(reportSelect.value);
      grid.querySelectorAll(".lg-report-card").forEach((card) => card.classList.toggle("active", card.dataset.reportValue === reportSelect.value));
    };
    reportSelect.addEventListener("change", syncCards);
    window.addEventListener("hashchange", () => setTimeout(syncCards, 0));
    setTimeout(syncCards, 0);
  }

  // Rótulo do botão de tema e cor da barra do navegador acompanham o tema atual.
  const themeLabel = document.querySelector("#theme-toggle .theme-label");
  const themeColors = document.querySelectorAll('meta[name="theme-color"]');
  function syncTheme() {
    const dark = document.documentElement.dataset.theme === "dark";
    if (themeLabel) themeLabel.textContent = dark ? "Tema escuro" : "Tema claro";
    themeColors.forEach((meta) => {
      meta.setAttribute("content", dark ? "#07090d" : "#f4f5f7");
      meta.removeAttribute("media");
    });
  }
  new MutationObserver(syncTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  syncTheme();
})();
