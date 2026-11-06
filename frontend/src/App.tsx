import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Bot, Sparkles, Home, Wrench, ShoppingBag, User as UserIcon,
  RefreshCw, LogOut, Shield, Send, Plus, Trash2, ExternalLink,
  Menu, X, ChevronLeft, Wallet, Radio, Settings,
} from 'lucide-react';
import {
  api, authApi, publicApi, channelApi, walletApi, aiApi, adminApi,
  getToken, setToken, getAdminToken, setAdminToken, ApiError,
} from './api';

type Tab = 'home' | 'tools' | 'services' | 'account' | 'channels' | 'wallet' | 'admin';

type User = {
  id?: number;
  first_name?: string;
  username?: string;
  telegram_id?: string;
  balance_toman?: number;
};

type Service = { id: number; title: string; description?: string; icon?: string; url?: string };

export default function App() {
  const [tab, setTab] = useState<Tab>('home');
  const [user, setUser] = useState<User | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [config, setConfig] = useState<Record<string, string>>({});
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [logoTaps, setLogoTaps] = useState(0);

  // login
  const [loginBusy, setLoginBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // tools
  const [prompt, setPrompt] = useState('');
  const [generated, setGenerated] = useState('');

  // channels
  const [channels, setChannels] = useState<any[]>([]);
  const [chatRef, setChatRef] = useState('');
  const [channelId, setChannelId] = useState('');
  const [postText, setPostText] = useState('');

  // wallet
  const [plans, setPlans] = useState<any[]>([]);
  const [subscription, setSubscription] = useState<any>(null);
  const [redeemCode, setRedeemCode] = useState('');

  // admin
  const [adminPw, setAdminPw] = useState('');
  const [adminOk, setAdminOk] = useState(!!getAdminToken());
  const [adminSettings, setAdminSettings] = useState<Record<string, string>>({});

  const showToast = (msg: string) => {
    setToast(msg);
    setError('');
    setTimeout(() => setToast(''), 3500);
  };
  const showError = (e: unknown) => {
    const msg = e instanceof Error ? e.message : 'خطای نامشخص';
    setError(msg);
    setToast('');
  };

  const loadPublic = useCallback(async () => {
    try {
      const [cfg, svc] = await Promise.all([publicApi.config(), publicApi.services()]);
      setConfig(cfg || {});
      setServices(Array.isArray(svc) ? svc : []);
      setApiOnline(true);
    } catch {
      setApiOnline(false);
    }
  }, []);

  const loadMe = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      return;
    }
    try {
      const me = await authApi.me();
      setUser(me);
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 401) setUser(null);
      // network/5xx: keep token
    }
  }, []);

  useEffect(() => {
    loadPublic();
    loadMe();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadPublic, loadMe]);

  async function startTelegramLogin() {
    setLoginBusy(true);
    setError('');
    try {
      if (pollRef.current) clearInterval(pollRef.current);
      const d = await authApi.start();
      const botUrl = d.bot_url;
      // Prefer Telegram app deep link on mobile
      const openUrl = botUrl.replace('https://t.me/', 'tg://resolve?domain=').replace('?start=', '&start=');
      window.open(botUrl, '_blank');
      try {
        window.location.href = openUrl;
      } catch {
        /* ignore */
      }
      showToast('در تلگرام دکمه تأیید ورود را بزن. در حال بررسی…');

      let tries = 0;
      pollRef.current = setInterval(async () => {
        tries += 1;
        if (tries > 60) {
          if (pollRef.current) clearInterval(pollRef.current);
          setLoginBusy(false);
          showError(new Error('مهلت ورود تمام شد. دوباره تلاش کن.'));
          return;
        }
        try {
          const st = await authApi.status(d.session_id, d.poll_secret);
          if (st.status === 'approved' && st.access_token) {
            if (pollRef.current) clearInterval(pollRef.current);
            setToken(st.access_token);
            setUser(st.user || null);
            setLoginBusy(false);
            showToast('ورود موفق بود');
            setTab('home');
          } else if (st.status === 'expired' || st.status === 'denied') {
            if (pollRef.current) clearInterval(pollRef.current);
            setLoginBusy(false);
            showError(new Error(st.status === 'denied' ? 'ورود رد شد' : 'درخواست ورود منقضی شد'));
          }
        } catch {
          /* keep polling on transient errors */
        }
      }, 2500);
    } catch (e) {
      showError(e);
      setLoginBusy(false);
    }
  }

  async function logout() {
    try {
      await authApi.logout();
    } catch {
      /* ignore */
    }
    setToken(null);
    setUser(null);
    showToast('خارج شدی');
  }

  async function refreshChannels() {
    if (!getToken()) return;
    setBusy(true);
    try {
      setChannels(await channelApi.list());
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function addChannel() {
    if (!chatRef.trim()) return;
    setBusy(true);
    try {
      await channelApi.verify(chatRef.trim());
      setChatRef('');
      await refreshChannels();
      showToast('کانال متصل شد');
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function createPost() {
    if (!channelId || !postText.trim()) return;
    setBusy(true);
    try {
      await channelApi.createPost(Number(channelId), postText.trim());
      setPostText('');
      showToast('پست ذخیره شد');
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function loadWallet() {
    if (!getToken()) return;
    setBusy(true);
    try {
      const [p, s] = await Promise.all([publicApi.plans(), walletApi.subscriptions()]);
      setPlans(Array.isArray(p) ? p : []);
      setSubscription(s?.active || s?.items?.[0] || null);
      await loadMe();
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function buyPlan(code: string) {
    setBusy(true);
    try {
      await walletApi.purchase(code);
      showToast('اشتراک فعال شد');
      await loadWallet();
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function redeem() {
    if (!redeemCode.trim()) return;
    setBusy(true);
    try {
      await walletApi.redeem(redeemCode.trim());
      setRedeemCode('');
      showToast('کد با موفقیت اعمال شد');
      await loadMe();
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function runAi() {
    if (!prompt.trim()) return;
    setBusy(true);
    try {
      const r = await aiApi.generate(prompt.trim());
      setGenerated(r.text || r as any);
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function adminLogin() {
    setBusy(true);
    try {
      const r = await adminApi.login(adminPw);
      setAdminToken(r.access_token);
      setAdminOk(true);
      setAdminPw('');
      const s = await adminApi.settings();
      setAdminSettings(s || {});
      showToast('ورود مدیر موفق');
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function saveAdminSettings() {
    setBusy(true);
    try {
      await adminApi.saveSettings(adminSettings);
      showToast('تنظیمات ذخیره شد');
      await loadPublic();
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  function onLogoTap() {
    const n = logoTaps + 1;
    setLogoTaps(n);
    if (n >= 7) {
      setLogoTaps(0);
      setTab('admin');
      setMenuOpen(false);
    }
    setTimeout(() => setLogoTaps(0), 2000);
  }

  const homeTitle = config.home_title || 'ابزارهای رشد کسب‌وکار، یک‌جا';
  const homeSub = config.home_subtitle || 'از تولید محتوا تا مدیریت و رشد شبکه‌های اجتماعی.';
  const appName = config.app_name || 'VIRA';

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand-wrap">
            <button className="icon-button" onClick={() => setMenuOpen((v) => !v)} aria-label="منو">
              {menuOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
            {!user ? (
              <button className="login-mini" onClick={startTelegramLogin} disabled={loginBusy}>
                {loginBusy ? 'در حال ورود…' : 'ورود'}
              </button>
            ) : (
              <button className="avatar-button" title={user.first_name || ''}>
                {(user.first_name || 'U').slice(0, 1)}
              </button>
            )}
          </div>
          <button className="brand" onClick={onLogoTap} type="button">
            <span className="brand-mark">V</span>
            <span className="brand-word">{appName}<span className="brand-dot">.</span></span>
          </button>
        </div>
      </header>

      {menuOpen && (
        <div className="dropdown-menu" role="menu">
          <button onClick={() => { setTab('home'); setMenuOpen(false); }}><Home size={16} /> خانه</button>
          <button onClick={() => { setTab('tools'); setMenuOpen(false); }}><Wrench size={16} /> ابزارها</button>
          <button onClick={() => { setTab('services'); setMenuOpen(false); }}><ShoppingBag size={16} /> خدمات</button>
          <button onClick={() => { setTab('account'); setMenuOpen(false); }}><UserIcon size={16} /> حساب</button>
          {user && (
            <>
              <button onClick={() => { setTab('channels'); setMenuOpen(false); refreshChannels(); }}><Radio size={16} /> کانال‌ها</button>
              <button onClick={() => { setTab('wallet'); setMenuOpen(false); loadWallet(); }}><Wallet size={16} /> کیف پول</button>
              <button onClick={() => { logout(); setMenuOpen(false); }}><LogOut size={16} /> خروج</button>
            </>
          )}
        </div>
      )}

      {apiOnline === false && (
        <div className="announcement" style={{ margin: '12px 16px 0' }}>
          <span>ارتباط با سرور برقرار نشد. اتصال اینترنت را بررسی کن. متن‌های پیش‌فرض نمایش داده می‌شود.</span>
          <button className="retry-btn" onClick={loadPublic}><RefreshCw size={14} /> تلاش دوباره</button>
        </div>
      )}
      {error && <div className="inline-error" style={{ margin: '10px 16px 0' }}>{error}</div>}
      {toast && <div className="toast">{toast}</div>}

      <main className="main-content">
        {tab === 'home' && (
          <div className="home-page">
            <section className="hero-card">
              <div className="hero-glow" />
              <div className="hero-copy">
                <span className="eyebrow">DIGITAL GROWTH ASSISTANT</span>
                <h1>{homeTitle}</h1>
                <p>{homeSub}</p>
                <button className="hero-button" onClick={() => setTab(user ? 'tools' : 'account')}>
                  شروع کار <ChevronLeft size={16} />
                </button>
              </div>
              <div className="hero-emblem">
                <img src="./images/vira-hero.svg" alt="" />
              </div>
              <div className="hero-bottom">
                <span>VIRA / 03</span>
                <span>CREATE · GROW · MANAGE</span>
              </div>
            </section>

            <div className="section-heading">
              <div>
                <span className="eyebrow">WORKSPACE</span>
                <h2>چه کاری انجام می‌دهی؟</h2>
              </div>
            </div>

            <div className="feature-grid">
              <button className="feature-card blue" onClick={() => setTab(user ? 'channels' : 'account')}>
                <div className="feature-icon"><Radio size={20} /></div>
                <span className="feature-tag">تلگرام</span>
                <strong>مدیریت شبکه‌ها</strong>
                <small>اتصال کانال و زمان‌بندی انتشار</small>
              </button>
              <button className="feature-card violet" onClick={() => setTab('tools')}>
                <div className="feature-icon"><Sparkles size={20} /></div>
                <span className="feature-tag">هوشمند</span>
                <strong>استودیو AI</strong>
                <small>پست، کپشن، تبلیغ و تقویم محتوا</small>
              </button>
              <button className="feature-card green" onClick={() => setTab('services')}>
                <div className="feature-icon"><ShoppingBag size={20} /></div>
                <span className="feature-tag">خدمات</span>
                <strong>سفارش خدمات</strong>
                <small>ممبر، ویو و رشد کانال</small>
              </button>
              <button className="feature-card orange" onClick={() => setTab('account')}>
                <div className="feature-icon"><Shield size={20} /></div>
                <span className="feature-tag">امنیت</span>
                <strong>حساب و امنیت</strong>
                <small>ورود تلگرام و مدیریت نشست</small>
              </button>
            </div>

            {services.length > 0 && (
              <>
                <div className="section-heading service-head">
                  <h2>خدمات</h2>
                  <button className="subtle-link" onClick={() => setTab('services')}>همه</button>
                </div>
                <div className="service-mini-list">
                  {services.slice(0, 4).map((s) => (
                    <button
                      key={s.id}
                      className="service-mini"
                      onClick={() => s.url && window.open(s.url, '_blank')}
                    >
                      <div className="service-mini-icon">{s.icon || '⚡'}</div>
                      <div className="service-mini-copy">
                        <b>{s.title}</b>
                        <small>{s.description || ''}</small>
                      </div>
                      <ExternalLink size={14} />
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {tab === 'tools' && (
          <div className="page-stack">
            <div className="page-heading">
              <button className="back-button" onClick={() => setTab('home')}><ChevronLeft size={18} /></button>
              <h2>ابزارها</h2>
            </div>
            <section className="wide-feature ai-feature">
              <div className="wide-icon"><Sparkles size={22} /></div>
              <div>
                <h3>تولید متن با AI</h3>
                <p>متن از سرور VIRA تولید می‌شود. کلید API فقط روی سرور است.</p>
              </div>
            </section>
            {!user && (
              <div className="status-panel">
                <div className="status-dot" />
                <div>
                  <b>ورود لازم است</b>
                  <p>برای استفاده از AI وارد حساب تلگرام شو.</p>
                </div>
              </div>
            )}
            <textarea
              className="text-input"
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="مثلاً: یک کپشن تبلیغاتی برای کانال فروش بنویس"
              disabled={!user}
            />
            <button className="primary-btn" disabled={!user || busy || !prompt.trim()} onClick={runAi}>
              <Send size={16} /> تولید
            </button>
            {generated && (
              <div className="tool-card">
                <b>نتیجه</b>
                <p style={{ whiteSpace: 'pre-wrap', marginTop: 8 }}>{generated}</p>
              </div>
            )}
          </div>
        )}

        {tab === 'services' && (
          <div className="page-stack">
            <div className="page-heading">
              <button className="back-button" onClick={() => setTab('home')}><ChevronLeft size={18} /></button>
              <h2>خدمات</h2>
            </div>
            {services.length === 0 ? (
              <div className="state-block state-empty">
                <p>هنوز خدمتی از پنل مدیر ثبت نشده است.</p>
                <button className="retry-btn" onClick={loadPublic}><RefreshCw size={14} /> بروزرسانی</button>
              </div>
            ) : (
              services.map((s) => (
                <button
                  key={s.id}
                  className="wide-feature"
                  onClick={() => {
                    const url = s.url || config.services_bot_url;
                    if (url) window.open(url, '_blank');
                    else showError(new Error('لینک این خدمت هنوز تنظیم نشده'));
                  }}
                >
                  <div className="wide-icon">{s.icon || '⚡'}</div>
                  <div>
                    <h3>{s.title}</h3>
                    <p>{s.description || 'سفارش از طریق ربات خدمات'}</p>
                  </div>
                </button>
              ))
            )}
          </div>
        )}

        {tab === 'account' && (
          <div className="page-stack">
            <div className="page-heading">
              <button className="back-button" onClick={() => setTab('home')}><ChevronLeft size={18} /></button>
              <h2>حساب کاربری</h2>
            </div>
            {!user ? (
              <div className="center-panel">
                <div className="auth-lock"><Bot size={28} /></div>
                <h2>ورود با تلگرام</h2>
                <p>
                  بدون رمز و بدون Session. فقط با ربات اختصاصی VIRA وارد شو.
                  هویت از طریق deep-link امن تأیید می‌شود.
                </p>
                <button className="telegram-btn" onClick={startTelegramLogin} disabled={loginBusy}>
                  <Bot size={18} />
                  {loginBusy ? 'در انتظار تأیید تلگرام…' : 'ورود با تلگرام'}
                </button>
                <div className="safe-note"><Shield size={14} /> توکن و رمز تلگرام هرگز از تو خواسته نمی‌شود</div>
              </div>
            ) : (
              <div className="tool-card">
                <b>{user.first_name || 'کاربر'}</b>
                <small style={{ display: 'block', marginTop: 6 }}>
                  @{user.username || '—'} · شناسه {user.telegram_id || user.id}
                </small>
                <p style={{ marginTop: 12 }}>
                  موجودی: {Number(user.balance_toman || 0).toLocaleString('fa-IR')} تومان
                </p>
                <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                  <button className="primary-btn" onClick={() => { setTab('wallet'); loadWallet(); }}>
                    <Wallet size={16} /> کیف پول
                  </button>
                  <button className="ghost" onClick={logout}><LogOut size={16} /> خروج</button>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === 'channels' && user && (
          <div className="page-stack">
            <div className="page-heading">
              <button className="back-button" onClick={() => setTab('home')}><ChevronLeft size={18} /></button>
              <h2>کانال‌ها</h2>
            </div>
            <div className="tool-card">
              <h3>اتصال کانال</h3>
              <p className="muted">ربات باید ادمین کانال باشد و خودت هم ادمین/مالک باشی.</p>
              <input
                className="text-input"
                value={chatRef}
                onChange={(e) => setChatRef(e.target.value)}
                placeholder="@channel یا -100..."
              />
              <button className="primary-btn" disabled={busy} onClick={addChannel}>
                <Plus size={16} /> اتصال
              </button>
            </div>
            <div className="tool-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h3>کانال‌های من</h3>
                <button className="ghost" onClick={refreshChannels}><RefreshCw size={14} /></button>
              </div>
              {channels.map((c) => (
                <div key={c.id} className="list-item" style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid rgba(255,255,255,.06)' }}>
                  <div>
                    <b>{c.title || c.username || c.id}</b>
                    <small style={{ display: 'block' }}>{c.chat_id}</small>
                  </div>
                  <button
                    className="icon-button"
                    onClick={async () => {
                      try {
                        await channelApi.remove(c.id);
                        refreshChannels();
                      } catch (e) {
                        showError(e);
                      }
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
              {!channels.length && <div className="empty-mini">کانالی متصل نیست</div>}
            </div>
            <div className="tool-card">
              <h3>ذخیره پیش‌نویس پست</h3>
              <select className="text-input" value={channelId} onChange={(e) => setChannelId(e.target.value)}>
                <option value="">انتخاب کانال</option>
                {channels.map((c) => (
                  <option key={c.id} value={c.id}>{c.title || c.username || c.id}</option>
                ))}
              </select>
              <textarea className="text-input" rows={3} value={postText} onChange={(e) => setPostText(e.target.value)} placeholder="متن پست..." />
              <button className="primary-btn" disabled={busy || !channelId || !postText.trim()} onClick={createPost}>
                ذخیره
              </button>
            </div>
          </div>
        )}

        {tab === 'wallet' && user && (
          <div className="page-stack">
            <div className="page-heading">
              <button className="back-button" onClick={() => setTab('account')}><ChevronLeft size={18} /></button>
              <h2>کیف پول و اشتراک</h2>
            </div>
            <div className="tool-card">
              <small>موجودی</small>
              <h2 style={{ margin: '6px 0' }}>{Number(user.balance_toman || 0).toLocaleString('fa-IR')} تومان</h2>
              <button className="ghost" onClick={loadWallet}><RefreshCw size={14} /> بروزرسانی</button>
            </div>
            <div className="tool-card">
              <h3>اشتراک فعال</h3>
              {subscription ? (
                <p>{subscription.title || subscription.plan_code} — تا {subscription.expires_at || 'نامحدود'}</p>
              ) : (
                <p className="muted">اشتراک فعالی نیست</p>
              )}
            </div>
            <div className="tool-card">
              <h3>خرید اشتراک از موجودی</h3>
              <div className="plans" style={{ display: 'grid', gap: 10 }}>
                {plans.map((p) => (
                  <div key={p.plan_code} className="plan-card" style={{ padding: 12, border: '1px solid rgba(255,255,255,.08)', borderRadius: 14 }}>
                    <b>{p.title}</b>
                    <small style={{ display: 'block' }}>{p.duration_days} روز</small>
                    <strong>{p.price_toman ? Number(p.price_toman).toLocaleString('fa-IR') + ' تومان' : 'قیمت تنظیم نشده'}</strong>
                    <button
                      className="primary-btn"
                      style={{ marginTop: 8 }}
                      disabled={busy || !p.enabled}
                      onClick={() => buyPlan(p.plan_code)}
                    >
                      خرید
                    </button>
                  </div>
                ))}
                {!plans.length && <div className="empty-mini">طرحی تعریف نشده — قیمت را از پنل مدیر تنظیم کن</div>}
              </div>
            </div>
            <div className="tool-card">
              <h3>اعمال کد پرداخت</h3>
              <input className="text-input" value={redeemCode} onChange={(e) => setRedeemCode(e.target.value)} placeholder="کد zpd_..." />
              <button className="primary-btn" disabled={busy || !redeemCode.trim()} onClick={redeem}>اعمال کد</button>
            </div>
          </div>
        )}

        {tab === 'admin' && (
          <div className="page-stack">
            <div className="page-heading">
              <button className="back-button" onClick={() => setTab('home')}><ChevronLeft size={18} /></button>
              <h2>پنل مدیریت</h2>
            </div>
            {!adminOk ? (
              <div className="center-panel">
                <div className="auth-lock"><Settings size={28} /></div>
                <h2>ورود مدیر</h2>
                <input
                  className="text-input"
                  type="password"
                  value={adminPw}
                  onChange={(e) => setAdminPw(e.target.value)}
                  placeholder="رمز مدیر"
                />
                <button className="primary-btn" disabled={busy || !adminPw} onClick={adminLogin}>ورود</button>
              </div>
            ) : (
              <div className="tool-card">
                <h3>تنظیمات عمومی</h3>
                {['app_name', 'home_title', 'home_subtitle', 'services_bot_url', 'auth_bot_username', 'support_url'].map((k) => (
                  <label key={k} style={{ display: 'block', marginBottom: 10 }}>
                    <small>{k}</small>
                    <input
                      className="text-input"
                      value={adminSettings[k] || ''}
                      onChange={(e) => setAdminSettings({ ...adminSettings, [k]: e.target.value })}
                    />
                  </label>
                ))}
                <button className="primary-btn" disabled={busy} onClick={saveAdminSettings}>ذخیره</button>
                <button
                  className="ghost"
                  style={{ marginTop: 8 }}
                  onClick={() => {
                    setAdminToken(null);
                    setAdminOk(false);
                  }}
                >
                  خروج مدیر
                </button>
              </div>
            )}
          </div>
        )}
      </main>

      <nav className="bottom-nav">
        <button className={`nav-item ${tab === 'home' ? 'active' : ''}`} onClick={() => setTab('home')}>
          <Home size={20} /><span>خانه</span>
        </button>
        <button className={`nav-item ${tab === 'tools' ? 'active' : ''}`} onClick={() => setTab('tools')}>
          <Wrench size={20} /><span>ابزارها</span>
        </button>
        <button className={`nav-item ${tab === 'services' ? 'active' : ''}`} onClick={() => setTab('services')}>
          <ShoppingBag size={20} /><span>خدمات</span>
        </button>
        <button className={`nav-item ${tab === 'account' || tab === 'wallet' ? 'active' : ''}`} onClick={() => setTab('account')}>
          <UserIcon size={20} /><span>حساب</span>
        </button>
      </nav>
    </div>
  );
}
