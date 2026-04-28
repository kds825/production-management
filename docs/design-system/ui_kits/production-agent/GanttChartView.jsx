// ============================================================
// 화면 2: 간트차트 뷰 (Gantt Chart View) — 2026-04-24 ~ 2026-05-09
// 사용자 실제 화면 구조를 유지하면서 브랜드 톤(KBI Sunrise Red /
// Warm Gray / Champagne Gold / PwC neutrals)으로 정리한 버전.
// ============================================================
const { useState: useStateG, useMemo: useMemoG } = React;
const { Icon: IconG, Pill: PillG } = window.UI;

// 호기 — 카테고리 별 그룹
const G_MACHINES = [
  { id: '54BO 1호',     cat: '연선' },
  { id: '54BO 2호',     cat: '연선' },
  { id: '54BO 3호',     cat: '연선' },
  { id: 'AL6BO',        cat: '연선' },
  { id: 'T6BO',         cat: '연선' },
  { id: 'B100EXT',      cat: '절연·차폐' },
  { id: 'CV 1호',       cat: '고압연선' },
  { id: 'CV 2호',       cat: '고압연선' },
  { id: '小4BO(12BO)',   cat: '연합' },
  { id: 'Laying Up',    cat: '연합' },
  { id: 'T/P 1호',      cat: 'T/P' },
  { id: 'T/P 2호',      cat: 'T/P' },
];

// 날짜 범위: 4/24(금) ~ 5/9(토)
const RANGE_START = new Date(2026, 3, 24); // month is 0-indexed
const RANGE_DAYS = 16;
const TODAY = new Date(2026, 3, 27);
const DAY_W = 84;

const dayKey = (offset) => {
  const d = new Date(RANGE_START); d.setDate(d.getDate() + offset);
  return d;
};
const dayLabel = (d) => `${d.getMonth()+1}/${d.getDate()}`;
const weekdayKo = (d) => '일월화수목금토'[d.getDay()];
const isWeekend = (d) => d.getDay() === 0 || d.getDay() === 6;

