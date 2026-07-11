(function () {
  "use strict";

  var API = "/renaiss/api";
  var messages = {
    ko: {
      skip: "본문으로 건너뛰기", brandHome: "Renaiss 콜라보 도감 홈", primaryNavigation: "주요 메뉴", languageSelection: "언어 선택", collectionSummary: "수집 요약", mobileNavigation: "모바일 주요 메뉴",
      collabArchive: "콜라보 도감", tabDex: "도감", tabLeaderboard: "리더보드",
      tabCommands: "명령어", tabGuide: "가이드", telegramLogin: "Telegram 로그인", logout: "로그아웃",
      loginUnavailable: "로그인 설정 중", openGroup: "Telegram 게임방", collabProject: "TGPoke × Renaiss 콜라보 프로젝트", dexTitle: "카드 도감",
      dexDescription: "포획한 카드와 현재 콜라보 카드 풀의 완성도를 확인하세요.", loadingCatalog: "카드 풀 불러오는 중",
      completion: "달성률", signInForProgress: "로그인하면 내 진행도를 볼 수 있습니다.", collectedTypes: "수집 종류",
      totalCopies: "보유 수량", signInNote: "보유 카드를 표시하려면 Telegram으로 로그인하세요.",
      searchLabel: "카드 검색", searchPlaceholder: "카드명, 세트 또는 번호 검색", grade: "등급", allGrades: "모든 등급",
      set: "세트", allSets: "모든 세트", ownership: "보유 여부", allCards: "전체", owned: "보유", missing: "미보유",
      archived: "아카이브", sort: "정렬", sortSet: "세트 순", sortName: "이름 순", sortGrade: "높은 등급 순",
      sortRecent: "최근 획득 순", loadingCards: "카드 불러오는 중", reset: "초기화", loadingCollection: "도감을 불러오는 중입니다.",
      loadMore: "더 보기", archiveProgress: "이번 주 Lucky Catch", leaderboardTitle: "리더보드",
      leaderboardDescription: "이번 주 공개 포획 당첨 횟수만 표시하며 매주 KST에 새로 시작합니다.",
      loadingLeaderboard: "리더보드를 불러오는 중입니다.", leaderboardPrivacy: "동률은 같은 순위입니다. 가격, 자산, 도감 완성도와 Daily Pick 기록은 합산하지 않습니다.",
      botCommands: "Renaiss 봇에서 사용", commandsTitle: "명령어", commandsDescription: "게임 시작은 한 글자면 충분합니다. 버튼을 누르면 명령어가 복사됩니다.",
      commandCatch: "공개 포획 참여", commandCatchHelp: "카드가 나타났을 때 랜덤 추첨과 가격 추측에 참여합니다.",
      commandCards: "내 컬렉션", commandCardsHelp: "등급과 세트별 보유 카드를 확인합니다.", commandPrice: "참고 가격",
      commandPriceHelp: "검증된 Renaiss 참고 가격과 출처를 확인합니다.", commandFlex: "포획 자랑하기",
      commandFlexHelp: "운 좋게 잡은 카드를 그룹에 공유합니다.", commandMarket: "Daily Pick",
      commandMarketHelp: "검증 파일럿이 열린 동안 하루 한 장을 선택합니다.", copy: "복사", copied: "복사됨",
      firstRound: "첫 라운드", guideTitle: "빠른 가이드", guideDescription: "핵심 게임은 Telegram 공개 그룹 안에서 끝납니다.",
      guideOneTitle: "카드가 나타나면 c 입력", guideOneBody: "누적 자산과 무관한 랜덤 포획 추첨에 참여합니다.",
      guideTwoTitle: "공개 전에 가격 추측", guideTwoBody: "FMV가 숨겨진 상태에서 가격 범위를 고르고 안목을 시험합니다.",
      guideThreeTitle: "Renaiss 근거와 함께 결과 확인", guideThreeBody: "검증된 가격, 출처와 신뢰 상태를 그룹에서 함께 봅니다.",
      guideFourTitle: "도감은 기록, Daily Pick은 별도", guideFourBody: "수집 완성도는 자랑용이며 Daily Pick 결과나 점수가 되지 않습니다.",
      noTradingTitle: "투자·거래 게임이 아닙니다.", noTradingBody: "현금, 매수·매도, 수익 보장 또는 실물 카드 소유권을 제공하지 않습니다. 모든 가격은 실험적 참고 데이터입니다.",
      collaborationNotice: "TGPoke가 운영하는 Renaiss 콜라보 프로젝트 페이지이며 Renaiss 공식 홈페이지가 아닙니다.",
      signedIn: "{name}님의 컬렉션을 표시하고 있습니다.", catalogCount: "{cards}종 · {sets}개 세트", previewCatalogCount: "미리보기 · {cards}종 · {sets}개 세트",
      progressDetail: "{owned} / {total}종", types: "{n}종", cards: "{n}장", resultCount: "{total}종 중 {shown}종 표시",
      cardOwned: "보유 ×{n}", cardMissing: "미보유", cardPublic: "도감", cardArchived: "아카이브", noSet: "세트 정보 없음",
      noResults: "조건에 맞는 카드가 없습니다.", catalogPreparing: "Renaiss 카드 풀을 준비 중입니다.", loadFailed: "데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
      noLeaders: "이번 주 공개 포획 당첨 기록이 아직 없습니다.", luckyCatches: "공개 포획 당첨", you: "나", authFailed: "Telegram 로그인에 실패했습니다. 다시 시도해 주세요.",
      previewSignedIn: "미리보기 계정으로 로그인했습니다."
    },
    en: {
      skip: "Skip to content", brandHome: "Renaiss collaboration collection home", primaryNavigation: "Primary navigation", languageSelection: "Language selection", collectionSummary: "Collection summary", mobileNavigation: "Mobile navigation",
      collabArchive: "Collab collection", tabDex: "Collection", tabLeaderboard: "Leaderboard",
      tabCommands: "Commands", tabGuide: "Guide", telegramLogin: "Sign in with Telegram", logout: "Sign out",
      loginUnavailable: "Login setup pending", openGroup: "Open Telegram game room", collabProject: "TGPoke × Renaiss collaboration", dexTitle: "Card collection",
      dexDescription: "Browse your catches and completion across the current collaboration card pool.", loadingCatalog: "Loading card pool",
      completion: "Completion", signInForProgress: "Sign in to view your progress.", collectedTypes: "Collected",
      totalCopies: "Total copies", signInNote: "Sign in with Telegram to mark the cards you own.",
      searchLabel: "Search cards", searchPlaceholder: "Search card, set, or number", grade: "Grade", allGrades: "All grades",
      set: "Set", allSets: "All sets", ownership: "Ownership", allCards: "All", owned: "Owned", missing: "Missing",
      archived: "Archived", sort: "Sort", sortSet: "Set order", sortName: "Name", sortGrade: "Highest grade",
      sortRecent: "Recently collected", loadingCards: "Loading cards", reset: "Reset", loadingCollection: "Loading collection.",
      loadMore: "Load more", archiveProgress: "This week's Lucky Catch", leaderboardTitle: "Leaderboard",
      leaderboardDescription: "Only public catch wins from this week are shown. The board restarts weekly in KST.",
      loadingLeaderboard: "Loading leaderboard.", leaderboardPrivacy: "Ties share a rank. Prices, assets, collection completion, and Daily Pick records are never combined.",
      botCommands: "Use these in the Renaiss bot", commandsTitle: "Commands", commandsDescription: "One letter starts the game. Tap a row to copy the command.",
      commandCatch: "Join a public catch", commandCatchHelp: "Join the random draw and hidden-price guess when a card appears.",
      commandCards: "My collection", commandCardsHelp: "View cards by grade and set.", commandPrice: "Reference price",
      commandPriceHelp: "View a verified Renaiss reference value and its sources.", commandFlex: "Share a lucky catch",
      commandFlexHelp: "Share a lucky card with the group.", commandMarket: "Daily Pick",
      commandMarketHelp: "Choose one card per day while the verified pilot is open.", copy: "Copy", copied: "Copied",
      firstRound: "Your first round", guideTitle: "Quick guide", guideDescription: "The core game finishes inside the public Telegram group.",
      guideOneTitle: "Type c when a card appears", guideOneBody: "Join a random catch that does not depend on accumulated wealth.",
      guideTwoTitle: "Guess before the reveal", guideTwoBody: "Choose a price range while FMV is hidden and test your insight.",
      guideThreeTitle: "See the result with Renaiss evidence", guideThreeBody: "Review the verified value, sources, and confidence together in the group.",
      guideFourTitle: "Collection is a record; Daily Pick is separate", guideFourBody: "Completion is for sharing and never becomes a Daily Pick result or score.",
      noTradingTitle: "This is not an investment or trading game.", noTradingBody: "There is no cash, buying, selling, guaranteed return, or ownership of a physical card. All prices are experimental reference data.",
      collaborationNotice: "Operated by TGPoke for a Renaiss collaboration project. This is not the official Renaiss website.",
      signedIn: "Showing {name}'s collection.", catalogCount: "{cards} types · {sets} sets", previewCatalogCount: "Preview · {cards} types · {sets} sets",
      progressDetail: "{owned} / {total} types", types: "{n} types", cards: "{n} cards", resultCount: "Showing {shown} of {total} types",
      cardOwned: "Owned ×{n}", cardMissing: "Missing", cardPublic: "Collection", cardArchived: "Archived", noSet: "No set data",
      noResults: "No cards match these filters.", catalogPreparing: "The Renaiss card pool is being prepared.", loadFailed: "Could not load data. Please try again shortly.",
      noLeaders: "No public catch wins yet this week.", luckyCatches: "Public catch wins", you: "You", authFailed: "Telegram sign-in failed. Please try again.",
      previewSignedIn: "Signed in with the preview account."
    }
  };

  var state = {
    locale: "ko", view: "dex", config: null, user: null, page: 1, cards: [], total: 0,
    hasMore: false, loading: false, leaderboardLoaded: false, catalogTotal: 0,
    summaryData: null, summaryAuthenticated: false
  };
  var searchTimer;

  function t(key, values) {
    var text = (messages[state.locale] && messages[state.locale][key]) || messages.ko[key] || key;
    Object.keys(values || {}).forEach(function (name) { text = text.replace("{" + name + "}", String(values[name])); });
    return text;
  }
  function escapeHTML(value) {
    return String(value == null ? "" : value).replace(/[&<>'"]/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char];
    });
  }
  function number(value) { return new Intl.NumberFormat(state.locale === "ko" ? "ko-KR" : "en-US").format(Number(value || 0)); }
  function safeImage(value) {
    try { var url = new URL(String(value || "")); return url.protocol === "https:" ? url.href : ""; } catch (error) { return ""; }
  }

  function applyLocale(locale, updateURL) {
    state.locale = locale === "en" ? "en" : "ko";
    document.documentElement.lang = state.locale;
    document.querySelectorAll("[data-i18n]").forEach(function (element) { element.textContent = t(element.dataset.i18n); });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (element) { element.placeholder = t(element.dataset.i18nPlaceholder); });
    document.querySelectorAll("[data-i18n-aria]").forEach(function (element) { element.setAttribute("aria-label", t(element.dataset.i18nAria)); });
    document.querySelectorAll("[data-locale]").forEach(function (button) { button.setAttribute("aria-pressed", String(button.dataset.locale === state.locale)); });
    document.title = state.locale === "ko" ? "Renaiss 콜라보 도감 | TGPoke" : "Renaiss Collaboration Collection | TGPoke";
    localStorage.setItem("renaiss_locale", state.locale);
    if (updateURL) {
      var url = new URL(location.href);
      if (state.locale === "en") url.searchParams.set("lang", "en"); else url.searchParams.delete("lang");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
    renderAuth();
    if (state.summaryData) renderSummary(state.summaryData, state.summaryAuthenticated);
    if (state.cards.length) renderCards();
    if (state.leaderboardLoaded) loadLeaderboard(true);
  }

  function setView(view, updateURL) {
    if (["dex", "leaderboard", "commands", "guide"].indexOf(view) < 0) view = "dex";
    state.view = view;
    document.querySelectorAll("[data-view]").forEach(function (button) {
      var active = button.dataset.view === view;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll("[data-panel]").forEach(function (panel) {
      var active = panel.dataset.panel === view;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    if (updateURL) history.replaceState(null, "", location.pathname + location.search + (view === "dex" ? "" : "#" + view));
    if (view === "leaderboard" && !state.leaderboardLoaded) loadLeaderboard(false);
    if (updateURL) window.scrollTo(0, 0);
  }

  function renderAuth() {
    var button = document.getElementById("loginButton");
    if (!button) return;
    if (state.user) {
      button.disabled = false;
      button.textContent = (state.user.display_name || "Collector") + " · " + t("logout");
      button.dataset.action = "logout";
      document.getElementById("sessionNote").textContent = t("signedIn", { name: state.user.display_name || "Collector" });
      document.getElementById("sessionNote").classList.add("is-authenticated");
    } else {
      var available = Boolean(state.config && state.config.telegram_login_available);
      button.disabled = !available;
      button.textContent = available ? t("telegramLogin") : t("loginUnavailable");
      button.dataset.action = "login";
      document.getElementById("sessionNote").textContent = t("signInNote");
      document.getElementById("sessionNote").classList.remove("is-authenticated");
    }
    var owned = document.getElementById("ownedFilter");
    Array.prototype.forEach.call(owned.options, function (option) {
      option.disabled = !state.user && (option.value === "owned" || option.value === "archived");
    });
  }

  async function loadConfigAndAuth() {
    try {
      var values = await Promise.all([
        fetch(API + "/config", { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); }),
        fetch(API + "/auth/me", { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); })
      ]);
      state.config = values[0];
      state.user = values[1] && values[1].ok ? values[1].user : null;
    } catch (error) { state.config = null; state.user = null; }
    var groupLink = document.getElementById("telegramGroupLink");
    if (groupLink && state.config && state.config.telegram_group_url) {
      groupLink.href = state.config.telegram_group_url;
      groupLink.hidden = false;
    }
    renderAuth();
  }

  async function authenticate() {
    if (!state.config || !state.config.telegram_login_available) return;
    if (state.config.preview) {
      try {
        var response = await fetch(state.config.telegram_login_url, { method: "POST", credentials: "same-origin" });
        var data = await response.json();
        if (!data.ok) throw new Error("preview auth failed");
        state.user = data.user;
        renderAuth();
        await loadCollection(true);
      } catch (error) { window.alert(t("authFailed")); }
      return;
    }
    location.href = state.config.telegram_login_url;
  }

  async function logout() {
    try { await fetch(API + "/auth/logout", { method: "POST", credentials: "same-origin" }); } catch (error) {}
    state.user = null;
    state.leaderboardLoaded = false;
    document.getElementById("ownedFilter").value = "all";
    renderAuth();
    await loadCollection(true);
    if (state.view === "leaderboard") loadLeaderboard(true);
  }

  function filters() {
    return {
      q: document.getElementById("searchInput").value.trim(), grade: document.getElementById("gradeFilter").value,
      set: document.getElementById("setFilter").value, owned: document.getElementById("ownedFilter").value,
      sort: document.getElementById("sortFilter").value
    };
  }
  function query() {
    var params = new URLSearchParams(filters());
    params.set("page", String(state.page)); params.set("per_page", "12");
    return params.toString();
  }
  function setOptions(select, rows, valueKey, label, first) {
    var current = select.value || "all";
    select.innerHTML = '<option value="all">' + escapeHTML(first) + "</option>" + (rows || []).map(function (row) {
      return '<option value="' + escapeHTML(row[valueKey]) + '">' + escapeHTML(label(row)) + "</option>";
    }).join("");
    select.value = Array.prototype.some.call(select.options, function (option) { return option.value === current; }) ? current : "all";
  }
  function renderSummary(data, authenticated) {
    var summary = data.summary || {};
    state.summaryData = data;
    state.summaryAuthenticated = authenticated;
    var total = Number(summary.catalog_total || 0), owned = Number(summary.owned_in_catalog || 0), completion = authenticated ? Number(summary.completion_pct || 0) : 0;
    state.catalogTotal = total;
    document.getElementById("completionBar").style.width = Math.max(0, Math.min(100, completion)) + "%";
    document.getElementById("completionValue").textContent = authenticated ? completion.toFixed(1) + "%" : "—";
    document.getElementById("completionDetail").textContent = authenticated ? t("progressDetail", { owned: number(owned), total: number(total) }) : t("signInForProgress");
    document.getElementById("ownedValue").textContent = authenticated ? t("types", { n: number(owned) }) : "—";
    document.getElementById("quantityValue").textContent = authenticated ? t("cards", { n: number(summary.total_quantity) }) : "—";
    document.getElementById("catalogScope").textContent = t(summary.is_preview ? "previewCatalogCount" : "catalogCount", { cards: number(total), sets: number(summary.sets_total) });
    setOptions(document.getElementById("gradeFilter"), (data.grades || []).map(function (value) { return { value: value }; }), "value", function (row) { return row.value; }, t("allGrades"));
    setOptions(document.getElementById("setFilter"), data.sets || [], "set_code", function (row) { return row.set_name ? row.set_name + " · " + row.set_code : row.set_code; }, t("allSets"));
    renderAuth();
  }
  function cardMarkup(card) {
    var owned = Boolean(card.owned), archived = card.source_kind === "archived", image = safeImage(card.image_url);
    var status = archived ? t("cardArchived") : state.user ? (owned ? t("cardOwned", { n: number(card.quantity) }) : t("cardMissing")) : t("cardPublic");
    var meta = [card.grade, card.set_name || card.set_code, card.collector_number].filter(Boolean).join(" · ") || t("noSet");
    return '<article class="card-tile ' + (owned ? "is-owned" : "is-missing") + (archived ? " is-archived" : "") + '">' +
      '<div class="card-art' + (image ? "" : " is-broken") + '">' + (image ? '<img src="' + escapeHTML(image) + '" alt="' + escapeHTML(card.card_name) + '" loading="lazy" decoding="async">' : "") + '<span class="card-fallback" aria-hidden="true">' + escapeHTML(String(card.card_name || "R").charAt(0)) + "</span></div>" +
      '<div class="card-body"><h2>' + escapeHTML(card.card_name || "Renaiss card") + '</h2><p class="card-meta">' + escapeHTML(meta) + '</p><div class="card-state"><span>' + escapeHTML(card.language || "") + "</span><strong>" + escapeHTML(status) + "</strong></div></div></article>";
  }
  function renderCards() {
    var grid = document.getElementById("cardGrid");
    grid.setAttribute("aria-busy", "false");
    grid.innerHTML = state.cards.length ? state.cards.map(cardMarkup).join("") : '<p class="state-message">' + escapeHTML(t("noResults")) + "</p>";
    grid.querySelectorAll("img").forEach(function (image) { image.addEventListener("error", function () { image.parentElement.classList.add("is-broken"); }, { once: true }); });
    document.getElementById("resultCount").textContent = t("resultCount", { total: number(state.total), shown: number(state.cards.length) });
    document.getElementById("loadMoreWrap").hidden = !state.hasMore;
  }
  async function loadCollection(reset) {
    if (state.loading) return;
    if (reset) { state.page = 1; state.cards = []; document.getElementById("cardGrid").innerHTML = '<p class="state-message">' + escapeHTML(t("loadingCollection")) + "</p>"; }
    else state.page += 1;
    state.loading = true;
    try {
      var response = await fetch(API + "/collection?" + query(), { credentials: "same-origin", cache: "no-store" });
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error("collection unavailable");
      renderSummary(data, Boolean(data.authenticated));
      state.cards = reset ? (data.cards || []) : state.cards.concat(data.cards || []);
      state.total = Number(data.total_filtered || 0); state.hasMore = Boolean(data.has_more);
      if (!data.available) document.getElementById("cardGrid").innerHTML = '<p class="state-message">' + escapeHTML(t("catalogPreparing")) + "</p>";
      else renderCards();
    } catch (error) { document.getElementById("cardGrid").innerHTML = '<p class="state-message">' + escapeHTML(t("loadFailed")) + "</p>"; }
    finally { state.loading = false; }
  }
  function leaderMarkup(row) {
    return '<article class="leader-row' + (row.is_me ? " is-me" : "") + '"><strong class="leader-rank">' + number(row.rank) + '</strong><div class="leader-name"><strong>' + escapeHTML(row.display_name) + (row.is_me ? " · " + escapeHTML(t("you")) : "") + '</strong><span>KST · weekly reset</span></div><div class="leader-wins"><strong>' + number(row.lucky_catches) + '</strong><span>' + escapeHTML(t("luckyCatches")) + '</span></div></article>';
  }
  async function loadLeaderboard(force) {
    var list = document.getElementById("leaderboardList");
    if (state.leaderboardLoaded && !force) return;
    list.innerHTML = '<p class="state-message">' + escapeHTML(t("loadingLeaderboard")) + "</p>";
    try {
      var response = await fetch(API + "/leaderboard?limit=20", { credentials: "same-origin", cache: "no-store" });
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error("leaderboard unavailable");
      list.innerHTML = data.rows && data.rows.length ? data.rows.map(leaderMarkup).join("") : '<p class="state-message">' + escapeHTML(t("noLeaders")) + "</p>";
      state.leaderboardLoaded = true;
    } catch (error) { list.innerHTML = '<p class="state-message">' + escapeHTML(t("loadFailed")) + "</p>"; }
  }
  function copyCommand(button) {
    var value = button.dataset.copy || "";
    var task = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(value) : Promise.reject();
    task.catch(function () { var area = document.createElement("textarea"); area.value = value; document.body.appendChild(area); area.select(); document.execCommand("copy"); area.remove(); });
    button.classList.add("is-copied"); button.querySelector("b").textContent = t("copied");
    setTimeout(function () { button.classList.remove("is-copied"); button.querySelector("b").textContent = t("copy"); }, 1200);
  }
  function resetFilters() {
    document.getElementById("searchInput").value = ""; document.getElementById("gradeFilter").value = "all";
    document.getElementById("setFilter").value = "all"; document.getElementById("ownedFilter").value = "all";
    document.getElementById("sortFilter").value = "set"; loadCollection(true);
  }
  function bind() {
    document.querySelectorAll("[data-view]").forEach(function (button) { button.addEventListener("click", function () { setView(button.dataset.view, true); }); });
    document.querySelectorAll("[data-locale]").forEach(function (button) { button.addEventListener("click", function () { applyLocale(button.dataset.locale, true); }); });
    document.getElementById("loginButton").addEventListener("click", function () { state.user ? logout() : authenticate(); });
    document.getElementById("searchInput").addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(function () { loadCollection(true); }, 260); });
    ["gradeFilter", "setFilter", "ownedFilter", "sortFilter"].forEach(function (id) { document.getElementById(id).addEventListener("change", function () { loadCollection(true); }); });
    document.getElementById("resetFilters").addEventListener("click", resetFilters);
    document.getElementById("loadMoreButton").addEventListener("click", function () { loadCollection(false); });
    document.querySelectorAll("[data-copy]").forEach(function (button) { button.addEventListener("click", function () { copyCommand(button); }); });
    window.addEventListener("hashchange", function () { setView(location.hash.slice(1) || "dex", false); });
  }
  async function init() {
    var params = new URLSearchParams(location.search);
    state.locale = params.get("lang") === "en" ? "en" : (localStorage.getItem("renaiss_locale") === "en" ? "en" : "ko");
    applyLocale(state.locale, false); bind(); setView(location.hash.slice(1) || "dex", false);
    await loadConfigAndAuth();
    if (params.get("auth") === "failed") { window.alert(t("authFailed")); params.delete("auth"); history.replaceState(null, "", location.pathname + (params.toString() ? "?" + params : "") + location.hash); }
    else if (params.has("auth")) { params.delete("auth"); history.replaceState(null, "", location.pathname + (params.toString() ? "?" + params : "") + location.hash); }
    await loadCollection(true);
  }
  init();
}());
