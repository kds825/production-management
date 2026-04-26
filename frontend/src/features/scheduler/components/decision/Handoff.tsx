/**
 * ❸ Handoff — 전공정→본→후공정 시간축.
 *
 * union 분기:
 *   - HandoffBlock: 일반 (시스/연선)
 *   - WipMatchBlock: 연선의 경우 재공 매칭
 *   - OutsourceHandoffBlock: 외주 분기 (일 단위)
 *
 * predecessor_label 은 백엔드 phrasing 이 이미 "연선 (절연 스킵 · TFR-GV)"
 * 같은 변형 처리. 본 컴포넌트는 string render 만.
 */

import type {
  HandoffBlock,
  OutsourceHandoffBlock,
  WipMatchBlock,
} from "./decisionCardTypes";

function formatKst(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function formatDay(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

interface Props {
  handoff: HandoffBlock | null;
  wipMatch: WipMatchBlock | null;
  outsourceHandoff: OutsourceHandoffBlock | null;
  defaultExpanded: boolean;
}

export function Handoff({
  handoff,
  wipMatch,
  outsourceHandoff,
  defaultExpanded,
}: Props) {
  const filled = [handoff, wipMatch, outsourceHandoff].filter(Boolean).length;
  if (filled === 0) return null;
  return (
    <details
      className="border-t border-pwc-gray-200 px-4 py-3"
      open={defaultExpanded}
    >
      <summary className="text-pwc-subTitle2 cursor-pointer list-none flex items-center justify-between">
        <span>❸ 앞·뒤 작업과 어떻게 이어지나요?</span>
        <span className="text-pwc-caption text-pwc-gray-500" aria-hidden>
          ▼
        </span>
      </summary>
      <div className="mt-3">
        {handoff ? <HandoffRows block={handoff} /> : null}
        {wipMatch ? <WipMatchRows block={wipMatch} /> : null}
        {outsourceHandoff ? <OutsourceRows block={outsourceHandoff} /> : null}
      </div>
    </details>
  );
}

function HandoffRows({ block }: { block: HandoffBlock }) {
  return (
    <table className="w-full text-pwc-body">
      <tbody>
        <tr className="border-b border-pwc-gray-200">
          <td className="py-2 text-pwc-gray-500">전공정</td>
          <td className="py-2">{block.predecessor_label}</td>
          <td className="py-2 text-right font-mono">
            {formatKst(block.predecessor_end_at)} 종료
          </td>
        </tr>
        <tr className="border-b border-pwc-gray-200">
          <td className="py-2 text-pwc-gray-500">갭</td>
          <td className="py-2 text-pwc-gray-600" colSpan={2}>
            {block.gap_minutes}분
          </td>
        </tr>
        <tr className="border-b border-pwc-gray-200 bg-pwc-primary-100">
          <td className="py-2 text-pwc-gray-500">본 작업</td>
          <td className="py-2 font-medium" colSpan={2}>
            <span className="font-mono">{formatKst(block.self_start_at)}</span>{" "}
            → <span className="font-mono">{formatKst(block.self_end_at)}</span>
          </td>
        </tr>
        {block.successor_label ? (
          <tr>
            <td className="py-2 text-pwc-gray-500">후공정</td>
            <td className="py-2">{block.successor_label}</td>
            <td className="py-2 text-right font-mono">
              {formatKst(block.successor_first_slot_at)} 시작 가능
            </td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

function WipMatchRows({ block }: { block: WipMatchBlock }) {
  return (
    <div className="space-y-2 text-pwc-body">
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">매칭 재공 ID</span>
        <span className="font-mono">{block.matched_wip_id ?? "—"}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">재공 총 길이</span>
        <span className="font-mono">{block.wip_total_m.toLocaleString()}m</span>
      </div>
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">사용</span>
        <span className="font-mono">{block.wip_used_m.toLocaleString()}m</span>
      </div>
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">잔여</span>
        <span className="font-mono">
          {block.remainder_m.toLocaleString()}m ({block.loss_pct.toFixed(1)}%)
        </span>
      </div>
    </div>
  );
}

function OutsourceRows({ block }: { block: OutsourceHandoffBlock }) {
  return (
    <div className="space-y-2 text-pwc-body">
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">협력사</span>
        <span className="font-medium">{block.vendor_name}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">발주</span>
        <span className="font-mono">{formatDay(block.order_at)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">외주 작업 기간</span>
        <span className="font-mono">
          {formatDay(block.outsource_start_at)} ~{" "}
          {formatDay(block.outsource_end_at)}
        </span>
      </div>
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">입고</span>
        <span className="font-mono">{formatDay(block.inbound_at)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-pwc-gray-500">리드 타임</span>
        <span className="text-pwc-gray-600">{block.lead_days}일</span>
      </div>
      {block.successor_label ? (
        <div className="flex justify-between border-t border-pwc-gray-200 pt-2">
          <span className="text-pwc-gray-500">후공정</span>
          <span>
            {block.successor_label} —{" "}
            <span className="font-mono">
              {formatDay(block.successor_first_slot_at)}
            </span>{" "}
            시작 가능
          </span>
        </div>
      ) : null}
    </div>
  );
}
