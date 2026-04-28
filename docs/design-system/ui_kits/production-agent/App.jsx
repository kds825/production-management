// App shell — wires sidebar nav to BatchAllocationView / GanttChartView
const { useState: useAppState } = React;
const { Sidebar, TopBar, Crumbs } = window.UI;

const VIEWS = {
  dash:  { crumb: ['Home', '대시보드'],     title: '대시보드',     render: () => <PlaceholderView label="대시보드" /> },
  batch: { crumb: ['Home', '생산계획', '배치 뷰'],     title: '배치 뷰',     render: () => <window.BatchAllocationView /> },
  gantt: { crumb: ['Home', '생산계획', '간트차트'],   title: '간트차트',   render: () => <window.GanttChartView /> },
  resource: { crumb: ['Home', '자원 관리'],   title: '자원 관리',   render: () => <PlaceholderView label="자원 관리" /> },
  sim:   { crumb: ['Home', '시뮬레이션'],   title: '시뮬레이션',   render: () => <PlaceholderView label="시뮬레이션" /> },
};

const PlaceholderView = ({ label }) => (
  <div style={{
    flex:1, minHeight:400, background:'#fff', border:'1px solid var(--border-1)', borderRadius:4,
    display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', gap: 8,
    color:'var(--fg-3)', fontSize:14
  }}>
    <div style={{fontSize:18, fontWeight:600, color:'var(--fg-2)'}}>{label}</div>
    <div>UI 키트 데모 — 배치 뷰 또는 간트차트로 이동해 보세요.</div>
  </div>
);

const App = () => {
  const [view, setView] = useAppState('gantt');
  const cfg = VIEWS[view];

  return (
    <div className="app">
      <Sidebar active={view} onNav={setView} />
      <main className="main">
        <TopBar />
        <Crumbs trail={cfg.crumb} />
        <div className="content">
          {cfg.render()}
        </div>
      </main>
    </div>
  );
};

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