// 작업: start/end는 "RANGE_START 부터의 일(day) offset, 0–RANGE_DAYS"
// kind: planned | progress | done | delay   (delay는 보더 강조 + 지연일수 칩)
const G_TASKS = [
  // 54BO 1호 (3, 0)
  { id:'T-A1', mIdx:0, start:3.0, end:4.0, kind:'progress', label:'240SQ', meters:'13,600m',           progress:0.55, delayDays:19 },
  { id:'T-A2', mIdx:0, start:4.1, end:5.9, kind:'planned',  label:'120SQ', meters:'21,300m' },
  { id:'T-A3', mIdx:0, start:6.0, end:7.0, kind:'planned',  label:'400SQ', meters:'1통 · 8.1km',       delayDays:8 },
  { id:'T-A4', mIdx:0, start:10.0, end:12.5, kind:'planned', label:'95SQ',  meters:'5통 · 48,500m' },
  { id:'T-A5', mIdx:0, start:12.6, end:13.4, kind:'planned', label:'185SQ', meters:'1통 · 9,600m',     delayDays:23 },
  { id:'T-A6', mIdx:0, start:13.5, end:14.4, kind:'planned', label:'300SQ', meters:'1통',              delayDays:9 },
  { id:'T-A7', mIdx:0, start:14.5, end:15.5, kind:'planned', label:'300SQ', meters:'1통',              delayDays:6 },

  // 54BO 2호
  { id:'T-B1', mIdx:1, start:3.2, end:7.0, kind:'progress', label:'1250KCMIL', meters:'138,860m',     progress:0.40 },
  { id:'T-B2', mIdx:1, start:9.5, end:14.0, kind:'planned', label:'1250KCMIL', meters:'138,860m' },

  // 54BO 3호
  { id:'T-C1', mIdx:2, start:3.2, end:7.0, kind:'progress', label:'1250KCMIL', meters:'138,860m',     progress:0.50 },
  { id:'T-C2', mIdx:2, start:9.5, end:14.0, kind:'planned', label:'1250KCMIL', meters:'138,860m' },

  // AL6BO
  { id:'T-D1', mIdx:3, start:3.0, end:5.5, kind:'planned',  label:'1250KCMIL', meters:'138,710m' },

  // T6BO (다수)
  { id:'T-E1', mIdx:4, start:3.0, end:3.6, kind:'progress', label:'400SQ',  meters:'4통 지연',         progress:0.6 },
  { id:'T-E2', mIdx:4, start:3.6, end:4.0, kind:'progress', label:'300SQ',  meters:'18,9 — 3통 지연',  progress:0.4 },
  { id:'T-E3', mIdx:4, start:4.1, end:4.5, kind:'planned',  label:'5C',     meters:'1통' },
  { id:'T-E4', mIdx:4, start:4.6, end:5.4, kind:'progress', label:'4C × 35SQ', meters:'1통 — 58,200m', progress:0.3 },
  { id:'T-E5', mIdx:4, start:6.6, end:7.5, kind:'planned',  label:'4C × 25SQ', meters:'1통 — 40,500m', delayDays:11 },

  // B100EXT (작은 블록 多)
  { id:'T-F1', mIdx:5, start:3.0, end:3.5, kind:'planned',  label:'4C × …',  meters:'6,9 — 1통' },
  { id:'T-F2', mIdx:5, start:3.5, end:3.7, kind:'done',     label:'5C',      meters:'완료' },
  { id:'T-F3', mIdx:5, start:3.7, end:3.9, kind:'planned',  label:'5C',      meters:'1통' },
  { id:'T-F4', mIdx:5, start:4.5, end:5.0, kind:'progress', label:'120SQ',   meters:'1통',             progress:0.5 },
  { id:'T-F5', mIdx:5, start:5.0, end:5.3, kind:'planned',  label:'4',       meters:'1통' },
  { id:'T-F6', mIdx:5, start:6.5, end:6.8, kind:'planned',  label:'4',       meters:'1통' },
  { id:'T-F7', mIdx:5, start:10.5,end:11.2, kind:'planned', label:'4″',      meters:'98,636 — 1통' },
  { id:'T-F8', mIdx:5, start:11.3,end:12.0, kind:'progress',label:'95SQ',    meters:'1통',             progress:0.4, delayDays:23 },
  { id:'T-F9', mIdx:5, start:12.1,end:12.6, kind:'planned', label:'1…',      meters:'1통' },
  { id:'T-FA', mIdx:5, start:12.7,end:13.5, kind:'planned', label:'300SQ',   meters:'18,1 — 1통',      delayDays:9 },

  // CV 1호 / 2호
  { id:'T-G1', mIdx:6, start:3.5, end:7.5,  kind:'progress', label:'1250KCMIL', meters:'138,710m',    progress:0.6 },
  { id:'T-G2', mIdx:6, start:10.0,end:14.0, kind:'planned',  label:'1250KCMIL', meters:'138,710m' },
  { id:'T-H1', mIdx:7, start:3.5, end:7.5,  kind:'progress', label:'1250KCMIL', meters:'138,710m',    progress:0.5 },
  { id:'T-H2', mIdx:7, start:10.0,end:14.0, kind:'planned',  label:'1250KCMIL', meters:'138,710m' },

  // 小4BO
  { id:'T-I1', mIdx:8, start:4.0, end:4.7,  kind:'planned',  label:'4C × 1…', meters:'6.33 — 1통' },
  { id:'T-I2', mIdx:8, start:6.0, end:6.6,  kind:'planned',  label:'',        meters:'1통' },
  { id:'T-I3', mIdx:8, start:11.0,end:11.7, kind:'planned',  label:'4C × 25…',meters:'9,541m' },

  // Laying Up
  { id:'T-J1', mIdx:9, start:4.4, end:4.7,  kind:'progress', label:'',        meters:'1통',           progress:0.6 },
  { id:'T-J2', mIdx:9, start:11.5,end:12.2, kind:'progress', label:'4C × 35SQ', meters:'12,4m',       progress:0.4, delayDays:11 },

  // T/P 1호
  { id:'T-K1', mIdx:10, start:5.6, end:6.0, kind:'planned', label:'',        meters:'1통' },
  { id:'T-K2', mIdx:10, start:13.0,end:13.4,kind:'planned', label:'9…',      meters:'1통' },
];

const TASK_KIND_LABEL = { progress: '진행 중', planned: '예정', done: '완료', delay: '지연' };

// ── Bar ─────────────────────────────────────────────────────
const GanttBar = ({ task, selected, onClick }) => {
  const left  = task.start * DAY_W;
  const width = (task.end - task.start) * DAY_W;
  const isDelay = !!task.delayDays;
  return (
    <div
      className={`gbar gbar--${task.kind} ${isDelay ? 'gbar--has-delay' : ''} ${selected ? 'is-selected' : ''}`}
      style={{ left, width }}
      onClick={onClick}
      title={`${task.label} · ${task.meters}`}
    >
      {task.kind === 'progress' && (
        <div className="gbar__fill" style={{ width: `${(task.progress || 0) * 100}%` }} />
      )}
      <div className="gbar__body">
        <span className="gbar__label">{task.label}</span>
        {task.meters && width > 80 && <span className="gbar__meta">{task.meters}</span>}
        {isDelay && width > 70 && (
          <span className="gbar__delay">+{task.delayDays}일</span>
        )}
      </div>
    </div>
  );
};

