"use client";

import type { Equipment } from "../types";

interface EquipmentSidebarProps {
  equipment: Equipment;
}

// 공정 유형별 아이콘 매핑
const PROCESS_ICONS: Record<string, string> = {
  drawing: "🔧",
  stranding: "🔄",
  hv_insulation: "⚡",
  lv_insulation: "🔌",
  taping: "📐",
  cabling: "🔗",
  lv_jacketing: "🛡️",
  hv_jacketing: "🛡️",
  neutral_wire: "⚙️",
};

// 공정 유형 한국어 레이블
const PROCESS_LABELS: Record<string, string> = {
  drawing: "신선",
  stranding: "연선",
  hv_insulation: "고압절연",
  lv_insulation: "저압절연",
  taping: "테이핑",
  cabling: "성케이블",
  lv_jacketing: "저압자켓",
  hv_jacketing: "고압자켓",
  neutral_wire: "중성선",
};

export function EquipmentSidebar({ equipment }: EquipmentSidebarProps) {
  const icon = PROCESS_ICONS[equipment.process_type] ?? "🏭";
  const label =
    PROCESS_LABELS[equipment.process_type] ?? equipment.process_type;

  // 백엔드 status 값은 "available" (FIX M-5: 과거 "active" 오류 수정)
  const isAvailable = equipment.status === "available";
  const statusColor = isAvailable ? "#16A34A" : "#9CA3AF";

  return (
    <div
      className="flex items-center gap-1.5 px-2 h-full"
      style={{
        width: "100%",
        minWidth: 0,
        borderRight: "1px solid #E5E7EB",
        backgroundColor: "#FAFAFA",
      }}
      title={`${equipment.name} — ${label}`}
    >
      {/* 상태 표시 dot */}
      <div
        className="flex-shrink-0 w-1.5 h-1.5 rounded-full"
        style={{ backgroundColor: statusColor }}
      />

      {/* 공정 아이콘 */}
      <span className="flex-shrink-0 text-xs leading-none" aria-hidden>
        {icon}
      </span>

      {/* 설비명 */}
      <span
        className="text-xs font-medium truncate"
        style={{ color: "#4A2C2A" }}
      >
        {equipment.name}
      </span>
    </div>
  );
}
