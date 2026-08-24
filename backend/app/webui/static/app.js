const app = document.getElementById('app');
const toastElement = document.getElementById('toast');
const TOKEN_KEY = 'creative_studio_access_token';
const ACCOUNT_CACHE_TTL_MS = 5 * 60 * 1000;

const state = {
  route: window.location.pathname,
  token: window.sessionStorage.getItem(TOKEN_KEY),
  user: null,
  balance: null,
  accountSummaryLoaded: false,
  accountSummaryLoadedAt: 0,
  accountSummaryRefreshPromise: null,
  isAdmin: false,
  adminProviders: [],
  adminProbeCompleted: false,
  previewUrls: [],
  imageSessionEntries: [],
  imageHistoryHydrated: false,
  imageHistoryLoading: false,
  referenceMediaEntries: [],
  imageReferenceLimit: 3,
  maskMediaEntry: null,
  referenceMediaLoading: false,
  referenceMediaHydrated: false,
  canvasPreviewUrls: [],
  canvasPreviewRefreshTimer: null,
  canvasListCache: null,
  canvasListCacheAt: 0,
  navigationEpoch: 0,
  loadingRoute: '',
  loadingEpoch: 0,
};

function escapeHTML(value = '') {
  return String(value).replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);
}

function toast(message) {
  toastElement.textContent = message;
  toastElement.classList.add('show');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => toastElement.classList.remove('show'), 2800);
}

function setToken(token) {
  const previousToken = state.token;
  if (!token && previousToken) {
    state.token = previousToken;
    clearAccountSummaryCache();
  }
  state.token = token;
  if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
  else {
    window.sessionStorage.removeItem(TOKEN_KEY);
  }
}

function accountSummaryCacheKey() {
  // Cache entries are partitioned by a short token fingerprint. The token
  // itself is never copied to localStorage, so another account in the same
  // browser cannot immediately reuse this account's cached balance.
  let hash = 2166136261;
  for (const character of String(state.token || '')) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
  return `creative_studio_account_summary:${hash.toString(16)}`;
}

function clearAccountSummaryCache() {
  if (!state.token) return;
  try { window.localStorage.removeItem(accountSummaryCacheKey()); } catch (_error) { /* storage may be disabled */ }
}

function hydrateAccountSummaryCache() {
  if (!state.token || state.user || state.balance) return false;
  try {
    const cached = JSON.parse(window.localStorage.getItem(accountSummaryCacheKey()) || 'null');
    const savedAt = Number(cached?.savedAt || 0);
    if (!cached?.user || !cached?.balance || !savedAt || Date.now() - savedAt > ACCOUNT_CACHE_TTL_MS) {
      window.localStorage.removeItem(accountSummaryCacheKey());
      return false;
    }
    state.user = cached.user;
    state.balance = cached.balance;
    state.isAdmin = Boolean(cached.isAdmin);
    state.adminProviders = Array.isArray(cached.adminProviders) ? cached.adminProviders : [];
    state.adminProbeCompleted = Boolean(cached.adminProbeCompleted);
    state.accountSummaryLoaded = true;
    state.accountSummaryLoadedAt = savedAt;
    return true;
  } catch (_error) {
    return false;
  }
}

function persistAccountSummaryCache() {
  if (!state.token || !state.user || !state.balance) return;
  try {
    window.localStorage.setItem(accountSummaryCacheKey(), JSON.stringify({
      savedAt: state.accountSummaryLoadedAt || Date.now(),
      user: state.user,
      balance: state.balance,
      isAdmin: state.isAdmin,
      adminProviders: state.adminProviders,
      adminProbeCompleted: state.adminProbeCompleted,
    }));
  } catch (_error) { /* storage may be disabled or full */ }
}

function errorMessage(payload, status) {
  if (typeof payload?.detail === 'string') return payload.detail;
  if (Array.isArray(payload?.detail)) return payload.detail.map(item => item.msg).join('；');
  return `请求失败 (${status})`;
}

async function api(path, options = {}) {
  const { timeoutMs = 15_000, ...fetchOptions } = options;
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
  const hasFormDataBody = typeof FormData !== 'undefined' && options.body instanceof FormData;
  if (hasFormDataBody) headers.delete('Content-Type');
  else if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const controller = new AbortController();
  const externalSignal = fetchOptions.signal;
  let timeoutHandle;
  let removeExternalAbortListener = null;
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort(externalSignal.reason);
    else {
      const abortFromCaller = () => controller.abort(externalSignal.reason);
      externalSignal.addEventListener('abort', abortFromCaller, { once: true });
      removeExternalAbortListener = () => externalSignal.removeEventListener('abort', abortFromCaller);
    }
  }
  timeoutHandle = window.setTimeout(() => controller.abort(), Math.max(1_000, Number(timeoutMs) || 15_000));
  try {
    const response = await window.fetch(path, { ...fetchOptions, headers, signal: controller.signal });
    const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401 && state.token) {
        setToken(null);
        state.user = null;
        state.accountSummaryLoaded = false;
        state.accountSummaryLoadedAt = 0;
      }
      const error = new Error(errorMessage(payload, response.status));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  } catch (error) {
    if (controller.signal.aborted && !externalSignal?.aborted) {
      const timeoutError = new Error(`请求超时（${Math.round(Math.max(1_000, Number(timeoutMs) || 15_000) / 1000)} 秒），请稍后重试`);
      timeoutError.status = 408;
      timeoutError.payload = { detail: timeoutError.message };
      throw timeoutError;
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutHandle);
    removeExternalAbortListener?.();
  }
}

function resetImageWorkspaceState() {
  clearImagePreviewUrls();
  state.imageSessionEntries = [];
  state.imageHistoryHydrated = false;
  state.referenceMediaEntries = [];
  state.referenceMediaHydrated = false;
  state.maskMediaEntry = null;
  state.canvasListCache = null;
  state.canvasListCacheAt = 0;
}

async function optionalApi(path, fallback) {
  try {
    return await api(path);
  } catch (error) {
    if (error.status === 404) return fallback;
    throw error;
  }
}

function navigate(path, { replace = false } = {}) {
  if (replace) window.history.replaceState({}, '', path);
  else window.history.pushState({}, '', path);
  state.route = path;
  render();
}

function formatCredits(value = '0.00') {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : '0.00';
}

function formatBytes(value = 0) {
  const bytes = Math.max(Number(value) || 0, 0);
  if (bytes < 1000) return `${Math.round(bytes)} B`;
  const units = ['KB', 'MB', 'GB', 'TB', 'PB', 'EB'];
  let amount = bytes;
  let index = -1;
  do {
    amount /= 1000;
    index += 1;
  } while (amount >= 1000 && index < units.length - 1);
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: amount >= 100 ? 0 : 2 }).format(amount)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(parsed);
}

function initials(email = '') {
  return (email.trim()[0] || 'U').toUpperCase();
}

function authView(mode) {
  const registering = mode === 'register';
  app.innerHTML = `<main class="auth-shell">
    <section class="auth-brand">
      <div class="brand"><div class="brand-symbol">∞</div><div><strong>乐云工坊</strong><span>专业 AI 创作工作台</span></div></div>
      <div class="auth-message"><span class="eyebrow" style="color:#8ed5ae">Leyun Studio</span><h1>把灵感，变成可以继续创作的作品。</h1><p>一个账户管理创作空间、个人资产、存储额度与钱包账务。</p></div>
    </section>
    <section class="auth-form-wrap">
      <form class="auth-form" id="auth-form">
        <span class="eyebrow">${registering ? '创建账户' : '欢迎回来'}</span>
        <h2>${registering ? '开始新的创作空间' : '登录创作工作台'}</h2>
        <p>${registering ? '注册后会建立一个零消费额度的个人账户空间。' : '继续管理您的账户、钱包和创作资料。'}</p>
        <div class="field"><label for="email">邮箱地址</label><input id="email" name="email" type="email" autocomplete="email" required placeholder="name@example.com"></div>
        <div class="field"><label for="password">访问密码</label><input id="password" name="password" type="password" minlength="12" autocomplete="${registering ? 'new-password' : 'current-password'}" required placeholder="至少 12 个字符"></div>
        <button class="primary-btn wide" type="submit">${registering ? '注册并进入账户中心' : '登录账户中心'}</button>
        ${registering ? '' : '<div class="auth-switch"><button class="text-btn" type="button" id="forgot-password">忘记密码？</button></div>'}
        <div class="auth-switch">${registering ? '已经有账户？' : '还没有账户？'} <button class="text-btn" type="button" id="auth-switch">${registering ? '返回登录' : '立即注册'}</button></div>
      </form>
    </section>
  </main>`;

  document.getElementById('auth-switch').addEventListener('click', () => navigate(registering ? '/login' : '/register'));
  document.getElementById('forgot-password')?.addEventListener('click', () => navigate('/forgot-password'));
  document.getElementById('auth-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const credentials = { email: form.get('email'), password: form.get('password') };
    const submit = event.currentTarget.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      if (registering) await api('/api/v1/auth/register', { method: 'POST', body: JSON.stringify(credentials) });
      const session = await api('/api/v1/auth/login', { method: 'POST', body: JSON.stringify(credentials) });
      setToken(session.access_token);
      state.user = null;
      state.balance = null;
      state.accountSummaryLoaded = false;
      state.accountSummaryLoadedAt = 0;
      state.isAdmin = false;
      state.adminProviders = [];
      state.adminProbeCompleted = false;
      resetImageWorkspaceState();
      navigate('/workspace/account', { replace: true });
    } catch (error) {
      toast(error.message);
      submit.disabled = false;
    }
  });
}

function navigationItem(label, path, symbol) {
  const active = state.route === path ? ' active' : '';
  return `<button class="nav-item${active}" data-route="${path}"><span class="nav-icon">${symbol}</span><span>${label}</span></button>`;
}

function shell(title, content, pageClass = '') {
  // Async page loaders can finish after the user has already navigated away.
  // Reject their late shell writes by associating stable page titles with
  // routes; otherwise an old “账户” response can overwrite the new image
  // workbench after navigation.
  const titleRoutes = {
    '个人账户': '/workspace/account',
    '钱包': '/workspace/wallet',
    '图片生成': '/workspace/images',
    '局部重绘': '/workspace/inpainting',
    '无限画布': '/workspace/canvases',
    'LLM 设置': '/workspace/llm-settings',
    '提示词库': '/workspace/assets',
    '素材库管理': '/workspace/assets',
    '资产库': '/workspace/assets',
    '生成任务': '/workspace/generations',
    '模型目录': '/workspace/models',
    'Provider 成本': '/admin/provider-costs',
    '模型价格': '/admin/model-routing',
    '充值包': '/admin/recharge-packages',
    '兑换码': '/admin/redeem-codes',
    '任务管理': '/admin/generation-tasks',
    '用户管理': '/admin/users',
    '存储额度': '/admin/storage-allowance',
    '生成容量': '/admin/generation-capacity',
    '支付设置': '/admin/payment-settings',
    '邮件设置': '/admin/email-settings',
    '公告与客服': '/admin/platform-content',
    'RunningHub 能力目录': '/admin/runninghub-capabilities',
    '模型路由': '/admin/model-routing',
  };
  const expectedRoute = titleRoutes[title];
  if (expectedRoute && expectedRoute !== state.route) return false;
  if (state.loadingRoute && (
    state.loadingRoute !== state.route || state.loadingEpoch !== state.navigationEpoch
  )) return false;
  window.unmountAdminVue?.();
  const email = state.user?.email || '账户加载中';
  const currentBalance = formatCredits(state.balance?.available_credits);
  const availableStorage = state.user?.storage_allowance
    ? formatBytes(state.user.storage_allowance.available_bytes)
    : '—';
  app.innerHTML = `<div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-symbol">∞</div><div><strong>乐云工坊</strong><span>创作工作台</span></div></div>
      <div class="nav-label">账户中心</div>
      <nav class="nav" aria-label="账户中心导航">
        ${navigationItem('个人账户', '/workspace/account', '人')}
        ${navigationItem('钱包', '/workspace/wallet', '¥')}
        ${navigationItem('图片生成', '/workspace/images', '图')}
        ${navigationItem('局部重绘', '/workspace/inpainting', '绘')}
        ${navigationItem('无限画布', '/workspace/canvases', '∞')}
        ${navigationItem('LLM 设置', '/workspace/llm-settings', 'AI')}
        ${navigationItem('资产库', '/workspace/assets', '资')}
        ${navigationItem('生成任务', '/workspace/generations', '生')}
        ${navigationItem('模型目录', '/workspace/models', '模')}
        ${state.isAdmin ? navigationItem('RunningHub 能力', '/admin/runninghub-capabilities', 'RH') : ''}
        ${state.isAdmin ? navigationItem('模型路由', '/admin/model-routing', '路') : ''}
        ${state.isAdmin ? navigationItem('Provider 成本', '/admin/provider-costs', '本') : ''}
        ${state.isAdmin ? navigationItem('充值包', '/admin/recharge-packages', '充') : ''}
        ${state.isAdmin ? navigationItem('兑换码', '/admin/redeem-codes', '码') : ''}
        ${state.isAdmin ? navigationItem('支付设置', '/admin/payment-settings', '付') : ''}
        ${state.isAdmin ? navigationItem('用户管理', '/admin/users', '户') : ''}
        ${state.isAdmin ? navigationItem('任务管理', '/admin/generation-tasks', '任') : ''}
        ${state.isAdmin ? navigationItem('存储额度', '/admin/storage-allowance', '存') : ''}
        ${state.isAdmin ? navigationItem('生成容量', '/admin/generation-capacity', '并') : ''}
        ${state.isAdmin ? navigationItem('公告与客服', '/admin/platform-content', '告') : ''}
        ${state.isAdmin ? navigationItem('邮件设置', '/admin/email-settings', '邮') : ''}
      </nav>
      <div class="sidebar-spacer"></div>
      <div class="sidebar-user"><div class="user-row"><div class="avatar">${escapeHTML(initials(email))}</div><div class="user-copy"><strong>${escapeHTML(email)}</strong><span>${currentBalance} 额度</span></div><button class="icon-btn" id="logout" title="退出登录" aria-label="退出登录">↪</button></div></div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><div class="topbar-title">${escapeHTML(title)}</div><div class="topbar-actions"><div class="balance-pill storage-pill">可用容量 <strong>${availableStorage}</strong></div><div class="balance-pill">可用额度 <strong>${currentBalance}</strong></div><button class="topbar-content-button" type="button" data-platform-content="announcement" title="公告" aria-label="公告">🔔</button><button class="topbar-content-button" type="button" data-platform-content="support" title="客服" aria-label="客服">🎧</button><button class="secondary-btn" data-route="/workspace/wallet">前往钱包</button></div></header>
      <main class="page${pageClass ? ` ${escapeHTML(pageClass)}` : ''}">${content}</main>
      <div class="platform-content-modal" data-platform-content-modal hidden><section role="dialog" aria-modal="true" aria-labelledby="platform-content-title"><header><div><span data-platform-content-symbol></span><strong id="platform-content-title"></strong></div><button type="button" data-platform-content-close aria-label="关闭">×</button></header><div data-platform-content-body></div></section></div>
    </div>
  </div>`;
  document.querySelectorAll('[data-route]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.route)));
  bindPlatformContentDialog();
  document.getElementById('logout').addEventListener('click', async () => {
    await api('/api/v1/auth/logout', { method: 'POST' }).catch(() => {});
    setToken(null);
    state.user = null;
    state.balance = null;
    state.accountSummaryLoaded = false;
    state.accountSummaryLoadedAt = 0;
    state.isAdmin = false;
    state.adminProviders = [];
    state.adminProbeCompleted = false;
    resetImageWorkspaceState();
    navigate('/login', { replace: true });
  });
  return true;
}

function loadingPage(title) {
  state.loadingRoute = state.route;
  state.loadingEpoch = state.navigationEpoch;
  shell(title, '<div class="loading">正在读取账户数据…</div>');
}

async function ensureAccountSummary() {
  hydrateAccountSummaryCache();
  const summaryAge = Date.now() - state.accountSummaryLoadedAt;
  if (state.accountSummaryLoaded && state.user && state.balance && summaryAge < ACCOUNT_CACHE_TTL_MS) {
    // The cached account shell is enough to render the next page immediately;
    // refresh quietly once it is older than one minute.
    if (summaryAge < 60_000) return;
    if (!state.accountSummaryRefreshPromise) {
      state.accountSummaryRefreshPromise = refreshAccountSummary()
        .catch(error => {
          if (error.status === 401 || !state.token) navigate('/login', { replace: true });
        })
        .finally(() => { state.accountSummaryRefreshPromise = null; });
    }
    return;
  }
  if (state.accountSummaryRefreshPromise) return state.accountSummaryRefreshPromise;
  state.accountSummaryRefreshPromise = refreshAccountSummary();
  try {
    await state.accountSummaryRefreshPromise;
  } finally {
    state.accountSummaryRefreshPromise = null;
  }
}

async function refreshAccountSummary() {
  const [userResult, balanceResult, providersResult] = await Promise.allSettled([
    api('/api/v1/auth/me'),
    api('/api/v1/credits/balance'),
    state.adminProbeCompleted ? Promise.resolve(state.adminProviders) : detectAdminProviders(),
  ]);
  if (userResult.status === 'rejected') throw userResult.reason;
  if (balanceResult.status === 'rejected') {
    if (state.user && state.balance) return;
    throw balanceResult.reason;
  }
  state.user = userResult.value;
  state.balance = balanceResult.value;
  if (providersResult.status === 'fulfilled') state.adminProviders = providersResult.value;
  state.accountSummaryLoaded = true;
  state.accountSummaryLoadedAt = Date.now();
  persistAccountSummaryCache();
}

function passwordResetRequestPage() {
  app.innerHTML = `<main class="auth-shell">
    <section class="auth-brand"><div class="brand"><div class="brand-symbol">∞</div><div><strong>乐云工坊</strong><span>专业 AI 创作工作台</span></div></div><div class="auth-message"><span class="eyebrow" style="color:#8ed5ae">Account recovery</span><h1>安全找回您的账户。</h1><p>为保护隐私，无论邮箱是否注册，页面都会显示相同结果。</p></div></section>
    <section class="auth-form-wrap"><form class="auth-form" id="password-reset-request-form"><span class="eyebrow">找回密码</span><h2>发送重置邮件</h2><p>输入注册邮箱。如果账户存在且邮件服务可用，您会收到一封 30 分钟内有效的邮件。</p><div class="field"><label for="reset-email">邮箱地址</label><input id="reset-email" name="email" type="email" autocomplete="email" required maxlength="320" placeholder="name@example.com"></div><button class="primary-btn wide" type="submit">发送重置邮件</button><div class="auth-switch"><button class="text-btn" type="button" id="reset-back-login">返回登录</button></div></form></section>
  </main>`;
  document.getElementById('reset-back-login').onclick = () => navigate('/login');
  document.getElementById('password-reset-request-form').onsubmit = async event => {
    event.preventDefault();
    const submit = event.currentTarget.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const form = new FormData(event.currentTarget);
      await api('/api/v1/auth/password-reset', { method: 'POST', body: JSON.stringify({ email: form.get('email') }) });
      event.currentTarget.innerHTML = '<span class="eyebrow">找回密码</span><h2>请求已受理</h2><p>如果该邮箱对应账户，重置邮件将发送到该地址。请检查收件箱和垃圾邮件目录。</p><button class="primary-btn wide" type="button" id="reset-request-complete">返回登录</button>';
      document.getElementById('reset-request-complete').onclick = () => navigate('/login');
    } catch (error) {
      toast(error.message);
      submit.disabled = false;
    }
  };
}

function resetPasswordPage() {
  const token = new URLSearchParams(window.location.hash.slice(1)).get('token') || '';
  window.history.replaceState({}, '', '/reset-password');
  app.innerHTML = `<main class="auth-shell">
    <section class="auth-brand"><div class="brand"><div class="brand-symbol">∞</div><div><strong>乐云工坊</strong><span>专业 AI 创作工作台</span></div></div><div class="auth-message"><span class="eyebrow" style="color:#8ed5ae">Password reset</span><h1>设置新的访问密码。</h1><p>链接只能使用一次；成功后所有设备上的登录会话都会失效。</p></div></section>
    <section class="auth-form-wrap"><form class="auth-form" id="reset-password-form"><span class="eyebrow">重置密码</span><h2>${token ? '设置新密码' : '重置链接无效'}</h2>${token ? '<p>新密码至少需要 12 个字符。</p><div class="field"><label for="reset-new-password">新密码</label><input id="reset-new-password" name="new_password" type="password" minlength="12" autocomplete="new-password" required></div><div class="field"><label for="reset-confirm-password">确认新密码</label><input id="reset-confirm-password" name="confirm_password" type="password" minlength="12" autocomplete="new-password" required></div><button class="primary-btn wide" type="submit">确认重置</button>' : '<p>链接缺少令牌，请从最新的密码重置邮件重新打开。</p><button class="primary-btn wide" type="button" id="invalid-reset-back">返回登录</button>'}</form></section>
  </main>`;
  if (!token) {
    document.getElementById('invalid-reset-back').onclick = () => navigate('/login');
    return;
  }
  document.getElementById('reset-password-form').onsubmit = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (form.get('new_password') !== form.get('confirm_password')) return toast('两次输入的新密码不一致');
    const submit = event.currentTarget.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      await api('/api/v1/auth/reset-password', { method: 'POST', body: JSON.stringify({ token, new_password: form.get('new_password') }) });
      setToken(null);
      state.user = null;
      event.currentTarget.innerHTML = '<span class="eyebrow">重置密码</span><h2>密码已更新</h2><p>所有旧登录会话均已失效，请使用新密码重新登录。</p><button class="primary-btn wide" type="button" id="reset-password-complete">返回登录</button>';
      document.getElementById('reset-password-complete').onclick = () => navigate('/login', { replace: true });
    } catch (error) {
      toast(error.message);
      submit.disabled = false;
    }
  };
}

async function authenticatedPlatformContentImage(url) {
  const headers = new Headers({ Authorization: `Bearer ${state.token}` });
  const response = await window.fetch(url, { headers });
  if (!response.ok) throw new Error('内容图片暂时无法加载');
  const objectUrl = window.URL.createObjectURL(await response.blob());
  state.previewUrls.push(objectUrl);
  return objectUrl;
}

function bindPlatformContentDialog() {
  const modal = document.querySelector('[data-platform-content-modal]');
  if (!modal) return;
  const close = () => { modal.hidden = true; };
  modal.querySelector('[data-platform-content-close]')?.addEventListener('click', close);
  modal.addEventListener('click', event => { if (event.target === modal) close(); });
  document.querySelectorAll('[data-platform-content]').forEach(button => button.addEventListener('click', async () => {
    const kind = button.dataset.platformContent;
    const isAnnouncement = kind === 'announcement';
    const title = isAnnouncement ? '平台公告' : '联系客服';
    const body = modal.querySelector('[data-platform-content-body]');
    modal.querySelector('#platform-content-title').textContent = title;
    modal.querySelector('[data-platform-content-symbol]').textContent = isAnnouncement ? '🔔' : '🎧';
    body.innerHTML = '<div class="platform-content-loading">正在加载…</div>';
    modal.hidden = false;
    try {
      const content = await api('/api/v1/platform-content');
      const textValue = isAnnouncement ? content.announcement_text : content.support_text;
      const imageUrl = isAnnouncement ? content.announcement_image_url : content.support_image_url;
      let imageHTML = '';
      if (imageUrl) imageHTML = `<img src="${escapeHTML(await authenticatedPlatformContentImage(imageUrl))}" alt="${title}图片">`;
      body.innerHTML = `${imageHTML}<div class="platform-content-text">${escapeHTML(textValue || (isAnnouncement ? '暂无公告' : '客服信息暂未配置')).replaceAll('\n', '<br>')}</div>`;
    } catch (error) {
      body.innerHTML = `<div class="platform-content-loading">${escapeHTML(error.message)}</div>`;
    }
  }));
}

async function verifyEmailPage() {
  const token = new URLSearchParams(window.location.hash.slice(1)).get('token') || '';
  window.history.replaceState({}, '', '/verify-email');
  app.innerHTML = `<main class="auth-shell">
    <section class="auth-brand"><div class="brand"><div class="brand-symbol">∞</div><div><strong>乐云工坊</strong><span>专业 AI 创作工作台</span></div></div><div class="auth-message"><span class="eyebrow" style="color:#8ed5ae">Email verification</span><h1>正在确认您的邮箱。</h1><p>验证链接只可使用一次，并会在 24 小时后失效。</p></div></section>
    <section class="auth-form-wrap"><div class="auth-form" id="verification-result"><span class="eyebrow">邮箱验证</span><h2>正在验证…</h2><p>请稍候。</p></div></section>
  </main>`;
  const result = document.getElementById('verification-result');
  if (!token) {
    result.innerHTML = '<span class="eyebrow">邮箱验证</span><h2>验证链接无效</h2><p>链接缺少验证令牌，请从最新验证邮件重新打开。</p><button class="primary-btn wide" id="verification-continue">返回登录</button>';
  } else {
    try {
      await api('/api/v1/auth/verify-email', { method: 'POST', body: JSON.stringify({ token }) });
      if (state.user) state.user.email_verified = true;
      result.innerHTML = '<span class="eyebrow">邮箱验证</span><h2>邮箱验证成功</h2><p>您的登录邮箱已经确认，可以继续使用账户中心。</p><button class="primary-btn wide" id="verification-continue">继续</button>';
    } catch (error) {
      result.innerHTML = `<span class="eyebrow">邮箱验证</span><h2>无法完成验证</h2><p>${escapeHTML(error.message)}</p><button class="primary-btn wide" id="verification-continue">返回登录</button>`;
    }
  }
  document.getElementById('verification-continue').onclick = () => navigate(state.token ? '/workspace/account' : '/login', { replace: true });
}

async function detectAdminProviders() {
  if (state.adminProbeCompleted) return state.adminProviders;
  try {
    const providers = await api('/api/v1/admin/providers', { timeoutMs: 5_000 });
    state.isAdmin = true;
    state.adminProbeCompleted = true;
    return providers;
  } catch (error) {
    if (error.status === 403) {
      state.isAdmin = false;
      state.adminProbeCompleted = true;
      return [];
    }
    if (error.status === 404) {
      try {
        await api('/api/v1/admin/runninghub-capabilities', { timeoutMs: 5_000 });
        state.isAdmin = true;
        state.adminProbeCompleted = true;
        return [];
      } catch (fallbackError) {
        if (fallbackError.status === 403 || fallbackError.status === 404) {
          state.isAdmin = false;
          state.adminProbeCompleted = true;
          return [];
        }
        throw fallbackError;
      }
    }
    throw error;
  }
}

async function accountPage() {
  loadingPage('个人账户');
  try {
    await ensureAccountSummary();
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    return shell('个人账户', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
  const user = state.user;
  const storage = user.storage_allowance || { limit_bytes: 0, used_bytes: 0, available_bytes: 0 };
  const limit = Math.max(Number(storage.limit_bytes) || 0, 0);
  const used = Math.max(Number(storage.used_bytes) || 0, 0);
  const percentage = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
  const verification = user.email_verified
    ? '<span class="badge">邮箱已验证</span>'
    : '<span class="badge warning">邮箱待验证</span>';
  shell('个人账户', `<div class="page-head"><div><h1>个人账户</h1><p>查看身份信息、账户归属与存储额度使用情况。</p></div></div>
    <section class="profile-grid">
      <article class="panel profile-card"><div class="avatar">${escapeHTML(initials(user.email))}</div><h2>${escapeHTML(user.email)}</h2><p>个人创作者账户</p>${verification}${user.email_verified ? '' : '<button class="secondary-btn profile-action" type="button" id="resend-verification">重新发送验证邮件</button>'}</article>
      <article class="panel"><div class="section-head" style="margin-top:0"><div><h2>账户信息</h2><p>这些标识用于隔离您的创作资料和账务数据。</p></div></div><dl class="detail-list">
        <div class="detail-row"><dt>登录邮箱</dt><dd>${escapeHTML(user.email)}</dd></div>
        <div class="detail-row"><dt>邮箱状态</dt><dd>${user.email_verified ? '已验证' : '待验证'}</dd></div>
        <div class="detail-row"><dt>用户标识</dt><dd class="mono">${escapeHTML(user.user_id)}</dd></div>
        <div class="detail-row"><dt>账户空间标识</dt><dd class="mono">${escapeHTML(user.account_space_id)}</dd></div>
      </dl></article>
    </section>
    <div class="section-head"><div><h2>账户安全</h2><p>修改密码后，所有设备上的登录会话都会立即失效。</p></div></div>
    <section class="panel security-panel"><form id="change-password-form" class="security-form">
      <div class="field"><label for="current-password">当前密码</label><input id="current-password" name="current_password" type="password" autocomplete="current-password" required></div>
      <div class="field"><label for="new-password">新密码</label><input id="new-password" name="new_password" type="password" minlength="12" autocomplete="new-password" required placeholder="至少 12 个字符"></div>
      <div class="field"><label for="confirm-password">确认新密码</label><input id="confirm-password" name="confirm_password" type="password" minlength="12" autocomplete="new-password" required></div>
      <button class="primary-btn" type="submit">修改密码</button>
    </form></section>
    <div class="section-head"><div><h2>存储空间</h2><p>仅持久媒体计入，账户内相同内容按哈希去重。</p></div></div>
    <section class="grid three">
      <article class="stat-card"><span>存储额度上限</span><strong>${formatBytes(storage.limit_bytes)}</strong><small>由平台管理员统一配置</small></article>
      <article class="stat-card"><span>持久媒体已用</span><strong>${formatBytes(storage.used_bytes)}</strong><small>临时、过期和已释放媒体不计入</small></article>
      <article class="stat-card"><span>存储额度剩余</span><strong>${formatBytes(storage.available_bytes)}</strong><div class="progress" aria-label="存储额度使用率"><span style="width:${percentage.toFixed(2)}%"></span></div></article>
    </section>
    <div class="section-head"><div><h2>消费额度</h2><p>消费额度与存储额度相互独立。</p></div><button class="text-btn" data-route="/workspace/wallet">查看钱包明细</button></div>
    <section class="grid two"><article class="stat-card"><span>可用额度</span><strong>${formatCredits(state.balance.available_credits)}</strong></article><article class="stat-card"><span>冻结额度</span><strong>${formatCredits(state.balance.frozen_credits)}</strong></article></section>`);
  const resend = document.getElementById('resend-verification');
  if (resend) resend.onclick = async () => {
    resend.disabled = true;
    try {
      await api('/api/v1/auth/email-verification', { method: 'POST' });
      toast('验证邮件已发送，请检查收件箱');
    } catch (error) {
      toast(error.message);
      resend.disabled = false;
    }
  };
  document.getElementById('change-password-form').onsubmit = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (form.get('new_password') !== form.get('confirm_password')) return toast('两次输入的新密码不一致');
    const submit = event.currentTarget.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      await api('/api/v1/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: form.get('current_password'), new_password: form.get('new_password') }),
      });
      setToken(null);
      state.user = null;
      state.balance = null;
      state.accountSummaryLoaded = false;
      toast('密码已修改，请使用新密码重新登录');
      navigate('/login', { replace: true });
    } catch (error) {
      toast(error.message);
      submit.disabled = false;
    }
  };
}

