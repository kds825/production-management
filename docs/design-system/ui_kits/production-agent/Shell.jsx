// Icons — minimal inline SVG set, sized 16/20px, stroke-based to match PwC outline kit
const Icon = ({ name, size = 16, color = 'currentColor', stroke = 1.6 }) => {
  const s = size;
  const paths = {
    dashboard: <g><rect x="3" y="3" width="7" height="9" rx="1.2"/><rect x="14" y="3" width="7" height="5" rx="1.2"/><rect x="14" y="11" width="7" height="10" rx="1.2"/><rect x="3" y="15" width="7" height="6" rx="1.2"/></g>,
    grid: <g><rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/><rect x="3" y="14" width="7" height="7" rx="1.2"/><rect x="14" y="14" width="7" height="7" rx="1.2"/></g>,
    gantt: <g><line x1="3" y1="6" x2="14" y2="6"/><line x1="7" y1="12" x2="20" y2="12"/><line x1="5" y1="18" x2="17" y2="18"/></g>,
    layers: <g><path d="M12 3 3 8l9 5 9-5-9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17l9 5 9-5"/></g>,
    sim: <g><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2 2M16.4 16.4l2 2M5.6 18.4l2-2M16.4 7.6l2-2"/></g>,
    search: <g><circle cx="11" cy="11" r="6.5"/><line x1="20" y1="20" x2="16" y2="16"/></g>,
    bell: <g><path d="M6 8a6 6 0 0112 0v4l1.5 3h-15L6 12V8z"/><path d="M10 19a2 2 0 004 0"/></g>,
    chevronDown: <polyline points="6 9 12 15 18 9"/>,
    chevronRight: <polyline points="9 6 15 12 9 18"/>,
    plus: <g><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></g>,
    play: <polygon points="6 4 20 12 6 20 6 4"/>,
    download: <g><path d="M12 4v12"/><polyline points="7 11 12 16 17 11"/><line x1="4" y1="20" x2="20" y2="20"/></g>,
    filter: <g><polygon points="3 5 21 5 14 13 14 19 10 19 10 13 3 5"/></g>,
    send: <polygon points="3 12 21 4 14 21 11 13 3 12"/>,
    sparkle: <g><path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6L12 4z"/><path d="M19 16l.7 1.8L21.5 18.5l-1.8.7L19 21l-.7-1.8L16.5 18.5l1.8-.7L19 16z"/></g>,
    close: <g><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></g>,
    check: <polyline points="5 12 10 17 19 8"/>,
    warn: <g><path d="M12 4l10 17H2L12 4z"/><line x1="12" y1="11" x2="12" y2="15"/><circle cx="12" cy="18" r=".6" fill="currentColor"/></g>,
    flag: <g><path d="M5 4v17"/><path d="M5 4h12l-2 4 2 4H5"/></g>,
    user: <g><circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 5-6 8-6s7 2 8 6"/></g>,
    zoomIn: <g><circle cx="11" cy="11" r="6.5"/><line x1="20" y1="20" x2="16" y2="16"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></g>,
    zoomOut: <g><circle cx="11" cy="11" r="6.5"/><line x1="20" y1="20" x2="16" y2="16"/><line x1="8" y1="11" x2="14" y2="11"/></g>,
    settings: <g><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 00.3 1.7l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-1.7-.3 1.6 1.6 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.6 1.6 0 00-1-1.5 1.6 1.6 0 00-1.7.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.6 1.6 0 00.3-1.7 1.6 1.6 0 00-1.5-1H3a2 2 0 110-4h.1a1.6 1.6 0 001.5-1 1.6 1.6 0 00-.3-1.7l-.1-.1a2 2 0 112.8-2.8l.1.1a1.6 1.6 0 001.7.3h.1a1.6 1.6 0 001-1.5V3a2 2 0 114 0v.1a1.6 1.6 0 001 1.5 1.6 1.6 0 001.7-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.6 1.6 0 00-.3 1.7v.1a1.6 1.6 0 001.5 1H21a2 2 0 110 4h-.1a1.6 1.6 0 00-1.5 1z"/></g>,
  };
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" style={{flexShrink:0}}>
      {paths[name] || null}
    </svg>
  );
};

const Sidebar = ({ active, onNav }) => {
  const items = [
    { id: 'dash', label: '대시보드', icon: 'dashboard' },
    { id: 'batch', label: '배치 뷰', icon: 'grid' },
    { id: 'gantt', label: '간트차트', icon: 'gantt' },
    { id: 'resource', label: '자원 관리', icon: 'layers' },
    { id: 'sim', label: '시뮬레이션', icon: 'sim' },
  ];
  return (
    <aside className="sb">
      <div className="sb__brand">
        <img src="../../assets/kbi-symbol.png" alt="KBI" />
        <div>
          <div className="name">KBI 생산계획</div>
          <div className="sub">AI Agent</div>
        </div>
      </div>
      <div className="sb__section">Workspace</div>
      {items.map(it => (
        <div key={it.id}
             className={`sb__item ${active === it.id ? 'is-active' : ''}`}
             onClick={() => onNav(it.id)}>
          <Icon name={it.icon} size={16} stroke={1.6} />
          <span>{it.label}</span>
        </div>
      ))}
      <div className="sb__section" style={{marginTop: 8}}>Reports</div>
      <div className="sb__item"><Icon name="download" size={16}/><span>리포트 내보내기</span></div>
      <div className="sb__item"><Icon name="settings" size={16}/><span>설정</span></div>
      <div className="sb__foot">v0.4.2 · prod-202604</div>
    </aside>
  );
};

const TopBar = ({ crumbTitle }) => (
  <header className="topbar">
    <div className="topbar__pwc">
      <img src="../../assets/pwc-logo.svg" alt="PwC" />
    </div>
    <div className="topbar__sep"></div>
    <div className="topbar__title"><b>코스모링크</b> · 생산계획 Agent</div>
    <div className="topbar__spacer"></div>
    <div className="topbar__search">
      <Icon name="search" size={14} color="var(--fg-3)" />
      <input placeholder="작업코드 · 호기 · 품번 검색" />
    </div>
    <button className="icon-btn" aria-label="알림"><Icon name="bell" size={18}/><span className="dot"></span></button>
    <div className="avatar">JK</div>
  </header>
);

const Crumbs = ({ trail }) => (
  <div className="crumbs">
    {trail.map((t, i) => (
      <React.Fragment key={i}>
        {i > 0 && <span className="sep">/</span>}
        {i === trail.length - 1 ? <b>{t}</b> : <span>{t}</span>}
      </React.Fragment>
    ))}
  </div>
);

const KpiCard = ({ label, value, unit, delta, deltaDir, tone }) => (
  <div className="kpi" data-tone={tone}>
    <div className="kpi__label">{label}</div>
    <div className="kpi__num">{value}{unit && <small>{unit}</small>}</div>
    {delta && (
      <div className={`kpi__delta ${deltaDir}`}>
        {deltaDir === 'up' ? '▲' : deltaDir === 'down' ? '▼' : '─'} {delta}
      </div>
    )}
  </div>
);

const Pill = ({ kind, children }) => (
  <span className={`pill pill--${kind}`}><span className="dot"></span>{children}</span>
);

window.UI = { Icon, Sidebar, TopBar, Crumbs, KpiCard, Pill };
