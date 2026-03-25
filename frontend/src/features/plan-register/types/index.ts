export type { ProductionBatch } from "@/features/scheduler/types";

export type VoltageType = "전체" | "고압" | "저압";
export type EquipmentGroup = "연선" | "B100" | "A100" | "A120";

export interface UploadedFile {
  name: string;
  size: number;
  uploadedAt: Date;
}
