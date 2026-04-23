/**
 * ConflictResolutionModal contract 테스트.
 *
 * RTL 미도입 상태에서 renderToStaticMarkup 으로 HTML 문자열 검증 —
 * 이벤트 핸들러 호출 같은 interactive 동작은 커버하지 못하지만
 * 한국어 라벨, 조건부 섹션, disabled 상태 등 view contract 는 안정적으로 검증 가능.
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ConflictResolutionModal } from "../ConflictResolutionModal";
import type { CascadePreviewResponse } from "../../api/cascade.types";

function mkPreview(
  over: Partial<CascadePreviewResponse> = {},
): CascadePreviewResponse {
  return {
    request_id: "r1",
    summary: "PO-123 절연 3h 늘어, 2건 재배치",
    pushes: [],
    pulls: [],
    unresolved: [],
    can_auto_resolve: true,
    iter_count: 1,
    truncated: false,
    ...over,
  };
}

describe("ConflictResolutionModal", () => {
  it("renders Korean reason label for push", () => {
    const html = renderToStaticMarkup(
      <ConflictResolutionModal
        preview={mkPreview({
          pushes: [
            {
              task_id: "B",
              equipment_code: "EQ1",
              batch_label: "PO-2",
              old_start: "2026-04-20T12:00:00",
              old_end: "2026-04-20T14:00:00",
              new_start: "2026-04-20T14:00:00",
              new_end: "2026-04-20T16:00:00",
              reason: "same_equipment_conflict",
            },
          ],
        })}
        pullToggle={true}
        onPullToggle={() => {}}
        onApply={() => {}}
        onClose={() => {}}
      />,
    );
    expect(html).toContain("같은 설비의 다음 블록과 충돌로 밀림");
  });

  it("renders summary when present", () => {
    const html = renderToStaticMarkup(
      <ConflictResolutionModal
        preview={mkPreview()}
        pullToggle={true}
        onPullToggle={() => {}}
        onApply={() => {}}
        onClose={() => {}}
      />,
    );
    expect(html).toContain("PO-123 절연 3h 늘어");
  });

  it("disables apply when unresolved exists", () => {
    const html = renderToStaticMarkup(
      <ConflictResolutionModal
        preview={mkPreview({
          unresolved: [
            {
              task_id: "A",
              equipment_code: "EQ1",
              batch_label: "PO-A",
              reason: "due_date_violation",
              detail: "납기 초과",
            },
          ],
        })}
        pullToggle={true}
        onPullToggle={() => {}}
        onApply={() => {}}
        onClose={() => {}}
      />,
    );
    expect(html).toMatch(/disabled/);
    expect(html).toContain("납기 초과 — 재배치 불가");
  });

  it("renders manual adjust CTA when handler provided", () => {
    const html = renderToStaticMarkup(
      <ConflictResolutionModal
        preview={mkPreview({
          unresolved: [
            {
              task_id: "A",
              equipment_code: "EQ1",
              batch_label: "PO-A",
              reason: "due_date_violation",
              detail: "",
            },
          ],
        })}
        pullToggle={true}
        onPullToggle={() => {}}
        onApply={() => {}}
        onClose={() => {}}
        onManualAdjust={() => {}}
      />,
    );
    expect(html).toContain("수동 조정 진입");
  });

  it("shows pull toggle checkbox only when pulls present", () => {
    const withPull = renderToStaticMarkup(
      <ConflictResolutionModal
        preview={mkPreview({
          pulls: [
            {
              task_id: "S",
              equipment_code: "EQ2",
              batch_label: "PO-S",
              old_start: "2026-04-20T15:00:00",
              old_end: "2026-04-20T18:00:00",
              new_start: "2026-04-20T12:00:00",
              new_end: "2026-04-20T15:00:00",
              reason: "successor_slack_available",
            },
          ],
        })}
        pullToggle={true}
        onPullToggle={() => {}}
        onApply={() => {}}
        onClose={() => {}}
      />,
    );
    expect(withPull).toContain("Pull 포함");

    const withoutPull = renderToStaticMarkup(
      <ConflictResolutionModal
        preview={mkPreview()}
        pullToggle={true}
        onPullToggle={() => {}}
        onApply={() => {}}
        onClose={() => {}}
      />,
    );
    expect(withoutPull).not.toContain("Pull 포함");
  });
});