const orderStatus = { pending: '待支付', paid: '已到账', charged_back: '已拒付' };
const postingKind = { recharge: '充值', admin_grant: '人工充值', reversal: '冲销', freeze: '冻结', settlement: '结算', release: '释放' };

function paymentMethodsView(methods) {
  if (!methods.length) return '<div class="empty">暂未开放支付途径。平台配置后会显示在这里。</div>';
  return `<div class="method-list">${methods.map((method, index) => `<button class="method${index === 0 ? ' active' : ''}" data-provider="${escapeHTML(method.payment_provider)}"><span class="method-mark">${escapeHTML(method.display_name.slice(0, 1))}</span><span>${escapeHTML(method.display_name)}</span></button>`).join('')}</div>`;
}

function submitPaymentCheckout(checkout) {
  if (!checkout?.action_url || checkout.method !== 'POST' || !checkout.parameters) {
    throw new Error('支付网关未返回有效的收银台信息');
  }
  const form = document.createElement('form');
  form.action = checkout.action_url;
  form.method = 'POST';
  if (!(navigator.userAgent.includes('Safari') && !navigator.userAgent.includes('Chrome'))) form.target = '_blank';
  Object.entries(checkout.parameters).forEach(([name, value]) => {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = String(value);
    form.appendChild(input);
  });
  document.body.appendChild(form);
  form.submit();
  form.remove();
}

function packagesView(packages) {
  if (!packages.length) return '<div class="empty">当前没有可售特惠充值包。</div>';
  return `<div class="package-grid">${packages.map(item => `<article class="package-card"><h3>${escapeHTML(item.package_code)}</h3><div class="price">¥${escapeHTML(item.payment_cny)}</div><p>到账 ${formatCredits(item.credits)} 额度</p><button class="primary-btn" data-package="${escapeHTML(item.version_id)}">创建充值订单</button></article>`).join('')}</div>`;
}

function directRechargeView(rate) {
  const creditsPerCny = Number(rate?.credits_per_cny || 0);
  const presets = rate?.preset_payment_cny || ['1.00', '2.00', '5.00', '10.00', '100.00'];
  const preview = payment => Number.isFinite(creditsPerCny)
    ? (Number(payment) * creditsPerCny).toFixed(4).replace(/\.?0+$/, '')
    : '—';
  return `<section class="panel"><div class="section-head" style="margin-top:0"><div><h2>普通充值</h2><p>当前全局比例：<strong>1 元 = ${formatCredits(rate.credits_per_cny)} 额度</strong>。选择金额或输入自定义金额，支付成功后按页面显示的额度到账。</p></div></div>
    <div class="package-grid">${presets.map(payment => `<article class="package-card"><h3>充值 ${escapeHTML(Number(payment))} 元</h3><div class="price">¥${escapeHTML(payment)}</div><p>到账 ${escapeHTML(preview(payment))} 额度</p><button class="primary-btn" data-direct-recharge="${escapeHTML(payment)}">立即充值</button></article>`).join('')}
      <article class="package-card"><h3>自定义金额</h3><form id="direct-recharge-custom-form"><div class="field"><label>支付金额（元）</label><input name="payment_cny" type="number" min="0.01" max="1000000" step="0.01" value="20.00" required></div><p>预计到账 <strong data-direct-preview>${escapeHTML(preview('20.00'))}</strong> 额度</p><button class="primary-btn" type="submit">按此金额充值</button></form></article>
    </div></section>`;
}

function ordersView(orders) {
  if (!orders.length) return '<div class="empty">暂无充值订单。</div>';
  return `<div class="table-wrap"><table><thead><tr><th>订单</th><th>充值包</th><th>支付金额</th><th>到账额度</th><th>支付途径</th><th>状态</th><th>创建时间</th></tr></thead><tbody>${orders.map(order => `<tr><td class="mono">${escapeHTML(order.order_id)}</td><td>${escapeHTML(order.package_code)}</td><td>¥${escapeHTML(order.payment_cny)}</td><td>${formatCredits(order.credits)}</td><td>${escapeHTML(order.payment_provider)}</td><td><span class="status ${escapeHTML(order.status)}">${escapeHTML(orderStatus[order.status] || order.status)}</span></td><td>${formatDate(order.created_at)}</td></tr>`).join('')}</tbody></table></div>`;
}

function ledgerView(statement) {
  const entries = statement.entries || [];
  if (!entries.length) return '<div class="empty">暂无额度账务记录。</div>';
  const page = Number(statement.page || 1);
  const totalPages = Number(statement.total_pages || 1);
  return `<div class="table-wrap"><table><thead><tr><th>类型</th><th>可用额度变动</th><th>冻结额度变动</th><th>变动后可用</th><th>引用</th><th>时间</th></tr></thead><tbody>${entries.map(entry => `<tr><td><span class="status ${escapeHTML(entry.kind)}">${escapeHTML(postingKind[entry.kind] || entry.kind)}</span></td><td>${escapeHTML(entry.delta_available_credits)}</td><td>${escapeHTML(entry.delta_frozen_credits)}</td><td>${escapeHTML(entry.available_credits_after)}</td><td class="mono">${escapeHTML(entry.reference)}</td><td>${formatDate(entry.occurred_at)}</td></tr>`).join('')}</tbody></table></div>
    <div class="row-actions" style="justify-content:center;margin-top:16px"><button class="secondary-btn" type="button" data-ledger-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button><span>第 ${page} / ${totalPages} 页 · 共 ${Number(statement.total_entries || 0)} 条</span><button class="secondary-btn" type="button" data-ledger-page="${page + 1}" ${page >= totalPages ? 'disabled' : ''}>下一页</button></div>`;
}

async function walletPage(ledgerPage = 1) {
  loadingPage('钱包');
  try {
    await ensureAccountSummary();
    const [statement, packages, methods, orders, rechargeRate] = await Promise.all([
      optionalApi(`/api/v1/credits/ledger?page=${encodeURIComponent(ledgerPage)}&page_size=20`, { entries: [], page: 1, page_size: 20, total_entries: 0, total_pages: 1 }),
      optionalApi('/api/v1/recharge-packages', []),
      optionalApi('/api/v1/payment-methods', []),
      optionalApi('/api/v1/recharge-orders', []),
      api('/api/v1/recharge-rate'),
    ]);
    shell('钱包', `<div class="page-head"><div><h1>钱包</h1><p>查看消费额度、支付途径、充值订单和不可改写的账务记录。</p></div></div>
      <section class="grid two"><article class="stat-card"><span>可用额度</span><strong>${formatCredits(state.balance.available_credits)}</strong><small>充值取得的额度永久有效</small></article><article class="stat-card"><span>冻结额度</span><strong>${formatCredits(state.balance.frozen_credits)}</strong><small>生成任务执行期间暂时占用</small></article></section>
      <div class="section-head"><div><h2>支付途径</h2><p>页面只显示渠道名称，不保存或返回任何支付凭据。</p></div></div>${paymentMethodsView(methods)}
      ${directRechargeView(rechargeRate)}
      <div class="section-head"><div><h2>特惠充值包</h2><p>特惠包使用自己单独的支付金额和赠送额度，不受普通充值全局比例影响。</p></div></div>${packagesView(packages)}
      <div class="section-head"><div><h2>充值订单</h2><p>订单按创建时间从新到旧排列。</p></div></div><div id="orders">${ordersView(orders)}</div>
      <div class="section-head"><div><h2>额度账务记录</h2><p>充值、冻结、结算、释放与冲销均以不可改写的记录表达，每页显示 20 条。</p></div></div>${ledgerView(statement)}`);

    let selectedProvider = methods[0]?.payment_provider || '';
    document.querySelectorAll('[data-provider]').forEach(button => button.addEventListener('click', () => {
      selectedProvider = button.dataset.provider;
      document.querySelectorAll('[data-provider]').forEach(item => item.classList.toggle('active', item === button));
    }));
    document.querySelectorAll('[data-package]').forEach(button => button.addEventListener('click', async () => {
      if (!selectedProvider) return toast('支付途径尚未开放，可联系管理员人工充值');
      button.disabled = true;
      try {
        const order = await api('/api/v1/recharge-orders', {
          method: 'POST',
          headers: { 'Idempotency-Key': window.crypto.randomUUID() },
          body: JSON.stringify({ package_version_id: button.dataset.package, payment_provider: selectedProvider }),
        });
        submitPaymentCheckout(order.checkout);
        toast('充值订单已创建，请在收银台完成支付');
        walletPage();
      } catch (error) {
        toast(error.message);
        button.disabled = false;
      }
    }));
    document.querySelectorAll('[data-ledger-page]').forEach(button => button.addEventListener('click', () => {
      if (!button.disabled) walletPage(Number(button.dataset.ledgerPage));
    }));
    const createDirectRecharge = async (paymentCny, button) => {
      if (!selectedProvider) return toast('支付途径尚未开放，可联系管理员人工充值');
      button.disabled = true;
      try {
        const order = await api('/api/v1/recharge-orders/direct', {
          method: 'POST',
          headers: { 'Idempotency-Key': window.crypto.randomUUID() },
          body: JSON.stringify({ payment_cny: String(paymentCny), payment_provider: selectedProvider }),
        });
        submitPaymentCheckout(order.checkout);
        toast(`充值订单已创建，支付成功将到账 ${formatCredits(order.credits)} 额度`);
        walletPage();
      } catch (error) {
        toast(error.message);
        button.disabled = false;
      }
    };
    document.querySelectorAll('[data-direct-recharge]').forEach(button => button.addEventListener('click', () => {
      createDirectRecharge(button.dataset.directRecharge, button);
    }));
    const customForm = document.getElementById('direct-recharge-custom-form');
    const customInput = customForm?.elements.payment_cny;
    const updateDirectPreview = () => {
      const credits = Number(customInput?.value) * Number(rechargeRate.credits_per_cny);
      const preview = document.querySelector('[data-direct-preview]');
      if (preview) preview.textContent = Number.isFinite(credits) && credits > 0 ? formatCredits(credits) : '—';
    };
    customInput?.addEventListener('input', updateDirectPreview);
    customForm?.addEventListener('submit', event => {
      event.preventDefault();
      createDirectRecharge(customInput.value, event.currentTarget.querySelector('[type="submit"]'));
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('钱包', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

const modelAvailabilityLabel = { available: '可用', maintenance: '暂不可用' };

function userModelCatalogTable(models) {
  const specifications = models.flatMap(model => (model.output_specs || []).map(specification => ({
    logical_model: model.logical_model,
    ...specification,
  })));
  if (!specifications.length) return '<div class="empty">当前没有已发布的图片模型规格。</div>';
  return `<div class="table-wrap"><table><thead><tr><th>逻辑模型</th><th>成品规格</th><th>参考图上限</th><th>每张价格</th><th>当前状态</th></tr></thead><tbody>${specifications.map(specification => `<tr><td><strong>${escapeHTML(specification.logical_model)}</strong></td><td>${escapeHTML(specification.output_spec)}</td><td>${normalizedImageReferenceLimit(specification.max_reference_images)} 张</td><td>${formatCredits(specification.credits_per_result)} 额度</td><td><span class="status ${escapeHTML(specification.status)}">${escapeHTML(modelAvailabilityLabel[specification.status] || specification.status)}</span></td></tr>`).join('')}</tbody></table></div>`;
}

async function workspaceModelsPage() {
  loadingPage('模型目录');
  try {
    await ensureAccountSummary();
    const catalog = await optionalApi('/api/v1/image-models', { data: [] });
    shell('模型目录', `<div class="page-head"><div><h1>图片模型目录</h1><p>查看平台发布的逻辑模型、成品规格、每张额度价格和当前可用状态。</p></div></div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>逻辑模型</h2><p>平台负责选择兼容来源；目录不会显示 API 来源、模型路由、Provider 成本或凭据。</p></div></div>${userModelCatalogTable(catalog.data || [])}</section>`);
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('模型目录', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

async function streamGenerationTask(taskId, onMedia = null, onTask = null) {
  const headers = new Headers({ Accept: 'text/event-stream' });
  if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
  const response = await window.fetch(
    `/api/v1/generation-tasks/${encodeURIComponent(taskId)}/events`,
    { headers },
  );
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(errorMessage(payload, response.status));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = '';
  let terminalTask = null;
  let mediaItemCount = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
      const events = buffered.split(/\r?\n\r?\n/);
      buffered = events.pop() || '';
      for (const event of events) {
        const eventName = event.split(/\r?\n/)
          .find(line => line.startsWith('event:'))?.slice(6).trim() || 'message';
        const data = event.split(/\r?\n/)
          .filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart())
          .join('\n');
        if (!data) continue;
        const payload = JSON.parse(data);
        if (eventName === 'media') {
          mediaItemCount += Array.isArray(payload) ? payload.length : 1;
          if (onMedia) await onMedia(payload);
          continue;
        }
        const task = payload;
        if (onTask) await onTask(task);
        if (['succeeded', 'failed', 'cancelled'].includes(task.status)) terminalTask = task;
      }
      if (terminalTask) {
        if (terminalTask.status !== 'succeeded') return terminalTask;
        const delivered = Math.max(0, Number(terminalTask.delivered_quantity || 0));
        if (!delivered || mediaItemCount >= delivered || done) return terminalTask;
      }
      if (done) throw new Error('任务状态连接提前关闭');
    }
  } finally {
    reader.releaseLock();
  }
}

function llmProviderCards(providers) {
  if (!providers.length) return '<div class="empty">尚未配置 LLM Provider。添加后，智能画布会自动显示对应模型。</div>';
  return `<div class="llm-provider-grid">${providers.map(provider => `<article class="panel llm-provider-card">
    <div class="section-head" style="margin-top:0"><div><h2>${escapeHTML(provider.display_name)}</h2><p>${escapeHTML(provider.code)} · ${provider.enabled ? '已启用' : '已停用'}</p></div><span class="badge">Key ····${escapeHTML(provider.key_fingerprint || '')}</span></div>
    <dl class="detail-list"><div class="detail-row"><dt>API 地址</dt><dd class="mono">${escapeHTML(provider.base_url)}</dd></div><div class="detail-row"><dt>文本模型</dt><dd>${(provider.models || []).map(model => `<span class="llm-model-chip">${escapeHTML(model)}</span>`).join(' ')}</dd></div></dl>
    <div class="row-actions"><button class="secondary-btn" data-edit-llm="${escapeHTML(provider.id)}">编辑</button><button class="danger-btn" data-delete-llm="${escapeHTML(provider.id)}">删除</button></div>
  </article>`).join('')}</div>`;
}

async function workspaceLLMSettingsPage(editingId = '') {
  loadingPage('LLM 设置');
  try {
    await ensureAccountSummary();
    const providers = await api('/api/v1/llm-providers');
    const editing = providers.find(provider => provider.id === editingId);
    shell('LLM 设置', `<div class="page-head"><div><h1>LLM 设置</h1><p>配置您自己的文本模型 API，仅供当前账户的智能画布使用。生图 Provider 仍由平台统一管理。</p></div></div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>${editing ? '编辑 LLM Provider' : '添加 LLM Provider'}</h2><p>支持 OpenAI-compatible /chat/completions。API Key 保存后不会返回明文。</p></div></div>
        <form id="llm-provider-form" class="admin-form-grid">
          <div class="field"><label>Provider 代码</label><input name="code" required maxlength="64" value="${escapeHTML(editing?.code || '')}" placeholder="openai"></div>
          <div class="field"><label>显示名称</label><input name="display_name" required maxlength="120" value="${escapeHTML(editing?.display_name || '')}" placeholder="我的 OpenAI"></div>
          <div class="field span-two"><label>API 基础地址</label><input name="base_url" type="url" required value="${escapeHTML(editing?.base_url || 'https://api.openai.com/v1')}" placeholder="https://api.openai.com/v1"></div>
          <div class="field span-two"><label>API Key（只写）</label><input name="api_key" type="password" autocomplete="new-password" ${editing ? '' : 'required'} placeholder="${editing ? '留空则保留现有 Key' : '保存后不可读取'}"></div>
          <div class="field span-two"><label>文本模型（每行一个，也可用逗号分隔）</label><textarea name="models" rows="4" required placeholder="gpt-4.1-mini\ngpt-4o-mini">${escapeHTML((editing?.models || []).join('\n'))}</textarea></div>
          <div class="field"><label><input name="enabled" type="checkbox" ${editing?.enabled === false ? '' : 'checked'}> 启用此 Provider</label></div>
          <div class="row-actions"><button class="primary-btn" type="submit">${editing ? '保存修改' : '添加 Provider'}</button>${editing ? '<button class="secondary-btn" type="button" id="cancel-llm-edit">取消编辑</button>' : ''}</div>
        </form>
      </section>
      <div class="section-head"><div><h2>我的 LLM Provider</h2><p>这些配置按账户空间隔离，不会影响平台的图片生成路由。</p></div></div>${llmProviderCards(providers)}`);
    document.getElementById('llm-provider-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const models = String(form.get('models') || '').split(/[\n,，]+/).map(value => value.trim()).filter(Boolean);
      const body = { code: form.get('code'), display_name: form.get('display_name'), base_url: form.get('base_url'), api_key: form.get('api_key'), models, enabled: form.get('enabled') === 'on' };
      try {
        await api(editing ? `/api/v1/llm-providers/${encodeURIComponent(editing.id)}` : '/api/v1/llm-providers', { method: editing ? 'PATCH' : 'POST', body: JSON.stringify(body) });
        toast(editing ? 'LLM Provider 已更新' : 'LLM Provider 已添加');
        workspaceLLMSettingsPage();
      } catch (error) { toast(error.message); }
    });
    document.getElementById('cancel-llm-edit')?.addEventListener('click', () => workspaceLLMSettingsPage());
    document.querySelectorAll('[data-edit-llm]').forEach(button => button.addEventListener('click', () => workspaceLLMSettingsPage(button.dataset.editLlm)));
    document.querySelectorAll('[data-delete-llm]').forEach(button => button.addEventListener('click', async () => {
      if (!window.confirm('确定删除这个 LLM Provider？删除后画布将无法继续使用该配置。')) return;
      try { await api(`/api/v1/llm-providers/${encodeURIComponent(button.dataset.deleteLlm)}`, { method: 'DELETE' }); toast('LLM Provider 已删除'); workspaceLLMSettingsPage(); } catch (error) { toast(error.message); }
    }));
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('LLM 设置', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

function centeredDeleteConfirm(message, title = '确认删除', confirmLabel = '确认删除') {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'delete-confirm-dialog';
    dialog.innerHTML = `<form method="dialog"><div class="delete-confirm-mark">!</div><div class="delete-confirm-copy"><h3>${escapeHTML(title)}</h3><p>${escapeHTML(message)}</p></div><div class="delete-confirm-actions"><button class="secondary-btn" value="cancel">取消</button><button class="danger-btn" value="confirm">${escapeHTML(confirmLabel)}</button></div></form>`;
    const finish = value => { dialog.remove(); resolve(value); };
    dialog.addEventListener('close', () => finish(dialog.returnValue === 'confirm'), { once: true });
    dialog.addEventListener('cancel', event => { event.preventDefault(); dialog.close('cancel'); });
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close('cancel'); });
    document.body.appendChild(dialog);
    dialog.showModal();
  });
}

function centeredNotice(message, title = '尺寸提示') {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'delete-confirm-dialog notice-dialog';
    dialog.innerHTML = `<form method="dialog"><div class="delete-confirm-mark">!</div><div class="delete-confirm-copy"><h3>${escapeHTML(title)}</h3><p>${escapeHTML(message)}</p></div><div class="delete-confirm-actions"><button class="primary-btn" value="confirm">我知道了</button></div></form>`;
    const finish = () => { dialog.remove(); resolve(); };
    dialog.addEventListener('close', finish, { once: true });
    dialog.addEventListener('cancel', event => { event.preventDefault(); dialog.close('confirm'); });
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close('confirm'); });
    document.body.appendChild(dialog);
    dialog.showModal();
  });
}

function canvasPreviewHTML(preview) {
  if (preview?.status === 'generating') {
    return '<div class="canvas-preview canvas-preview-generating"><span class="canvas-preview-spinner">↻</span><strong>正在生成</strong></div>';
  }
  if (preview?.url) {
    return `<div class="canvas-preview"><img src="${escapeHTML(preview.url)}" alt="画布最后生成的效果图"></div>`;
  }
  return '<div class="canvas-preview canvas-preview-empty">暂无效果图</div>';
}

function latestCanvasPreviewTask(canvasId, tasks) {
  return tasks
    .filter(task => task.canvas_id === canvasId && ['queued', 'running', 'succeeded'].includes(task.status))
    .sort((left, right) => Date.parse(right.created_at || '') - Date.parse(left.created_at || ''))[0] || null;
}

function clearCanvasPreviewUrls() {
  state.canvasPreviewUrls.forEach(url => window.URL.revokeObjectURL(url));
  state.canvasPreviewUrls = [];
}

function canvasListCacheKey() {
  return `${accountSummaryCacheKey()}:canvases`;
}

function readCanvasListCache() {
  if (state.canvasListCache && Date.now() - state.canvasListCacheAt < ACCOUNT_CACHE_TTL_MS) return state.canvasListCache;
  try {
    const cached = JSON.parse(window.localStorage.getItem(canvasListCacheKey()) || 'null');
    if (!Array.isArray(cached?.canvases) || Date.now() - Number(cached.savedAt || 0) > ACCOUNT_CACHE_TTL_MS) return null;
    state.canvasListCache = cached.canvases;
    state.canvasListCacheAt = Number(cached.savedAt || Date.now());
    return state.canvasListCache;
  } catch (_error) {
    return null;
  }
}

function persistCanvasListCache(canvases) {
  state.canvasListCache = canvases;
  state.canvasListCacheAt = Date.now();
  try {
    window.localStorage.setItem(canvasListCacheKey(), JSON.stringify({ savedAt: state.canvasListCacheAt, canvases }));
  } catch (_error) { /* storage may be disabled or full */ }
}

async function authenticatedCanvasPreviewUrl(media) {
  const headers = new Headers({ Authorization: `Bearer ${state.token}` });
  const response = await window.fetch(`/api/v1/media/${encodeURIComponent(media.media_id)}/content`, { headers });
  if (!response.ok) return '';
  const url = window.URL.createObjectURL(await response.blob());
  state.canvasPreviewUrls.push(url);
  return url;
}

async function loadCanvasPreviews(canvases) {
  const tasks = await optionalApi('/api/v1/generation-tasks/recent?limit=100', []);
  const entries = await Promise.all(canvases.map(async canvas => {
    let task = latestCanvasPreviewTask(canvas.canvas_id, tasks);
    if (!task) {
      const canvasTasks = await optionalApi(
        `/api/v1/canvases/${encodeURIComponent(canvas.canvas_id)}/generation-tasks/recent?limit=20`,
        [],
      );
      task = latestCanvasPreviewTask(canvas.canvas_id, canvasTasks);
    }
    if (!task) return [canvas.canvas_id, {status: 'empty'}];
    if (['queued', 'running'].includes(task.status)) {
      return [canvas.canvas_id, {status: 'generating'}];
    }
    const media = await optionalApi(`/api/v1/generation-tasks/${encodeURIComponent(task.task_id)}/media`, []);
    const available = media
      .filter(item => item.kind === 'image' && ['temporary', 'persistent'].includes(item.state)
        && (!item.expires_at || new Date(item.expires_at).getTime() > Date.now()))
      .sort((left, right) => Date.parse(right.created_at || '') - Date.parse(left.created_at || ''));
    if (!available.length) return [canvas.canvas_id, {status: 'empty'}];
    const url = await authenticatedCanvasPreviewUrl(available[0]);
    return [canvas.canvas_id, url ? {status: 'ready', url} : {status: 'empty'}];
  }));
  return new Map(entries);
}

function canvasesTable(canvases, previews = new Map()) {
  if (!canvases.length) {
    return '<div class="empty">当前账户空间还没有画布。输入名称创建第一张无限画布。</div>';
  }
  return `<div class="table-wrap canvas-list-table"><table><thead><tr><th>效果图</th><th>画布名称</th><th>版本</th><th>更新时间</th><th>画布类型</th><th>操作</th></tr></thead><tbody>${canvases.map(canvas => `<tr>
    <td>${canvasPreviewHTML(previews.get(canvas.canvas_id))}</td>
    <td><strong>${escapeHTML(canvas.title || '未命名画布')}</strong></td>
    <td>${escapeHTML(canvas.version)}</td>
    <td>${formatDate(canvas.updated_at)}</td>
    <td>${canvas.kind === 'smart' ? '智能画布' : '<span class="status">已停用（历史数据保留）</span>'}</td>
    <td><div class="row-actions">${canvas.kind === 'smart' ? `<button class="primary-btn" type="button" data-canvas-open="${escapeHTML(canvas.canvas_id)}" data-canvas-kind="smart">打开画布</button>` : '<span class="image-edit-sub">历史画布不提供编辑入口</span>'}<button class="danger-btn" type="button" data-canvas-delete="${escapeHTML(canvas.canvas_id)}" data-canvas-title="${escapeHTML(canvas.title || '未命名画布')}">${canvas.kind === 'smart' ? '永久删除' : '删除历史画布'}</button>${canvas.kind === 'smart' ? `<button class="secondary-btn" type="button" data-canvas-export="${escapeHTML(canvas.canvas_id)}" data-canvas-title="${escapeHTML(canvas.title || '未命名画布')}">导出</button>` : ''}</div></td>
  </tr>`).join('')}</tbody></table></div>`;
}

function canvasEditorUrl(canvasId, kind) {
  const encoded = encodeURIComponent(canvasId);
  // 经典画布已停止提供新入口；保留 kind 参数只为兼容旧调用方，
  // 所有可打开的画布统一进入当前智能画布编辑器。
  return `/workspace/canvases/${encoded}/smart?id=${encoded}`;
}

function canvasWorkspaceContent(canvases, previews = new Map()) {
  return `<div class="page-head"><div><h1>无限画布</h1><p>在可缩放画布中组织图片、提示词和生成节点。画布与媒体仅属于当前账户空间。</p></div></div>
    <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>新建画布</h2><p>首版开放图片、提示词、循环、平台生图和输出节点；其他本地 Provider 能力暂不开放。</p></div></div>
      <form class="canvas-create-form" id="canvas-create-form"><div class="field canvas-title-field"><label for="canvas-title">画布名称</label><input id="canvas-title" name="title" maxlength="80" required placeholder="例如：夏季主视觉"></div><button class="primary-btn" type="submit">创建并打开</button></form>
    </section>
    <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>我的画布</h2><p>保存采用版本校验，避免不同浏览器窗口静默覆盖彼此的修改。</p></div><button class="secondary-btn" type="button" data-canvas-refresh>刷新</button></div>${canvasesTable(canvases, previews)}</section>`;
}

function bindCanvasListActions() {
  document.getElementById('canvas-create-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('[type="submit"]');
    const values = new FormData(form);
    button.disabled = true;
    try {
      const canvas = await api('/api/v1/canvases', {
        method: 'POST',
        body: JSON.stringify({ title: values.get('title'), kind: 'smart' }),
      });
      window.location.assign(canvasEditorUrl(canvas.canvas_id, canvas.kind));
    } catch (error) {
      toast(error.message);
      button.disabled = false;
    }
  });
  document.querySelectorAll('[data-canvas-open]').forEach(button => button.addEventListener('click', () => {
    window.location.assign(canvasEditorUrl(button.dataset.canvasOpen, button.dataset.canvasKind));
  }));
  document.querySelectorAll('[data-canvas-export]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await exportSmartCanvasFromList(button.dataset.canvasExport, button.dataset.canvasTitle);
      toast('智能画布工作流已导出');
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  }));
  document.querySelectorAll('[data-canvas-delete]').forEach(button => button.addEventListener('click', async () => {
    if (!await centeredDeleteConfirm(`确认不可恢复地删除“${button.dataset.canvasTitle}”吗？`)) return;
    button.disabled = true;
    try {
      await deleteCanvas(button.dataset.canvasDelete);
      toast('画布已删除');
      await workspaceCanvasesPage();
    } catch (error) {
      const detail = error.payload?.detail;
      if (error.status === 409 && detail?.confirm_required) {
        const confirmed = await centeredDeleteConfirm(detail.message || '画布仍有任务运行，是否继续删除？');
        if (confirmed) {
          try {
            await deleteCanvas(button.dataset.canvasDelete, true);
            toast('画布已删除，运行中的任务将继续执行');
            await workspaceCanvasesPage();
            return;
          } catch (confirmedError) {
            toast(confirmedError.message);
          }
        }
      } else {
        toast(error.message);
      }
      button.disabled = false;
    }
  }));
  document.querySelector('[data-canvas-refresh]')?.addEventListener('click', () => workspaceCanvasesPage());
}

