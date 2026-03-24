"use client";

import type { Equipment } from "../types";

interface EquipmentSidebarProps {
  equipment: Equipment;
}

// 공정별 SVG 아이콘 (PwC 스타일 — 미니멀, 단색, 16px)
function ProcessIcon({ type }: { type: string }) {
  const color = PROCESS_COLORS[type] ?? "#78909C";

  switch (type) {
    case "drawing":
      // 신선: 가느다란 선을 뽑는 형태 (화살표+선)
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path
            d="M2 12L12 2"
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
          />
          <path
            d="M8 2h4v4"
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "stranding":
      // 연선: 꼬인 선 (나선형)
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path
            d="M2 7c2-3 4 3 5 0s3-3 5 0"
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
          />
          <path
            d="M2 10c2-3 4 3 5 0s3-3 5 0"
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
            opacity="0.4"
          />
        </svg>
      );
    case "hv_insulation":
    case "lv_insulation":
      // 절연: 동심원 (케이블 단면)
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <circle cx="7" cy="7" r="5.5" stroke={color} strokeWidth="1.2" />
          <circle cx="7" cy="7" r="3" stroke={color} strokeWidth="1.2" />
          <circle cx="7" cy="7" r="1" fill={color} />
        </svg>
      );
    case "taping":
      // 테이핑: 감는 형태 (나선 원)
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path
            d="M7 1.5a5.5 5.5 0 110 11 5.5 5.5 0 010-11z"
            stroke={color}
            strokeWidth="1.2"
            strokeDasharray="3 2"
          />
          <circle cx="7" cy="7" r="2" stroke={color} strokeWidth="1.2" />
        </svg>
      );
    case "cabling":
      // 연합: 여러 선이 합쳐지는 형태
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path
            d="M2 3l5 4-5 4"
            stroke={color}
            strokeWidth="1.3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M7 7h5"
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      );
    case "lv_jacketing":
    case "hv_jacketing":
      // 시스: 외피를 씌우는 형태 (사각+원)
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect
            x="1.5"
            y="3.5"
            width="11"
            height="7"
            rx="2"
            stroke={color}
            strokeWidth="1.2"
          />
          <circle
            cx="7"
            cy="7"
            r="2"
            stroke={color}
            strokeWidth="1"
            opacity="0.5"
          />
        </svg>
      );
    case "neutral_wire":
      // 중성선: N 마크
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path
            d="M3 11V3l8 8V3"
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    default:
      // 기본: 기어
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <circle cx="7" cy="7" r="3" stroke={color} strokeWidth="1.2" />
          <circle
            cx="7"
            cy="7"
            r="5.5"
            stroke={color}
            strokeWidth="1"
            strokeDasharray="2 2"
          />
        </svg>
      );
  }
}

// 공정별 색상 (PwC 스타일 — 차분한 톤)
const PROCESS_COLORS: Record<string, string> = {
  drawing: "#5C6BC0", // 인디고
  stranding: "#26A69A", // 틸
  hv_insulation: "#C41230", // KBI Red
  lv_insulation: "#EF6C00", // 오렌지
  taping: "#7E57C2", // 퍼플
  cabling: "#42A5F5", // 블루
  lv_jacketing: "#66BB6A", // 그린
  hv_jacketing: "#C41230", // KBI Red
  neutral_wire: "#78909C", // 그레이블루
};

// 공정 유형 한국어 레이블
const PROCESS_LABELS: Record<string, string> = {
  drawing: "신선",
  stranding: "연선",
  hv_insulation: "고압절연",
  lv_insulation: "저압절연",
  taping: "테이핑",
  cabling: "연합",
  lv_jacketing: "저압시스",
  hv_jacketing: "고압시스",
  neutral_wire: "중성선",
};

export function EquipmentSidebar({ equipment }: EquipmentSidebarProps) {
  const label =
    PROCESS_LABELS[equipment.process_type] ?? equipment.process_type;
  const processColor = PROCESS_COLORS[equipment.process_type] ?? "#78909C";
  const isAvailable = equipment.status === "available";

  return (
    <div
      className="flex items-center gap-2 px-2.5 h-full"
      style={{
        width: "100%",
        minWidth: 0,
        borderRight: "1px solid #E5E7EB",
        backgroundColor: "#FAFAFA",
      }}
      title={`${equipment.name} — ${label}`}
    >
      {/* 상태 dot */}
      <div
        className="flex-shrink-0 w-1.5 h-1.5 rounded-full"
        style={{ backgroundColor: isAvailable ? "#16A34A" : "#D1D5DB" }}
      />

      {/* 공정 아이콘 (SVG) */}
      <div className="flex-shrink-0">
        <ProcessIcon type={equipment.process_type} />
      </div>

      {/* 설비명 + 공정 라벨 */}
      <div className="min-w-0 flex-1">
        <span
          className="text-xs font-semibold truncate block leading-tight"
          style={{ color: "#1A1A1A" }}
        >
          {equipment.name}
        </span>
        <span
          className="text-[9px] truncate block leading-tight"
          style={{ color: processColor }}
        >
          {label}
        </span>
      </div>
    </div>
  );
}
