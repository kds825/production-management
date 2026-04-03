import type { EquipmentGroup } from "@/features/plan-register/types";

export type ProcessGroup = "연선" | "절연" | "시스" | "T/P";

export const PROCESS_GROUP_MAP: Record<EquipmentGroup, ProcessGroup> = {
  연선: "연선",
  B100: "절연",
  A100: "시스",
  A120: "시스",
  "T/P": "T/P",
};

export const EQUIPMENT_BY_PROCESS: Record<ProcessGroup, EquipmentGroup[]> = {
  연선: ["연선"],
  절연: ["B100"],
  시스: ["A100", "A120"],
  "T/P": ["T/P"],
};

export const PROCESS_STATUS_COLORS = {
  진행: "#16A34A",
  대기: "#F59E0B",
} as const;

export type ProcessStatus = keyof typeof PROCESS_STATUS_COLORS;