async function deleteCanvas(canvasId, confirmRunningTasks = false) {
  const suffix = confirmRunningTasks ? '?confirm_running_tasks=true' : '';
  return api(`/api/v1/canvases/${encodeURIComponent(canvasId)}${suffix}`, { method: 'DELETE' });
}

function smartCanvasExportFilename(title) {
  const safeTitle = String(title || '智能画布')
    .replace(/[\\/:*?"<>|]+/g, '_')
    .replace(/\s+/g, '-')
    .slice(0, 48) || '智能画布';
  const timestamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '');
  return `${safeTitle}-工作流-${timestamp}.zip`;
}

async function exportSmartCanvasFromList(canvasId, title) {
  const canvas = await api(`/api/v1/canvases/${encodeURIComponent(canvasId)}`);
  const document = canvas.document || {};
  const nodes = Array.isArray(document.nodes) ? document.nodes : [];
  if (!nodes.length) throw new Error('智能画布中还没有可导出的节点');
  const headers = new Headers({
    Authorization: `Bearer ${state.token}`,
    'Content-Type': 'application/json',
  });
  const response = await window.fetch(
    `/api/v1/canvases/${encodeURIComponent(canvasId)}/workflows/export`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({
        format: 'infinite-smart-canvas-workflow',
        version: 1,
        canvas_type: 'smart',
        exported_at: Date.now(),
        nodes,
        connections: Array.isArray(document.connections) ? document.connections : [],
      }),
    },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(errorMessage(payload, response.status));
  }
  const url = window.URL.createObjectURL(await response.blob());
  triggerImageDownload(url, smartCanvasExportFilename(title));
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

async function workspaceCanvasesPage({ background = false } = {}) {
  if (!background) {
    loadingPage('无限画布');
    const cachedCanvases = readCanvasListCache();
    if (cachedCanvases) {
      shell('无限画布', canvasWorkspaceContent(cachedCanvases));
      bindCanvasListActions();
    }
  }
  try {
    window.clearTimeout(state.canvasPreviewRefreshTimer);
    state.canvasPreviewRefreshTimer = null;
    clearCanvasPreviewUrls();
    await ensureAccountSummary();
    const canvases = (await optionalApi('/api/v1/canvases', []))
      .sort((left, right) => {
        const createdDifference = Date.parse(right.created_at || '') - Date.parse(left.created_at || '');
        return createdDifference || String(right.canvas_id || '').localeCompare(String(left.canvas_id || ''));
      });
    const previews = await loadCanvasPreviews(canvases);
    persistCanvasListCache(canvases);
    shell('无限画布', canvasWorkspaceContent(canvases, previews));
    bindCanvasListActions();

    if ([...previews.values()].some(preview => preview.status === 'generating')) {
      state.canvasPreviewRefreshTimer = window.setTimeout(() => {
        if (state.route === '/workspace/canvases') void workspaceCanvasesPage({ background: true });
      }, 4000);
    }

  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('无限画布', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

let promptAssetView = { libraryId: 'system', category: 'all', selectedId: '', query: '', editing: false, creating: false };

const promptCategoryNames = { view: '视角', storyboard: '分镜', character: '角色', product: '产品', lighting: '光影', custom: '我的' };

function promptAssetCurrentLibrary(data) {
  const libraries = data?.libraries || [];
  return libraries.find(item => item.id === promptAssetView.libraryId) || libraries[0] || null;
}

function promptAssetFilteredItems(library) {
  const query = promptAssetView.query.trim().toLowerCase();
  return (library?.items || []).filter(item => {
    if (promptAssetView.category !== 'all' && item.category !== promptAssetView.category) return false;
    return !query || [item.name, item.scene, item.positive, item.negative].join(' ').toLowerCase().includes(query);
  });
}

function promptAssetEditor(item, library, creating = false) {
  const value = item || { name: '', scene: '', positive: '', negative: '', category: promptAssetView.category === 'all' ? 'custom' : promptAssetView.category };
  return `<form class="prompt-workbench-editor" data-prompt-save="${escapeHTML(item?.id || '')}">
    <label><span>名称</span><input name="name" value="${escapeHTML(value.name || '')}" placeholder="提示词名称" required></label>
    <label><span>用途说明</span><textarea name="scene" placeholder="说明适用场景和用途">${escapeHTML(value.scene || '')}</textarea></label>
    <label><span>分类</span><select name="category">${(library?.categories || []).map(category => `<option value="${escapeHTML(category.id)}" ${category.id === value.category ? 'selected' : ''}>${escapeHTML(category.name)}</option>`).join('')}</select></label>
    <label class="prompt-editor-large"><span>正向提示词</span><textarea name="positive" required placeholder="输入正向提示词">${escapeHTML(value.positive || '')}</textarea></label>
    <label class="prompt-editor-large"><span>负向提示词</span><textarea name="negative" placeholder="输入负向提示词">${escapeHTML(value.negative || '')}</textarea></label>
    <div class="prompt-editor-actions"><button class="secondary-btn" type="button" data-prompt-edit-cancel>取消</button><button class="primary-btn" type="submit">${creating ? '创建提示词' : '保存修改'}</button></div>
  </form>`;
}

function promptAssetPreview(item, library) {
  if (promptAssetView.creating) return `<div class="prompt-preview-head"><div><strong>新增提示词</strong><small>保存到 ${escapeHTML(library?.name || '提示词库')}</small></div></div>${promptAssetEditor(null, library, true)}`;
  if (!item) return '<div class="prompt-preview-empty"><span>⌁</span><strong>选择一条提示词查看详情</strong></div>';
  if (promptAssetView.editing) return `<div class="prompt-preview-head"><div><strong>编辑提示词</strong><small>${escapeHTML(item.name)}</small></div></div>${promptAssetEditor(item, library)}`;
  const params = Object.entries(item.params || {});
  return `<div class="prompt-preview-head"><div><strong>提示词预览</strong><small>${escapeHTML(promptCategoryNames[item.category] || item.category || '未分类')}</small></div><div class="prompt-preview-actions"><button class="workbench-icon-btn" type="button" data-prompt-edit title="编辑">✎</button><button class="workbench-icon-btn danger" type="button" data-prompt-remove="${escapeHTML(item.id)}" title="删除">♲</button></div></div>
    <div class="prompt-preview-scroll"><h2>${escapeHTML(item.name)}</h2><p class="prompt-scene">${escapeHTML(item.scene || '未填写用途说明')}</p>
      <section class="prompt-copy-block"><header><strong>正向提示词</strong><span>${String(item.positive || '').length} 字符</span></header><div>${escapeHTML(item.positive || '')}</div></section>
      <section class="prompt-copy-block"><header><strong>负向提示词</strong><span>${String(item.negative || '').length} 字符</span></header><div>${escapeHTML(item.negative || '未设置')}</div></section>
      ${params.map(([key, value]) => `<section class="prompt-param"><strong>${escapeHTML(key)}</strong><span>${escapeHTML(String(value))}</span></section>`).join('')}
    </div>`;
}

function renderPromptAssetWorkbench(data) {
  const libraries = data?.libraries || [];
  if (!libraries.some(item => item.id === promptAssetView.libraryId)) promptAssetView.libraryId = libraries[0]?.id || '';
  const library = promptAssetCurrentLibrary(data);
  const items = promptAssetFilteredItems(library);
  if (!items.some(item => item.id === promptAssetView.selectedId)) promptAssetView.selectedId = items[0]?.id || '';
  const selected = (library?.items || []).find(item => item.id === promptAssetView.selectedId) || null;
  const categories = library?.categories || [];
  return `<div class="asset-workbench-head"><div><h1>提示词库</h1><p>按词库、分组、内容和预览管理提示词。</p></div><div class="asset-head-actions"><span class="asset-ready">准备就绪</span><button class="secondary-btn" type="button" data-prompt-refresh>↻ 刷新</button></div></div>
    <div class="prompt-workbench">
      <aside class="prompt-library-pane">
        <header><div><strong>提示词库</strong><small>可创建多个词库</small></div><button class="workbench-icon-btn" type="button" data-prompt-library-add title="新建提示词库">＋</button></header>
        <div class="prompt-tree">${libraries.map(lib => `<div class="prompt-tree-library"><button type="button" class="prompt-tree-root ${lib.id === library?.id ? 'active' : ''}" data-prompt-library="${escapeHTML(lib.id)}"><span>⌁</span><strong>${escapeHTML(lib.name)}</strong><em>${(lib.items || []).length}</em></button>${lib.id === library?.id ? `<div class="prompt-tree-categories"><button type="button" class="${promptAssetView.category === 'all' ? 'active' : ''}" data-prompt-category="all"><span>☷</span>全部提示词<em>${(lib.items || []).length}</em></button>${categories.map(category => `<button type="button" class="${promptAssetView.category === category.id ? 'active' : ''}" data-prompt-category="${escapeHTML(category.id)}"><span>◇</span>${escapeHTML(category.name)}<em>${(lib.items || []).filter(item => item.category === category.id).length}</em></button>`).join('')}</div>` : ''}</div>`).join('')}</div>
      </aside>
      <main class="prompt-list-pane">
        <header><div><strong>${escapeHTML(library?.name || '提示词库')}</strong><small>共 ${items.length} 条提示词</small></div><div class="prompt-list-tools"><label>⌕<input data-prompt-search value="${escapeHTML(promptAssetView.query)}" placeholder="搜索名称、说明或正文"></label><button class="primary-btn" type="button" data-prompt-new>＋ 新增</button><button class="secondary-btn" type="button" disabled>☷ 批量管理</button></div></header>
        <div class="prompt-card-list">${items.map(item => `<button type="button" class="prompt-list-card ${item.id === selected?.id ? 'active' : ''}" data-prompt-select="${escapeHTML(item.id)}"><div><strong>${escapeHTML(item.name)}</strong><span>${escapeHTML(promptCategoryNames[item.category] || item.category || '未分类')}</span></div><p>${escapeHTML(item.scene || '未填写用途说明')}</p><article>${escapeHTML(item.positive || '')}</article></button>`).join('') || '<div class="prompt-list-empty">当前分组暂无提示词</div>'}</div>
      </main>
      <aside class="prompt-preview-pane">${promptAssetPreview(selected, library)}</aside>
    </div>`;
}

async function workspaceAssetsPage() {
  loadingPage('素材库管理');
  try {
    await ensureAccountSummary();
    let response = await api('/api/v1/prompt-libraries');
    const repaint = () => {
      const content = renderPromptAssetWorkbench(response.library);
    shell('提示词库', content, 'asset-workbench-page');
      bindPromptAssetWorkbench();
    };
    const refresh = async () => { response = await api('/api/v1/prompt-libraries'); repaint(); };
    const bindPromptAssetWorkbench = () => {
      document.querySelector('[data-prompt-refresh]')?.addEventListener('click', refresh);
      document.querySelectorAll('[data-prompt-library]').forEach(button => button.addEventListener('click', () => { promptAssetView = { ...promptAssetView, libraryId: button.dataset.promptLibrary, category: 'all', selectedId: '', editing: false, creating: false }; repaint(); }));
      document.querySelectorAll('[data-prompt-category]').forEach(button => button.addEventListener('click', () => { promptAssetView.category = button.dataset.promptCategory; promptAssetView.selectedId = ''; promptAssetView.editing = false; promptAssetView.creating = false; repaint(); }));
      document.querySelectorAll('[data-prompt-select]').forEach(button => button.addEventListener('click', () => { promptAssetView.selectedId = button.dataset.promptSelect; promptAssetView.editing = false; promptAssetView.creating = false; repaint(); }));
      document.querySelector('[data-prompt-search]')?.addEventListener('input', event => { promptAssetView.query = event.target.value; promptAssetView.selectedId = ''; repaint(); requestAnimationFrame(() => { const input = document.querySelector('[data-prompt-search]'); input?.focus(); input?.setSelectionRange(input.value.length, input.value.length); }); });
      document.querySelector('[data-prompt-new]')?.addEventListener('click', () => { promptAssetView.creating = true; promptAssetView.editing = false; repaint(); });
      document.querySelector('[data-prompt-edit]')?.addEventListener('click', () => { promptAssetView.editing = true; repaint(); });
      document.querySelector('[data-prompt-edit-cancel]')?.addEventListener('click', () => { promptAssetView.editing = false; promptAssetView.creating = false; repaint(); });
      document.querySelector('[data-prompt-library-add]')?.addEventListener('click', async () => { const name = window.prompt('提示词库名称', '新提示词库'); if (!name?.trim()) return; const result = await api('/api/v1/prompt-libraries', { method: 'POST', body: JSON.stringify({ name }) }); promptAssetView.libraryId = result.prompt_library.id; promptAssetView.category = 'all'; await refresh(); });
      document.querySelector('[data-prompt-save]')?.addEventListener('submit', async event => { event.preventDefault(); const values = new FormData(event.currentTarget); const id = event.currentTarget.dataset.promptSave; const body = { library_id: promptAssetView.libraryId, name: values.get('name'), scene: values.get('scene'), category: values.get('category') || 'custom', positive: values.get('positive'), negative: values.get('negative'), params: {} }; const result = await api(id ? `/api/v1/prompt-libraries/items/${encodeURIComponent(id)}` : '/api/v1/prompt-libraries/items', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(body) }); promptAssetView.selectedId = result.item.id; promptAssetView.editing = false; promptAssetView.creating = false; await refresh(); });
      document.querySelector('[data-prompt-remove]')?.addEventListener('click', async buttonEvent => { const id = buttonEvent.currentTarget.dataset.promptRemove; if (!await centeredDeleteConfirm('确认删除这条提示词吗？')) return; await api(`/api/v1/prompt-libraries/items/${encodeURIComponent(id)}`, { method: 'DELETE' }); promptAssetView.selectedId = ''; await refresh(); });
    };
    repaint();
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('素材库管理', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

function clearImagePreviewUrls() {
  state.previewUrls.forEach(url => window.URL.revokeObjectURL(url));
  state.previewUrls = [];
}

async function mapWithConcurrency(items, limit, mapper) {
  const values = Array.from(items || []);
  if (!values.length) return [];
  const results = new Array(values.length);
  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < values.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await mapper(values[index], index);
    }
  };
  await Promise.all(Array.from({ length: Math.min(Math.max(1, limit), values.length) }, worker));
  return results;
}

async function authenticatedMediaObjectUrl(media, { thumbnail = false, timeoutMs = 8_000 } = {}) {
  const headers = new Headers();
  headers.set('Authorization', `Bearer ${state.token}`);
  const endpoint = thumbnail
    ? `/api/v1/media/${encodeURIComponent(media.media_id)}/thumbnail?size=512`
    : `/api/v1/media/${encodeURIComponent(media.media_id)}/content`;
  const controller = new AbortController();
  const timeoutHandle = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await window.fetch(endpoint, { headers, signal: controller.signal });
  } finally {
    window.clearTimeout(timeoutHandle);
  }
  if (!response.ok) throw new Error('图片内容当前不可用');
  const url = window.URL.createObjectURL(await response.blob());
  state.previewUrls.push(url);
  return url;
}

async function authenticatedReferenceObjectUrl(media, { thumbnail = false, timeoutMs = 8_000 } = {}) {
  const headers = new Headers();
  headers.set('Authorization', `Bearer ${state.token}`);
  const rawPreviewUrl = String(media.preview_url || '');
  const endpoint = thumbnail && media.media_id
    ? `/api/v1/reference-media/${encodeURIComponent(media.media_id)}/thumbnail?size=512`
    : rawPreviewUrl;
  const controller = new AbortController();
  const timeoutHandle = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await window.fetch(endpoint, { headers, signal: controller.signal });
  } finally {
    window.clearTimeout(timeoutHandle);
  }
  if (!response.ok) throw new Error('参考图片当前不可用');
  const url = window.URL.createObjectURL(await response.blob());
  state.previewUrls.push(url);
  return url;
}

async function loadOriginalReferenceUrl(media) {
  if (!media) return '';
  if (!media.originalUrl) media.originalUrl = await authenticatedReferenceObjectUrl(media);
  return media.originalUrl;
}

async function restoreRecentReferenceMedia() {
  state.referenceMediaLoading = true;
  renderReferenceMediaList();
  try {
    const recent = (await optionalApi('/api/v1/reference-media/recent?limit=12', [])).slice(0, 12);
    const savedMaskId = window.localStorage.getItem(imageMaskMediaKey()) || '';
    if (!state.route || isImageWorkspaceRoute()) {
      state.referenceMediaEntries = recent.filter(media => media.media_id !== savedMaskId)
        .map(media => ({ ...media, previewUrl: '' }));
      const savedMask = recent.find(media => media.media_id === savedMaskId);
      state.maskMediaEntry = savedMask ? { ...savedMask, previewUrl: '' } : null;
      if (!savedMask) window.localStorage.removeItem(imageMaskMediaKey());
    }
    void mapWithConcurrency(recent, 3, async media => {
      try {
        const previewUrl = await authenticatedReferenceObjectUrl(media, { thumbnail: true });
        if (state.route && !isImageWorkspaceRoute()) {
          window.URL.revokeObjectURL(previewUrl);
          state.previewUrls = state.previewUrls.filter(url => url !== previewUrl);
          return;
        }
        const entry = state.maskMediaEntry?.media_id === media.media_id
          ? state.maskMediaEntry
          : state.referenceMediaEntries.find(item => item.media_id === media.media_id);
        if (entry) entry.previewUrl = previewUrl;
      } catch (_error) {
        state.referenceMediaEntries = state.referenceMediaEntries.filter(item => item.media_id !== media.media_id);
        if (state.maskMediaEntry?.media_id === media.media_id) {
          state.maskMediaEntry = null;
          window.localStorage.removeItem(imageMaskMediaKey());
        }
      } finally {
        if (!state.route || isImageWorkspaceRoute()) {
          renderReferenceMediaList();
          renderMaskMedia();
        }
      }
    });
  } finally {
    state.referenceMediaLoading = false;
    state.referenceMediaHydrated = true;
    if (!state.route || isImageWorkspaceRoute()) renderReferenceMediaList();
    if (!state.route || isImageWorkspaceRoute()) renderMaskMedia();
  }
}

function referenceMediaListHTML() {
  if (state.referenceMediaLoading) return '<span class="field-hint">正在恢复参考图片…</span>';
  if (!state.referenceMediaEntries.length) return '<span class="field-hint">尚未选择参考图</span>';
  return `<div class="image-reference-list">${state.referenceMediaEntries.map(media => `
    <figure class="image-reference-thumbnail">
      ${media.previewUrl
        ? `<button class="image-reference-expand" type="button" data-reference-expand="${escapeHTML(media.media_id)}" aria-label="放大参考图 ${escapeHTML(media.original_name)}"><img src="${escapeHTML(media.previewUrl)}" alt="参考图 ${escapeHTML(media.original_name)}"></button>`
        : '<span class="image-generation-spinner" aria-hidden="true">↻</span>'}
      <figcaption title="${escapeHTML(media.original_name)}">${escapeHTML(media.original_name)}</figcaption>
      <button type="button" data-reference-delete="${escapeHTML(media.media_id)}" aria-label="删除参考图 ${escapeHTML(media.original_name)}">×</button>
    </figure>`).join('')}</div>`;
}

function normalizedImageReferenceLimit(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 && parsed <= 16 ? parsed : 3;
}

function imageReferenceListHeight() {
  return 112;
}

function imageReferenceGridLayout(containerWidth, entryCount) {
  const count = Math.max(1, entryCount);
  const rows = count <= 5 ? 1 : 2;
  const columns = count <= 3 ? 3 : Math.ceil(count / rows);
  const gap = count <= 3 ? 8 : 6;
  const availableWidth = Math.max(240, containerWidth || 0);
  const size = Math.max(24, Math.floor(Math.min(
    (availableWidth - Math.max(0, columns - 1) * gap) / columns,
    (imageReferenceListHeight() - Math.max(0, rows - 1) * gap) / rows,
  )));
  return { columns, rows, gap, size };
}

function applyImageReferenceGridLayout(container) {
  const list = container?.querySelector('.image-reference-list');
  if (!list) return;
  const layout = imageReferenceGridLayout(container.clientWidth, state.referenceMediaEntries.length);
  list.style.setProperty('--reference-grid-columns', String(layout.columns));
  list.style.setProperty('--reference-grid-rows', String(layout.rows));
  list.style.setProperty('--reference-grid-gap', `${layout.gap}px`);
  list.style.setProperty('--reference-thumbnail-size', `${layout.size}px`);
}

function syncImageReferenceLimit(form) {
  const option = form?.querySelector('[name="model_spec"]')?.selectedOptions?.[0];
  state.imageReferenceLimit = normalizedImageReferenceLimit(option?.dataset.maxReferenceImages);
  renderReferenceMediaList();
}

function renderReferenceMediaList() {
  const container = app.querySelector('[data-reference-list]');
  if (container) {
    container.style.setProperty('--reference-list-height', `${imageReferenceListHeight()}px`);
    container.innerHTML = referenceMediaListHTML();
    applyImageReferenceGridLayout(container);
    if (!container._referenceExpandBound) {
      container._referenceExpandBound = true;
      container.addEventListener('click', event => {
        const button = event.target.closest('[data-reference-expand]');
        if (!button || !container.contains(button)) return;
        const media = state.referenceMediaEntries.find(item => item.media_id === button.dataset.referenceExpand);
        if (!media?.previewUrl) return;
        button.disabled = true;
        loadOriginalReferenceUrl(media)
          .then(url => openImageLightbox(url, `参考图 ${media.original_name}`))
          .catch(error => toast(error.message || '原图暂时无法加载'))
          .finally(() => { button.disabled = false; });
      });
    }
  }
  const referenceInput = app.querySelector('[name="references"]');
  if (referenceInput) referenceInput.disabled = state.imageReferenceLimit === 0;
  app.querySelectorAll('[data-reference-limit-copy]').forEach(copy => {
    copy.textContent = state.imageReferenceLimit
      ? `支持 PNG、JPEG、WebP，当前模型最多 ${state.imageReferenceLimit} 张参考图；刷新后仍保留 24 小时`
      : '当前模型不支持上传参考图；请选择其他模型';
  });
  app.querySelectorAll('[data-reference-delete]').forEach(button => { button.onclick = async () => {
    button.disabled = true;
    try {
      await api(`/api/v1/reference-media/${encodeURIComponent(button.dataset.referenceDelete)}`, { method: 'DELETE' });
      const removed = state.referenceMediaEntries.find(media => media.media_id === button.dataset.referenceDelete);
      if (removed?.previewUrl) {
        window.URL.revokeObjectURL(removed.previewUrl);
        state.previewUrls = state.previewUrls.filter(url => url !== removed.previewUrl);
      }
      state.referenceMediaEntries = state.referenceMediaEntries.filter(media => media.media_id !== button.dataset.referenceDelete);
      renderReferenceMediaList();
      toast('参考图已删除');
    } catch (error) {
      button.disabled = false;
      toast(error.message);
    }
  }; });
}

if (!window._imageReferenceResizeBound) {
  window._imageReferenceResizeBound = true;
  window.addEventListener('resize', () => {
    const container = app.querySelector('[data-reference-list]');
    if (container) applyImageReferenceGridLayout(container);
  });
}

async function uploadSelectedReferenceFiles(files) {
  const limit = state.imageReferenceLimit;
  if (!limit) throw new Error('当前模型不支持上传参考图');
  if (files.length > limit) throw new Error(`当前模型单次最多选择 ${limit} 张参考图`);
  if (state.referenceMediaEntries.length + files.length > limit) {
    throw new Error(`当前模型最多上传 ${limit} 张参考图，请先删除不用的图片`);
  }
  for (const file of files) {
    const media = await api('/api/v1/reference-media/content', {
      method: 'POST',
      headers: {
        'Content-Type': file.type || 'application/octet-stream',
        'X-Reference-Filename': encodeURIComponent(file.name || 'reference-image'),
      },
      body: file,
    });
    state.referenceMediaEntries.push({ ...media, previewUrl: await authenticatedReferenceObjectUrl(media, { thumbnail: true }) });
    renderReferenceMediaList();
  }
}

function imageGenerationModelOptions(models, selectedValue = '') {
  const specifications = models.flatMap(model => (model.output_specs || []).map(specification => ({
    logical_model: model.logical_model,
    ...specification,
  })));
  if (!specifications.length) return '<option value="">暂无可用图片模型</option>';
  return specifications.map(specification => {
    const value = `${specification.logical_model}|||${specification.output_spec}`;
    const unavailable = specification.status !== 'available';
    const selected = value === selectedValue ? 'selected' : '';
    const maxReferenceImages = normalizedImageReferenceLimit(specification.max_reference_images);
    return `<option value="${escapeHTML(value)}" data-max-reference-images="${maxReferenceImages}" ${selected} ${unavailable ? 'disabled' : ''}>${escapeHTML(specification.logical_model)} · ${escapeHTML(specification.output_spec)} · ${formatCredits(specification.credits_per_result)} 额度 · 参考图 ${maxReferenceImages} 张${unavailable ? '（暂不可用）' : ''}</option>`;
  }).join('');
}

const imageSizeByOutput = {
  '1k|1:1': '1024 × 1024', '1k|4:3': '1024 × 768', '1k|16:9': '1280 × 720',
  '1k|3:4': '768 × 1024', '1k|9:16': '720 × 1280',
  '2k|1:1': '2048 × 2048', '2k|4:3': '2048 × 1536', '2k|16:9': '2048 × 1152',
  '2k|3:4': '1536 × 2048', '2k|9:16': '1152 × 2048',
  '4k|1:1': '2880 × 2880', '4k|4:3': '3264 × 2448', '4k|16:9': '3840 × 2160',
  '4k|3:4': '2448 × 3264', '4k|9:16': '2160 × 3840',
};

function imageSettingsKey() {
  const surface = state.route === '/workspace/inpainting' ? 'inpainting' : 'images';
  return `creative_studio_image_settings:${surface}:${state.user?.user_id || 'account'}`;
}

function isImageWorkspaceRoute() {
  return state.route === '/workspace/images' || state.route === '/workspace/inpainting';
}

function loadImageSettings() {
  try {
    return JSON.parse(window.localStorage.getItem(imageSettingsKey()) || '{}');
  } catch (_error) {
    return {};
  }
}

function saveImageSettings(form) {
  const values = new FormData(form);
  const customWidthInput = form.querySelector('[name="custom_width"]');
  const customHeightInput = form.querySelector('[name="custom_height"]');
  window.localStorage.setItem(imageSettingsKey(), JSON.stringify({
    model_spec: values.get('model_spec'),
    resolution_tier: values.get('resolution_tier'),
    aspect_ratio: values.get('aspect_ratio'),
    output_format: values.get('output_format'),
    quantity: values.get('quantity'),
    input_fidelity: values.get('input_fidelity'),
    custom_width: customWidthInput?.dataset.customValue || values.get('custom_width'),
    custom_height: customHeightInput?.dataset.customValue || values.get('custom_height'),
  }));
}

function validCustomPixelDimensions(width, height) {
  return [width, height].every(value => (
    Number.isInteger(value) && value >= 256 && value <= 8192 && value % 16 === 0
  ));
}

function selectedOption(value, selected) {
  return value === selected ? 'selected' : '';
}

function imageSessionResultsHTML() {
  if (state.imageHistoryLoading && !state.imageSessionEntries.length) return `<div class="image-results-empty">
    <div class="image-generation-spinner" aria-hidden="true">↻</div>
    <strong>正在恢复最近结果</strong>
    <span>工作台已经可以使用，历史图片会在加载后显示。</span>
  </div>`;
  if (!state.imageSessionEntries.length) return `<div class="image-results-empty">
    <div class="image-results-empty-icon" aria-hidden="true">✦</div>
    <strong>尚未生成图片</strong>
    <span>选择模型来源，填写画面描述并设置输出参数后开始生成。</span>
  </div>`;
  const cards = [...state.imageSessionEntries].reverse().flatMap(entry => {
    const newestMedia = [...(entry.media || [])].reverse();
    if (entry.status === 'pending') {
      const queued = entry.taskStatus === 'queued';
      const completedActivityLabel = queued ? '其他图片正在排队' : '下一张正在生图';
      return [
      ...newestMedia.map(media => `
      <figure class="image-session-card">
        <span class="image-result-number">#${media.sessionNumber}</span>
        <button class="image-session-preview" type="button" data-image-expand="${media.sessionNumber}" aria-label="放大第 ${media.sessionNumber} 张图片"><img src="${escapeHTML(media.previewUrl)}" alt="生成结果 #${media.sessionNumber}"></button>
        <figcaption><span>已生成，${completedActivityLabel}</span><small>${escapeHTML(entry.logicalModel)}</small></figcaption>
      </figure>`),
      ...Array.from({ length: Math.max(0, entry.quantity - (entry.media || []).length) }, (_, offset) => {
        const activityLabel = queued ? '正在排队' : offset === 0 ? '正在生图' : '等待前一张完成';
        return `
      <figure class="image-session-card image-generation-pending">
        <span class="image-result-number">#${entry.startNumber + (entry.media || []).length + offset}</span>
        <div class="image-session-pending-body"><span class="image-generation-spinner" aria-hidden="true">↻</span><strong>请求 ${(entry.media || []).length + offset + 1} ${activityLabel}</strong></div>
        <figcaption><span>${activityLabel}</span><small>${escapeHTML(entry.logicalModel)}</small></figcaption>
      </figure>`;
      }),
      ];
    }
    if (entry.status === 'failed') {
      const failureLabel = String(entry.message || '').includes('已按超时结束')
        ? '超时退款'
        : String(entry.message || '').includes('上游明确失败') ? '上游明确失败' : '生成未完成';
      return [`
      <figure class="image-session-card image-session-failed">
        <span class="image-result-number">#${entry.startNumber}</span>
        <div class="image-session-pending-body"><strong>生成未完成</strong><span>${escapeHTML(entry.message || '请检查任务状态后重试')}</span></div>
        <figcaption><span>${failureLabel}</span><small>${escapeHTML(entry.logicalModel)}</small></figcaption>
      </figure>`];
    }
    return newestMedia.map(media => `
      <figure class="image-session-card">
        <span class="image-result-number">#${media.sessionNumber}</span>
        <button class="image-session-preview" type="button" data-image-expand="${media.sessionNumber}" aria-label="放大第 ${media.sessionNumber} 张图片"><img src="${escapeHTML(media.previewUrl)}" alt="生成结果 #${media.sessionNumber}"></button>
        <figcaption><span>${escapeHTML(entry.logicalModel)}</span><span class="image-card-actions"><button class="image-download-button" type="button" data-image-download="${media.sessionNumber}" aria-label="下载第 ${media.sessionNumber} 张图片">⇩</button><button class="image-delete-button" type="button" data-image-delete="${escapeHTML(media.media_id)}" aria-label="删除第 ${media.sessionNumber} 张图片">删除</button></span></figcaption>
        <div class="image-creative-actions"><button type="button" data-image-use-reference="${escapeHTML(media.media_id)}">作为参考图</button><button type="button" data-image-reuse-prompt="${escapeHTML(media.media_id)}">复用提示词</button></div>
        <details class="image-parameter-details"><summary data-image-details>参数详情</summary><dl>
          <div><dt>模型</dt><dd>${escapeHTML(entry.logicalModel)}</dd></div>
          <div><dt>清晰度</dt><dd>${escapeHTML(entry.params?.resolution_tier || entry.outputSpec || '—')}</dd></div>
          <div><dt>比例</dt><dd>${escapeHTML(entry.params?.aspect_ratio || '—')}</dd></div>
          <div><dt>格式</dt><dd>${escapeHTML((entry.params?.output_format || media.mime_type?.split('/')[1] || '—').toUpperCase())}</dd></div>
          <div><dt>生成时间</dt><dd>${escapeHTML(formatDate(entry.createdAt || media.created_at))}</dd></div>
        </dl></details>
      </figure>`);
  });
  return `<div class="image-session-grid">${cards.join('')}</div>`;
}

function sessionMedia() {
  return state.imageSessionEntries.flatMap(entry => entry.media || []);
}

function imageEntryForMediaId(mediaId) {
  return state.imageSessionEntries.find(entry => (entry.media || []).some(media => media.media_id === mediaId));
}

function imageFilename(media) {
  const created = new Date(media.created_at || Date.now());
  const stamp = Number.isNaN(created.getTime()) ? new Date() : created;
  const part = value => String(value).padStart(2, '0');
  const timestamp = `${stamp.getUTCFullYear()}${part(stamp.getUTCMonth() + 1)}${part(stamp.getUTCDate())}-${part(stamp.getUTCHours())}${part(stamp.getUTCMinutes())}${part(stamp.getUTCSeconds())}`;
  const extension = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' }[media.mime_type] || 'bin';
  return `${timestamp}-${String(media.sessionNumber).padStart(2, '0')}.${extension}`;
}

function triggerImageDownload(url, filename) {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

async function downloadImageArchive(mediaIds) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  headers.set('Authorization', `Bearer ${state.token}`);
  const response = await window.fetch('/api/v1/media/archive', {
    method: 'POST', headers, body: JSON.stringify({ media_ids: mediaIds }),
  });
  if (!response.ok) throw new Error('全部下载暂不可用');
  const url = window.URL.createObjectURL(await response.blob());
  try {
    const now = new Date();
    triggerImageDownload(url, `generated-images-${now.toISOString().replace(/[-:]/g, '').slice(0, 15)}.zip`);
  } finally {
    window.URL.revokeObjectURL(url);
  }
}

function forgetImageMedia(mediaId) {
  state.imageSessionEntries = state.imageSessionEntries.flatMap(entry => {
    const removed = (entry.media || []).find(media => media.media_id === mediaId);
    if (removed?.previewUrl) {
      window.URL.revokeObjectURL(removed.previewUrl);
      state.previewUrls = state.previewUrls.filter(url => url !== removed.previewUrl);
    }
    const media = (entry.media || []).filter(item => item.media_id !== mediaId);
    return entry.status === 'succeeded' && media.length === 0 ? [] : [{ ...entry, media, quantity: media.length || entry.quantity }];
  });
}

async function deleteImageMedia(mediaId) {
  await api(`/api/v1/media/${encodeURIComponent(mediaId)}`, { method: 'DELETE' });
  forgetImageMedia(mediaId);
  state.user = await api('/api/v1/auth/me');
  renderImageSessionResults();
}

async function restoreRecentImageResults() {
  // Keep this restore routine independently testable and resilient when a
  // cached script evaluates only the workspace function.
  const runBounded = typeof mapWithConcurrency === 'function'
    ? mapWithConcurrency
    : async (items, limit, mapper) => {
      const values = Array.from(items || []);
      const results = new Array(values.length);
      let nextIndex = 0;
      const worker = async () => {
        while (nextIndex < values.length) {
          const index = nextIndex;
          nextIndex += 1;
          results[index] = await mapper(values[index], index);
        }
      };
      await Promise.all(Array.from({ length: Math.min(Math.max(1, limit), values.length) }, worker));
      return results;
    };
  clearImagePreviewUrls();
  state.imageSessionEntries = [];
  state.imageHistoryLoading = true;
  if (typeof renderImageSessionResults === 'function') renderImageSessionResults();
  try {
    const recentTasks = await optionalApi('/api/v1/generation-tasks/recent?limit=100', []);
    const standaloneTasks = recentTasks
      .filter(task => task.canvas_id === null && ['queued', 'running', 'succeeded'].includes(task.status))
      // Restoring every historical task also downloads every result just to
      // paint the first screen. Keep the API's compatibility limit at 100, but
      // only hydrate the newest 24 standalone tasks in the workbench.
      .slice(0, 24)
      .reverse();
    let nextNumber = 1;
    const taskEntries = standaloneTasks.map(task => {
      const startNumber = nextNumber;
      const quantity = Math.max(1, Number(task.quantity) || 1);
      nextNumber += quantity;
      return { task, startNumber, quantity };
    });
    const entries = (await runBounded(taskEntries, 4, async ({ task, startNumber, quantity }) => {
      if (['queued', 'running'].includes(task.status)) return {
        taskId: task.task_id,
        status: 'pending',
        taskStatus: task.status,
        quantity,
        startNumber,
        logicalModel: task.logical_model,
        prompt: task.prompt,
        params: task.params || {},
        createdAt: task.created_at,
        media: [],
      };
      try {
        const taskMedia = await optionalApi(`/api/v1/generation-tasks/${encodeURIComponent(task.task_id)}/media`, []);
        const available = taskMedia.filter(item => item.state === 'temporary'
          && (!item.expires_at || new Date(item.expires_at).getTime() > Date.now()));
        const restored = await runBounded(available, 4, async (item, index) => {
          try {
            return {
              ...item,
              sessionNumber: startNumber + index,
              // The workbench always paints a bounded thumbnail. The original
              // bytes are fetched only when the user opens or downloads one.
              previewUrl: await authenticatedMediaObjectUrl(item, { thumbnail: true }),
            };
          } catch (_error) {
            // An expired or concurrently deleted object is simply absent from the restored 24-hour workspace.
            return null;
          }
        });
        const media = restored.filter(Boolean);
        return media.length ? {
          taskId: task.task_id,
          status: 'succeeded',
          quantity: media.length,
          startNumber,
          logicalModel: task.logical_model,
          prompt: task.prompt,
          params: task.params || {},
          createdAt: task.created_at,
          media,
        } : null;
      } catch (_error) {
        // A single stale task must not prevent the workbench from opening.
        return null;
      }
    })).filter(Boolean);
    if (state.route && !isImageWorkspaceRoute()) return;
    let nextLiveNumber = entries.reduce((total, entry) => total + entry.quantity, 0) + 1;
    const restoredTaskIds = new Set(entries.map(entry => entry.taskId));
    const liveEntries = state.imageSessionEntries.filter(entry => !restoredTaskIds.has(entry.taskId));
    liveEntries.forEach(entry => {
      const oldStartNumber = entry.startNumber;
      entry.startNumber = nextLiveNumber;
      (entry.media || []).forEach(media => {
        media.sessionNumber = nextLiveNumber + Math.max(0, media.sessionNumber - oldStartNumber);
      });
      nextLiveNumber += entry.quantity;
    });
    state.imageSessionEntries = [...entries, ...liveEntries];
    state.imageSessionEntries
      .filter(entry => entry.status === 'pending')
      .forEach(entry => { void observeImageSessionTask(entry); });
  } finally {
    state.imageHistoryLoading = false;
    state.imageHistoryHydrated = true;
    if (!state.route || isImageWorkspaceRoute()) {
      if (typeof renderImageSessionResults === 'function') renderImageSessionResults();
    }
  }
}

function isGptImage2ModelName(model) {
  const normalized = String(model || '').trim().toLowerCase().replaceAll('_', '-');
  return normalized === 'gpt-image-2'
    || normalized.startsWith('gpt-image-2-')
    || normalized.endsWith('-gpt-image-2')
    || normalized.includes('-gpt-image-2-');
}

function imageMaskMediaKey() {
  return `creative_studio_image_mask:${state.user?.user_id || 'account'}`;
}

function maskMediaHTML() {
  const media = state.maskMediaEntry;
  if (!media) return '<span class="field-hint">尚未涂抹遮罩区域</span>';
  return `<div class="image-mask-preview">
    ${media.previewUrl ? `<img src="${escapeHTML(media.previewUrl)}" alt="局部重绘遮罩">` : '<span class="image-generation-spinner" aria-hidden="true">↻</span>'}
    <span><strong>遮罩已就绪</strong><small>涂抹区域将被重绘</small></span>
    <button type="button" data-mask-delete="${escapeHTML(media.media_id)}" aria-label="删除局部重绘遮罩">×</button>
  </div>`;
}

function renderMaskMedia() {
  const container = app.querySelector('[data-mask-list]');
  if (container) container.innerHTML = maskMediaHTML();
  const button = app.querySelector('[data-mask-delete]');
  if (!button) return;
  button.onclick = async () => {
    button.disabled = true;
    try {
      await api(`/api/v1/reference-media/${encodeURIComponent(button.dataset.maskDelete)}`, { method: 'DELETE' });
      if (state.maskMediaEntry?.previewUrl) window.URL.revokeObjectURL(state.maskMediaEntry.previewUrl);
      state.maskMediaEntry = null;
      window.localStorage.removeItem(imageMaskMediaKey());
      renderMaskMedia();
      toast('遮罩已删除');
    } catch (error) {
      button.disabled = false;
      toast(error.message);
    }
  };
}

async function uploadMaskFile(file) {
  if (file.type !== 'image/png') throw new Error('局部重绘遮罩必须是 PNG 文件');
  if (state.maskMediaEntry) {
    await api(`/api/v1/reference-media/${encodeURIComponent(state.maskMediaEntry.media_id)}`, { method: 'DELETE' });
    if (state.maskMediaEntry.previewUrl) window.URL.revokeObjectURL(state.maskMediaEntry.previewUrl);
  }
  const upload = new FormData();
  upload.append('file', file, file.name);
  const media = await api('/api/v1/reference-media', { method: 'POST', body: upload });
  state.maskMediaEntry = { ...media, previewUrl: await authenticatedReferenceObjectUrl(media, { thumbnail: true }) };
  window.localStorage.setItem(imageMaskMediaKey(), media.media_id);
  renderMaskMedia();
}

function imageMaskEditorHTML() {
  return `<div class="image-mask-editor" data-image-mask-editor hidden>
    <div class="image-mask-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="image-mask-editor-title">
      <header class="image-mask-editor-toolbar">
        <div class="image-mask-editor-title"><strong id="image-mask-editor-title">涂抹局部重绘区域</strong><span>白色覆盖处将由模型重新绘制</span></div>
        <div class="image-mask-editor-actions"><button class="secondary-btn" type="button" data-mask-editor-close>关闭</button><button class="primary-btn" type="button" data-mask-editor-complete>完成遮罩</button></div>
      </header>
      <div class="image-mask-editor-controls">
        <button class="secondary-btn" type="button" data-mask-editor-zoom-out>缩小</button>
        <button class="secondary-btn" type="button" data-mask-editor-zoom-in>放大</button>
        <button class="secondary-btn" type="button" data-mask-editor-undo disabled>撤销</button>
        <button class="secondary-btn" type="button" data-mask-editor-clear>清除遮罩</button>
        <label><span>缩放 <b data-mask-editor-zoom-label>100%</b></span><input data-mask-editor-zoom type="range" min="50" max="200" step="10" value="100"></label>
        <label><span>画笔 <b data-mask-editor-brush-label>56px</b></span><input data-mask-editor-brush type="range" min="5" max="200" step="1" value="56"></label>
      </div>
      <div class="image-mask-editor-viewport" data-mask-editor-viewport>
        <div class="image-mask-editor-surface" data-mask-editor-surface><img data-mask-editor-image alt="待局部重绘原图"><canvas data-mask-editor-canvas></canvas></div>
      </div>
      <p class="image-mask-editor-hint">按住鼠标或手指在原图上涂抹；涂抹区域会生成透明 PNG 遮罩，原图尺寸保持不变。</p>
    </div>
  </div>`;
}

function loadMaskEditorImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('原图加载失败，请重新上传后再试'));
    image.src = url;
  });
}

