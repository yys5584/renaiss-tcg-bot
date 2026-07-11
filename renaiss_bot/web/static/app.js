(function () {
  "use strict";

  var API = "/renaiss/api";
  var messages = {
    ko: {
      skip: "본문으로 건너뛰기", tgpokeHome: "TGPoke 홈", tgpokeNavigation: "TGPoke 주요 메뉴",
      navHome: "홈", navMyCards: "내 카드", navDex: "도감", navAttendance: "출석", navTiers: "티어표",
      languageSelection: "언어 선택", openMenu: "메뉴 열기", closeMenu: "메뉴 닫기", renaissNavigation: "Renaiss 메뉴",
      telegramLogin: "로그인", logout: "로그아웃", loginUnavailable: "로그인 준비 중", openGroup: "봇 시작", openGameRoom: "Telegram 게임방",
      renaissDex: "도감", renaissMyCards: "내 카드", leaderboardTitle: "리더보드", guideTitle: "가이드",
      collabProject: "TGPoke × Renaiss 콜라보", dexTitle: "Renaiss 카드 도감", dexDescription: "콜라보 카드 풀과 내 포획 기록을 한곳에서 확인합니다.",
      mycardsTitle: "내 Renaiss 카드", mycardsDescription: "Telegram 봇에서 포획한 카드와 완성률을 확인합니다.",
      catalogTotal: "카드풀", collectedTypes: "내 보유", completion: "완성률", collectionSummary: "수집 요약",
      signInBannerTitle: "내 도감을 연결할까요?", signInNote: "Telegram으로 로그인하면 보유·미보유 카드가 표시됩니다.",
      signedIn: "{name}님의 포획 기록과 연결되었습니다.", mycardsLoginTitle: "내 카드는 로그인 후 볼 수 있습니다.",
      mycardsLoginBody: "Telegram 계정을 연결하면 봇에서 포획한 카드만 모아 보여줍니다.",
      loadingCards: "카드 불러오는 중", searchLabel: "카드 검색", searchPlaceholder: "카드명, 세트 또는 번호",
      grade: "등급", allGrades: "모든 등급", set: "세트", allSets: "모든 세트", ownership: "보유 여부",
      allCards: "전체", owned: "보유", missing: "미보유", archived: "아카이브", sort: "정렬",
      sortSet: "세트 순", sortName: "이름 순", sortGrade: "높은 등급 순", sortRecent: "최근 획득 순",
      reset: "초기화", loadingCollection: "도감을 불러오는 중입니다.", loadMore: "더 보기",
      loginTitle: "Telegram으로 내 도감 연결", loginDescription: "봇에서 사용한 Telegram 계정으로 로그인하면 포획 카드와 완성률을 불러옵니다.",
      continueTelegram: "Telegram으로 계속", browseWithoutLogin: "로그인 없이 공개 도감 보기",
      loginNoteOneTitle: "보유 여부만 연결", loginNoteOneBody: "가격·자산·지갑 잔액은 도감에 표시하지 않습니다.",
      loginNoteTwoTitle: "안전한 OIDC 로그인", loginNoteTwoBody: "비밀번호나 Telegram 인증 코드를 TGPoke에 입력하지 않습니다.",
      authFailed: "Telegram 로그인에 실패했습니다. 다시 시도해 주세요.", authSuccess: "Telegram 계정이 연결되었습니다.",
      archiveProgress: "이번 주 Lucky Catch", leaderboardDescription: "이번 주 공개 포획 당첨 횟수만 표시하며 매주 KST에 새로 시작합니다.",
      loadingLeaderboard: "리더보드를 불러오는 중입니다.", leaderboardPrivacy: "동률은 같은 순위입니다. 가격, 자산, 도감 완성도와 Daily Pick 기록은 합산하지 않습니다.",
      botCommands: "Renaiss 봇에서 사용", commandsTitle: "명령어", commandsDescription: "게임 시작은 한 글자면 충분합니다. 누르면 명령어가 복사됩니다.",
      commandCatch: "공개 포획 참여", commandCatchHelp: "카드가 나타났을 때 랜덤 추첨과 가격 추측에 참여합니다.",
      commandCards: "내 컬렉션", commandCardsHelp: "등급과 세트별 보유 카드를 확인합니다.", commandPrice: "참고 가격",
      commandPriceHelp: "검증된 Renaiss 참고 가격과 출처를 확인합니다.", commandFlex: "포획 자랑하기", commandFlexHelp: "운 좋게 잡은 카드를 그룹에 공유합니다.",
      copy: "복사", copied: "복사됨", firstRound: "첫 라운드", guideOneTitle: "카드가 나타나면 c 입력",
      guideOneBody: "누적 자산과 무관한 랜덤 포획 추첨에 참여합니다.", guideTwoTitle: "공개 전에 가격 추측",
      guideTwoBody: "FMV가 숨겨진 상태에서 가격 범위를 고르고 안목을 시험합니다.", guideThreeTitle: "Renaiss 근거와 함께 결과 확인",
      guideThreeBody: "검증된 가격, 출처와 신뢰 상태를 그룹에서 함께 봅니다.", noTradingTitle: "투자·거래 게임이 아닙니다.",
      noTradingBody: "현금, 매수·매도, 수익 보장 또는 실물 카드 소유권을 제공하지 않습니다.",
      collaborationNotice: "TGPoke가 운영하는 Renaiss 콜라보 프로젝트이며 Renaiss 공식 홈페이지가 아닙니다.",
      progressDetail: "{owned} / {total}종", types: "{n}종", resultCount: "{total}종 중 {shown}종 표시",
      cardOwned: "보유 ×{n}", cardMissing: "미보유", cardPublic: "공개 도감", cardArchived: "아카이브", noSet: "세트 정보 없음",
      noResults: "조건에 맞는 카드가 없습니다.", catalogPreparing: "Renaiss 카드 풀을 준비 중입니다.",
      loadFailed: "데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.", noLeaders: "이번 주 공개 포획 당첨 기록이 아직 없습니다.",
      luckyCatches: "공개 포획 당첨", you: "나"
    },
    en: {
      skip: "Skip to content", tgpokeHome: "TGPoke home", tgpokeNavigation: "TGPoke navigation",
      navHome: "Home", navMyCards: "My cards", navDex: "Collection", navAttendance: "Attendance", navTiers: "Tiers",
      languageSelection: "Language selection", openMenu: "Open menu", closeMenu: "Close menu", renaissNavigation: "Renaiss navigation",
      telegramLogin: "Sign in", logout: "Sign out", loginUnavailable: "Login setup pending", openGroup: "Start bot", openGameRoom: "Telegram game room",
      renaissDex: "Collection", renaissMyCards: "My cards", leaderboardTitle: "Leaderboard", guideTitle: "Guide",
      collabProject: "TGPoke × Renaiss collaboration", dexTitle: "Renaiss card collection", dexDescription: "Browse the collaboration card pool and your public catches in one place.",
      mycardsTitle: "My Renaiss cards", mycardsDescription: "Review cards caught through the Telegram bot and your completion.",
      catalogTotal: "Card pool", collectedTypes: "Collected", completion: "Completion", collectionSummary: "Collection summary",
      signInBannerTitle: "Connect your collection", signInNote: "Sign in with Telegram to mark cards as owned or missing.",
      signedIn: "Connected to {name}'s catch record.", mycardsLoginTitle: "Sign in to see your cards.",
      mycardsLoginBody: "Connect Telegram to show only the cards you caught through the bot.",
      loadingCards: "Loading cards", searchLabel: "Search cards", searchPlaceholder: "Card, set, or number",
      grade: "Grade", allGrades: "All grades", set: "Set", allSets: "All sets", ownership: "Ownership",
      allCards: "All", owned: "Owned", missing: "Missing", archived: "Archived", sort: "Sort",
      sortSet: "Set order", sortName: "Name", sortGrade: "Highest grade", sortRecent: "Recently collected",
      reset: "Reset", loadingCollection: "Loading collection.", loadMore: "Load more",
      loginTitle: "Connect your collection with Telegram", loginDescription: "Sign in with the Telegram account used in the bot to load catches and completion.",
      continueTelegram: "Continue with Telegram", browseWithoutLogin: "Browse the public collection without signing in",
      loginNoteOneTitle: "Ownership only", loginNoteOneBody: "Prices, assets, and wallet balances never appear in the collection.",
      loginNoteTwoTitle: "Secure OIDC sign-in", loginNoteTwoBody: "You never enter a password or Telegram verification code on TGPoke.",
      authFailed: "Telegram sign-in failed. Please try again.", authSuccess: "Telegram account connected.",
      archiveProgress: "This week's Lucky Catch", leaderboardDescription: "Only public catch wins from this week are shown. The board restarts weekly in KST.",
      loadingLeaderboard: "Loading leaderboard.", leaderboardPrivacy: "Ties share a rank. Prices, assets, completion, and Daily Pick records are never combined.",
      botCommands: "Use these in the Renaiss bot", commandsTitle: "Commands", commandsDescription: "One letter starts the game. Tap a row to copy the command.",
      commandCatch: "Join a public catch", commandCatchHelp: "Join the random draw and hidden-price guess when a card appears.",
      commandCards: "My collection", commandCardsHelp: "View cards by grade and set.", commandPrice: "Reference price",
      commandPriceHelp: "View a verified Renaiss reference value and its sources.", commandFlex: "Share a lucky catch", commandFlexHelp: "Share a lucky card with the group.",
      copy: "Copy", copied: "Copied", firstRound: "Your first round", guideOneTitle: "Type c when a card appears",
      guideOneBody: "Join a random catch that does not depend on accumulated wealth.", guideTwoTitle: "Guess before the reveal",
      guideTwoBody: "Choose a price range while FMV is hidden and test your insight.", guideThreeTitle: "See the result with Renaiss evidence",
      guideThreeBody: "Review the verified value, sources, and confidence together in the group.", noTradingTitle: "This is not an investment or trading game.",
      noTradingBody: "There is no cash, buying, selling, guaranteed return, or ownership of a physical card.",
      collaborationNotice: "Operated by TGPoke for a Renaiss collaboration project. This is not the official Renaiss website.",
      progressDetail: "{owned} / {total} types", types: "{n} types", resultCount: "Showing {shown} of {total} types",
      cardOwned: "Owned ×{n}", cardMissing: "Missing", cardPublic: "Public collection", cardArchived: "Archived", noSet: "No set data",
      noResults: "No cards match these filters.", catalogPreparing: "The Renaiss card pool is being prepared.",
      loadFailed: "Could not load data. Please try again shortly.", noLeaders: "No public catch wins yet this week.",
      luckyCatches: "Public catch wins", you: "You"
    }
  };

  var state = {
    locale: "ko", page: "pokedex", config: null, user: null, pageNumber: 1,
    cards: [], total: 0, hasMore: false, loading: false, leaderboardLoaded: false,
    summaryData: null, authStatus: null
  };
  var searchTimer;

  function t(key, values) {
    var text = (messages[state.locale] && messages[state.locale][key]) || messages.ko[key] || key;
    Object.keys(values || {}).forEach(function (name) { text = text.replace("{" + name + "}", String(values[name])); });
    return text;
  }

  function escapeHTML(value) {
    return String(value == null ? "" : value).replace(/[&<>'"]/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character];
    });
  }

  function number(value) {
    return new Intl.NumberFormat(state.locale === "ko" ? "ko-KR" : "en-US").format(Number(value || 0));
  }

  function safeImage(value) {
    try {
      var url = new URL(String(value || ""));
      return url.protocol === "https:" ? url.href : "";
    } catch (error) { return ""; }
  }

  function pageFromLocation() {
    var mapping = {
      "/renaiss": "pokedex", "/renaiss/": "pokedex", "/renaiss/pokedex": "pokedex",
      "/renaiss/collection": "pokedex", "/renaiss/mycards": "mycards",
      "/renaiss/leaderboard": "leaderboard", "/renaiss/guide": "guide", "/renaiss/commands": "guide",
      "/renaiss/login": "login"
    };
    if (location.pathname === "/renaiss" && location.hash === "#leaderboard") return "leaderboard";
    if (location.pathname === "/renaiss" && ["#commands", "#guide"].indexOf(location.hash) >= 0) return "guide";
    return mapping[location.pathname.replace(/\/$/, "")] || "pokedex";
  }

  function localizedPath(path, extra) {
    var params = new URLSearchParams(extra || {});
    if (state.locale === "en") params.set("lang", "en");
    var query = params.toString();
    return path + (query ? "?" + query : "");
  }

  function pageTitle() {
    var titles = {
      pokedex: state.locale === "ko" ? "Renaiss 카드 도감 | TGPoke" : "Renaiss Card Collection | TGPoke",
      mycards: state.locale === "ko" ? "내 Renaiss 카드 | TGPoke" : "My Renaiss Cards | TGPoke",
      leaderboard: state.locale === "ko" ? "Renaiss 리더보드 | TGPoke" : "Renaiss Leaderboard | TGPoke",
      guide: state.locale === "ko" ? "Renaiss 가이드 | TGPoke" : "Renaiss Guide | TGPoke",
      login: state.locale === "ko" ? "Renaiss Telegram 로그인 | TGPoke" : "Renaiss Telegram Sign-in | TGPoke"
    };
    return titles[state.page];
  }

  function applyLocale(locale, updateURL) {
    state.locale = locale === "en" ? "en" : "ko";
    document.documentElement.lang = state.locale;
    document.querySelectorAll("[data-i18n]").forEach(function (element) { element.textContent = t(element.dataset.i18n); });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (element) { element.placeholder = t(element.dataset.i18nPlaceholder); });
    document.querySelectorAll("[data-i18n-aria]").forEach(function (element) { element.setAttribute("aria-label", t(element.dataset.i18nAria)); });
    document.querySelectorAll("[data-locale]").forEach(function (button) { button.setAttribute("aria-pressed", String(button.dataset.locale === state.locale)); });
    document.querySelectorAll("[data-route]").forEach(function (link) { link.href = localizedPath("/renaiss/" + link.dataset.route); });
    document.querySelectorAll("[data-login-page]").forEach(function (link) { link.href = localizedPath("/renaiss/login"); });
    document.querySelectorAll("[data-public-dex]").forEach(function (link) { link.href = localizedPath("/renaiss/pokedex"); });
    document.title = pageTitle();
    localStorage.setItem("renaiss_locale", state.locale);
    if (updateURL) {
      var params = new URLSearchParams(location.search);
      if (state.locale === "en") params.set("lang", "en"); else params.delete("lang");
      history.replaceState(null, "", location.pathname + (params.toString() ? "?" + params : "") + location.hash);
    }
    renderPageLabels();
    renderAuth();
    if (state.summaryData) renderSummary(state.summaryData, Boolean(state.summaryData.authenticated));
    if (state.cards.length) renderCards();
    if (state.leaderboardLoaded) loadLeaderboard(true);
  }

  function showPage() {
    document.querySelectorAll("[data-page-section]").forEach(function (section) {
      section.hidden = section.dataset.pageSection.split(",").indexOf(state.page) < 0;
    });
    document.querySelectorAll("[data-route]").forEach(function (link) {
      var active = link.dataset.route === state.page;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
    renderPageLabels();
    updateCollectionAccess();
  }

  function renderPageLabels() {
    var title = document.getElementById("collectionTitle");
    var description = document.getElementById("collectionDescription");
    var heading = document.getElementById("collectionHeading");
    if (!title || !description || !heading) return;
    var personal = state.page === "mycards";
    title.textContent = t(personal ? "mycardsTitle" : "dexTitle");
    description.textContent = t(personal ? "mycardsDescription" : "dexDescription");
    heading.textContent = t(personal ? "renaissMyCards" : "renaissDex");
    document.querySelector(".ownership-field").hidden = personal;
  }

  function updateCollectionAccess() {
    var publicBanner = document.getElementById("publicLoginBanner");
    var authWall = document.getElementById("mycardsAuthWall");
    var collection = document.getElementById("collectionSection");
    if (!publicBanner || !authWall || !collection) return;
    publicBanner.hidden = state.page !== "pokedex" || Boolean(state.user);
    authWall.hidden = state.page !== "mycards" || Boolean(state.user);
    collection.hidden = state.page === "mycards" && !state.user;
  }

  function closeMobileMenu() {
    var menu = document.getElementById("mobileMenu");
    var button = document.getElementById("menuButton");
    menu.classList.remove("is-open");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", t("openMenu"));
  }

  function renderAuth() {
    var available = Boolean(state.config && state.config.telegram_login_available);
    document.querySelectorAll("[data-auth-action]").forEach(function (button) {
      if (state.user) {
        button.disabled = false;
        button.textContent = (state.user.display_name || "Collector") + " · " + t("logout");
        button.classList.remove("btn-connect");
        button.classList.add("btn-logout");
      } else {
        button.disabled = false;
        button.textContent = available ? t("telegramLogin") : t("loginUnavailable");
        button.classList.remove("btn-logout");
        button.classList.add("btn-connect");
      }
    });
    var start = document.getElementById("telegramStartButton");
    if (start) {
      start.disabled = !state.user && !available;
      start.textContent = state.user ? t("renaissMyCards") : (available ? t("continueTelegram") : t("loginUnavailable"));
    }
    var note = document.getElementById("sessionNote");
    if (note) {
      note.textContent = state.user ? t("signedIn", { name: state.user.display_name || "Collector" }) : t("signInNote");
      note.classList.toggle("is-authenticated", Boolean(state.user));
    }
    var owned = document.getElementById("ownedFilter");
    if (owned) {
      Array.prototype.forEach.call(owned.options, function (option) {
        option.disabled = !state.user && ["owned", "archived"].indexOf(option.value) >= 0;
      });
    }
    var authMessage = document.getElementById("authMessage");
    if (authMessage) {
      authMessage.hidden = !state.authStatus && !state.user;
      authMessage.classList.toggle("is-success", state.authStatus === "success" || Boolean(state.user));
      authMessage.textContent = state.authStatus === "failed" ? t("authFailed") : ((state.authStatus === "success" || state.user) ? t("authSuccess") : "");
    }
    updateCollectionAccess();
  }

  async function loadConfigAndAuth() {
    try {
      var values = await Promise.all([
        fetch(API + "/config", { credentials: "same-origin", cache: "no-store" }).then(function (response) { return response.json(); }),
        fetch(API + "/auth/me", { credentials: "same-origin", cache: "no-store" }).then(function (response) { return response.json(); })
      ]);
      state.config = values[0];
      state.user = values[1] && values[1].ok ? values[1].user : null;
    } catch (error) {
      state.config = null;
      state.user = null;
    }
    document.querySelectorAll("[data-group-link]").forEach(function (link) {
      if (state.config && state.config.telegram_group_url) {
        link.href = state.config.telegram_group_url;
        link.hidden = false;
      }
    });
    renderAuth();
  }

  async function authenticate() {
    if (state.user) {
      location.href = localizedPath("/renaiss/mycards");
      return;
    }
    if (!state.config || !state.config.telegram_login_available) return;
    if (state.config.preview) {
      try {
        var response = await fetch(state.config.telegram_login_url, { method: "POST", credentials: "same-origin" });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error("preview auth failed");
        location.href = localizedPath("/renaiss/mycards", { auth: "success" });
      } catch (error) {
        state.authStatus = "failed";
        renderAuth();
      }
      return;
    }
    location.href = state.config.telegram_login_url;
  }

  async function logout() {
    try { await fetch(API + "/auth/logout", { method: "POST", credentials: "same-origin" }); } catch (error) {}
    state.user = null;
    state.authStatus = null;
    state.summaryData = null;
    state.cards = [];
    document.getElementById("ownedValue").textContent = "—";
    document.getElementById("completionValue").textContent = "—";
    renderAuth();
    if (state.page === "pokedex") await loadCollection(true);
  }

  function filters() {
    return {
      q: document.getElementById("searchInput").value.trim(),
      grade: document.getElementById("gradeFilter").value,
      set: document.getElementById("setFilter").value,
      owned: state.page === "mycards" ? "mine" : document.getElementById("ownedFilter").value,
      sort: document.getElementById("sortFilter").value
    };
  }

  function query() {
    var params = new URLSearchParams(filters());
    params.set("page", String(state.pageNumber));
    params.set("per_page", "12");
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
    var total = Number(summary.catalog_total || 0);
    var owned = Number(summary.owned_in_catalog || 0);
    var completion = authenticated ? Number(summary.completion_pct || 0) : 0;
    document.getElementById("catalogTotalValue").textContent = t("types", { n: number(total) });
    document.getElementById("ownedValue").textContent = authenticated ? t("types", { n: number(owned) }) : "—";
    document.getElementById("completionValue").textContent = authenticated ? completion.toFixed(1) + "%" : "—";
    setOptions(document.getElementById("gradeFilter"), (data.grades || []).map(function (value) { return { value: value }; }), "value", function (row) { return row.value; }, t("allGrades"));
    setOptions(document.getElementById("setFilter"), data.sets || [], "set_code", function (row) { return row.set_name ? row.set_name + " · " + row.set_code : row.set_code; }, t("allSets"));
    renderAuth();
  }

  function cardMarkup(card) {
    var owned = Boolean(card.owned);
    var archived = card.source_kind === "archived";
    var image = safeImage(card.image_url);
    var visualState = !state.user ? "is-public" : (owned ? "is-owned" : "is-missing");
    var status = archived ? t("cardArchived") : (!state.user ? t("cardPublic") : (owned ? t("cardOwned", { n: number(card.quantity) }) : t("cardMissing")));
    var meta = [card.grade, card.set_name || card.set_code, card.collector_number].filter(Boolean).join(" · ") || t("noSet");
    return '<article class="card-tile ' + visualState + (archived ? " is-archived" : "") + '">' +
      '<div class="card-art' + (image ? "" : " is-broken") + '">' +
      (image ? '<img src="' + escapeHTML(image) + '" alt="' + escapeHTML(card.card_name) + '" loading="lazy" decoding="async">' : "") +
      '<span class="card-fallback" aria-hidden="true">' + escapeHTML(String(card.card_name || "R").charAt(0)) + "</span></div>" +
      '<div class="card-body"><h2>' + escapeHTML(card.card_name || "Renaiss card") + '</h2><p class="card-meta">' + escapeHTML(meta) +
      '</p><div class="card-state"><span>' + escapeHTML(card.language || "") + "</span><strong>" + escapeHTML(status) + "</strong></div></div></article>";
  }

  function renderCards() {
    var grid = document.getElementById("cardGrid");
    grid.setAttribute("aria-busy", "false");
    grid.innerHTML = state.cards.length ? state.cards.map(cardMarkup).join("") : '<p class="state-message">' + escapeHTML(t("noResults")) + "</p>";
    grid.querySelectorAll("img").forEach(function (image) {
      image.addEventListener("error", function () { image.parentElement.classList.add("is-broken"); }, { once: true });
    });
    document.getElementById("resultCount").textContent = t("resultCount", { total: number(state.total), shown: number(state.cards.length) });
    document.getElementById("loadMoreWrap").hidden = !state.hasMore;
  }

  async function loadCollection(reset) {
    if (state.loading || ["pokedex", "mycards"].indexOf(state.page) < 0) return;
    if (reset) {
      state.pageNumber = 1;
      state.cards = [];
      document.getElementById("cardGrid").innerHTML = '<p class="state-message">' + escapeHTML(t("loadingCollection")) + "</p>";
    } else state.pageNumber += 1;
    state.loading = true;
    try {
      var response = await fetch(API + "/collection?" + query(), { credentials: "same-origin", cache: "no-store" });
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error("collection unavailable");
      renderSummary(data, Boolean(data.authenticated));
      state.cards = reset ? (data.cards || []) : state.cards.concat(data.cards || []);
      state.total = Number(data.total_filtered || 0);
      state.hasMore = Boolean(data.has_more);
      if (!data.available) document.getElementById("cardGrid").innerHTML = '<p class="state-message">' + escapeHTML(t("catalogPreparing")) + "</p>";
      else renderCards();
    } catch (error) {
      document.getElementById("cardGrid").innerHTML = '<p class="state-message">' + escapeHTML(t("loadFailed")) + "</p>";
    } finally {
      state.loading = false;
      updateCollectionAccess();
    }
  }

  function leaderMarkup(row) {
    return '<article class="leader-row' + (row.is_me ? " is-me" : "") + '"><strong class="leader-rank">' + number(row.rank) +
      '</strong><div class="leader-name"><strong>' + escapeHTML(row.display_name) + (row.is_me ? " · " + escapeHTML(t("you")) : "") +
      '</strong><span>KST · weekly reset</span></div><div class="leader-wins"><strong>' + number(row.lucky_catches) +
      '</strong><span>' + escapeHTML(t("luckyCatches")) + "</span></div></article>";
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
    } catch (error) {
      list.innerHTML = '<p class="state-message">' + escapeHTML(t("loadFailed")) + "</p>";
    }
  }

  function copyCommand(button) {
    var value = button.dataset.copy || "";
    var task = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(value) : Promise.reject();
    task.catch(function () {
      var area = document.createElement("textarea");
      area.value = value;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    });
    button.classList.add("is-copied");
    button.querySelector("b").textContent = t("copied");
    setTimeout(function () {
      button.classList.remove("is-copied");
      button.querySelector("b").textContent = t("copy");
    }, 1200);
  }

  function resetFilters() {
    document.getElementById("searchInput").value = "";
    document.getElementById("gradeFilter").value = "all";
    document.getElementById("setFilter").value = "all";
    document.getElementById("ownedFilter").value = "all";
    document.getElementById("sortFilter").value = "set";
    loadCollection(true);
  }

  function bind() {
    document.querySelectorAll("[data-locale]").forEach(function (button) {
      button.addEventListener("click", function () { applyLocale(button.dataset.locale, true); });
    });
    document.querySelectorAll("[data-auth-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        closeMobileMenu();
        if (state.user) logout(); else location.href = localizedPath("/renaiss/login");
      });
    });
    document.querySelectorAll("[data-login-start]").forEach(function (button) { button.addEventListener("click", authenticate); });
    document.getElementById("searchInput").addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () { loadCollection(true); }, 260);
    });
    ["gradeFilter", "setFilter", "ownedFilter", "sortFilter"].forEach(function (id) {
      document.getElementById(id).addEventListener("change", function () { loadCollection(true); });
    });
    document.getElementById("resetFilters").addEventListener("click", resetFilters);
    document.getElementById("loadMoreButton").addEventListener("click", function () { loadCollection(false); });
    document.querySelectorAll("[data-copy]").forEach(function (button) { button.addEventListener("click", function () { copyCommand(button); }); });

    var menuButton = document.getElementById("menuButton");
    var mobileMenu = document.getElementById("mobileMenu");
    menuButton.addEventListener("click", function () {
      var open = !mobileMenu.classList.contains("is-open");
      mobileMenu.classList.toggle("is-open", open);
      menuButton.setAttribute("aria-expanded", String(open));
      menuButton.setAttribute("aria-label", t(open ? "closeMenu" : "openMenu"));
    });
    mobileMenu.querySelectorAll("a").forEach(function (link) { link.addEventListener("click", closeMobileMenu); });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeMobileMenu(); });
  }

  async function init() {
    state.page = pageFromLocation();
    var params = new URLSearchParams(location.search);
    state.locale = params.has("lang") ? (params.get("lang") === "en" ? "en" : "ko") : (localStorage.getItem("renaiss_locale") === "en" ? "en" : "ko");
    state.authStatus = params.get("auth");
    if (state.authStatus) {
      params.delete("auth");
      history.replaceState(null, "", location.pathname + (params.toString() ? "?" + params : "") + location.hash);
    }
    applyLocale(state.locale, false);
    bind();
    showPage();
    await loadConfigAndAuth();
    if (["pokedex", "mycards"].indexOf(state.page) >= 0) await loadCollection(true);
    if (state.page === "leaderboard") await loadLeaderboard(false);
  }

  init();
}());
