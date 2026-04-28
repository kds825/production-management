// ============================================================
// 화면 1: 생산 배치 뷰 (Batch Allocation View)
// 호기별 배치 매트릭스 + KPI + AI Agent 채팅 패널
// ============================================================
const { useState, useMemo } = React;
const { Icon, KpiCard, Pill } = window.UI;

// --- Mock 데이터: 호기별 시간대 배치 ----------------------------
const MACHINES = [
  { id: '1호기', spec: 'KFR-450 / Φ4–8mm', load: 0.62 },
  { id: '2호기', spec: 'KFR-600 / Φ8–14mm', load: 0.94 },
  { id: '3호기', spec: 'KFR-450 / Φ4–8mm', load: 0.41 },
  { id: '4호기', spec: 'KFR-800 / Φ14–22mm', load: 0.78 },
  { id: '5호기', spec: 'KFR-600 / Φ8–14mm', load: 0.55 },
  { id: '6호기', spec: 'KFR-300 / Φ2–4mm', load: 0.88 },
];

// 작업 상태: progress(진행중) · idle(대기) · done(완료) · delay(지연)
const SLOTS = ['오전 06–14', '오후 14–22', '야간 22–06'];

const JOBS = {
  '1호기': [
    { code: 'KBI-2418', meters: 600, status: 'done',     spec: 'CV 3.5sq' },
    { code: 'KBI-2419', meters: 320, status: 'progress', spec: 'CV 5.5sq' },
    { code: 'KBI-2420', meters: 480, status: 'idle',     spec: 'HFIX 2.5' },
  ],
  '2호기': [
    { code: 'KBI-2402', meters: 240, status: 'delay',    spec: 'F-CV 14sq' },
    { code: 'KBI-2421', meters: 700, status: 'progress', spec: 'F-CV 14sq' },
    { code: 'KBI-2422', meters: 540, status: 'idle',     spec: 'CV 16sq' },
  ],
  '3호기': [
    { code: 'KBI-2410', meters: 800, status: 'done',     spec: 'CV 6.0sq' },
    { code: null },
    { code: 'KBI-2423', meters: 420, status: 'idle',     spec: 'IV 4.0sq' },
  ],
  '4호기': [
    { code: 'KBI-2401', meters: 920, status: 'done',     spec: 'TFR-CV 22' },
    { code: 'KBI-2424', meters: 760, status: 'progress', spec: 'TFR-CV 22' },
    { code: 'KBI-2425', meters: 640, status: 'idle',     spec: 'F-CV 16' },
  ],
  '5호기': [
    { code: 'KBI-2426', meters: 380, status: 'idle',     spec: 'CV 10sq' },
    { code: 'KBI-2427', meters: 520, status: 'progress', spec: 'CV 10sq' },
    { code: null },
  ],
  '6호기': [
    { code: 'KBI-2403', meters: 280, status: 'delay',    spec: 'IV 1.5' },
    { code: 'KBI-2428', meters: 460, status: 'progress', spec: 'HFIX 1.5' },
    { code: 'KBI-2429', meters: 510, status: 'idle',     spec: 'IV 2.5' },
  ],
};

const STATUS_LABEL = { progress: '진행 중', idle: '대기', done: '완료', delay: '지연' };

// --- Job Block: 한 셀의 작업 표시 -------------------------------
const JobBlock = ({ job, onClick }) => {
  if (!job?.code) {
    return <div className="job job--empty" onClick={onClick}>비어 있음</div>;
  }
  return (
    <div className={`job job--${job.status}`} onClick={onClick}>
      <div className="job__head">
        <span className="job__code">{job.code}</span>
        <span className="job__status">{STATUS_LABEL[job.status]}</span>
      </div>
      <div className="job__spec">{job.spec}</div>
      <div className="job__meters">{job.meters.toLocaleString()} m</div>
    </div>
  );
};

// --- 부하율 바 -----------------------------------------------------
const LoadBar = ({ value }) => {
  const pct = Math.round(value * 100);
  const over = value > 0.8;
  return (
    <div className="loadbar">
      <div className="loadbar__track">
        <div className="loadbar__fill" style={{ width: `${pct}%`, background: over ? 'var(--kbi-sunrise-red)' : 'var(--kbi-warm-gray)' }} />
      </div>
      <span className="loadbar__num" style={{ color: over ? 'var(--kbi-sunrise-red)' : 'var(--fg-1)' }}>{pct}%</span>
    </div>
  );
};

// --- AI Agent 채팅 패널 -------------------------------------------
const AGENT_SEED = [
  { who: 'agent', text: '안녕하세요. 오늘 배치 검토 도와드릴까요? 2호기 부하율이 94%로 임계치를 넘었습니다.', meta: '08:42' },
];