function bindImageMaskEditor() {
  const editor = app.querySelector('[data-image-mask-editor]');
  if (!editor) return;
  const canvas = editor.querySelector('[data-mask-editor-canvas]');
  const image = editor.querySelector('[data-mask-editor-image]');
  const surface = editor.querySelector('[data-mask-editor-surface]');
  const viewport = editor.querySelector('[data-mask-editor-viewport]');
  const zoomInput = editor.querySelector('[data-mask-editor-zoom]');
  const brushInput = editor.querySelector('[data-mask-editor-brush]');
  const zoomLabel = editor.querySelector('[data-mask-editor-zoom-label]');
  const brushLabel = editor.querySelector('[data-mask-editor-brush-label]');
  const undoButton = editor.querySelector('[data-mask-editor-undo]');
  const completeButton = editor.querySelector('[data-mask-editor-complete]');
  let baseScale = 1;
  let drawing = false;
  let lastPoint = null;
  let history = [];

  const context = () => canvas.getContext('2d');
  const updateUndo = () => { undoButton.disabled = history.length === 0; };
  const refreshSurfaceSize = () => {
    if (!canvas.width || !canvas.height) return;
    const zoom = Number(zoomInput.value) / 100;
    surface.style.width = `${Math.round(canvas.width * baseScale * zoom)}px`;
    surface.style.height = `${Math.round(canvas.height * baseScale * zoom)}px`;
    zoomLabel.textContent = `${zoomInput.value}%`;
  };
  const pointFromEvent = event => {
    const bounds = canvas.getBoundingClientRect();
    return { x: (event.clientX - bounds.left) * canvas.width / Math.max(1, bounds.width), y: (event.clientY - bounds.top) * canvas.height / Math.max(1, bounds.height) };
  };
  const paintLine = (from, to) => {
    const ctx = context();
    ctx.save();
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.lineWidth = Number(brushInput.value);
    ctx.strokeStyle = 'rgba(255,255,255,.56)';
    ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke(); ctx.restore();
  };
  const close = () => { editor.hidden = true; drawing = false; lastPoint = null; document.body.classList.remove('image-mask-editor-open'); };
  const open = async () => {
    const source = state.referenceMediaEntries[0];
    if (!source) return toast('请先上传待编辑原图');
    if (!source.previewUrl) return toast('原图正在加载，请稍后再试');
    try {
      // Reference cards use a thumbnail; the editor is the explicit point at
      // which the single original image is fetched.
      const originalUrl = await loadOriginalReferenceUrl(source);
      const loaded = await loadMaskEditorImage(originalUrl);
      image.src = originalUrl;
      canvas.width = loaded.naturalWidth; canvas.height = loaded.naturalHeight;
      context().clearRect(0, 0, canvas.width, canvas.height);
      history = []; updateUndo(); editor.hidden = false;
      document.body.classList.add('image-mask-editor-open');
      await new Promise(resolve => requestAnimationFrame(resolve));
      baseScale = Math.min(1, Math.max(.08, (viewport.clientWidth - 48) / canvas.width), Math.max(.08, (viewport.clientHeight - 48) / canvas.height));
      zoomInput.value = '100'; refreshSurfaceSize();
    } catch (error) { toast(error.message); }
  };

  app.querySelector('[data-mask-editor-open]')?.addEventListener('click', open);
  editor.querySelector('[data-mask-editor-close]')?.addEventListener('click', close);
  editor.addEventListener('click', event => { if (event.target === editor) close(); });
  zoomInput.addEventListener('input', refreshSurfaceSize);
  brushInput.addEventListener('input', () => { brushLabel.textContent = `${brushInput.value}px`; });
  editor.querySelector('[data-mask-editor-zoom-out]')?.addEventListener('click', () => { zoomInput.value = String(Math.max(50, Number(zoomInput.value) - 10)); refreshSurfaceSize(); });
  editor.querySelector('[data-mask-editor-zoom-in]')?.addEventListener('click', () => { zoomInput.value = String(Math.min(200, Number(zoomInput.value) + 10)); refreshSurfaceSize(); });
  editor.querySelector('[data-mask-editor-clear]')?.addEventListener('click', () => {
    if (!canvas.width) return;
    history.push(context().getImageData(0, 0, canvas.width, canvas.height));
    if (history.length > 20) history.shift();
    context().clearRect(0, 0, canvas.width, canvas.height); updateUndo();
  });
  undoButton.addEventListener('click', () => { const snapshot = history.pop(); if (snapshot) context().putImageData(snapshot, 0, 0); updateUndo(); });
  canvas.addEventListener('pointerdown', event => {
    event.preventDefault(); canvas.setPointerCapture?.(event.pointerId);
    history.push(context().getImageData(0, 0, canvas.width, canvas.height));
    if (history.length > 20) history.shift();
    updateUndo(); drawing = true; lastPoint = pointFromEvent(event);
    paintLine(lastPoint, { x: lastPoint.x + .01, y: lastPoint.y + .01 });
  });
  canvas.addEventListener('pointermove', event => { if (!drawing) return; event.preventDefault(); const point = pointFromEvent(event); paintLine(lastPoint, point); lastPoint = point; });
  const stopDrawing = event => { if (event?.pointerId != null) canvas.releasePointerCapture?.(event.pointerId); drawing = false; lastPoint = null; };
  canvas.addEventListener('pointerup', stopDrawing); canvas.addEventListener('pointercancel', stopDrawing);
  completeButton.addEventListener('click', async () => {
    const sourcePixels = context().getImageData(0, 0, canvas.width, canvas.height);
    let painted = false;
    const maskCanvas = document.createElement('canvas'); maskCanvas.width = canvas.width; maskCanvas.height = canvas.height;
    const maskContext = maskCanvas.getContext('2d'); const maskPixels = maskContext.createImageData(canvas.width, canvas.height);
    for (let offset = 0; offset < sourcePixels.data.length; offset += 4) {
      const selected = sourcePixels.data[offset + 3] > 8; painted ||= selected;
      maskPixels.data[offset] = 0; maskPixels.data[offset + 1] = 0; maskPixels.data[offset + 2] = 0;
      maskPixels.data[offset + 3] = selected ? 0 : 255;
    }
    if (!painted) return toast('请先涂抹需要重绘的区域');
    maskContext.putImageData(maskPixels, 0, 0);
    completeButton.disabled = true; completeButton.textContent = '正在保存…';
    try {
      const blob = await new Promise(resolve => maskCanvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('遮罩生成失败，请重试');
      await uploadMaskFile(new File([blob], 'inpainting-mask.png', { type: 'image/png' }));
      close(); toast('遮罩区域已保存');
    } catch (error) { toast(error.message); }
    finally { completeButton.disabled = false; completeButton.textContent = '完成遮罩'; }
  });
}

async function completeImageSessionEntry(entry, task) {
  const media = await api(`/api/v1/generation-tasks/${encodeURIComponent(task.task_id)}/media`);
  const uniqueMedia = [...new Map((Array.isArray(media) ? media : []).map(item => [
    item.media_id || item.result_reference,
    item,
  ])).values()];
  entry.media = await Promise.all(uniqueMedia.map(async (item, index) => ({
      ...item,
      sessionNumber: entry.startNumber + index,
      previewUrl: await authenticatedMediaObjectUrl(item, { thumbnail: true }),
  })));
  entry.status = 'succeeded';
  entry.taskId = task.task_id;
  entry.createdAt = task.created_at;
  state.user = await api('/api/v1/auth/me');
  renderImageSessionResults();
}

async function updateImageSessionMedia(entry, media) {
  const existingIds = new Set((entry.media || []).map(item => item.media_id));
  const additions = media.filter(item => !existingIds.has(item.media_id));
  if (!additions.length) return;
  const startOffset = (entry.media || []).length;
  const hydrated = await Promise.all(additions.map(async (item, index) => ({
    ...item,
    sessionNumber: entry.startNumber + startOffset + index,
    previewUrl: await authenticatedMediaObjectUrl(item, { thumbnail: true }),
  })));
  entry.media = [...(entry.media || []), ...hydrated];
  renderImageSessionResults();
}

async function loadOriginalMediaUrl(media) {
  if (!media) return '';
  if (!media.originalUrl) media.originalUrl = await authenticatedMediaObjectUrl(media);
  return media.originalUrl;
}

async function pollImageSessionTask(taskId, onTask = null) {
  let delayMs = 1200;
  for (let attempt = 0; attempt < 300; attempt += 1) {
    if (attempt) {
      await new Promise(resolve => window.setTimeout(resolve, delayMs));
      delayMs = Math.min(5000, Math.round(delayMs * 1.35));
    }
    const current = await api(`/api/v1/generation-tasks/${encodeURIComponent(taskId)}`);
    if (onTask) await onTask(current);
    if (['succeeded', 'failed', 'cancelled'].includes(current.status)) return current;
  }
  throw new Error('任务状态查询超时，请稍后重新进入页面查看结果');
}

async function observeImageSessionTask(entry) {
  if (!entry?.taskId || entry.status !== 'pending') return;
  const observers = state.imageTaskObservers || (state.imageTaskObservers = new Set());
  if (observers.has(entry.taskId)) return;
  observers.add(entry.taskId);
  try {
    {
      const onTask = current => {
        entry.taskStatus = current.status;
        renderImageSessionResults();
      };
      let task;
      if (typeof streamGenerationTask === 'function') {
        try {
          task = await streamGenerationTask(
            entry.taskId,
            media => updateImageSessionMedia(entry, media),
            onTask,
          );
        } catch (_) {
          task = await pollImageSessionTask(entry.taskId, onTask);
        }
      } else {
        task = await pollImageSessionTask(entry.taskId, onTask);
      }
      if (task.status === 'succeeded') {
        await completeImageSessionEntry(entry, task);
        toast(task.partial_delivery
          ? (task.completion_message || `上游仅完成 ${entry.media.length}/${entry.quantity} 张`)
          : `已生成 ${entry.media.length} 张图片`);
        return;
      }
      if (['failed', 'cancelled'].includes(task.status)) {
        entry.status = 'failed';
        entry.message = task.failure_message || '图片生成未完成';
        renderImageSessionResults();
        return;
      }
    }
  } catch (error) {
    toast(error.message || '任务状态暂时不可用');
  } finally {
    observers.delete(entry.taskId);
  }
}

async function continueImageSessionEntry(entry, task) {
  entry.taskId = task.task_id;
  entry.createdAt = task.created_at;
  if (task.status === 'succeeded') {
    await completeImageSessionEntry(entry, task);
    return;
  }
  if (['queued', 'running'].includes(task.status)) {
    entry.status = 'pending';
    entry.taskStatus = task.status;
    renderImageSessionResults();
    void observeImageSessionTask(entry);
    return;
  }
  throw new Error(task.failure_message || '图片生成未完成');
}

function openImageLightbox(source, alt) {
  const lightbox = app.querySelector('[data-image-lightbox]');
  const lightboxImage = lightbox?.querySelector('img');
  if (!lightbox || !lightboxImage || !source) return;
  const transform = (lightbox._imageTransform ||= { scale: 1, x: 0, y: 0 });
  transform.scale = 1;
  transform.x = 0;
  transform.y = 0;
  lightboxImage.src = source;
  lightboxImage.alt = alt;
  lightboxImage.style.transform = 'translate(0px, 0px) scale(1)';
  lightbox.hidden = false;
}

function bindImageSessionActions() {
  app.querySelectorAll('[data-image-download]').forEach(button => { button.onclick = async () => {
    const media = sessionMedia().find(item => item.sessionNumber === Number(button.dataset.imageDownload));
    if (!media) return;
    button.disabled = true;
    try { triggerImageDownload(await loadOriginalMediaUrl(media), imageFilename(media)); }
    catch (error) { toast(error.message || '原图暂时无法加载'); }
    finally { button.disabled = false; }
  }; });
  const lightbox = app.querySelector('[data-image-lightbox]');
  const lightboxStage = lightbox?.querySelector('[data-image-lightbox-stage]');
  const lightboxImage = lightbox?.querySelector('img');
  const transform = lightbox ? (lightbox._imageTransform ||= { scale: 1, x: 0, y: 0 }) : null;
  const applyLightboxTransform = () => {
    if (!lightboxImage || !transform) return;
    lightboxImage.style.transform = `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`;
  };
  const resetLightboxTransform = () => {
    if (!transform) return;
    transform.scale = 1;
    transform.x = 0;
    transform.y = 0;
    applyLightboxTransform();
  };
  app.querySelectorAll('[data-image-expand]').forEach(button => { button.onclick = async () => {
    const media = sessionMedia().find(item => item.sessionNumber === Number(button.dataset.imageExpand));
    if (!media) return;
    button.disabled = true;
    try { openImageLightbox(await loadOriginalMediaUrl(media), `生成结果 #${media.sessionNumber}`); }
    catch (error) { toast(error.message || '原图暂时无法加载'); }
    finally { button.disabled = false; }
  }; });
  if (lightboxStage && !lightboxStage._imageInteractionBound) {
    lightboxStage._imageInteractionBound = true;
    lightboxStage.addEventListener('wheel', event => {
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
      transform.scale = Math.max(1, Math.min(8, transform.scale * factor));
      if (transform.scale === 1) { transform.x = 0; transform.y = 0; }
      applyLightboxTransform();
    }, { passive: false });
    lightboxStage.addEventListener('pointerdown', event => {
      if (transform.scale <= 1) return;
      lightboxStage.setPointerCapture(event.pointerId);
      transform.drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    });
    lightboxStage.addEventListener('pointermove', event => {
      if (!transform.drag || transform.drag.pointerId !== event.pointerId) return;
      transform.x += event.clientX - transform.drag.x;
      transform.y += event.clientY - transform.drag.y;
      transform.drag.x = event.clientX;
      transform.drag.y = event.clientY;
      applyLightboxTransform();
    });
    const stopDrag = event => {
      if (transform.drag?.pointerId === event.pointerId) delete transform.drag;
    };
    lightboxStage.addEventListener('pointerup', stopDrag);
    lightboxStage.addEventListener('pointercancel', stopDrag);
    lightboxStage.addEventListener('dblclick', resetLightboxTransform);
  }
  const changeZoom = factor => {
    if (!transform) return;
    transform.scale = Math.max(1, Math.min(8, transform.scale * factor));
    if (transform.scale === 1) { transform.x = 0; transform.y = 0; }
    applyLightboxTransform();
  };
  const zoomIn = app.querySelector('[data-image-zoom-in]');
  if (zoomIn) zoomIn.onclick = () => changeZoom(1.25);
  const zoomOut = app.querySelector('[data-image-zoom-out]');
  if (zoomOut) zoomOut.onclick = () => changeZoom(0.8);
  const zoomReset = app.querySelector('[data-image-zoom-reset]');
  if (zoomReset) zoomReset.onclick = resetLightboxTransform;
  const lightboxClose = app.querySelector('[data-image-lightbox-close]');
  if (lightboxClose) lightboxClose.onclick = () => { lightbox.hidden = true; resetLightboxTransform(); };
  app.querySelectorAll('[data-image-reuse-prompt]').forEach(button => { button.onclick = () => {
    const entry = imageEntryForMediaId(button.dataset.imageReusePrompt);
    const prompt = app.querySelector('[data-image-generation-form] [name="prompt"]');
    if (!entry || !prompt) return;
    prompt.value = entry.prompt || '';
    prompt.focus();
    toast('提示词已复用');
  }; });
  app.querySelectorAll('[data-image-use-reference]').forEach(button => { button.onclick = async () => {
    if (state.referenceMediaEntries.length >= state.imageReferenceLimit) return toast(`当前模型最多上传 ${state.imageReferenceLimit} 张参考图，请先删除不用的图片`);
    button.disabled = true;
    try {
      const reference = await api(`/api/v1/media/${encodeURIComponent(button.dataset.imageUseReference)}/use-as-reference`, { method: 'POST' });
      state.referenceMediaEntries.push({ ...reference, previewUrl: await authenticatedReferenceObjectUrl(reference, { thumbnail: true }) });
      renderReferenceMediaList();
      toast('已添加为参考图');
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  }; });
  app.querySelectorAll('[data-image-delete]').forEach(button => { button.onclick = async () => {
    if (!await centeredDeleteConfirm('删除后不可恢复，确定删除这张图片吗？')) return;
    button.disabled = true;
    try {
      await deleteImageMedia(button.dataset.imageDelete);
      toast('图片已删除，存储空间已释放');
    } catch (error) {
      button.disabled = false;
      toast(error.message);
    }
  }; });
  const clearButton = app.querySelector('[data-image-clear]');
  if (clearButton) clearButton.onclick = async () => {
    const media = sessionMedia();
    if (!media.length || !await centeredDeleteConfirm('清空会永久删除当前全部生成结果，且不可恢复。是否继续？', '确认清空')) return;
    clearButton.disabled = true;
    try {
      for (const item of media) await deleteImageMedia(item.media_id);
      toast('生成结果已清空，存储空间已释放');
    } catch (error) {
      clearButton.disabled = false;
      toast(error.message);
    }
  };
  const downloadAllButton = app.querySelector('[data-image-download-all]');
  if (downloadAllButton) downloadAllButton.onclick = async () => {
    const media = sessionMedia();
    if (!media.length) return toast('当前页面还没有可下载图片');
    downloadAllButton.disabled = true;
    try {
      await downloadImageArchive(media.map(item => item.media_id));
    } catch (error) {
      toast(error.message);
    } finally {
      downloadAllButton.disabled = false;
    }
  };
}

function renderImageSessionResults() {
  const body = app.querySelector('.image-workbench-results-body');
  if (body) {
    body.innerHTML = imageSessionResultsHTML();
    body.scrollTop = 0;
  }
  bindImageSessionActions();
}

function workspaceImagesPage(forcedOperation = '') {
  try {
    const inpaintingPage = forcedOperation === 'inpaint';
    const settings = loadImageSettings();
    const resolutionTier = ['1k', '2k', '4k'].includes(settings.resolution_tier) ? settings.resolution_tier : '4k';
    const aspectRatio = ['1:1', '4:3', '16:9', '3:4', '9:16', 'custom'].includes(settings.aspect_ratio) ? settings.aspect_ratio : '1:1';
    const customWidth = Number(settings.custom_width) || 1024;
    const customHeight = Number(settings.custom_height) || 1024;
    const outputFormat = ['png', 'jpeg', 'webp'].includes(settings.output_format) ? settings.output_format : 'png';
    const quantity = Math.max(1, Math.min(5, Number(settings.quantity) || 1));
    const operation = inpaintingPage ? 'inpaint' : state.referenceMediaEntries.length ? 'edit' : 'generate';
    const inputFidelity = ['auto', 'low', 'high'].includes(settings.input_fidelity) ? settings.input_fidelity : 'high';
    shell(inpaintingPage ? '局部重绘' : '图片生成', `<form class="image-generation-form image-workbench${inpaintingPage ? ' image-workbench-inpainting' : ''}" data-image-generation-form data-image-form-ready="false">
      <aside class="image-workbench-sidebar">
        <section class="image-workbench-card image-model-card">
          <div class="image-workbench-card-head"><div><h2>模型来源</h2><p>选择平台已发布且当前可用的模型配置。</p></div><span class="image-workbench-badge">平台模型</span></div>
          <div class="field"><label>模型</label><select name="model_spec" required disabled><option value="">正在加载可用图片模型…</option></select></div>
          ${inpaintingPage ? '<input name="operation" type="hidden" value="inpaint"><div class="image-inpainting-mode"><strong>局部重绘模式</strong><span>适用于 gpt-image-2 系列模型</span></div>' : ''}
        </section>
        <section class="image-workbench-card image-content-card">
          <div class="image-workbench-card-head"><div><h2>${inpaintingPage ? '重绘素材' : '创作内容'}</h2><p data-image-operation-help>${inpaintingPage ? '上传原图并涂抹重绘区域。' : '参考图数量按所选模型联动。'}</p></div><span class="image-workbench-badge" data-image-operation-badge>${inpaintingPage ? '局部重绘' : '文生图'}</span></div>
          <div class="field image-reference-field"><label>${inpaintingPage ? '待编辑原图（第 1 张）' : '参考图片'}</label><label class="image-reference-dropzone"><input name="references" type="file" accept="image/png,image/jpeg,image/webp" multiple><span class="image-reference-icon" aria-hidden="true">⇧</span><span><strong>${inpaintingPage ? '选择原图，可追加上下文参考图' : '选择一张或多张参考图片'}</strong><small class="image-reference-notice"><b aria-hidden="true">!</b><span data-reference-limit-copy>支持 PNG、JPEG、WebP，当前模型最多 ${state.imageReferenceLimit} 张参考图；刷新后仍保留 24 小时</span></small></span></label><div data-reference-list style="--reference-list-height:${imageReferenceListHeight()}px">${referenceMediaListHTML()}</div></div>
          ${inpaintingPage ? `<div class="field image-mask-field" data-image-mask-field><label>局部重绘遮罩</label><button class="image-mask-editor-launch" type="button" data-mask-editor-open><span class="image-reference-icon" aria-hidden="true">✎</span><span><strong>${state.maskMediaEntry ? '重新编辑遮罩区域' : '打开遮罩编辑器'}</strong><small>在原图上直接涂抹需要重绘的位置，无需上传遮罩图片。</small></span></button><div data-mask-list>${maskMediaHTML()}</div></div>` : ''}
        </section>
        <section class="image-workbench-card image-output-card">
          <div class="image-workbench-card-head"><div><h2>输出设置</h2><p>选择尺寸、格式和数量。</p></div></div>
          <div class="image-output-grid">
            <div class="field"><label>清晰度</label><select name="resolution_tier"><option value="1k" ${selectedOption('1k', resolutionTier)}>1K</option><option value="2k" ${selectedOption('2k', resolutionTier)}>2K</option><option value="4k" ${selectedOption('4k', resolutionTier)}>4K</option></select></div>
            <div class="field"><label>预设尺寸</label><select name="aspect_ratio"><option value="1:1" ${selectedOption('1:1', aspectRatio)}>1:1</option><option value="4:3" ${selectedOption('4:3', aspectRatio)}>4:3</option><option value="16:9" ${selectedOption('16:9', aspectRatio)}>16:9</option><option value="3:4" ${selectedOption('3:4', aspectRatio)}>3:4</option><option value="9:16" ${selectedOption('9:16', aspectRatio)}>9:16</option><option value="custom" ${selectedOption('custom', aspectRatio)}>自定义尺寸</option></select></div>
            <div class="image-custom-size${aspectRatio === 'custom' ? ' is-active' : ''}" data-image-custom-size aria-disabled="${aspectRatio === 'custom' ? 'false' : 'true'}">
              <div class="image-custom-size-head"><strong>自定义像素</strong><span>宽高必须是 16 的倍数，范围 256–8192 像素</span></div>
              <div class="image-custom-size-inputs"><label><span>宽</span><input name="custom_width" type="number" min="256" max="8192" step="16" value="${customWidth}" data-custom-value="${customWidth}" ${aspectRatio === 'custom' ? '' : 'readonly'}></label><b>×</b><label><span>高</span><input name="custom_height" type="number" min="256" max="8192" step="16" value="${customHeight}" data-custom-value="${customHeight}" ${aspectRatio === 'custom' ? '' : 'readonly'}></label></div>
            </div>
            <div class="field"><label>格式</label><select name="output_format"><option value="png" ${selectedOption('png', outputFormat)}>PNG</option><option value="jpeg" ${selectedOption('jpeg', outputFormat)}>JPEG</option><option value="webp" ${selectedOption('webp', outputFormat)}>WEBP</option></select></div>
            <div class="field"><label>生成数量</label><select name="quantity">${Array.from({ length: 5 }, (_, index) => `<option value="${index + 1}" ${selectedOption(String(index + 1), String(quantity))}>${index + 1} 张（${index + 1} 次请求）</option>`).join('')}</select></div>
            <div class="field image-fidelity-field" data-image-fidelity-field><label>输入保真度</label><select name="input_fidelity"><option value="auto" ${selectedOption('auto', inputFidelity)}>自动</option><option value="low" ${selectedOption('low', inputFidelity)}>低</option><option value="high" ${selectedOption('high', inputFidelity)}>高</option></select></div>
          </div>
          <div class="image-output-summary"><span>本次输出</span><strong data-image-output-summary></strong></div>
        </section>
      </aside>
      <section class="image-workbench-results">
        <div class="image-workbench-results-head"><div><h2>生成结果</h2><p>结果保存24小时，但占用个人存储空间，不用请及时删除</p></div><div class="image-result-actions"><button class="secondary-btn" type="button" data-image-download-all>⇩ 全部下载</button><button class="text-btn" type="button" data-image-clear>清空</button></div></div>
        <div class="image-workbench-results-body">${imageSessionResultsHTML()}</div>
      </section>
      <section class="image-chat-composer" aria-label="画面描述">
        <div class="image-chat-input"><textarea id="image-prompt-input" name="prompt" rows="6" required maxlength="8000" aria-label="画面描述" placeholder="${inpaintingPage ? '例如：将人物身后的墙面改成暖色木饰面，保留人物和光影…' : '输入主体、构图、风格、光线等画面要求…'}"></textarea><button class="primary-btn image-generate-button" type="submit" data-image-generate-button disabled>正在加载…</button></div>
      </section>
      <div class="image-lightbox" data-image-lightbox hidden><div class="image-lightbox-stage" data-image-lightbox-stage><img alt="生成结果大图"></div><div class="image-lightbox-toolbar"><button type="button" data-image-zoom-out aria-label="缩小">−</button><button type="button" data-image-zoom-reset aria-label="恢复适配">适配</button><button type="button" data-image-zoom-in aria-label="放大">＋</button></div><button class="image-lightbox-close" type="button" data-image-lightbox-close aria-label="关闭大图">×</button></div>
      ${inpaintingPage ? imageMaskEditorHTML() : ''}
    </form>`, 'image-workbench-page');

    const form = app.querySelector('[data-image-generation-form]');
    const referenceInput = form?.querySelector('[name="references"]');
    referenceInput?.addEventListener('change', async () => {
      const files = Array.from(referenceInput.files || []);
      referenceInput.value = '';
      if (!files.length) return;
      try {
        await uploadSelectedReferenceFiles(files);
      } catch (error) {
        toast(error.message);
      }
    });
    bindImageMaskEditor();
    const updateOperationUI = () => {
      if (!form) return;
      const mode = inpaintingPage ? 'inpaint' : state.referenceMediaEntries.length ? 'edit' : 'generate';
      const maskField = form.querySelector('[data-image-mask-field]');
      const fidelityField = form.querySelector('[data-image-fidelity-field]');
      const badge = form.querySelector('[data-image-operation-badge]');
      const help = form.querySelector('[data-image-operation-help]');
      if (maskField) maskField.hidden = mode !== 'inpaint';
      if (fidelityField) fidelityField.hidden = false;
      if (badge) badge.textContent = mode === 'inpaint' ? '局部重绘' : mode === 'edit' ? '图像编辑' : '文生图';
      if (help) help.textContent = mode === 'inpaint'
        ? '第 1 张参考图是待编辑原图，请在内置编辑器中涂抹重绘区域。'
        : mode === 'edit' ? '至少上传 1 张待编辑图片，可追加上下文参考图。' : '描述画面，可选上传参考图后切换为图片编辑。';
    };
    const updateOutputSummary = () => {
      if (!form) return;
      const tier = form.querySelector('[name="resolution_tier"]')?.value || '4k';
      const ratio = form.querySelector('[name="aspect_ratio"]')?.value || '1:1';
      const isCustom = ratio === 'custom';
      const customSize = form.querySelector('[data-image-custom-size]');
      const resolutionSelect = form.querySelector('[name="resolution_tier"]');
      const widthInput = form.querySelector('[name="custom_width"]');
      const heightInput = form.querySelector('[name="custom_height"]');
      const presetDimensions = (imageSizeByOutput[`${tier}|${ratio}`] || '').match(/\d+/g) || [];
      if (customSize) {
        customSize.classList.toggle('is-active', isCustom);
        customSize.setAttribute('aria-disabled', String(!isCustom));
        [widthInput, heightInput].forEach((input, index) => {
          if (!input) return;
          if (isCustom) {
            if (input.readOnly) input.value = input.dataset.customValue || input.value;
            input.readOnly = false;
            input.dataset.customValue = input.value;
          } else {
            if (!input.readOnly) input.dataset.customValue = input.value;
            input.value = presetDimensions[index] || '';
            input.readOnly = true;
          }
        });
      }
      if (resolutionSelect) resolutionSelect.disabled = isCustom;
      const format = form.querySelector('[name="output_format"]')?.value || 'png';
      const quantity = Math.max(1, Math.min(5, Number(form.querySelector('[name="quantity"]')?.value) || 1));
      const summary = form.querySelector('[data-image-output-summary]');
      const button = form.querySelector('[data-image-generate-button]');
      const width = Number(widthInput?.value);
      const height = Number(heightInput?.value);
      if (summary) summary.textContent = `${isCustom ? `${width || '—'} × ${height || '—'}` : imageSizeByOutput[`${tier}|${ratio}`]} · ${isCustom ? '自定义' : tier.toUpperCase()} · ${format.toUpperCase()}`;
      if (button) {
        const ready = form.dataset.imageFormReady === 'true';
        button.textContent = ready ? `✦ 生成 ${quantity} 张图片（Ctrl + Enter）` : '正在加载模型…';
        button.disabled = !ready;
      }
    };
    form?.addEventListener('change', event => {
      if (event.target.matches('[name="model_spec"]')) syncImageReferenceLimit(form);
      updateOperationUI();
      updateOutputSummary();
      saveImageSettings(form);
    });
    form?.addEventListener('input', event => {
      if (event.target.matches('[name="custom_width"], [name="custom_height"]')) {
        updateOutputSummary();
        saveImageSettings(form);
      }
    });
    updateOutputSummary();
    updateOperationUI();
    const promptInput = form?.querySelector('[name="prompt"]');
    promptInput?.addEventListener('keydown', event => {
      if (event.isComposing || !event.ctrlKey || event.key !== 'Enter') return;
      const button = form.querySelector('[data-image-generate-button]');
      if (!button || button.disabled) return;
      event.preventDefault();
      form.requestSubmit(button);
    });
    renderReferenceMediaList();
    renderMaskMedia();
    bindImageSessionActions();
    const restores = [];
    if (!state.imageHistoryHydrated) restores.push(restoreRecentImageResults());
    if (!state.referenceMediaHydrated) restores.push(restoreRecentReferenceMedia());
    void Promise.all(restores).catch(error => {
      if (isImageWorkspaceRoute()) toast(error.message || '最近图片暂时无法恢复');
    });
    void Promise.all([
      ensureAccountSummary(),
      optionalApi('/api/v1/image-models', { data: [] }),
    ]).then(([, catalog]) => {
      if (!isImageWorkspaceRoute()) return;
      const currentForm = app.querySelector('[data-image-generation-form]');
      const modelSelect = currentForm?.querySelector('[name="model_spec"]');
      if (!currentForm || !modelSelect) return;
      modelSelect.innerHTML = imageGenerationModelOptions(catalog.data || [], settings.model_spec);
      const hasAvailableModel = Array.from(modelSelect.options)
        .some(option => option.value && !option.disabled);
      modelSelect.disabled = !hasAvailableModel;
      currentForm.dataset.imageFormReady = hasAvailableModel ? 'true' : 'false';
      syncImageReferenceLimit(currentForm);
      updateOutputSummary();
    }).catch(error => {
      if (!state.token) return navigate('/login', { replace: true });
      if (!isImageWorkspaceRoute()) return;
      const modelSelect = app.querySelector('[data-image-generation-form] [name="model_spec"]');
      if (modelSelect) modelSelect.innerHTML = '<option value="">模型目录暂时无法加载</option>';
      toast(error.message);
    });
    form?.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      if (Number(state.user?.storage_allowance?.available_bytes || 0) < 10_000_000) {
        return toast('个人存储空间不足 10MB，请清理后再生成');
      }
      const button = event.currentTarget.querySelector('[type="submit"]');
      const [logicalModel, outputSpec] = String(values.get('model_spec')).split('|||');
      const requestedQuantity = Number(values.get('quantity'));
      const selectedOperation = inpaintingPage ? 'inpaint' : state.referenceMediaEntries.length ? 'edit' : 'generate';
      const customSizeSelected = values.get('aspect_ratio') === 'custom';
      const customWidthValue = Number(values.get('custom_width'));
      const customHeightValue = Number(values.get('custom_height'));
      if (customSizeSelected && !validCustomPixelDimensions(customWidthValue, customHeightValue)) {
        return centeredNotice('自定义像素尺寸不对，请修改！');
      }
      if (selectedOperation === 'inpaint' && !isGptImage2ModelName(logicalModel)) return toast('局部重绘当前仅支持 gpt-image-2');
      if (selectedOperation !== 'generate' && !state.referenceMediaEntries.length) return toast('图片编辑必须上传至少 1 张原图');
      if (state.referenceMediaEntries.length > state.imageReferenceLimit) return toast(`当前模型最多上传 ${state.imageReferenceLimit} 张参考图，请删除多余图片`);
      if (selectedOperation === 'inpaint' && !state.maskMediaEntry) return toast('请先打开遮罩编辑器并涂抹重绘区域');
      const startNumber = state.imageSessionEntries.reduce((total, entry) => total + entry.quantity, 0) + 1;
      const entry = {
        status: 'pending', quantity: requestedQuantity, startNumber, logicalModel,
        prompt: values.get('prompt'),
        params: {
          aspect_ratio: values.get('aspect_ratio'),
          resolution_tier: customSizeSelected ? '' : values.get('resolution_tier'),
          size: customSizeSelected ? `${customWidthValue}x${customHeightValue}` : '',
          output_format: values.get('output_format'),
          operation: selectedOperation,
          input_fidelity: selectedOperation === 'generate' ? 'auto' : values.get('input_fidelity') || 'auto',
        },
        media: [],
      };
      state.imageSessionEntries.push(entry);
      renderImageSessionResults();
      button.disabled = true;
      try {
        const referenceMediaIds = state.referenceMediaEntries.map(media => media.media_id);
        const task = await api('/api/v1/generation-tasks', {
          method: 'POST',
          body: JSON.stringify({
            task_id: window.crypto.randomUUID(),
            logical_model: logicalModel,
            output_spec: outputSpec,
            quantity: requestedQuantity,
            prompt: values.get('prompt'),
            params: {
              aspect_ratio: values.get('aspect_ratio'),
              resolution_tier: customSizeSelected ? '' : values.get('resolution_tier'),
              size: customSizeSelected ? `${customWidthValue}x${customHeightValue}` : '',
              output_format: values.get('output_format'),
              operation: selectedOperation,
              input_fidelity: selectedOperation === 'generate' ? 'auto' : values.get('input_fidelity') || 'auto',
            },
            reference_media_ids: referenceMediaIds,
            mask_media_id: selectedOperation === 'inpaint' ? state.maskMediaEntry.media_id : '',
          }),
        });
        await continueImageSessionEntry(entry, task);
        if (entry.status === 'succeeded') toast(`已生成 ${entry.media.length} 张图片`);
      } catch (error) {
        entry.status = 'failed';
        entry.message = error.message;
        renderImageSessionResults();
        toast(error.message);
      } finally {
        button.disabled = false;
      }
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell(forcedOperation === 'inpaint' ? '局部重绘' : '图片生成', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

function workspaceInpaintingPage() {
  return workspaceImagesPage('inpaint');
}

const generationTaskStatusLabel = {
  queued: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '生成失败',
  cancelled: '已取消',
};

function generationMediaStateLabel(media) {
  if (media.state === 'temporary') return `临时可用至 ${formatDate(media.expires_at)}`;
  if (media.state === 'persistent') return '已保留';
  if (media.state === 'expired') return '已过期';
  if (media.state === 'released') return '已释放';
  return '状态未知';
}

function generationResultSummary(task) {
  if (task.failure_message) return escapeHTML(task.failure_message);
  if (task.status !== 'succeeded') return '—';
  const results = task.results || [];
  const delivered = task.delivered_quantity ?? results.length;
  const states = results.map(media => generationMediaStateLabel(media)).join('；');
  return `<strong>已交付 ${escapeHTML(delivered)} 项</strong>${states ? `<br><span>${escapeHTML(states)}</span>` : ''}`;
}

function generationTaskSourceLabel(task, canvasesById) {
  if (!task.canvas_id) return '文生图';
  const canvas = canvasesById.get(task.canvas_id);
  if (!canvas) return '已删除画布';
  const kind = canvas.kind === 'smart' ? '智能画布' : '已停用画布';
  return `“${canvas.title || '未命名画布'}”-${kind}`;
}

function recentGenerationTasksTable(tasks) {
  if (!tasks.length) return '<div class="empty">当前账户空间还没有生成任务。</div>';
  return `<div class="table-wrap generation-history-table"><table><thead><tr><th>任务来源</th><th>逻辑模型</th><th>成品规格</th><th>请求数量</th><th>提交时冻结额度</th><th>状态</th><th>结果提示</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${tasks.map(task => `<tr><td><strong>${escapeHTML(task.source_label)}</strong></td><td>${escapeHTML(task.logical_model)}</td><td>${escapeHTML(task.output_spec)}</td><td>${escapeHTML(task.quantity)}</td><td>${formatCredits(task.frozen_credits)}</td><td><span class="status ${escapeHTML(task.status)}">${escapeHTML(generationTaskStatusLabel[task.status] || task.status)}</span></td><td>${generationResultSummary(task)}</td><td>${formatDate(task.updated_at)}</td><td><button class="text-btn" type="button" data-generation-view="${escapeHTML(task.task_id)}">查看</button></td></tr>`).join('')}</tbody></table></div>`;
}

async function openGenerationTaskViewer(task) {
  const previewUrls = [];
  const visibleMedia = (task.results || []).filter(media => (
    media.kind === 'image' && ['temporary', 'persistent'].includes(media.state)
  ));
  const previews = await Promise.all(visibleMedia.map(async media => {
    try {
      const headers = new Headers({ Authorization: `Bearer ${state.token}` });
      const response = await window.fetch(`/api/v1/media/${encodeURIComponent(media.media_id)}/content`, { headers });
      if (!response.ok) return null;
      const url = window.URL.createObjectURL(await response.blob());
      previewUrls.push(url);
      return { media, url };
    } catch (_) {
      return null;
    }
  }));
  const available = previews.filter(Boolean);
  const backdrop = document.createElement('div');
  backdrop.className = 'generation-viewer-backdrop';
  backdrop.innerHTML = `<section class="generation-viewer" role="dialog" aria-modal="true" aria-label="生成任务结果">
    <div class="generation-viewer-head"><div><h2>${escapeHTML(task.source_label)}</h2><p>${escapeHTML(generationTaskStatusLabel[task.status] || task.status)} · ${formatDate(task.updated_at)}</p></div><button type="button" data-generation-view-close aria-label="关闭">×</button></div>
    <div class="generation-viewer-grid">${available.length ? available.map(({ media, url }, index) => `<figure><img src="${escapeHTML(url)}" alt="任务结果 ${index + 1}"><figcaption>结果 ${index + 1} · ${escapeHTML(generationMediaStateLabel(media))}</figcaption></figure>`).join('') : '<div class="empty">当前任务没有仍可查看的24小时结果。</div>'}</div>
  </section>`;
  const close = () => {
    previewUrls.forEach(url => window.URL.revokeObjectURL(url));
    backdrop.remove();
  };
  backdrop.addEventListener('click', event => { if (event.target === backdrop) close(); });
  backdrop.querySelector('[data-generation-view-close]').addEventListener('click', close);
  document.body.appendChild(backdrop);
}

function generationFailureNotice(tasks) {
  const failed = tasks.filter(task => task.status === 'failed');
  if (!failed.length) return '';
  const message = failed[0].failure_message || '图片生成失败，请重新提交任务。';
  return `<div class="failure-notice" role="alert"><strong>${failed.length} 个最近任务未完成</strong><span>${escapeHTML(message)}</span></div>`;
}

async function workspaceGenerationsPage() {
  loadingPage('生成任务');
  try {
    await ensureAccountSummary();
    const canvases = await optionalApi('/api/v1/canvases', []);
    const loadedTasks = await optionalApi('/api/v1/generation-tasks/recent?limit=100', []);
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    const recentTasks = loadedTasks.filter(task => new Date(task.created_at).getTime() >= cutoff);
    const resultEntries = await Promise.all(recentTasks.filter(task => task.status === 'succeeded').map(async task => [
      task.task_id,
      await optionalApi(`/api/v1/generation-tasks/${encodeURIComponent(task.task_id)}/media`, []),
    ]));
    const resultsByTask = new Map(resultEntries);
    const canvasesById = new Map(canvases.map(canvas => [canvas.canvas_id, canvas]));
    const tasks = recentTasks.map(task => ({
      ...task,
      source_label: generationTaskSourceLabel(task, canvasesById),
      results: resultsByTask.get(task.task_id) || [],
    }));
    const hasTerminalTasks = tasks.some(task => ['succeeded', 'failed', 'cancelled'].includes(task.status));
    shell('生成任务', `<div class="page-head"><div><h1>最近生成任务</h1><p>查看排队、生成及最近完成或失败的任务。</p></div><div class="row-actions"><button class="secondary-btn" type="button" data-generation-clear-history ${hasTerminalTasks ? '' : 'disabled'}>清除已结束记录</button><button class="secondary-btn" type="button" data-generation-refresh>刷新状态</button></div></div>
      ${generationFailureNotice(tasks)}
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>最近任务</h2><p>最近24小时内生成的结果；清除只会隐藏已结束记录，不会删除任务、额度流水或生成图片。</p></div></div>${recentGenerationTasksTable(tasks)}</section>`, 'generation-history-page');
    app.querySelectorAll('[data-generation-view]').forEach(button => button.addEventListener('click', () => {
      const task = tasks.find(item => item.task_id === button.dataset.generationView);
      if (task) openGenerationTaskViewer(task);
    }));
    const refreshButton = app.querySelector('[data-generation-refresh]');
    refreshButton?.addEventListener('click', async () => {
      refreshButton.disabled = true;
      await workspaceGenerationsPage();
    });
    const clearButton = app.querySelector('[data-generation-clear-history]');
    clearButton?.addEventListener('click', async () => {
      const confirmed = await centeredDeleteConfirm(
        '只会从最近任务列表隐藏已结束的任务；任务记录、额度流水和生成图片不会被删除。',
        '清除已结束记录',
        '确认清除',
      );
      if (!confirmed) return;
      clearButton.disabled = true;
      try {
        const result = await api('/api/v1/generation-tasks/history', { method: 'DELETE' });
        toast(`已清除 ${Number(result.cleared_tasks || 0)} 条已结束记录`);
        await workspaceGenerationsPage();
      } catch (error) {
        toast(error.message);
        clearButton.disabled = false;
      }
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('生成任务', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

const routeHealthLabel = {
  unknown: '未检测', healthy: '可用', degraded: '性能下降', unhealthy: '不可用',
};

function providerOptions(providers) {
  if (!providers.length) return '<option value="">请先添加 API 来源</option>';
  return providers.map(provider => `<option value="${escapeHTML(provider.provider_id)}">${escapeHTML(provider.display_name)} · ${escapeHTML(provider.code)}</option>`).join('');
}

function routeOptions(routes, selected = '') {
  const automatic = '<option value="">自动选择可用低延时来源</option>';
  return automatic + routes.map(route => `<option value="${escapeHTML(route.route_id)}" ${route.route_id === selected ? 'selected' : ''}>${escapeHTML(route.provider_model_name)} · ${escapeHTML(route.route_id)}</option>`).join('');
}

function providersTable(providers) {
  if (!providers.length) return '<div class="empty">尚未配置 API 来源。新增来源后还需要创建模型路由并完成健康检测。</div>';
  return `<div class="table-wrap"><table><thead><tr><th>来源</th><th>API 地址</th><th>传输</th><th>共享并发池</th><th>凭据指纹</th><th>状态</th><th>操作</th></tr></thead><tbody>${providers.map(provider => `<tr>
    <td><strong>${escapeHTML(provider.display_name)}</strong><br><span class="mono">${escapeHTML(provider.code)}</span></td>
    <td class="mono">${escapeHTML(provider.base_url)}</td><td>${escapeHTML(provider.image_response_mode || 'auto')}</td>
    <td><span class="mono">${escapeHTML(provider.concurrency_group || provider.code)}</span><br>${Number(provider.max_concurrency || 20)} 并发 / ${Number(provider.request_timeout_seconds || 600)} 秒</td>
    <td class="mono">${escapeHTML(provider.key_fingerprint || '—')}</td>
    <td><span class="status ${provider.enabled ? 'healthy' : 'unknown'}">${provider.enabled ? '已启用' : '已停用'}</span></td>
    <td><div class="row-actions"><button class="text-btn" data-provider-edit="${escapeHTML(provider.provider_id)}">编辑</button><button class="text-btn" data-provider-toggle="${escapeHTML(provider.provider_id)}" data-enabled="${provider.enabled}">${provider.enabled ? '停用' : '启用'}</button><button class="danger-btn" data-provider-delete="${escapeHTML(provider.provider_id)}">永久删除</button></div></td>
  </tr>`).join('')}</tbody></table></div>`;
}

function routesTable(routes, providers, healthByRoute) {
  if (!routes.length) return '<div class="empty">尚未配置模型来源路由。</div>';
  const providerNames = Object.fromEntries(providers.map(provider => [provider.provider_id, provider.display_name]));
  const providersById = Object.fromEntries(providers.map(provider => [provider.provider_id, provider]));
  return `<div class="table-wrap routing-health-table"><table><thead><tr><th>来源路由</th><th>参考图上限</th><th>启用状态</th><th>健康</th><th>可参与选路</th><th>EWMA / P95</th><th>成功率</th><th>优先级</th><th>最近检测</th><th>操作</th></tr></thead><tbody>${routes.map(route => {
    const health = healthByRoute[route.route_id];
    const status = health?.status || route.health_status || 'unknown';
    const successRate = health ? `${(Number(health.success_rate) * 100).toFixed(1)}% · ${health.sample_count} 次` : '—';
    const latency = health ? `${health.ewma_latency_ms} / ${health.p95_latency_ms} ms` : '—';
    const provider = providersById[route.provider_id];
    let eligibility = { label: '可参与选路', status: 'healthy' };
    if (!provider?.enabled) eligibility = { label: '来源已停用', status: 'unknown' };
    else if (!route.enabled) eligibility = { label: '路由已停用', status: 'unknown' };
    else if (!health) eligibility = { label: '尚未完成健康检测', status: 'pending' };
    else if (!health.available) eligibility = { label: '最近检测不可用', status: 'unhealthy' };
    return `<tr><td><strong>${escapeHTML(providerNames[route.provider_id] || route.provider_id)}</strong><br><span class="mono">${escapeHTML(route.provider_model_name)} · ${escapeHTML(route.route_id)}</span></td>
      <td>${normalizedImageReferenceLimit(route.max_reference_images)} 张</td>
      <td>${provider?.enabled ? '来源已启用' : '来源已停用'}<br>${route.enabled ? '路由已启用' : '路由已停用'}</td>
      <td><span class="status ${escapeHTML(status)}">${escapeHTML(routeHealthLabel[status] || status)}</span></td>
      <td><span class="status ${escapeHTML(eligibility.status)}">${escapeHTML(eligibility.label)}</span></td>
      <td>${escapeHTML(latency)}</td><td>${escapeHTML(successRate)}</td><td>${escapeHTML(route.priority)}</td><td>${formatDate(health?.checked_at)}</td>
      <td><div class="row-actions"><button class="text-btn" data-health-check="${escapeHTML(route.route_id)}">检测</button><button class="text-btn" data-route-edit="${escapeHTML(route.route_id)}">编辑</button><button class="text-btn" data-route-toggle="${escapeHTML(route.route_id)}" data-enabled="${route.enabled}">${route.enabled ? '停用' : '启用'}</button><button class="danger-btn" data-route-delete="${escapeHTML(route.route_id)}">永久删除</button></div></td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function providerCostRouteOptions(routes, providers, selectedRouteId = '') {
  if (!routes.length) return '<option value="">请先创建模型路由</option>';
  const providerNames = Object.fromEntries(providers.map(provider => [provider.provider_id, provider.display_name]));
  return routes.map(route => `<option value="${escapeHTML(route.route_id)}" ${route.route_id === selectedRouteId ? 'selected' : ''}>${escapeHTML(route.logical_model)}/${escapeHTML(route.output_spec)} · ${escapeHTML(providerNames[route.provider_id] || route.provider_id)} · ${escapeHTML(route.provider_model_name)}</option>`).join('');
}

function providerCostHistoryView(versions) {
  if (!versions.length) return '<div class="empty">所选模型路由尚未发布 Provider 成本版本。</div>';
  const currentVersion = Math.max(...versions.map(version => Number(version.version)));
  return `<div class="table-wrap"><table><thead><tr><th>版本</th><th>单张成本</th><th>状态</th><th>更新时间</th><th>版本标识</th></tr></thead><tbody>${versions.map(version => {
    const current = Number(version.version) === currentVersion;
    return `<tr><td>v${escapeHTML(version.version)}</td><td>${escapeHTML(version.cost_per_image_yuan)} 元 ${escapeHTML(version.provider_currency)}</td><td><span class="status ${current ? 'healthy' : 'pending'}">${current ? '当前' : '历史'}</span></td><td>${formatDate(version.published_at)}</td><td class="mono">${escapeHTML(version.version_id)}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function providerCostSummaryView(summaries) {
  if (!summaries.length) return '<div class="empty">尚无已提交上游的生成尝试成本。</div>';
  return `<div class="table-wrap"><table><thead><tr><th>Provider</th><th>逻辑模型</th><th>币种</th><th>已提交尝试</th><th>计费图片数</th><th>估算支出（元）</th></tr></thead><tbody>${summaries.map(summary => `<tr><td><strong>${escapeHTML(summary.provider_display_name)}</strong><br><span class="mono">${escapeHTML(summary.provider_id)}</span></td><td>${escapeHTML(summary.logical_model)}</td><td>${escapeHTML(summary.provider_currency)}</td><td>${escapeHTML(summary.submitted_attempts)}</td><td>${escapeHTML(summary.submitted_images)}</td><td>${(Number(summary.total_cost_cents || 0) / 100).toFixed(2)}</td></tr>`).join('')}</tbody></table></div>`;
}

function localDateTimeValue(date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

async function adminProviderCostsPage(selectedRouteId = '') {
  loadingPage('Provider 成本');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('Provider 成本', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const [routes, summaries] = await Promise.all([
      api('/api/v1/admin/image-model-routes'),
      api('/api/v1/admin/provider-cost-summary'),
    ]);
    const selectedRoute = routes.find(route => route.route_id === selectedRouteId) || routes[0] || null;
    const versions = selectedRoute
      ? await api(`/api/v1/admin/provider-cost-rates?route_id=${encodeURIComponent(selectedRoute.route_id)}`)
      : [];
    shell('Provider 成本', `<div class="page-head"><div><h1>Provider 成本</h1><p>记录平台向上游采购每张图片的成本，用于保留可审计的历史核算依据；它不会影响用户售价，也不会参与模型选路。</p></div></div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>它如何生效</h2><p>成本版本会固化到每次生成尝试，确保上游调价后仍能还原当时成本。缺少已生效成本版本时，任务保持排队且不会调用上游。用户售价在“模型路由与价格”中配置。</p></div></div></section>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>设置当前成本</h2><p>每条模型路由只显示一个当前成本；新成本立即生效并升级版本号，旧版本保留用于历史生成尝试审计。</p></div></div>
        <form id="provider-cost-form" class="admin-form-grid">
          <div class="field span-two"><label>模型路由</label><select name="route_id" required ${routes.length ? '' : 'disabled'}>${providerCostRouteOptions(routes, state.adminProviders, selectedRoute?.route_id)}</select></div>
          <div class="field"><label>Provider 计费币种</label><input name="provider_currency" value="RMB" minlength="3" maxlength="3" required></div>
          <div class="field"><label>每张成本（元）</label><input name="cost_per_image_yuan" type="number" min="0" step="0.01" required placeholder="0.12"></div>
          <button class="primary-btn" type="submit" ${routes.length ? '' : 'disabled'}>保存并升级版本</button>
        </form>
      </section>
      <div class="section-head"><div><h2>Provider 支出估算</h2><p>按 Provider 和逻辑模型累计已提交上游的生成尝试；每次重试分别计入。这是配置成本估算，不代表 Provider 最终账单。</p></div></div>${providerCostSummaryView(summaries)}
      <div class="section-head"><div><h2>成本版本历史</h2><p>旧版本不会物理覆盖，历史生成尝试仍引用当时固化的版本。</p></div></div><div id="provider-cost-history">${providerCostHistoryView(versions)}</div>`);

    const form = document.getElementById('provider-cost-form');
    const routeSelect = form.elements.route_id;
    routeSelect.addEventListener('change', () => adminProviderCostsPage(routeSelect.value));
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      const route = routes.find(item => item.route_id === values.get('route_id'));
      if (!route) return toast('请选择模型路由');
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), async () => {
        await api(`/api/v1/admin/provider-cost-rates/${encodeURIComponent(route.route_id)}`, {
          method: 'PUT',
          body: JSON.stringify({
            provider_currency: values.get('provider_currency'),
            cost_per_image_yuan: values.get('cost_per_image_yuan'),
          }),
        });
      }, 'Provider 当前成本已更新', () => adminProviderCostsPage(route.route_id));
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('Provider 成本', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

function modelPriceOptions(prices, selectedVersionId = '') {
  if (!prices.length) return '<option value="">当前没有可发布的逻辑模型规格</option>';
  return prices.map(price => `<option value="${escapeHTML(price.version_id)}" ${price.version_id === selectedVersionId ? 'selected' : ''}>${escapeHTML(price.logical_model)}/${escapeHTML(price.output_spec)} · 当前 ${escapeHTML(price.credits_per_result)} 额度/张</option>`).join('');
}

function modelPricesTable(prices) {
  if (!prices.length) return '<div class="empty">当前没有已生效的模型价格。</div>';
  return `<div class="table-wrap"><table><thead><tr><th>逻辑模型</th><th>成品规格</th><th>每张价格</th><th>生效时间</th><th>发布时间</th><th>操作</th></tr></thead><tbody>${prices.map(price => `<tr><td>${escapeHTML(price.logical_model)}</td><td>${escapeHTML(price.output_spec)}</td><td>${formatCredits(price.credits_per_result)} 额度</td><td>${formatDate(price.effective_from)}</td><td>${formatDate(price.published_at)}</td><td><button class="danger-btn" data-price-delete="${escapeHTML(price.version_id)}" data-price-label="${escapeHTML(`${price.logical_model}/${price.output_spec}`)}">删除价格</button></td></tr>`).join('')}</tbody></table></div>`;
}

async function adminModelPricesPage() {
  loadingPage('模型价格');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('模型价格', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const [prices, routes] = await Promise.all([
      api('/api/v1/model-prices'),
      api('/api/v1/admin/image-model-routes'),
    ]);
    const modelSpecs = [...new Map(routes.map(route => [`${route.logical_model}\u0000${route.output_spec}`, route])).values()];
    const selectedSpec = modelSpecs[0] || prices[0] || { logical_model: '', output_spec: '' };
    const defaultEffectiveTime = localDateTimeValue(new Date(Date.now() + 60_000));
    shell('模型价格', `<div class="page-head"><div><h1>模型价格</h1><p>配置用户购买生成结果时使用的额度价格。Provider 成本、汇率和毛利与这里相互独立。</p></div></div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>发布新价格版本</h2><p>新版本可以接近当前时间生效或安排为未来生效；已发布版本不能编辑或删除。</p></div></div>
        <form id="model-price-form" class="admin-form-grid">
          <div class="field"><label>逻辑模型</label><input name="logical_model" list="priced-logical-models" required value="${escapeHTML(selectedSpec.logical_model)}" placeholder="例如 gpt-image-2-kapi"><datalist id="priced-logical-models">${modelSpecs.map(item => `<option value="${escapeHTML(item.logical_model)}"></option>`).join('')}</datalist></div>
          <div class="field"><label>成品规格</label><input name="output_spec" required value="${escapeHTML(selectedSpec.output_spec)}" placeholder="例如 4k"></div>
          <div class="field"><label>每张价格（额度）</label><input name="credits_per_result" type="number" min="0.0001" step="0.0001" required placeholder="0.2000"></div>
          <div class="field"><label>生效时间</label><input name="effective_from" type="datetime-local" value="${defaultEffectiveTime}" required></div>
          <button class="primary-btn" type="submit">发布新价格版本</button>
        </form>
      </section>
      <div class="section-head"><div><h2>当前生效价格</h2><p>这里只显示每个逻辑模型规格当前生效的版本；未来版本将在生效后自动替换当前目录。</p></div></div>${modelPricesTable(prices)}`);

    const form = document.getElementById('model-price-form');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      const effectiveFrom = new Date(String(values.get('effective_from')));
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), async () => {
        await api('/api/v1/admin/model-prices', {
          method: 'POST',
          body: JSON.stringify({
            logical_model: values.get('logical_model'),
            output_spec: values.get('output_spec'),
            credits_per_result: values.get('credits_per_result'),
            effective_from: effectiveFrom.toISOString(),
          }),
        });
      }, '模型价格版本已发布', adminModelPricesPage);
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('模型价格', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

function rechargePackageCodeOptions(packages) {
  return packages.map(item => `<option value="${escapeHTML(item.package_code)}"></option>`).join('');
}

function rechargePackagesTable(packages) {
  if (!packages.length) return '<div class="empty">当前没有可售充值包。</div>';
  const rate = item => {
    const payment = Number(item.payment_cny);
    const credits = Number(item.credits);
    return payment > 0 && Number.isFinite(credits)
      ? (credits / payment).toFixed(4).replace(/\.?0+$/, '')
      : '—';
  };
  return `<div class="table-wrap"><table><thead><tr><th>充值包代码</th><th>支付金额</th><th>到账额度</th><th>换算率</th><th>生效时间</th><th>发布时间</th><th>版本标识</th></tr></thead><tbody>${packages.map(item => `<tr><td>${escapeHTML(item.package_code)}</td><td>¥${escapeHTML(item.payment_cny)}</td><td>${formatCredits(item.credits)} 额度</td><td><strong>${escapeHTML(rate(item))}</strong> 额度/元</td><td>${formatDate(item.effective_from)}</td><td>${formatDate(item.published_at)}</td><td class="mono">${escapeHTML(item.version_id)}</td></tr>`).join('')}</tbody></table></div>`;
}

async function adminRechargePackagesPage() {
  loadingPage('充值包');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('充值包', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const packages = await api('/api/v1/recharge-packages');
    const defaultEffectiveTime = localDateTimeValue(new Date(Date.now() + 60_000));
    shell('充值包', `<div class="page-head"><div><h1>特惠充值包</h1><p>配置独立于普通充值比例的特惠金额与赠送额度；支付网关及普通充值比例在“支付设置”中管理。</p></div></div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>特惠规则</h2><p>每个特惠包独立决定比例：<strong>换算率 = 到账额度 ÷ 支付金额</strong>，不受普通充值全局比例影响。</p></div></div></section>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>发布充值包版本</h2><p>沿用现有代码会发布新版本，输入新代码会创建首个版本；已发布版本不能编辑或删除。</p></div></div>
        <form id="recharge-package-form" class="admin-form-grid">
          <div class="field span-two"><label>充值包代码</label><input name="package_code" list="recharge-package-codes" required placeholder="starter"><datalist id="recharge-package-codes">${rechargePackageCodeOptions(packages)}</datalist></div>
          <div class="field"><label>支付金额（人民币）</label><input name="payment_cny" type="number" min="0.01" step="0.01" required placeholder="10.00"></div>
          <div class="field"><label>到账额度</label><input name="credits" type="number" min="0.0001" step="0.0001" required placeholder="10.0000"></div>
          <div class="field"><label>生效时间</label><input name="effective_from" type="datetime-local" value="${defaultEffectiveTime}" required></div>
          <button class="primary-btn" type="submit">发布充值包版本</button>
        </form>
      </section>
      <div class="section-head"><div><h2>当前可售充值包</h2><p>这里只显示每个充值包代码当前生效的版本；未来版本将在生效后自动替换当前目录。</p></div></div>${rechargePackagesTable(packages)}`);

    const form = document.getElementById('recharge-package-form');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      const effectiveFrom = new Date(String(values.get('effective_from')));
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), async () => {
        await api('/api/v1/admin/recharge-packages', {
          method: 'POST',
          body: JSON.stringify({
            package_code: values.get('package_code'),
            payment_cny: values.get('payment_cny'),
            credits: values.get('credits'),
            effective_from: effectiveFrom.toISOString(),
          }),
        });
      }, '充值包版本已发布', adminRechargePackagesPage);
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('充值包', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

function adminGenerationTasksTable(tasks) {
  if (!tasks.length) return '<div class="empty">当前没有排队中或生成中的任务。</div>';
  const statusLabels = { queued: '排队中', running: '生成中' };
  return `<div class="table-wrap admin-generation-task-table"><table><thead><tr><th>用户</th><th>任务</th><th>模型 / 规格</th><th>数量</th><th>冻结额度</th><th>状态</th><th>提交 / 开始时间</th><th>操作</th></tr></thead><tbody>${tasks.map(task => `<tr>
    <td><strong>${escapeHTML(task.user_email || '未知用户')}</strong><br><span class="mono">${escapeHTML(task.user_id)}</span></td>
    <td><span class="mono">${escapeHTML(task.task_id)}</span><br><span class="muted">${escapeHTML(String(task.prompt || '').slice(0, 60) || '—')}</span></td>
    <td><strong>${escapeHTML(task.logical_model)}</strong><br><span class="muted">${escapeHTML(task.output_spec)}</span></td>
    <td>${Number(task.quantity)}</td><td>${formatCredits(task.frozen_credits)}</td>
    <td><span class="status ${escapeHTML(task.status)}">${escapeHTML(statusLabels[task.status] || task.status)}</span></td>
    <td>${formatDate(task.created_at)}${task.started_at ? `<br><span class="muted">开始：${formatDate(task.started_at)}</span>` : ''}</td>
    <td><button class="danger-btn" type="button" data-admin-task-cancel="${escapeHTML(task.task_id)}" data-account-space="${escapeHTML(task.account_space_id)}" data-user-email="${escapeHTML(task.user_email || '未知用户')}" data-frozen-credits="${escapeHTML(task.frozen_credits)}">取消并退款</button></td>
  </tr>`).join('')}</tbody></table></div>`;
}

async function adminGenerationTasksPage() {
  loadingPage('任务管理');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('任务管理', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const tasks = await api('/api/v1/admin/generation-tasks/active');
    const queued = tasks.filter(task => task.status === 'queued').length;
    const running = tasks.filter(task => task.status === 'running').length;
    shell('任务管理', `<div class="page-head"><div><h1>当前生成任务</h1><p>查看全站排队中和生成中的任务。取消后会释放该任务的全部冻结额度；已经发往上游的请求可能仍会运行，但迟到结果不会交付。</p></div><button class="secondary-btn" type="button" data-admin-task-refresh>刷新</button></div>
      <section class="grid three"><article class="stat-card"><span>活动任务</span><strong>${tasks.length}</strong><small>排队中与生成中的任务总数</small></article><article class="stat-card"><span>排队中</span><strong>${queued}</strong><small>尚未开始 Provider 调用</small></article><article class="stat-card"><span>生成中</span><strong>${running}</strong><small>已经开始执行的任务</small></article></section>
      <section class="panel admin-panel"><div class="section-head" style="margin-top:0"><div><h2>任务明细</h2><p>列表按任务提交时间从早到晚排列。</p></div></div>${adminGenerationTasksTable(tasks)}</section>`);
    document.querySelector('[data-admin-task-refresh]')?.addEventListener('click', () => void adminGenerationTasksPage());
    document.querySelectorAll('[data-admin-task-cancel]').forEach(button => button.addEventListener('click', async () => {
      const confirmed = await centeredDeleteConfirm(
        `确定取消 ${button.dataset.userEmail} 的任务，并退回 ${formatCredits(button.dataset.frozenCredits)} 冻结额度吗？此操作不能恢复。`,
        '确认取消并退款',
        '确认取消',
      );
      if (!confirmed) return;
      await runAdminAction(button, () => api(
        `/api/v1/admin/generation-tasks/${encodeURIComponent(button.dataset.adminTaskCancel)}/cancel`,
        { method: 'POST', body: JSON.stringify({ account_space_id: button.dataset.accountSpace }) },
      ), '任务已取消，冻结额度已退回用户', adminGenerationTasksPage);
    }));
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('任务管理', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

let adminUserActivityView = { window: '7d', sort: 'consumed_credits', direction: 'desc', selectedEmail: '' };

function adminUserActivityTable(users) {
  const value = (user, key) => key === 'email' ? user.email.toLowerCase() : key === 'registered_at' ? new Date(user.registered_at).getTime() : Number(user[key] || 0);
  const sorted = [...users].sort((left, right) => {
    const a = value(left, adminUserActivityView.sort);
    const b = value(right, adminUserActivityView.sort);
    const result = typeof a === 'string' ? a.localeCompare(b) : a - b;
    return adminUserActivityView.direction === 'asc' ? result : -result;
  });
  const heading = (label, key) => `<button type="button" data-user-sort="${key}">${label}${adminUserActivityView.sort === key ? (adminUserActivityView.direction === 'asc' ? ' ↑' : ' ↓') : ' ↕'}</button>`;
  if (!sorted.length) return '<div class="empty">当前周期没有可统计的注册用户。</div>';
  return `<div class="table-wrap admin-user-activity-table"><table><thead><tr>
    <th>${heading('用户邮箱', 'email')}</th><th>${heading('消耗额度', 'consumed_credits')}</th><th>${heading('任务总数', 'total_tasks')}</th><th>${heading('成功任务', 'succeeded_tasks')}</th><th>${heading('失败任务', 'failed_tasks')}</th><th>${heading('当前余额', 'available_credits')}</th><th>${heading('注册时间', 'registered_at')}</th>
  </tr></thead><tbody>${sorted.map(user => `<tr><td><strong>${escapeHTML(user.email)}</strong></td><td>${formatCredits(user.consumed_credits)}</td><td>${Number(user.total_tasks)}</td><td>${Number(user.succeeded_tasks)}</td><td><span class="${Number(user.failed_tasks) ? 'admin-failure-count' : ''}">${Number(user.failed_tasks)}</span></td><td>${formatCredits(user.available_credits)}</td><td>${formatDate(user.registered_at)}</td></tr>`).join('')}</tbody></table></div>`;
}

function adminSelectedUserPanel(user) {
  if (!user) return '<div class="empty admin-user-search-empty">输入完整邮箱并点击查找，再进行充值或并发设置。</div>';
  return `<div class="admin-selected-user-head"><div><strong>${escapeHTML(user.email)}</strong><span class="status ${user.email_verified ? 'healthy' : 'unknown'}">${user.email_verified ? '邮箱已验证' : '邮箱未验证'}</span></div><div>可用额度 <b>${formatCredits(user.available_credits)}</b> · 冻结额度 <b>${formatCredits(user.frozen_credits)}</b></div></div>
    <div class="admin-user-control-grid">
      <form data-admin-generation-limit="${escapeHTML(user.user_id)}" class="admin-user-control-card"><div><strong>设置生成并发</strong><small>允许 1–50，超出任务继续排队。</small></div><div class="row-actions"><input name="execution_concurrency" type="number" min="1" max="50" value="${Number(user.generation_execution_concurrency || 2)}" required aria-label="单用户执行并发数"><button class="secondary-btn" type="submit">保存并发</button></div></form>
      <form data-admin-credit-grant="${escapeHTML(user.user_id)}" class="admin-user-control-card"><div><strong>人工充值</strong><small>充值会形成永久账务记录。</small></div><div class="row-actions"><input name="credits" type="number" min="0.0001" step="0.0001" required placeholder="额度"><input name="reason" required maxlength="255" placeholder="充值原因"><button class="primary-btn" type="submit">确认充值</button></div></form>
    </div><button class="text-btn" type="button" data-recharge-records="${escapeHTML(user.user_id)}" data-user-email="${escapeHTML(user.email)}">查看此用户充值记录</button><div id="admin-recharge-records"></div>`;
}

function adminRechargeRecordsView(records, email) {
  if (!records.length) return `<section class="panel"><div class="section-head" style="margin-top:0"><div><h2>${escapeHTML(email)} 的充值记录</h2></div></div><div class="empty">该用户暂无充值记录。</div></section>`;
  const typeLabels = { payment_recharge: '支付充值', admin_recharge: '人工充值', reversal: '充值冲正' };
  const statusLabels = { posted: '已入账', reversed: '已冲正' };
  return `<section class="panel"><div class="section-head" style="margin-top:0"><div><h2>${escapeHTML(email)} 的充值记录</h2><p>只显示支付充值、人工充值及相关冲正，不显示支付凭据或幂等引用。</p></div></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>额度</th><th>原因</th><th>状态</th></tr></thead><tbody>${records.map(record => `<tr><td>${formatDate(record.occurred_at)}</td><td>${escapeHTML(typeLabels[record.type] || record.type)}</td><td>${formatCredits(record.credits)}</td><td>${escapeHTML(record.reason || '—')}</td><td>${escapeHTML(statusLabels[record.status] || record.status)}</td></tr>`).join('')}</tbody></table></div></section>`;
}

async function adminUsersPage() {
  loadingPage('用户管理');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('用户管理', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const [users, initiallySelectedUser] = await Promise.all([
      api(`/api/v1/admin/user-activity?window=${encodeURIComponent(adminUserActivityView.window)}`),
      adminUserActivityView.selectedEmail
        ? api(`/api/v1/admin/users/by-email?email=${encodeURIComponent(adminUserActivityView.selectedEmail)}`).catch(() => null)
        : Promise.resolve(null),
    ]);
    shell('用户管理', `<div class="page-head"><div><h1>用户管理</h1><p>按邮箱查找并配置单个用户，同时查看各周期的额度消耗与任务质量。</p></div></div>
      <section class="panel admin-user-lookup"><div class="section-head" style="margin-top:0"><div><h2>按邮箱设置用户</h2><p>不会默认列出所有用户；请输入完整注册邮箱。</p></div></div><form data-admin-user-search class="admin-user-search"><input name="email" type="email" required value="${escapeHTML(adminUserActivityView.selectedEmail)}" placeholder="user@example.com"><button class="primary-btn" type="submit">查找用户</button></form><div data-admin-selected-user>${adminSelectedUserPanel(initiallySelectedUser)}</div></section>
      <section class="panel admin-user-activity"><div class="section-head" style="margin-top:0"><div><h2>用户用量统计</h2><p>消耗额度按成功交付数量和任务冻结时单价快照计算；失败任务不计入消费。</p></div><div class="admin-period-tabs"><button type="button" data-user-window="7d" class="${adminUserActivityView.window === '7d' ? 'active' : ''}">近 7 天</button><button type="button" data-user-window="30d" class="${adminUserActivityView.window === '30d' ? 'active' : ''}">近 30 天</button><button type="button" data-user-window="all" class="${adminUserActivityView.window === 'all' ? 'active' : ''}">全部时间</button></div></div><div data-admin-user-activity-table>${adminUserActivityTable(users)}</div></section>`);
    const bindSelectedUser = user => {
      const target = document.querySelector('[data-admin-selected-user]');
      if (target) target.innerHTML = adminSelectedUserPanel(user);
      document.querySelector('[data-admin-generation-limit]')?.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      const userId = event.currentTarget.dataset.adminGenerationLimit;
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api(
        `/api/v1/admin/users/${encodeURIComponent(userId)}/generation-limit`,
        {
          method: 'PUT',
          body: JSON.stringify({ execution_concurrency: Number(values.get('execution_concurrency')) }),
        },
      ), '用户执行并发已更新', adminUsersPage);
      });
      document.querySelector('[data-admin-credit-grant]')?.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      const userId = event.currentTarget.dataset.adminCreditGrant;
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api(
        `/api/v1/admin/users/${encodeURIComponent(userId)}/credit-grants`,
        {
          method: 'POST',
          headers: { 'Idempotency-Key': window.crypto.randomUUID() },
          body: JSON.stringify({ credits: values.get('credits'), reason: values.get('reason') }),
        },
      ), '人工充值已到账', adminUsersPage);
      });
      document.querySelector('[data-recharge-records]')?.addEventListener('click', async event => {
        const button = event.currentTarget;
      const records = await api(`/api/v1/admin/users/${encodeURIComponent(button.dataset.rechargeRecords)}/recharge-records`);
      document.getElementById('admin-recharge-records').innerHTML = adminRechargeRecordsView(records, button.dataset.userEmail);
      });
    };
    if (initiallySelectedUser) bindSelectedUser(initiallySelectedUser);
    document.querySelector('[data-admin-user-search]')?.addEventListener('submit', async event => {
      event.preventDefault();
      const email = String(new FormData(event.currentTarget).get('email') || '').trim().toLowerCase();
      const button = event.currentTarget.querySelector('[type="submit"]');
      button.disabled = true;
      try {
        const user = await api(`/api/v1/admin/users/by-email?email=${encodeURIComponent(email)}`);
        adminUserActivityView.selectedEmail = email;
        bindSelectedUser(user);
      } catch (error) { toast(error.message); }
      finally { button.disabled = false; }
    });
    document.querySelectorAll('[data-user-window]').forEach(button => button.addEventListener('click', () => { adminUserActivityView.window = button.dataset.userWindow; void adminUsersPage(); }));
    const bindSortButtons = () => document.querySelectorAll('[data-user-sort]').forEach(button => button.addEventListener('click', () => {
      const key = button.dataset.userSort;
      adminUserActivityView.direction = adminUserActivityView.sort === key && adminUserActivityView.direction === 'desc' ? 'asc' : 'desc';
      adminUserActivityView.sort = key;
      document.querySelector('[data-admin-user-activity-table]').innerHTML = adminUserActivityTable(users);
      bindSortButtons();
    }));
    bindSortButtons();
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('用户管理', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

let adminStorageAllowanceView = { selectedEmail: '' };

function adminStorageUserPanel(user, allowance) {
  if (!user || !allowance) return '<div class="empty admin-user-search-empty">输入完整注册邮箱并点击搜索，再为选中用户设置单独存储额度。</div>';
  const limitMb = Number(allowance.limit_bytes) / 1_000_000;
  return `<div class="admin-selected-user-head"><div><strong>${escapeHTML(user.email)}</strong><span class="status ${user.email_verified ? 'healthy' : 'unknown'}">${user.email_verified ? '邮箱已验证' : '邮箱未验证'}</span></div><div>当前存储额度 <b>${formatBytes(allowance.limit_bytes)}</b></div></div>
    <form data-admin-user-storage="${escapeHTML(user.user_id)}" class="admin-user-control-card admin-storage-user-control">
      <div><strong>设置该用户的存储额度</strong><small>单独额度优先于统一额度，且不会影响其他用户。</small></div>
      <div class="row-actions"><input name="limit_mb" type="number" min="0" step="1" value="${escapeHTML(limitMb)}" required aria-label="用户存储额度（MB）"><button class="primary-btn" type="submit">保存用户额度</button></div>
    </form>`;
}

async function adminStorageAllowancePage() {
  loadingPage('存储额度');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('存储额度', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const storage = state.user?.storage_allowance || null;
    const globalStorage = await api('/api/v1/admin/storage-allowance');
    const initiallySelectedUser = adminStorageAllowanceView.selectedEmail
      ? await api(`/api/v1/admin/users/by-email?email=${encodeURIComponent(adminStorageAllowanceView.selectedEmail)}`).catch(() => null)
      : null;
    const initiallySelectedAllowance = initiallySelectedUser
      ? await api(`/api/v1/admin/users/${encodeURIComponent(initiallySelectedUser.user_id)}/storage-allowance`)
      : null;
    const currentLimit = formatBytes(globalStorage.limit_bytes);
    const currentValue = escapeHTML(Number(globalStorage.limit_bytes) / 1_000_000);
    shell('存储额度', `<div class="page-head"><div><h1>存储额度</h1><p>配置全站统一上限，或搜索用户并为选中用户设置单独额度。</p></div></div>
      <section class="grid three">
        <article class="stat-card"><span>当前统一上限</span><strong>${currentLimit}</strong><small>未单独设置的用户使用此额度</small></article>
        <article class="stat-card"><span>当前账户已用</span><strong>${storage ? formatBytes(storage.used_bytes) : '—'}</strong><small>账户内相同内容按哈希去重</small></article>
        <article class="stat-card"><span>当前账户剩余</span><strong>${storage ? formatBytes(storage.available_bytes) : '—'}</strong><small>最低显示为零</small></article>
      </section>
      <section class="panel admin-panel"><div class="section-head" style="margin-top:0"><div><h2>调整统一上限</h2><p>请输入十进制 MB（1 MB = 1,000,000 bytes）。调低额度不会删除已有媒体，超额账户只会无法继续保留新媒体。</p></div></div>
        <form id="storage-allowance-form" class="admin-form-grid">
          <div class="field span-two"><label>统一存储额度（MB）</label><input name="limit_mb" type="number" min="0" step="1" value="${currentValue}" required placeholder="10000"></div>
          <button class="primary-btn" type="submit">保存统一上限</button>
        </form>
      </section>
      <section class="panel admin-user-lookup"><div class="section-head" style="margin-top:0"><div><h2>单独设置用户额度</h2><p>搜索完整注册邮箱；保存后仅覆盖选中用户的统一额度。</p></div></div>
        <form data-storage-user-search class="admin-user-search"><input name="email" type="email" required value="${escapeHTML(adminStorageAllowanceView.selectedEmail)}" placeholder="user@example.com"><button class="primary-btn" type="submit">搜索用户</button></form>
        <div data-storage-selected-user>${adminStorageUserPanel(initiallySelectedUser, initiallySelectedAllowance)}</div>
      </section>`);

    const bindUserStorageForm = user => {
      const userForm = document.querySelector('[data-admin-user-storage]');
      userForm?.addEventListener('submit', async event => {
        event.preventDefault();
        const values = new FormData(event.currentTarget);
        await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api(
          `/api/v1/admin/users/${encodeURIComponent(user.user_id)}/storage-allowance`,
          {
            method: 'PUT',
            body: JSON.stringify({ limit_bytes: Number(values.get('limit_mb')) * 1_000_000 }),
          },
        ), '用户存储额度已更新', adminStorageAllowancePage);
      });
    };
    if (initiallySelectedUser) bindUserStorageForm(initiallySelectedUser);

    document.querySelector('[data-storage-user-search]')?.addEventListener('submit', async event => {
      event.preventDefault();
      const email = String(new FormData(event.currentTarget).get('email') || '').trim();
      const button = event.currentTarget.querySelector('[type="submit"]');
      button.disabled = true;
      try {
        const user = await api(`/api/v1/admin/users/by-email?email=${encodeURIComponent(email)}`);
        const allowance = await api(`/api/v1/admin/users/${encodeURIComponent(user.user_id)}/storage-allowance`);
        adminStorageAllowanceView.selectedEmail = user.email;
        document.querySelector('[data-storage-selected-user]').innerHTML = adminStorageUserPanel(user, allowance);
        bindUserStorageForm(user);
      } catch (error) { toast(error.message); }
      finally { button.disabled = false; }
    });

    const form = document.getElementById('storage-allowance-form');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), async () => {
        await api('/api/v1/admin/storage-allowance', {
          method: 'PUT',
          body: JSON.stringify({ limit_bytes: Number(values.get('limit_mb')) * 1_000_000 }),
        });
      }, '统一存储额度已更新', adminStorageAllowancePage);
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('存储额度', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

async function adminGenerationCapacityPage() {
  loadingPage('生成容量');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('生成容量', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const capacity = await api('/api/v1/admin/generation-worker-capacity');
    shell('生成容量', `<div class="page-head"><div><h1>图片生成容量</h1><p>动态调整 Worker 执行能力和全站可积压的活动图片数量，无需重启服务。</p></div></div>
      <section class="grid three">
        <article class="stat-card"><span>已部署 Worker 上限</span><strong>${Number(capacity.deployed_worker_limit)}</strong><small>超过此数量需要在服务器扩容容器</small></article>
        <article class="stat-card"><span>当前启用 Worker</span><strong>${Number(capacity.enabled_workers)}</strong><small>其余已部署 Worker 保持待机</small></article>
        <article class="stat-card"><span>Worker 总执行容量</span><strong>${Number(capacity.total_concurrency)}</strong><small>仍会受到上游共享并发池限制</small></article>
        <article class="stat-card"><span>全站活动图片占用</span><strong>${Number(capacity.active_image_units)} / ${Number(capacity.global_active_image_limit)}</strong><small>排队 ${Number(capacity.queued_image_units)} · 正在生图 ${Number(capacity.running_image_units)}</small></article>
        <article class="stat-card"><span>任务自动截止时间</span><strong>${Number(capacity.task_deadline_minutes)} 分钟</strong><small>${Number(capacity.task_deadline_minutes) * 60} 秒 · 排队时间不计入</small></article>
      </section>
      <section class="panel admin-panel"><div class="section-head" style="margin-top:0"><div><h2>当前超时判定</h2><p>Worker 第一次准备调用上游时开始计时，达到 ${Number(capacity.task_deadline_minutes)} 分钟（${Number(capacity.task_deadline_minutes) * 60} 秒）即算任务超时。</p></div></div>
        <div class="grid three">
          <article class="stat-card"><span>排队阶段</span><strong>不计时</strong><small>只有取得用户和 Provider 执行槽后才开始</small></article>
          <article class="stat-card"><span>到达截止时间</span><strong>立即中止</strong><small>停止当前结果读取，也不再发送本批剩余图片请求</small></article>
          <article class="stat-card"><span>额度与迟到结果</span><strong>退回冻结额度</strong><small>任务标记失败；截止后到达的图片作废</small></article>
        </div>
      </section>
      <section class="panel admin-panel"><div class="section-head" style="margin-top:0"><div><h2>调整容量</h2><p>降低容量不会中断正在调用上游的任务，只影响后续任务；设置将在几秒内由所有 Worker 自动读取。</p></div></div>
        <form id="generation-capacity-form" class="admin-form-grid">
          <div class="field"><label>启用 Worker 数量</label><input name="enabled_workers" type="number" min="1" max="${Number(capacity.deployed_worker_limit)}" value="${Number(capacity.enabled_workers)}" required></div>
          <div class="field"><label>单 Worker 并发数</label><input name="concurrency_per_worker" type="number" min="1" max="50" value="${Number(capacity.concurrency_per_worker)}" required></div>
          <div class="field"><label>全站活动图片名额上限</label><input name="global_active_image_limit" type="number" min="1" max="100000" value="${Number(capacity.global_active_image_limit)}" required><small>按 queued + running 的图片数量计算；一批 4 张占 4 个名额</small></div>
          <div class="field"><label>任务自动截止时间（分钟）</label><input name="task_deadline_minutes" type="number" min="1" max="120" value="${Number(capacity.task_deadline_minutes)}" required><small>Provider 的绝对请求超时应小于或等于此值；达到时限后失败退款，迟到结果作废</small></div>
          <button class="primary-btn" type="submit">保存生成容量</button>
        </form>
      </section>`);
    document.getElementById('generation-capacity-form').addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api('/api/v1/admin/generation-worker-capacity', {
        method: 'PUT',
        body: JSON.stringify({
          enabled_workers: Number(values.get('enabled_workers')),
          concurrency_per_worker: Number(values.get('concurrency_per_worker')),
          global_active_image_limit: Number(values.get('global_active_image_limit')),
          task_deadline_minutes: Number(values.get('task_deadline_minutes')),
        }),
      }), '生成 Worker 容量已更新', adminGenerationCapacityPage);
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('生成容量', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

function paymentMethodSettingsText(methods) {
  const configured = methods?.length ? methods : [
    { payment_provider: 'alipay', display_name: '支付宝' },
    { payment_provider: 'wxpay', display_name: '微信支付' },
  ];
  return configured.map(method => `${method.payment_provider}|${method.display_name}`).join('\n');
}

function parsePaymentMethodSettings(value) {
  return String(value).split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
    const separator = line.indexOf('|');
    if (separator < 1 || separator === line.length - 1) throw new Error('支付方式必须使用“标识|显示名称”格式');
    return { payment_provider: line.slice(0, separator).trim(), display_name: line.slice(separator + 1).trim() };
  });
}

async function adminPaymentSettingsPage() {
  loadingPage('支付设置');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('支付设置', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const [settings, rechargeRate] = await Promise.all([
      api('/api/v1/admin/payment-settings'),
      api('/api/v1/admin/recharge-rate'),
    ]);
    const statusText = settings.enabled && settings.configured ? '已启用' : settings.configured ? '已配置，未启用' : '尚未完成配置';
    const statusClass = settings.enabled && settings.configured ? 'healthy' : 'unknown';
    const keyStatus = settings.merchant_key_configured ? '商户密钥已安全保存；留空表示保留' : '尚未保存商户密钥';
    const defaultPublicBaseUrl = window.location.protocol === 'https:' ? window.location.origin : '';
    shell('支付设置', `<div class="page-head"><div><h1>支付设置</h1><p>对照 New API 的易支付兼容协议，统一配置支付宝、微信等支付方式。</p></div><span class="status ${statusClass}">${statusText}</span></div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>普通充值换算比例</h2><p>用户选择 1、2、5、10、100 元或输入自定义金额时，统一按这里的比例换算。特惠充值包不使用此比例。</p></div></div>
        <form id="recharge-rate-form" class="admin-form-grid">
          <div class="field"><label>每 1 元兑换额度</label><input name="credits_per_cny" type="number" min="0.0001" max="1000000" step="0.0001" required value="${escapeHTML(rechargeRate.credits_per_cny)}"></div>
          <div class="field"><label>用户端显示</label><input value="1 元 = ${escapeHTML(rechargeRate.credits_per_cny)} 额度" disabled></div>
          <button class="primary-btn" type="submit">保存普通充值比例</button>
        </form>
      </section>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>易支付网关</h2><p>商户密钥保存在受控密钥目录，不写入数据库、不返回浏览器。</p></div></div>
        <form id="payment-settings-form" class="admin-form-grid">
          <div class="field span-two"><label><input name="enabled" type="checkbox" ${settings.enabled ? 'checked' : ''}> 启用在线支付</label><small>关闭后用户端不显示支付方式，已保存配置会保留。</small></div>
          <div class="field span-two"><label>网关基础地址</label><input name="gateway_url" type="url" required value="${escapeHTML(settings.gateway_url || '')}" placeholder="https://pay.example.com"><small>填写易支付站点地址，系统会自动提交到 /submit.php。</small></div>
          <div class="field span-two"><label>公开站点地址</label><input name="public_base_url" type="url" required value="${escapeHTML(settings.public_base_url || defaultPublicBaseUrl)}" placeholder="https://studio.example.com"><small>必须是外网可访问的 HTTPS Origin，网关将回调 ${escapeHTML((settings.public_base_url || defaultPublicBaseUrl) + '/api/v1/payments/epay/notify')}。</small></div>
          <div class="field"><label>商户 ID（PID）</label><input name="merchant_id" required autocomplete="off" value="${escapeHTML(settings.merchant_id || '')}" placeholder="1000"></div>
          <div class="field"><label>商户密钥</label><input name="merchant_key" type="password" autocomplete="new-password" placeholder="${escapeHTML(keyStatus)}"><small>${escapeHTML(keyStatus)}</small></div>
          <div class="field span-two"><label>支付方式</label><textarea name="methods" rows="4" required placeholder="alipay|支付宝&#10;wxpay|微信支付">${escapeHTML(paymentMethodSettingsText(settings.methods))}</textarea><small>每行一种，格式为“网关方式标识|用户显示名称”。</small></div>
          <button class="primary-btn" type="submit">保存支付设置</button>
        </form>
      </section>`);
    document.getElementById('recharge-rate-form').addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api('/api/v1/admin/recharge-rate', {
        method: 'PUT',
        body: JSON.stringify({ credits_per_cny: values.get('credits_per_cny') }),
      }), '普通充值比例已保存', adminPaymentSettingsPage);
    });
    document.getElementById('payment-settings-form').addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api('/api/v1/admin/payment-settings', {
        method: 'PUT',
        body: JSON.stringify({
          enabled: event.currentTarget.elements.enabled.checked,
          gateway_url: values.get('gateway_url'),
          public_base_url: values.get('public_base_url'),
          merchant_id: values.get('merchant_id'),
          merchant_key: values.get('merchant_key'),
          methods: parsePaymentMethodSettings(values.get('methods')),
        }),
      }), '支付设置已保存', adminPaymentSettingsPage);
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('支付设置', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

async function adminEmailSettingsPage() {
  loadingPage('邮件设置');
  try {
    await ensureAccountSummary();
    const settings = await api('/api/v1/admin/email-settings');
    const configuredStatus = settings.configured
      ? '<span class="status healthy">已配置</span>'
      : '<span class="status unknown">尚未配置</span>';
    const passwordStatus = settings.password_configured ? '密码已安全保存；留空表示保留' : '尚未保存密码';
    shell('邮件设置', `<div class="page-head"><div><h1>邮件设置</h1><p>配置真实 SMTP 邮箱验证投递。修改后立即作用于所有 Web 进程，无需重启。</p></div>${configuredStatus}</div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>站点与 SMTP</h2><p>SMTP 密码保存在受控密钥目录中，不写入数据库，也不会返回浏览器。</p></div></div>
        <form id="email-settings-form" class="admin-form-grid">
          <div class="field span-two"><label>公开站点地址</label><input name="public_base_url" type="url" required value="${escapeHTML(settings.public_base_url || '')}" placeholder="https://studio.example.com"><small>必须是 HTTPS Origin，不包含路径；验证链接将使用此地址。</small></div>
          <div class="field"><label>SMTP 主机</label><input name="smtp_host" required value="${escapeHTML(settings.smtp_host || '')}" placeholder="smtp.example.com"></div>
          <div class="field"><label>SMTP 端口</label><input name="smtp_port" type="number" min="1" max="65535" required value="${Number(settings.smtp_port || 587)}"></div>
          <div class="field"><label>发件地址</label><input name="smtp_sender" type="email" required value="${escapeHTML(settings.smtp_sender || '')}" placeholder="noreply@example.com"></div>
          <div class="field"><label>SMTP 用户名</label><input name="smtp_username" autocomplete="off" value="${escapeHTML(settings.smtp_username || '')}" placeholder="留空表示无需认证"></div>
          <div class="field"><label>SMTP 密码</label><input name="smtp_password" type="password" autocomplete="new-password" placeholder="${escapeHTML(passwordStatus)}"><small>${escapeHTML(passwordStatus)}</small></div>
          <div class="field"><label>安全模式</label><select name="smtp_security"><option value="starttls" ${settings.smtp_security === 'starttls' ? 'selected' : ''}>STARTTLS</option><option value="ssl" ${settings.smtp_security === 'ssl' ? 'selected' : ''}>SSL/TLS</option><option value="none" ${settings.smtp_security === 'none' ? 'selected' : ''}>无加密（仅受控内网）</option></select></div>
          <div class="field"><label>发送超时（秒）</label><input name="smtp_timeout_seconds" type="number" min="1" max="120" step="0.5" required value="${Number(settings.smtp_timeout_seconds || 10)}"></div>
          <button class="primary-btn" type="submit">保存邮件设置</button>
        </form>
      </section>`);
    document.getElementById('email-settings-form').addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api('/api/v1/admin/email-settings', {
        method: 'PUT',
        body: JSON.stringify({
          public_base_url: values.get('public_base_url'),
          smtp_host: values.get('smtp_host'),
          smtp_port: Number(values.get('smtp_port')),
          smtp_sender: values.get('smtp_sender'),
          smtp_username: values.get('smtp_username'),
          smtp_password: values.get('smtp_password'),
          smtp_security: values.get('smtp_security'),
          smtp_timeout_seconds: Number(values.get('smtp_timeout_seconds')),
        }),
      }), '邮件设置已保存', adminEmailSettingsPage);
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('邮件设置', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

async function adminPlatformContentPage() {
  loadingPage('公告与客服');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('公告与客服', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const settings = await api('/api/v1/platform-content');
    const preview = async (url, alt) => url ? `<img src="${escapeHTML(await authenticatedPlatformContentImage(url))}" alt="${alt}">` : '<span>尚未配置图片</span>';
    const [announcementPreview, supportPreview] = await Promise.all([
      preview(settings.announcement_image_url, '当前公告图片'),
      preview(settings.support_image_url, '当前客服图片'),
    ]);
    shell('公告与客服', `<div class="page-head"><div><h1>公告与客服</h1><p>编辑用户顶部图标中展示的图片与文字，保存后立即生效。</p></div></div>
      <form id="platform-content-form" class="platform-content-admin-grid">
        <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>公告内容</h2><p>适合放置活动、维护通知和平台说明。</p></div></div>
          <div class="platform-content-admin-preview">${announcementPreview}</div>
          <div class="field"><label>公告图片</label><input name="announcement_image" type="file" accept="image/png,image/jpeg,image/webp"><small>支持 PNG、JPEG、WebP，最大 5MB；不选择则保留原图。</small></div>
          <label class="checkbox-row"><input name="remove_announcement_image" type="checkbox"> 删除当前公告图片</label>
          <div class="field"><label>公告文字</label><textarea name="announcement_text" rows="9" maxlength="10000" placeholder="输入公告内容">${escapeHTML(settings.announcement_text || '')}</textarea></div>
        </section>
        <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>客服内容</h2><p>可放客服二维码、联系方式和服务时间。</p></div></div>
          <div class="platform-content-admin-preview">${supportPreview}</div>
          <div class="field"><label>客服图片</label><input name="support_image" type="file" accept="image/png,image/jpeg,image/webp"><small>支持 PNG、JPEG、WebP，最大 5MB；不选择则保留原图。</small></div>
          <label class="checkbox-row"><input name="remove_support_image" type="checkbox"> 删除当前客服图片</label>
          <div class="field"><label>客服文字</label><textarea name="support_text" rows="9" maxlength="10000" placeholder="输入客服联系方式与服务时间">${escapeHTML(settings.support_text || '')}</textarea></div>
        </section>
        <div class="platform-content-admin-actions"><button class="primary-btn" type="submit">保存公告与客服</button></div>
      </form>`);
    document.getElementById('platform-content-form').addEventListener('submit', async event => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('[type="submit"]');
      button.disabled = true;
      try {
        await api('/api/v1/admin/platform-content', { method: 'PUT', body: new FormData(event.currentTarget) });
        toast('公告与客服内容已保存');
        await adminPlatformContentPage();
      } catch (error) {
        toast(error.message);
        button.disabled = false;
      }
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('公告与客服', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

const runningHubInputCapabilityLabels = { text: '文本', image: '图片' };

function runningHubCapabilitiesTable(capabilities) {
  if (!capabilities.length) return '<div class="empty">尚未发布 RunningHub 能力。用户目录当前为空。</div>';
  return `<div class="table-wrap"><table><thead><tr><th>公开能力</th><th>输入能力</th><th>内部 workflow ID</th><th>状态</th><th>操作</th></tr></thead><tbody>${capabilities.map(capability => {
    const inputs = (capability.input_capabilities || []).map(value => runningHubInputCapabilityLabels[value] || value).join('、') || '无用户输入';
    return `<tr>
      <td><strong>${escapeHTML(capability.name)}</strong><br><span class="mono">${escapeHTML(capability.capability_id)}</span></td>
      <td>${escapeHTML(inputs)}</td>
      <td class="mono">${escapeHTML(capability.workflow_id)}</td>
      <td><span class="status ${capability.available ? 'healthy' : 'unknown'}">${capability.available ? '可用' : '已停用'}</span></td>
      <td><div class="row-actions"><button class="text-btn" data-runninghub-edit="${escapeHTML(capability.capability_id)}">编辑</button><button class="text-btn" data-runninghub-toggle="${escapeHTML(capability.capability_id)}" data-available="${capability.available}">${capability.available ? '停用' : '启用'}</button></div></td>
    </tr>`;
  }).join('')}</tbody></table></div>`;
}

function runningHubSchemaInputRow() {
  return `<tr data-runninghub-schema-input>
    <td><input name="input_key" required maxlength="64" pattern="[a-z][a-z0-9_]*" placeholder="prompt"></td>
    <td><input name="label" required maxlength="120" placeholder="提示词"></td>
    <td><select name="kind"><option value="text">文本</option><option value="image">图片</option></select></td>
    <td><label><input name="required" type="checkbox"> 必填</label></td>
    <td><div class="row-actions"><button class="text-btn" type="button" data-runninghub-schema-action="up">上移</button><button class="text-btn" type="button" data-runninghub-schema-action="down">下移</button><button class="text-btn" type="button" data-runninghub-schema-action="remove">移除</button></div></td>
  </tr>`;
}

function runningHubSchemaHistory(versions) {
  if (!versions.length) return '<div class="empty">尚未发布输入 schema。</div>';
  return `<div class="table-wrap"><table><thead><tr><th>版本</th><th>输入顺序</th><th>发布时间</th></tr></thead><tbody>${versions.map((version, index) => {
    const inputs = version.inputs.map(input => `${input.label} (${runningHubInputCapabilityLabels[input.kind] || input.kind}${input.required ? '，必填' : '，选填'})`).join(' → ');
    return `<tr><td><strong>v${version.version}</strong>${index === versions.length - 1 ? ' <span class="status healthy">当前</span>' : ''}<br><span class="mono">${escapeHTML(version.schema_version_id)}</span></td><td>${escapeHTML(inputs)}</td><td>${formatDate(version.published_at)}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function runningHubSchemaManagement(capabilities, schemaHistories) {
  if (!capabilities.length) return '';
  return `<div class="section-head"><div><h2>版本化输入 schema</h2><p>输入顺序会成为公开 schema 的稳定顺序。每次提交发布一个新版本；历史版本不可编辑或删除。</p></div></div>${capabilities.map(capability => {
    const versions = schemaHistories[capability.capability_id] || [];
    return `<section class="panel"><div class="section-head" style="margin-top:0"><div><h3>${escapeHTML(capability.name)}</h3><p><span class="mono">${escapeHTML(capability.capability_id)}</span> · ${versions.length ? `当前 v${versions.at(-1).version}` : '尚无 schema'}</p></div></div>
      <form data-runninghub-schema-form="${escapeHTML(capability.capability_id)}">
        <div class="table-wrap"><table><thead><tr><th>input_key</th><th>用户标签</th><th>类型</th><th>是否必填</th><th>顺序</th></tr></thead><tbody data-runninghub-schema-inputs>${runningHubSchemaInputRow()}</tbody></table></div>
        <div class="row-actions" style="margin-top:14px"><button class="secondary-btn" type="button" data-runninghub-schema-action="add">添加输入</button><button class="primary-btn" type="submit">发布新 schema 版本</button></div>
      </form>
      <div class="section-head"><div><h4>版本历史</h4><p>历史版本不可编辑或删除；用户目录只读取最新版本。</p></div></div>
      ${runningHubSchemaHistory(versions)}
    </section>`;
  }).join('')}`;
}

function runningHubUserPriceHistory(versions) {
  if (!versions.length) return '<div class="empty">尚未发布用户价格。</div>';
  const now = Date.now();
  const effective = versions.filter(version => new Date(version.effective_from).getTime() <= now);
  const current = effective.reduce((selected, version) => (
    !selected || new Date(selected.effective_from) < new Date(version.effective_from) ? version : selected
  ), null);
  return `<div class="table-wrap"><table><thead><tr><th>版本</th><th>每次能力使用</th><th>生效时间</th><th>发布时间</th><th>状态</th></tr></thead><tbody>${versions.map(version => {
    const isCurrent = current?.price_version_id === version.price_version_id;
    const isFuture = new Date(version.effective_from).getTime() > now;
    const statusLabel = isCurrent ? '当前生效' : (isFuture ? '未来生效' : '历史版本');
    const statusClass = isCurrent ? 'healthy' : (isFuture ? 'pending' : 'unknown');
    return `<tr><td><strong>v${version.version}</strong><br><span class="mono">${escapeHTML(version.price_version_id)}</span></td><td>${formatCredits(version.credits_per_run)} 额度</td><td>${formatDate(version.effective_from)}</td><td>${formatDate(version.published_at)}</td><td><span class="status ${statusClass}">${statusLabel}</span></td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function runningHubUserPriceManagement(capabilities, priceHistories) {
  if (!capabilities.length) return '';
  return `<div class="section-head"><div><h2>版本化用户价格</h2><p>价格按每次能力使用计费并独立于 Provider 成本。每次提交发布一个新版本；价格历史不可编辑或删除。</p></div></div>${capabilities.map(capability => {
    const versions = priceHistories[capability.capability_id] || [];
    return `<section class="panel"><div class="section-head" style="margin-top:0"><div><h3>${escapeHTML(capability.name)}</h3><p><span class="mono">${escapeHTML(capability.capability_id)}</span></p></div></div>
      <form data-runninghub-price-form="${escapeHTML(capability.capability_id)}" class="admin-form-grid">
        <div class="field"><label>每次能力使用额度</label><input name="credits_per_run" type="number" min="0.0001" step="0.0001" value="0.1000" required></div>
        <div class="field"><label>生效时间</label><input name="effective_from" type="datetime-local" value="${localDateTimeValue(new Date())}" required></div>
        <button class="primary-btn" type="submit">发布用户价格版本</button>
      </form>
      <div class="section-head"><div><h4>价格历史</h4><p>价格历史不可编辑或删除；用户目录只显示当前生效价格。</p></div></div>
      ${runningHubUserPriceHistory(versions)}
    </section>`;
  }).join('')}`;
}

async function adminRunningHubCapabilitiesPage() {
  loadingPage('RunningHub 能力目录');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('RunningHub 能力目录', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const capabilities = await api('/api/v1/admin/runninghub-capabilities');
    const schemaEntries = await Promise.all(capabilities.map(async capability => [
      capability.capability_id,
      await api(`/api/v1/admin/runninghub-capabilities/${encodeURIComponent(capability.capability_id)}/input-schema-versions`),
    ]));
    const schemaHistories = Object.fromEntries(schemaEntries);
    const priceEntries = await Promise.all(capabilities.map(async capability => [
      capability.capability_id,
      await api(`/api/v1/admin/runninghub-capabilities/${encodeURIComponent(capability.capability_id)}/price-versions`),
    ]));
    const priceHistories = Object.fromEntries(priceEntries);
    shell('RunningHub 能力目录', `<div class="page-head"><div><h1>RunningHub 能力目录</h1><p>发布稳定的用户能力身份，并在平台内部绑定 RunningHub 工作流。</p></div></div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>发布或编辑能力</h2><p>内部 workflow ID 仅供管理员绑定工作流；用户目录不会返回该字段。当前不提供删除，只能停用能力。</p></div></div>
        <form id="runninghub-capability-form" class="admin-form-grid">
          <input name="capability_id" type="hidden">
          <div class="field"><label>公开名称</label><input name="name" required maxlength="120" placeholder="商品摄影"></div>
          <div class="field"><label>内部 workflow ID</label><input name="workflow_id" required maxlength="255" autocomplete="off" placeholder="仅管理员可见"></div>
          <div class="field span-two"><label>粗粒度输入能力</label><div class="row-actions"><label><input name="input_capabilities" type="checkbox" value="text"> 文本</label><label><input name="input_capabilities" type="checkbox" value="image"> 图片</label></div></div>
          <div class="field"><label><input name="available" type="checkbox" checked> 发布后立即可用</label></div>
          <div class="row-actions"><button class="primary-btn" type="submit">保存能力</button><button class="secondary-btn" id="runninghub-capability-reset" type="button">取消编辑</button></div>
        </form>
      </section>
      <div class="section-head"><div><h2>已发布能力</h2><p>公开能力 ID 保持稳定；停用项仍保留在用户目录并显示为不可用。不提供删除。</p></div></div>${runningHubCapabilitiesTable(capabilities)}
      ${runningHubSchemaManagement(capabilities, schemaHistories)}
      ${runningHubUserPriceManagement(capabilities, priceHistories)}`);

    const form = document.getElementById('runninghub-capability-form');
    const resetForm = () => {
      form.reset();
      form.elements.capability_id.value = '';
      form.elements.available.checked = true;
      form.querySelectorAll('[name="input_capabilities"]').forEach(input => { input.disabled = false; });
    };
    document.getElementById('runninghub-capability-reset').addEventListener('click', resetForm);
    document.querySelectorAll('[data-runninghub-edit]').forEach(button => button.addEventListener('click', () => {
      const capability = capabilities.find(item => item.capability_id === button.dataset.runninghubEdit);
      if (!capability) return;
      form.elements.capability_id.value = capability.capability_id;
      form.elements.name.value = capability.name;
      form.elements.workflow_id.value = capability.workflow_id;
      form.elements.available.checked = capability.available;
      const selected = new Set(capability.input_capabilities || []);
      const hasSchema = (schemaHistories[capability.capability_id] || []).length > 0;
      form.querySelectorAll('[name="input_capabilities"]').forEach(input => {
        input.checked = selected.has(input.value);
        input.disabled = hasSchema;
      });
      form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      const capabilityId = String(values.get('capability_id') || '');
      const payload = {
        name: values.get('name'),
        workflow_id: values.get('workflow_id'),
        available: values.get('available') === 'on',
      };
      if (!capabilityId || !(schemaHistories[capabilityId] || []).length) payload.input_capabilities = values.getAll('input_capabilities');
      const path = capabilityId
        ? `/api/v1/admin/runninghub-capabilities/${encodeURIComponent(capabilityId)}`
        : '/api/v1/admin/runninghub-capabilities';
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api(path, {
        method: capabilityId ? 'PATCH' : 'POST',
        body: JSON.stringify(payload),
      }), capabilityId ? 'RunningHub 能力已更新' : 'RunningHub 能力已发布', adminRunningHubCapabilitiesPage);
    });
    document.querySelectorAll('[data-runninghub-toggle]').forEach(button => button.addEventListener('click', () => runAdminAction(
      button,
      () => api(`/api/v1/admin/runninghub-capabilities/${encodeURIComponent(button.dataset.runninghubToggle)}`, {
        method: 'PATCH',
        body: JSON.stringify({ available: button.dataset.available !== 'true' }),
      }),
      'RunningHub 能力状态已更新',
      adminRunningHubCapabilitiesPage,
    )));
    document.querySelectorAll('[data-runninghub-schema-form]').forEach(schemaForm => {
      schemaForm.addEventListener('click', event => {
        const button = event.target.closest('[data-runninghub-schema-action]');
        if (!button) return;
        const action = button.dataset.runninghubSchemaAction;
        const inputRows = schemaForm.querySelector('[data-runninghub-schema-inputs]');
        if (action === 'add') {
          inputRows.insertAdjacentHTML('beforeend', runningHubSchemaInputRow());
          return;
        }
        const row = button.closest('[data-runninghub-schema-input]');
        if (!row) return;
        if (action === 'up' && row.previousElementSibling) inputRows.insertBefore(row, row.previousElementSibling);
        if (action === 'down' && row.nextElementSibling) inputRows.insertBefore(row.nextElementSibling, row);
        if (action === 'remove' && inputRows.children.length > 1) row.remove();
      });
      schemaForm.addEventListener('submit', async event => {
        event.preventDefault();
        const capabilityId = event.currentTarget.dataset.runninghubSchemaForm;
        const inputs = [...event.currentTarget.querySelectorAll('[data-runninghub-schema-input]')].map(row => ({
          input_key: row.querySelector('[name="input_key"]').value,
          label: row.querySelector('[name="label"]').value,
          kind: row.querySelector('[name="kind"]').value,
          required: row.querySelector('[name="required"]').checked,
        }));
        await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api(
          `/api/v1/admin/runninghub-capabilities/${encodeURIComponent(capabilityId)}/input-schema-versions`,
          { method: 'POST', body: JSON.stringify({ inputs }) },
        ), 'RunningHub 输入 schema 新版本已发布', adminRunningHubCapabilitiesPage);
      });
    });
    document.querySelectorAll('[data-runninghub-price-form]').forEach(priceForm => {
      priceForm.addEventListener('submit', async event => {
        event.preventDefault();
        const capabilityId = event.currentTarget.dataset.runninghubPriceForm;
        const values = new FormData(event.currentTarget);
        await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api(
          `/api/v1/admin/runninghub-capabilities/${encodeURIComponent(capabilityId)}/price-versions`,
          {
            method: 'POST',
            body: JSON.stringify({
              credits_per_run: values.get('credits_per_run'),
              effective_from: new Date(values.get('effective_from')).toISOString(),
            }),
          },
        ), 'RunningHub 用户价格新版本已发布', adminRunningHubCapabilitiesPage);
      });
    });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('RunningHub 能力目录', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

async function adminRoutingPage() {
  loadingPage('模型路由');
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell('模型路由', '<div class="empty">当前账户没有平台管理员权限。</div>');
    const providers = state.adminProviders;
    const [routes, prices] = await Promise.all([
      api('/api/v1/admin/image-model-routes'),
      api('/api/v1/model-prices'),
    ]);
    const routeSpecs = [...new Map(routes.map(route => [`${route.logical_model}\u0000${route.output_spec}`, route])).values()];
    const policies = Object.fromEntries(await Promise.all(routeSpecs.map(async spec => [
      `${spec.logical_model}\u0000${spec.output_spec}`,
      await api(`/api/v1/admin/image-models/${encodeURIComponent(spec.logical_model)}/${encodeURIComponent(spec.output_spec)}/routing-policy`),
    ])));
    const healthEntries = await Promise.all(routes.map(async route => [
      route.route_id,
      await optionalApi(`/api/v1/admin/image-model-routes/${encodeURIComponent(route.route_id)}/health`, null),
    ]));
    const healthByRoute = Object.fromEntries(healthEntries);
    const defaultEffectiveTime = localDateTimeValue(new Date(Date.now() + 5 * 60_000));
    shell('模型路由', `<div class="page-head"><div><h1>模型路由与价格</h1><p>依次配置来源、逻辑模型映射、健康资格、用户售价和选择策略。用户只看到逻辑模型，不接触 API 地址、凭据或来源路由。</p></div></div>
      <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>① API 来源</h2><p>先登记上游连接并决定来源是否启用。API Key 为只写字段，保存后只显示不可逆短指纹；修改地址、Key 或模型映射后，相关路由会自动停用并等待重新检测。</p></div></div>
        <form id="provider-form" class="admin-form-grid">
          <input name="provider_id" type="hidden">
          <div class="field"><label>来源代码</label><input name="code" required placeholder="source-a"></div>
          <div class="field"><label>显示名称</label><input name="display_name" required placeholder="图片来源 A"></div>
          <div class="field span-two"><label>API 基础地址</label><input name="base_url" type="url" required placeholder="https://example.com/v1"></div>
          <div class="field"><label>图片响应模式</label><select name="image_response_mode"><option value="auto">自动兼容（推荐）</option><option value="sync_json">同步 JSON</option><option value="sse">SSE 流式</option><option value="async_task">异步 task_id</option></select></div>
          <div class="field"><label>上游账户共享组</label><input name="concurrency_group" required placeholder="例如 originboost-main"></div>
          <div class="field"><label>账户共享并发数</label><input name="max_concurrency" type="number" min="1" max="1000" value="50" required></div>
          <div class="field"><label>上游请求超时（秒）</label><input name="request_timeout_seconds" type="number" min="60" max="1800" value="600" required><small>这是绝对时限，SSE 心跳不会延长；应小于或等于任务自动截止时间。</small></div>
          <div class="field span-two"><label>API Key（只写）</label><input name="api_key" type="password" autocomplete="new-password" required placeholder="保存后不可读取"></div>
          <div class="row-actions"><button class="primary-btn" type="submit">保存来源</button><button class="secondary-btn" id="provider-form-reset" type="button" hidden>取消编辑</button></div>
        </form>
        <div class="section-head"><div><h3>已配置来源</h3><p>来源启用只是第一道开关，还需要路由启用且最近健康检测可用。</p></div></div>${providersTable(providers)}
      </section>
      <section class="panel admin-panel"><div class="section-head" style="margin-top:0"><div><h2>② 模型映射与路由</h2><p>把用户看到的逻辑模型与成品规格映射到来源的上游模型。售价不同的来源应使用不同逻辑模型。新路由默认停用，必须先检测成功再启用；优先级只在健康与性能指标相同时作为最后裁决，数值越小越优先。</p></div></div>
        <form id="route-form" class="admin-form-grid">
          <input name="route_id" type="hidden">
          <div class="field"><label>API 来源</label><select name="provider_id" required>${providerOptions(providers)}</select></div>
          <div class="field"><label>上游模型名称</label><input name="provider_model_name" value="gpt-image-2" required></div>
          <div class="field"><label>逻辑模型</label><input name="logical_model" value="gpt-image-2" required placeholder="例如 gpt-image-2-kapi"></div>
          <div class="field"><label>成品规格</label><input name="output_spec" value="4k" required placeholder="例如 4k"></div>
          <div class="field"><label>兼容组</label><input name="compatibility_group" value="gpt-image-2/4k/v1" required></div>
          <div class="field"><label>优先级</label><input name="priority" type="number" min="0" max="10000" value="100" required></div>
          <div class="field"><label>最大上传参考图张数</label><input name="max_reference_images" type="number" min="0" max="16" step="1" value="3" required><small>按逻辑模型与成品规格保存；gpt-image-2 当前建议不超过 3 张，多图仍受具体上游兼容性影响。</small></div>
          <div class="row-actions"><button class="primary-btn" type="submit" ${providers.length ? '' : 'disabled'}>创建路由</button><button class="secondary-btn" id="route-form-reset" type="button" hidden>取消编辑</button></div>
        </form>
      </section>
      <div class="section-head"><div><h2>③ 健康检测与选路资格</h2><p>只有“来源已启用 + 路由已启用 + 最近健康检测可用”才可参与选路。服务端按最近一次检测完成时间每 24 小时自动检测，管理员也可手动检测；检测期间保持上一次状态，检测不会自动改变启用开关。</p></div></div>${routesTable(routes, providers, healthByRoute)}
      <section class="panel admin-panel"><div class="section-head" style="margin-top:0"><div><h2>④ 用户售价</h2><p>售价按“逻辑模型 + 成品规格”设置，与 Provider 成本独立。先创建路由，再为对应逻辑模型发布价格。</p></div></div>
        <form id="routing-price-form" class="admin-form-grid">
          <div class="field span-two"><label>逻辑模型与成品规格</label><select name="model_spec" ${routeSpecs.length ? '' : 'disabled'}>${routeSpecs.map((spec, index) => `<option value="${index}">${escapeHTML(spec.logical_model)}/${escapeHTML(spec.output_spec)}</option>`).join('')}</select></div>
          <div class="field"><label>每张价格（额度）</label><input name="credits_per_result" type="number" min="0.0001" step="0.0001" required placeholder="0.2000"></div>
          <div class="field"><label>生效时间</label><input name="effective_from" type="datetime-local" value="${defaultEffectiveTime}" required></div>
          <button class="primary-btn" type="submit" ${routeSpecs.length ? '' : 'disabled'}>发布价格版本</button>
        </form>
        <div class="section-head"><div><h3>当前生效价格</h3><p>新版本生效后会自动替换当前价格，历史版本仍保留。</p></div></div>${modelPricesTable(prices)}
      </section>
      <section class="panel admin-panel"><div class="section-head" style="margin-top:0"><div><h2>⑤ 选择策略</h2><p>每个逻辑模型规格独立选择策略；指定来源不可用时会自动回退同一兼容组的其他健康来源。</p></div></div>
        ${routeSpecs.length ? routeSpecs.map(spec => { const key = `${spec.logical_model}\u0000${spec.output_spec}`; const matching = routes.filter(route => route.logical_model === spec.logical_model && route.output_spec === spec.output_spec); return `<form class="admin-form-grid policy-form" data-logical-model="${escapeHTML(spec.logical_model)}" data-output-spec="${escapeHTML(spec.output_spec)}"><div class="field span-two"><label>${escapeHTML(spec.logical_model)}/${escapeHTML(spec.output_spec)} 来源策略</label><select name="preferred_route_id">${routeOptions(matching, policies[key]?.preferred_route_id || '')}</select></div><button class="primary-btn" type="submit">保存策略</button></form>`; }).join('') : '<div class="empty">创建路由后可设置选择策略。</div>'}
      </section>`, 'admin-routing-page');

    const providerForm = document.getElementById('provider-form');
    const resetProviderForm = () => {
      providerForm.reset();
      providerForm.elements.provider_id.value = '';
      providerForm.elements.code.disabled = false;
      providerForm.elements.api_key.required = true;
      providerForm.elements.api_key.placeholder = '保存后不可读取';
      providerForm.elements.image_response_mode.value = 'auto';
      providerForm.elements.max_concurrency.value = '20';
      providerForm.elements.request_timeout_seconds.value = '600';
      providerForm.querySelector('[type="submit"]').textContent = '保存来源';
      document.getElementById('provider-form-reset').hidden = true;
    };
    providerForm.addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const providerId = form.get('provider_id');
      const apiKey = form.get('api_key');
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), async () => {
        if (providerId) {
          const body = { display_name: form.get('display_name'), base_url: form.get('base_url'), image_response_mode: form.get('image_response_mode'), concurrency_group: form.get('concurrency_group'), max_concurrency: Number(form.get('max_concurrency')), request_timeout_seconds: Number(form.get('request_timeout_seconds')) };
          if (apiKey) body.api_key = apiKey;
          await api(`/api/v1/admin/providers/${encodeURIComponent(providerId)}`, { method: 'PATCH', body: JSON.stringify(body) });
        } else {
          if (!apiKey) throw new Error('创建 API 来源时必须填写 API Key');
          await api('/api/v1/admin/providers', { method: 'POST', body: JSON.stringify({ code: form.get('code'), display_name: form.get('display_name'), protocol: 'openai_compatible_images', base_url: form.get('base_url'), api_key: apiKey, image_response_mode: form.get('image_response_mode'), concurrency_group: form.get('concurrency_group'), max_concurrency: Number(form.get('max_concurrency')), request_timeout_seconds: Number(form.get('request_timeout_seconds')) }) });
        }
      }, providerId ? 'API 来源已更新，连接变化会要求路由重新检测' : 'API 来源已保存');
    });
    document.getElementById('provider-form-reset').addEventListener('click', resetProviderForm);
    document.querySelectorAll('[data-provider-edit]').forEach(button => button.addEventListener('click', () => {
      const provider = providers.find(item => item.provider_id === button.dataset.providerEdit);
      if (!provider) return;
      providerForm.elements.provider_id.value = provider.provider_id;
      providerForm.elements.code.value = provider.code;
      providerForm.elements.code.disabled = true;
      providerForm.elements.display_name.value = provider.display_name;
      providerForm.elements.base_url.value = provider.base_url;
      providerForm.elements.image_response_mode.value = provider.image_response_mode || 'auto';
      providerForm.elements.concurrency_group.value = provider.concurrency_group || provider.code;
      providerForm.elements.max_concurrency.value = provider.max_concurrency || 20;
      providerForm.elements.request_timeout_seconds.value = provider.request_timeout_seconds || 600;
      providerForm.elements.api_key.value = '';
      providerForm.elements.api_key.required = false;
      providerForm.elements.api_key.placeholder = '留空表示不轮换';
      providerForm.querySelector('[type="submit"]').textContent = '保存修改';
      document.getElementById('provider-form-reset').hidden = false;
      providerForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }));
    document.querySelectorAll('[data-provider-delete]').forEach(button => button.addEventListener('click', async () => {
      const provider = providers.find(item => item.provider_id === button.dataset.providerDelete);
      if (!provider || !await centeredDeleteConfirm(`永久删除 API 来源「${provider.display_name}」？请先删除它的全部模型路由。此操作不可恢复。`)) return;
      runAdminAction(button, () => api(`/api/v1/admin/providers/${encodeURIComponent(provider.provider_id)}`, { method: 'DELETE' }), 'API 来源已永久删除');
    }));

    const routeForm = document.getElementById('route-form');
    const resetRouteForm = () => {
      routeForm.reset();
      routeForm.elements.route_id.value = '';
      routeForm.elements.provider_id.disabled = false;
      routeForm.elements.logical_model.disabled = false;
      routeForm.elements.output_spec.disabled = false;
      routeForm.elements.provider_model_name.disabled = false;
      routeForm.elements.compatibility_group.disabled = false;
      routeForm.elements.max_reference_images.value = '3';
      syncRouteReferenceLimitFromSpec();
      routeForm.querySelector('[type="submit"]').textContent = '创建路由';
      document.getElementById('route-form-reset').hidden = true;
    };
    const syncRouteReferenceLimitFromSpec = () => {
      if (routeForm.elements.route_id.value) return;
      const logicalModel = routeForm.elements.logical_model.value.trim();
      const outputSpec = routeForm.elements.output_spec.value.trim();
      const existingRoute = routes.find(route => route.logical_model === logicalModel && route.output_spec === outputSpec);
      if (existingRoute) {
        routeForm.elements.max_reference_images.value = normalizedImageReferenceLimit(existingRoute.max_reference_images);
      }
    };
    routeForm.elements.logical_model.addEventListener('change', syncRouteReferenceLimitFromSpec);
    routeForm.elements.output_spec.addEventListener('change', syncRouteReferenceLimitFromSpec);
    syncRouteReferenceLimitFromSpec();
    routeForm.addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const routeId = form.get('route_id');
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), async () => {
        const editable = { provider_model_name: form.get('provider_model_name'), compatibility_group: form.get('compatibility_group'), priority: Number(form.get('priority')), max_reference_images: Number(form.get('max_reference_images')) };
        if (routeId) {
          await api(`/api/v1/admin/image-model-routes/${encodeURIComponent(routeId)}`, { method: 'PATCH', body: JSON.stringify(editable) });
        } else {
          await api('/api/v1/admin/image-model-routes', { method: 'POST', body: JSON.stringify({ provider_id: form.get('provider_id'), logical_model: form.get('logical_model'), output_spec: form.get('output_spec'), ...editable }) });
        }
      }, routeId ? '模型路由已更新，请重新健康检测后启用' : '模型路由已创建');
    });
    document.getElementById('route-form-reset').addEventListener('click', resetRouteForm);
    document.querySelectorAll('[data-route-edit]').forEach(button => button.addEventListener('click', () => {
      const route = routes.find(item => item.route_id === button.dataset.routeEdit);
      if (!route) return;
      routeForm.elements.route_id.value = route.route_id;
      routeForm.elements.provider_id.value = route.provider_id;
      routeForm.elements.provider_id.disabled = true;
      routeForm.elements.logical_model.value = route.logical_model;
      routeForm.elements.logical_model.disabled = true;
      routeForm.elements.output_spec.value = route.output_spec;
      routeForm.elements.output_spec.disabled = true;
      routeForm.elements.provider_model_name.value = route.provider_model_name;
      routeForm.elements.provider_model_name.disabled = route.enabled;
      routeForm.elements.compatibility_group.value = route.compatibility_group;
      routeForm.elements.compatibility_group.disabled = route.enabled;
      routeForm.elements.priority.value = route.priority;
      routeForm.elements.max_reference_images.value = normalizedImageReferenceLimit(route.max_reference_images);
      routeForm.querySelector('[type="submit"]').textContent = '保存修改';
      document.getElementById('route-form-reset').hidden = false;
      routeForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (route.enabled) toast('路由已启用，本次只能修改优先级；停用后才能修改模型映射');
    }));
    document.querySelectorAll('[data-route-delete]').forEach(button => button.addEventListener('click', async () => {
      const route = routes.find(item => item.route_id === button.dataset.routeDelete);
      if (!route || !await centeredDeleteConfirm(`永久删除模型路由「${route.provider_model_name}」？若它是指定优先路由，策略会恢复为自动选择。历史任务和成本记录仍保留。`)) return;
      runAdminAction(button, () => api(`/api/v1/admin/image-model-routes/${encodeURIComponent(route.route_id)}`, { method: 'DELETE' }), '模型路由已永久删除');
    }));
    document.querySelectorAll('.policy-form').forEach(policyForm => policyForm.addEventListener('submit', async event => {
      event.preventDefault();
      const routeId = new FormData(event.currentTarget).get('preferred_route_id') || '';
      const logicalModel = event.currentTarget.dataset.logicalModel;
      const outputSpec = event.currentTarget.dataset.outputSpec;
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), async () => {
        await api(`/api/v1/admin/image-models/${encodeURIComponent(logicalModel)}/${encodeURIComponent(outputSpec)}/routing-policy`, { method: 'PUT', body: JSON.stringify({ mode: routeId ? 'preferred' : 'automatic', preferred_route_id: routeId }) });
      }, '路由策略已保存');
    }));
    const routingPriceForm = document.getElementById('routing-price-form');
    routingPriceForm.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      const spec = routeSpecs[Number(values.get('model_spec'))];
      if (!spec) return toast('请先创建逻辑模型路由');
      const selectedEffectiveTime = new Date(String(values.get('effective_from')));
      if (Number.isNaN(selectedEffectiveTime.getTime())) return toast('请选择有效的生效时间');
      const effectiveTime = selectedEffectiveTime.getTime() <= Date.now()
        ? new Date(Date.now() + 5_000)
        : selectedEffectiveTime;
      await runAdminAction(event.currentTarget.querySelector('[type="submit"]'), () => api('/api/v1/admin/model-prices', {
        method: 'POST',
        body: JSON.stringify({
          logical_model: spec.logical_model,
          output_spec: spec.output_spec,
          credits_per_result: values.get('credits_per_result'),
          effective_from: effectiveTime.toISOString(),
        }),
      }), '模型价格版本已发布');
    });
    document.querySelectorAll('[data-price-delete]').forEach(button => button.addEventListener('click', async () => {
      if (!await centeredDeleteConfirm(`删除价格「${button.dataset.priceLabel}」？删除后该逻辑模型将从用户模型目录移除，历史任务仍保留原价格。`)) return;
      await runAdminAction(button, () => api(`/api/v1/admin/model-prices/${encodeURIComponent(button.dataset.priceDelete)}`, {
        method: 'DELETE',
      }), '模型价格已删除');
    }));
    document.querySelectorAll('[data-provider-toggle]').forEach(button => button.addEventListener('click', () => runAdminAction(button, () => api(`/api/v1/admin/providers/${encodeURIComponent(button.dataset.providerToggle)}`, { method: 'PATCH', body: JSON.stringify({ enabled: button.dataset.enabled !== 'true' }) }), '来源状态已更新')));
    document.querySelectorAll('[data-route-toggle]').forEach(button => button.addEventListener('click', () => runAdminAction(button, () => api(`/api/v1/admin/image-model-routes/${encodeURIComponent(button.dataset.routeToggle)}`, { method: 'PATCH', body: JSON.stringify({ enabled: button.dataset.enabled !== 'true' }) }), '路由状态已更新')));
    document.querySelectorAll('[data-health-check]').forEach(button => button.addEventListener('click', () => runAdminAction(button, () => api(`/api/v1/admin/image-model-routes/${encodeURIComponent(button.dataset.healthCheck)}/health-check`, { method: 'POST' }), '健康检测已完成')));
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell('模型路由', `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

async function runAdminAction(button, action, successMessage, refreshPage = adminRoutingPage) {
  button.disabled = true;
  try {
    await action();
    toast(successMessage);
    await refreshPage();
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
}

const vueAdminRoutes = new Set([
  '/admin/users',
  '/admin/generation-tasks',
  '/admin/storage-allowance',
  '/admin/generation-capacity',
  '/admin/email-settings',
  '/admin/platform-content',
  '/admin/model-routing',
  '/admin/provider-costs',
  '/admin/recharge-packages',
  '/admin/redeem-codes',
  '/admin/payment-settings',
  '/admin/runninghub-capabilities',
]);

const vueAdminTitles = {
  '/admin/users': '用户管理',
  '/admin/generation-tasks': '任务管理',
  '/admin/storage-allowance': '存储额度',
  '/admin/generation-capacity': '生成容量',
  '/admin/email-settings': '邮件设置',
  '/admin/platform-content': '公告与客服',
  '/admin/model-routing': '模型路由',
  '/admin/provider-costs': 'Provider 成本',
  '/admin/recharge-packages': '充值包',
  '/admin/redeem-codes': '兑换码',
  '/admin/payment-settings': '支付设置',
  '/admin/runninghub-capabilities': 'RunningHub 能力目录',
};

const vueWorkspaceRoutes = new Set([
  '/workspace/account',
  '/workspace/wallet',
  '/workspace/generations',
  '/workspace/models',
  '/workspace/assets',
  '/workspace/llm-settings',
]);

const vueWorkspaceTitles = {
  '/workspace/account': '个人账户',
  '/workspace/wallet': '钱包',
  '/workspace/generations': '生成任务',
  '/workspace/models': '模型目录',
  '/workspace/assets': '资产库',
  '/workspace/llm-settings': 'LLM 设置',
};

function createVueBridge() {
  return {
    api: async (...args) => {
      try {
        return await api(...args);
      } catch (error) {
        if (!state.token) navigate('/login', { replace: true });
        throw error;
      }
    },
    toast,
    confirm: centeredDeleteConfirm,
    navigate: path => navigate(path),
    navigateToLogin: () => navigate('/login', { replace: true }),
    invalidateSession: message => {
      setToken(null);
      state.user = null;
      state.balance = null;
      state.accountSummaryLoaded = false;
      resetImageWorkspaceState();
      toast(message);
      navigate('/login', { replace: true });
    },
    checkout: submitPaymentCheckout,
    authenticatedImage: authenticatedPlatformContentImage,
    currentUser: state.user,
    currentBalance: state.balance,
  };
}

async function adminVuePage() {
  const route = state.route;
  const title = vueAdminTitles[route] || '管理后台';
  loadingPage(title);
  try {
    await ensureAccountSummary();
    if (!state.isAdmin) return shell(title, '<div class="empty">当前账户没有平台管理员权限。</div>');
    shell(title, '<div id="admin-vue-root"><div class="loading">正在加载 Vue 管理页面…</div></div>', 'vue-admin-page');
    const mount = () => {
      const element = document.getElementById('admin-vue-root');
      if (!element || state.route !== route || !window.mountAdminVue) return;
      window.mountAdminVue({
        element,
        route,
        bridge: createVueBridge(),
      });
    };
    if (window.mountAdminVue) mount();
    else window.addEventListener('admin-vue-ready', mount, { once: true });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell(title, `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

async function workspaceVuePage() {
  const route = state.route;
  const title = vueWorkspaceTitles[route] || '账户中心';
  loadingPage(title);
  try {
    await ensureAccountSummary();
    shell(title, '<div id="admin-vue-root"><div class="loading">正在加载 Vue 页面…</div></div>', 'vue-workspace-page');
    const mount = () => {
      const element = document.getElementById('admin-vue-root');
      if (!element || state.route !== route || !window.mountAdminVue) return;
      window.mountAdminVue({ element, route, bridge: createVueBridge() });
    };
    if (window.mountAdminVue) mount();
    else window.addEventListener('admin-vue-ready', mount, { once: true });
  } catch (error) {
    if (!state.token) return navigate('/login', { replace: true });
    shell(title, `<div class="empty">${escapeHTML(error.message)}</div>`);
  }
}

function render() {
  state.route = window.location.pathname;
  state.navigationEpoch += 1;
  // A navigation invalidates the previous page's account-loading placeholder.
  // Without clearing these markers, a synchronous workbench route (images and
  // inpainting) can be rejected by shell() as an obsolete render and leave the
  // previous page stuck on “正在读取账户数据…”.
  state.loadingRoute = '';
  state.loadingEpoch = 0;
  if (state.route !== '/workspace/canvases') {
    window.clearTimeout(state.canvasPreviewRefreshTimer);
    state.canvasPreviewRefreshTimer = null;
    clearCanvasPreviewUrls();
  }
  // Keep the in-memory image workbench snapshot while switching pages.
  if (state.route === '/verify-email') return verifyEmailPage();
  if (state.route === '/forgot-password') return passwordResetRequestPage();
  if (state.route === '/reset-password') return resetPasswordPage();
  if (state.route === '/' || state.route === '/login' || state.route === '/register') {
    if (state.token && state.route !== '/register') return navigate('/workspace/account', { replace: true });
    return authView(state.route === '/register' ? 'register' : 'login');
  }
  if (!state.token) return navigate('/login', { replace: true });
  if (vueAdminRoutes.has(state.route)) return adminVuePage();
  if (vueWorkspaceRoutes.has(state.route)) return workspaceVuePage();
  if (state.route === '/admin/runninghub-capabilities') return adminRunningHubCapabilitiesPage();
  if (state.route === '/admin/payment-settings') return adminPaymentSettingsPage();
  if (state.route === '/admin/recharge-packages') return adminRechargePackagesPage();
  if (state.route === '/admin/model-prices') return navigate('/admin/model-routing', { replace: true });
  if (state.route === '/admin/provider-costs') return adminProviderCostsPage();
  if (state.route === '/admin/model-routing') return adminRoutingPage();
  if (state.route === '/workspace/generations') return workspaceGenerationsPage();
  if (state.route === '/workspace/images') return workspaceImagesPage();
  if (state.route === '/workspace/inpainting') return workspaceInpaintingPage();
  if (state.route === '/workspace/canvases') return workspaceCanvasesPage();
  if (state.route === '/workspace/llm-settings') return workspaceLLMSettingsPage();
  if (state.route === '/workspace/assets') return workspaceAssetsPage();
  if (state.route === '/workspace/models') return workspaceModelsPage();
  if (state.route === '/workspace/wallet') return walletPage();
  if (state.route === '/workspace/account') return accountPage();
  return navigate('/workspace/account', { replace: true });
}

window.addEventListener('popstate', () => render());
render();
