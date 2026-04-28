"use client";

/**
 * /admin/kpi-dashboard — Phase 6 Step 7 KPI 보드 (CEO §3).
 *
 * Product KPI: card review time, card open rate
 * Eng triage KPI: fixed rate, line_anchor top 10, fix SLA P50
 */

import { useEffect, useState } from "react";

import { Header } from "@/shared/components/Header";
import { ApiError, apiFetch } from "@/shared/api/client";

interface FixedRate {
  days: number;
  total: number;
  fixed: number;
  fixed_rate: number;
  by_status: Record<string, number>;
}

interface SLA {
  p50_hours: number | null;
  n: number;
  target_hours?: number;
}

interface ReviewTime {
  avg_seconds: number | null;
  n: number;
}

interface OpenRate {
  avg_days_active: number;
  operators: number;
  window_days?: number;
}

interface KpiSummary {
  window_days: number;
  eng: {
    fixed_rate: FixedRate;
    line_anchor_top: { line_anchor: string; count: number }[];
    fix_sla_p50: SLA;
  };
  product: {
    card_review_time_avg: ReviewTime;
    card_open_rate: OpenRate;
  };
}

export default function KpiDashboardPage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<KpiSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // TODO(react19-migration): fetch-start sync setState. discriminated-union
    // FetchState 로 재구성하면 룰 통과. 기능적 회귀 없음, 별도 PR 추적.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    apiFetch<KpiSummary>(`/admin/kpi/decision-card?days=${days}`)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (e instanceof ApiError) {
          setError(`로드 실패 (${e.status}). run-id: ${e.runId ?? "—"}`);
        } else {
          setError("로드 실패");
        }
        setLoading(false);
      });
  }, [days]);

  return (
    <>
      <Header />
      <main className="max-w-5xl mx-auto px-4 py-6">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-pwc-title4 m-0">결정 카드 KPI 대시보드</h1>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="border border-pwc-gray-200 rounded px-2 py-1 text-pwc-body"
          >
            <option value={7}>최근 7일</option>
            <option value={30}>최근 30일</option>
            <option value={90}>최근 90일</option>
          </select>
        </div>

        {loading ? (
          <p className="text-pwc-body text-pwc-gray-500">불러오는 중…</p>
        ) : error ? (
          <p className="text-pwc-body text-pwc-status-danger-text">{error}</p>
        ) : data == null ? null : (
          <div className="space-y-6">
            <Section title="Product KPI (CEO/공장장 보고용)">
              <Grid>
                <Metric
                  label="📊 카드 평균 검토시간"
                  value={
                    data.product.card_review_time_avg.avg_seconds != null
                      ? `${(data.product.card_review_time_avg.avg_seconds / 60).toFixed(1)}분`
                      : "데이터 부족"
                  }
                  sub={`${data.product.card_review_time_avg.n}건`}
                />
                <Metric
                  label="📈 카드 진입률"
                  value={`${data.product.card_open_rate.avg_days_active.toFixed(1)}일/운영자`}
                  sub={`${data.product.card_open_rate.operators}명 활성`}
                />
              </Grid>
            </Section>

            <Section title="Eng triage KPI (개발자 weekly retro)">
              <Grid>
                <Metric
                  label="✅ Fix 비율"
                  value={`${(data.eng.fixed_rate.fixed_rate * 100).toFixed(1)}%`}
                  sub={`${data.eng.fixed_rate.fixed} / ${data.eng.fixed_rate.total}건`}
                />
                <Metric
                  label="⏱ Fix SLA P50"
                  value={
                    data.eng.fix_sla_p50.p50_hours != null
                      ? `${data.eng.fix_sla_p50.p50_hours}h`
                      : "데이터 부족"
                  }
                  sub={`목표 ≤ ${data.eng.fix_sla_p50.target_hours ?? 72}h, n=${data.eng.fix_sla_p50.n}`}
                  severity={
                    data.eng.fix_sla_p50.p50_hours != null &&
                    data.eng.fix_sla_p50.p50_hours >
                      (data.eng.fix_sla_p50.target_hours ?? 72)
                      ? "warn"
                      : "ok"
                  }
                />
              </Grid>
              <h3 className="text-pwc-subTitle2 mt-4 mb-2">
                line_anchor top 10
              </h3>
              {data.eng.line_anchor_top.length === 0 ? (
                <p className="text-pwc-body text-pwc-gray-500">데이터 없음</p>
              ) : (
                <ol className="list-decimal pl-6 space-y-1 text-pwc-body">
                  {data.eng.line_anchor_top.map((row) => (
                    <li key={row.line_anchor} className="font-mono">
                      {row.line_anchor}{" "}
                      <span className="text-pwc-gray-500">— {row.count}건</span>
                    </li>
                  ))}
                </ol>
              )}
            </Section>
          </div>
        )}
      </main>
    </>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border border-pwc-gray-200 rounded-md p-4">
      <h2 className="text-pwc-subTitle2 m-0 mb-3">{title}</h2>
      {children}
    </section>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-3 sm:grid-cols-2">{children}</div>;
}

function Metric({
  label,
  value,
  sub,
  severity,
}: {
  label: string;
  value: string;
  sub: string;
  severity?: "ok" | "warn";
}) {
  const bg =
    severity === "warn"
      ? "bg-pwc-status-warning-bg"
      : "bg-pwc-status-default-bg";
  return (
    <div className={`rounded-md p-3 ${bg}`}>
      <div className="text-pwc-subBody text-pwc-gray-500">{label}</div>
      <div className="text-pwc-title4 mt-1">{value}</div>
      <div className="text-pwc-caption text-pwc-gray-500 mt-1">{sub}</div>
    </div>
  );
}