const AgentPanel = ({ open, onClose, suggested }) => {
  const [messages, setMessages] = useState(AGENT_SEED);
  const [input, setInput] = useState('');

  const send = (text) => {
    if (!text.trim()) return;
    const next = [...messages, { who: 'user', text, meta: '방금' }];
    // Mock agent response
    setMessages(next);
    setInput('');
    setTimeout(() => {
      setMessages(m => [...m, {
        who: 'agent',
        text: '3호기 오후 슬롯이 가용합니다. 이동 시 납기 충족되며 부하율은 +12% 증가합니다. 적용할까요?',
        actions: [{ label: '적용', primary: true }, { label: '대안 보기' }],
        meta: '방금',
      }]);
    }, 700);
  };

  if (!open) return null;
  return (
    <aside className="agent">
      <div className="agent__head">
        <div className="agent__title">
          <span className="agent__avatar"><Icon name="sparkle" size={14} color="#fff"/></span>
          <div>
            <div className="agent__name">생산계획 Agent</div>
            <div className="agent__sub">실시간 · 제약조건 모니터링</div>
          </div>
        </div>
        <button className="icon-btn" onClick={onClose}><Icon name="close" size={16}/></button>
      </div>

      <div className="agent__body">
        {messages.map((m, i) => (
          <div key={i} className={`bubble bubble--${m.who}`}>
            {m.who === 'agent' && <span className="bubble__avatar"><Icon name="sparkle" size={11} color="#fff"/></span>}
            <div className="bubble__col">
              <div className="bubble__text">{m.text}</div>
              {m.actions && (
                <div className="bubble__actions">
                  {m.actions.map((a, j) => (
                    <button key={j} className={`btn btn--sm ${a.primary ? 'btn--primary' : 'btn--secondary'}`}>{a.label}</button>
                  ))}
                </div>
              )}
              <div className="bubble__meta">{m.meta}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="agent__suggest">
        {(suggested || ['1호기 오후를 3호기로 이동', '6호기 지연 작업 재할당', '내일 가동률 시뮬레이션']).map(s => (
          <button key={s} className="suggest-chip" onClick={() => send(s)}>{s}</button>
        ))}
      </div>

      <div className="agent__input">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') send(input); }}
          placeholder="자연어로 명령하세요  예: 1호기 오후 작업을 3호기로 옮겨줘"
        />
        <button className="agent__send" onClick={() => send(input)} aria-label="전송"><Icon name="send" size={16} color="#fff"/></button>
      </div>
    </aside>
  );
};

// --- Main view ----------------------------------------------------
const BatchAllocationView = () => {
  const [agentOpen, setAgentOpen] = useState(true);
  const [selected, setSelected] = useState(null);

  return (
    <div className={`bav ${agentOpen ? 'bav--with-agent' : ''}`}>
      <div className="bav__main">
        {/* KPI row */}
        <div className="kpi-row">
          <KpiCard label="가동률" value="87.4" unit="%" delta="2.1% vs 어제" deltaDir="up" tone="red" />
          <KpiCard label="납기율" value="96.2" unit="%" delta="0.8% vs 어제" deltaDir="down" tone="orange" />
          <KpiCard label="적체량" value="1,284" unit="m" delta="변동 없음" deltaDir="flat" tone="gold" />
          <KpiCard label="가용 호기" value="4" unit="/ 6" delta="2호기·6호기 임계" deltaDir="down" tone="warm" />
        </div>

        {/* 호기별 배치 매트릭스 */}
        <section className="card">
          <div className="card__head">
            <div>
              <div className="card__title">호기별 배치 매트릭스</div>
              <div className="card__sub">오늘 · 2026-04-26 (월) · 3교대 · 6 호기</div>
            </div>
            <div className="card__actions">
              <button className="btn btn--ghost btn--sm"><Icon name="filter" size={13}/> 필터</button>
              <button className="btn btn--secondary btn--sm">현재 배치 저장</button>
              <button className="btn btn--primary btn--sm"><Icon name="play" size={13} color="#fff"/> 최적화 실행</button>
            </div>
          </div>

          <div className="matrix">
            <table>
              <thead>
                <tr>
                  <th style={{width:'200px'}}>호기 / 사양</th>
                  {SLOTS.map(s => <th key={s}>{s}</th>)}
                  <th style={{width:'180px'}}>부하율</th>
                </tr>
              </thead>
              <tbody>
                {MACHINES.map(m => (
                  <tr key={m.id} className={selected === m.id ? 'is-selected' : ''} onClick={() => setSelected(m.id)}>
                    <td>
                      <div className="m-cell">
                        <div className="m-cell__id">{m.id}</div>
                        <div className="m-cell__spec">{m.spec}</div>
                      </div>
                    </td>
                    {JOBS[m.id].map((job, i) => (
                      <td key={i}><JobBlock job={job} /></td>
                    ))}
                    <td><LoadBar value={m.load} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="legend">
            <span className="legend__title">상태</span>
            <Pill kind="progress">진행 중</Pill>
            <Pill kind="idle">대기</Pill>
            <Pill kind="done">완료</Pill>
            <Pill kind="delay">지연</Pill>
            <span className="legend__sep"></span>
            <span className="legend__note">부하율 80% 초과 시 KBI Red로 강조</span>
          </div>
        </section>
      </div>

      {/* Agent toggle (collapsed) */}
      {!agentOpen && (
        <button className="agent-fab" onClick={() => setAgentOpen(true)}>
          <Icon name="sparkle" size={16} color="#fff"/>
          <span>Agent</span>
        </button>
      )}

      <AgentPanel open={agentOpen} onClose={() => setAgentOpen(false)} />
    </div>
  );
};

window.BatchAllocationView = BatchAllocationView;