// ── Detail panel ─────────────────────────────────────────────
const GanttDetail = ({ task }) => {
  if (!task) {
    return (
      <div className="detail">
        <div className="detail__left" style={{display:'flex', alignItems:'center', justifyContent:'center', color:'var(--fg-3)', fontSize:13}}>
          작업 바를 선택하면 상세 정보가 표시됩니다
        </div>
        <div className="detail__right"></div>
      </div>
    );
  }
  const sd = dayKey(task.start), ed = dayKey(task.end);
  const fmt = (d) => `${d.getMonth()+1}월 ${d.getDate()}일 ${weekdayKo(d)}`;
  const constraints = task.delayDays ? [
    { ok: true,  text: '원자재 재고 충족',                 sub: '동선 / 절연 PVC 정상' },
    { ok: false, text: `납기 위반 (+${task.delayDays}일)`, sub: '계약 납기 초과' },
    { ok: false, text: '선행 작업 미완료',                 sub: '연선 단계 적체' },
    { ok: true,  text: '작업자 자격 충족',                 sub: '주·야간 A 등급' },
  ] : [
    { ok: true, text: '원자재 재고 충족',  sub: '정상' },
    { ok: true, text: '납기 충족',         sub: `종료 예정: ${fmt(ed)}` },
    { ok: true, text: '선행 작업 완료',     sub: '의존성 0건' },
    { ok: true, text: '작업자 자격 충족',  sub: '정상' },
  ];
  const kindKey = task.delayDays ? 'delay' : task.kind;
  return (
    <div className="detail">
      <div className="detail__left">
        <div className="detail__title">
          <span className="code">{task.label || '미라벨'}</span>
          <PillG kind={kindKey === 'planned' ? 'idle' : kindKey}>
            {task.delayDays ? `+${task.delayDays}일 지연` : TASK_KIND_LABEL[task.kind]}
          </PillG>
        </div>
        <div className="detail__sub">{G_MACHINES[task.mIdx].id} · {G_MACHINES[task.mIdx].cat} · {task.meters}</div>
        <div className="detail__grid">
          <div className="detail__field"><span className="lbl">시작</span><span className="val">{fmt(sd)}</span></div>
          <div className="detail__field"><span className="lbl">종료</span><span className="val" style={{color: task.delayDays ? 'var(--kbi-sunrise-red)' : 'inherit'}}>{fmt(ed)}</span></div>
          <div className="detail__field"><span className="lbl">진척률</span><span className="val">{task.progress != null ? Math.round(task.progress*100)+'%' : (task.kind==='done' ? '100%' : '—')}</span></div>
          <div className="detail__field"><span className="lbl">소요</span><span className="val">{(task.end - task.start).toFixed(1)}일</span></div>
        </div>
        <div className="detail__actions">
          {task.delayDays
            ? <button className="btn btn--primary"><IconG name="sparkle" size={13} color="#fff"/> 재최적화 요청</button>
            : <button className="btn btn--secondary">일정 조정</button>}
          <button className="btn btn--ghost">이력 보기</button>
        </div>
      </div>
      <div className="detail__right">
        <div style={{fontSize:13, fontWeight:600, color:'var(--kbi-warm-gray)', marginBottom: 6}}>제약 조건 체크</div>
        <div style={{fontSize:11, color:'var(--fg-3)', marginBottom: 10}}>최적화 엔진이 실시간 검증</div>
        {constraints.map((c, i) => (
          <div key={i} className={`constraint ${c.ok ? '' : 'constraint--bad'}`}>
            <div className={`constraint__icon constraint__icon--${c.ok ? 'ok' : 'bad'}`}>
              <IconG name={c.ok ? 'check' : 'warn'} size={11} stroke={2.4} />
            </div>
            <div>
              <div className="constraint__text">{c.text}</div>
              <div className="constraint__sub">{c.sub}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

// ── Main view ───────────────────────────────────────────────
const GanttChartView = () => {
  const [zoom, setZoom] = useStateG('day');
  const [selectedId, setSelectedId] = useStateG('T-A1');
  const selected = G_TASKS.find(t => t.id === selectedId);

  const days = Array.from({length: RANGE_DAYS + 1}, (_, i) => dayKey(i));
  const totalWidth = (RANGE_DAYS + 1) * DAY_W;

  const todayOffset = (TODAY - RANGE_START) / 86400000;

  // Month boundary: detect day index where month flips
  const monthBoundaries = days.map((d, i) => i > 0 && d.getDate() === 1 ? i : null).filter(x => x != null);

  return (
    <div className="gantt-page">
      {/* Toolbar */}
      <div className="gantt-toolbar">
        <button className="chip"><span>4/24 — 5/9 · 16일</span><span className="chip__caret">▾</span></button>
        <button className="chip"><span>전체 호기 (12)</span><span className="chip__caret">▾</span></button>
        <button className="chip"><span>모든 작업유형</span><span className="chip__caret">▾</span></button>
        <button className="chip"><span>모든 상태</span><span className="chip__caret">▾</span></button>
        <div className="spacer"></div>
        <div className="zoom-group">
          <button className={zoom==='day'?'is-active':''} onClick={()=>setZoom('day')}>일</button>
          <button className={zoom==='week'?'is-active':''} onClick={()=>setZoom('week')}>주</button>
          <button className={zoom==='month'?'is-active':''} onClick={()=>setZoom('month')}>월</button>
        </div>
        <button className="btn btn--secondary btn--sm"><IconG name="download" size={13}/> 내보내기</button>
        <button className="btn btn--primary btn--sm"><IconG name="play" size={13} color="#fff"/> 최적화 실행</button>
      </div>

      {/* Status note (AI 톤 제거: 단순 헤어라인 메타 텍스트) */}
      <div className="gantt-note">
        <span className="dot dot--red"></span>
        <span>스케줄 완료</span>
        <span className="sep">·</span>
        <span>업데이트 2026-04-26 17:25:39</span>
        <span className="sep">·</span>
        <span>지연 작업 <b>9</b>건 / 진행 <b>14</b>건 / 예정 <b>21</b>건</span>
      </div>

      {/* Gantt card */}
      <div className="gantt-card">
        <div className="gantt-grid">
          {/* Machines column */}
          <div className="gantt-col-machines">
            <div className="gantt-machine-head">호기</div>
            {G_MACHINES.map(m => (
              <div key={m.id} className="gantt-machine-row">
                <div>
                  <div className="id">{m.id}</div>
                  <div className="spec">{m.cat}</div>
                </div>
              </div>
            ))}
          </div>

          {/* Time area */}
          <div className="gantt-time">
            <div className="gantt-time-head">
              <div className="gantt-time-head__inner" style={{width: totalWidth}}>
                {days.map((d, i) => (
                  <div key={i} className={`tick ${isWeekend(d) ? 'is-weekend' : ''} ${d.toDateString() === TODAY.toDateString() ? 'is-today' : ''}`}
                       style={{minWidth: DAY_W, flex: `0 0 ${DAY_W}px`}}>
                    <div className="tick__date">{dayLabel(d)}</div>
                    <div className="tick__wd">{weekdayKo(d)}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="gantt-rows" style={{width: totalWidth, position:'relative'}}>
              {/* Weekend bands */}
              {days.map((d, i) => isWeekend(d) && (
                <div key={`wk-${i}`} className="weekend-band" style={{left: i * DAY_W, width: DAY_W}} />
              ))}
              {/* Month boundary lines */}
              {monthBoundaries.map(i => (
                <div key={`mb-${i}`} className="month-boundary" style={{left: i * DAY_W}} />
              ))}
              {/* Day gridlines */}
              {days.map((d, i) => (
                <div key={`gl-${i}`} className="dayline" style={{left: i * DAY_W}} />
              ))}

              {G_MACHINES.map((m, mi) => (
                <div key={m.id} className="gantt-row">
                  {G_TASKS.filter(t => t.mIdx === mi).map(t => (
                    <GanttBar key={t.id} task={t} selected={selectedId === t.id} onClick={() => setSelectedId(t.id)} />
                  ))}
                </div>
              ))}

              {/* Today line */}
              <div className="gantt-now" style={{left: todayOffset * DAY_W}}>
                <span className="gantt-now__lbl">오늘 · 4/27</span>
              </div>
            </div>
          </div>
        </div>

        <GanttDetail task={selected} />
      </div>

      {/* Legend */}
      <div className="gantt-legend">
        <span className="lbl">상태</span>
        <span className="leg leg--progress"><i></i>진행 중</span>
        <span className="leg leg--planned"><i></i>예정</span>
        <span className="leg leg--done"><i></i>완료</span>
        <span className="leg leg--delay"><i></i>지연</span>
        <span className="sep"></span>
        <span className="lbl">바 안의 <b>+N일</b> 칩은 납기 대비 지연일수</span>
      </div>
    </div>
  );
};

window.GanttChartView = GanttChartView;
