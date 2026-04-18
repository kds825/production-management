"use client";

interface ParamSpec {
  key: string;
  label: string;
  unit: string;
  helperText: string;
  seedDefault: number;
}

interface ConstraintSpec {
  constraint_id: string;
  constraint_name: string;
  params: ParamSpec[];
}

export const EDITABLE_CONSTRAINTS: ConstraintSpec[] = [
  {
    constraint_id: "4-1",
    constraint_name: "규격교체 시간",
    params: [
      {
        key: "stranding_min",
        label: "연선 규격교체",
        unit: "분",
        helperText:
          "연선 공정에서 SQ 다른 제품으로 바뀔 때 설비 재조정 시간 (SpeedMaster 값 없을 때 fallback)",
        seedDefault: 210,
      },
      {
        key: "insulation_min",
        label: "저압절연 규격교체",
        unit: "분",
        helperText: "저압절연 공정 SQ 전환 시간",
        seedDefault: 60,
      },
      {
        key: "sheath_min",
        label: "시스 규격교체",
        unit: "분",
        helperText: "시스 공정 SQ 전환 시간",
        seedDefault: 30,
      },
      {
        key: "cv_min",
        label: "고압절연(CV) 규격교체",
        unit: "분",
        helperText: "고압절연 CV 공정 SQ 전환 시간",
        seedDefault: 300,
      },
    ],
  },
  {
    constraint_id: "4-2",
    constraint_name: "색상교체 시간",
    params: [
      {
        key: "sheath_color_min",
        label: "시스 색상교체",
        unit: "분",
        helperText:
          "저압시스 공정 색상 전환 시간 (SpeedMaster.setup_color_min 값 없을 때 fallback)",
        seedDefault: 120,
      },
    ],
  },
  {
    constraint_id: "4-4",
    constraint_name: "용접 시간",
    params: [
      {
        key: "welding_min",
        label: "연선 용접",
        unit: "분",
        helperText: "연선 공정 스플라이스 로트 용접 시간",
        seedDefault: 30,
      },
    ],
  },
];

function formatMin(m: number): string {
  if (!Number.isFinite(m) || m <= 0) return "0분";
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h === 0) return `${mm}분`;
  if (mm === 0) return `${h}시간`;
  return `${h}시간 ${mm}분`;
}

interface ParamRowProps {
  spec: ParamSpec;
  value: number;
  onChange: (v: number) => void;
  invalid: boolean;
}

function ParamRow({ spec, value, onChange, invalid }: ParamRowProps) {
  return (
    <div className="flex items-start gap-4 py-3">
      <div className="flex-1">
        <label className="block text-sm font-medium">{spec.label}</label>
        <p className="mt-0.5 text-xs text-gray-500">{spec.helperText}</p>
      </div>
      <div className="w-60">
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={0}
            step={1}
            value={Number.isFinite(value) ? value : 0}
            onChange={(e) => onChange(Number(e.target.value))}
            className={`w-24 rounded border px-2 py-1 text-right text-sm ${
              invalid ? "border-red-500" : "border-gray-300"
            }`}
          />
          <span className="text-xs text-gray-600">{spec.unit}</span>
          <span className="text-xs text-gray-400">({formatMin(value)})</span>
        </div>
        {invalid && (
          <p className="mt-1 text-xs text-red-600">
            0 이상의 숫자를 입력하세요
          </p>
        )}
      </div>
    </div>
  );
}

interface EditableParams {
  constraint_id: string;
  params_json: Record<string, number>;
}

interface ParamEditorProps {
  constraints: EditableParams[];
  editedParams: Record<string, Record<string, number>>;
  onParamsChange: (id: string, params: Record<string, number>) => void;
  onRestoreDefault: (id: string) => void;
}

export function ParamEditor({
  constraints,
  editedParams,
  onParamsChange,
  onRestoreDefault,
}: ParamEditorProps) {
  return (
    <div className="space-y-4">
      {EDITABLE_CONSTRAINTS.map((spec) => {
        const dbRow = constraints.find(
          (c) => c.constraint_id === spec.constraint_id,
        );
        const dbParams = dbRow?.params_json || {};
        const localPatch = editedParams[spec.constraint_id] || {};
        // edited values override DB values for display
        const current: Record<string, number> = { ...dbParams, ...localPatch };
        const hasLocalEdit = Object.keys(localPatch).length > 0;

        return (
          <div
            key={spec.constraint_id}
            className="rounded-lg border border-gray-200 bg-white"
          >
            <div className="flex items-center justify-between border-b bg-gray-50 px-4 py-2">
              <h3 className="text-sm font-semibold">
                <span className="font-mono text-xs text-gray-400">
                  {spec.constraint_id}
                </span>{" "}
                · {spec.constraint_name}
                {hasLocalEdit && (
                  <span className="ml-2 rounded bg-yellow-100 px-2 py-0.5 text-xs text-yellow-800">
                    수정됨
                  </span>
                )}
              </h3>
              <button
                onClick={() => onRestoreDefault(spec.constraint_id)}
                className="text-xs text-blue-600 hover:underline"
              >
                기본값으로 되돌리기
              </button>
            </div>
            <div className="divide-y px-4">
              {spec.params.map((p) => {
                const raw = current[p.key];
                const value = Number(raw ?? p.seedDefault);
                const invalid = !Number.isFinite(value) || value < 0;
                return (
                  <ParamRow
                    key={p.key}
                    spec={p}
                    value={value}
                    invalid={invalid}
                    onChange={(v) =>
                      onParamsChange(spec.constraint_id, {
                        ...(editedParams[spec.constraint_id] || {}),
                        [p.key]: v,
                      })
                    }
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
