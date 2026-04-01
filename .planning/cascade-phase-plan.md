# Phase: 간트차트 Cross-Process Cascade + 설비 검증

## Goal

연선→절연→시스 파이프라인에서 선행 공정 이동 시 후행 공정 자동 연동,
충돌 해소 모달, 설비-규격 드래그 검증을 구현한다.

## Wave 1: 설비 검증 강화 (독립, 병렬 가능)

### Task W1-A: 프론트엔드 SQ Range + Material 드래그 검증

**파일:** SchedulerView.tsx, equipment API, types
**변경:**

1. `EquipmentResponse`에 `range_min`, `range_max`, `material_limit` 필드 추가 (schemas.py)
2. `equipment.py` GET /equipment 응답에 해당 필드 포함
3. 프론트 `Equipment` 타입에 필드 추가 (types/index.ts)
4. `equipmentMatchesGroup()` 확장:
   - 기존 그룹 매칭 유지
   - 추가: task.spec에서 SQ 추출 → equipment.range_min/max 범위 확인
   - 추가: task에서 material 추출 → equipment.material_limit 확인
   - 불일치 시 `isIncompatible = true` (기존 비활성화 UX 그대로 활용)

### Task W1-B: 백엔드 PUT /tasks/{task_id} 설비 검증

**파일:** schedules.py
**변경:**

1. `update_task()`에서 `equipment_id` 변경 시:
   - EquipmentMaster에서 range_min/max, material_limit 조회
   - ProductionBatch에서 SQ, conductor_material 조회
   - SQ 범위 초과 → 422 "규격 {SQ}SQ가 설비 범위({min}~{max})를 초과합니다"
   - Material 불일치 → 422 "재질 {material}은 설비 {equip}에서 생산 불가합니다"

## Wave 2: Cross-Equipment Cascade (핵심)

### Task W2-A: 백엔드 Cascade Preview API

**파일:** schedules.py (새 엔드포인트)
**변경:**

1. `POST /api/schedules/cascade-preview` 엔드포인트 추가
   - Input: `{ task_id: str, new_start: datetime, new_end: datetime }`
   - 로직:
     a) 이동된 task의 batch에서 sales_order_id + process_name 확인
     b) \_PREDECESSOR_PROCESS 역참조: 이 공정이 선행인 후행 공정들 찾기
     c) 같은 수주번호의 후행 공정 task들 조회
     d) 이동 delta 계산 (new_start - old_start)
     e) 후행 task들에 delta 적용한 새 시간 계산
     f) 충돌 감지: 새 시간이 같은 설비의 다른 task와 겹치는지 확인
   - Output:
     ```json
     {
       "affected_tasks": [
         {
           "task_id": "TASK-123",
           "old_start": "...",
           "new_start": "...",
           "process": "저압절연",
           "reason": "선행 연선 이동에 따른 연동"
         }
       ],
       "conflicts": [
         {
           "task_id": "TASK-456",
           "conflict_with": "TASK-789",
           "equipment": "B100EXT",
           "overlap_min": 120,
           "resolution": "push_forward"
         }
       ],
       "can_auto_resolve": true // 충돌 없거나 push로 해소 가능
     }
     ```

### Task W2-B: 백엔드 Bulk Update API

**파일:** schedules.py (새 엔드포인트)
**변경:**

1. `PATCH /api/schedules/tasks/bulk-update` 엔드포인트 추가
   - Input: `{ updates: [{ task_id, new_start, new_end }] }`
   - DB 트랜잭션으로 일괄 업데이트
   - constraint_checker로 최종 검증 후 반환

### Task W2-C: 프론트엔드 Cascade 로직

**파일:** scheduleStore.ts, scheduler/page.tsx
**변경:**

1. `scheduleStore.ts`에 `previewCascade()` 액션 추가:
   - POST /api/schedules/cascade-preview 호출
   - 결과를 `cascadePreview` 상태에 저장
2. `moveTask()` 수정:
   - 이동된 task가 선행 공정인지 확인 (process_step 기반)
   - 선행이면 → previewCascade() 호출
   - `can_auto_resolve=true` → 자동 적용 (bulk-update API)
   - `can_auto_resolve=false` → ConflictResolutionModal 열기
3. `scheduler/page.tsx` handleDragEnd 수정:
   - cascade 결과 대기 후 처리

## Wave 3: 충돌 해소 모달 (Wave 2 의존)

### Task W3-A: ConflictResolutionModal 컴포넌트

**파일:** frontend/src/features/scheduler/components/ConflictResolutionModal.tsx (신규)
**구조:**

```
┌─────────────────────────────────────────────┐
│  ⚠ 선행 공정 이동으로 후행 작업에 영향       │
├─────────────────────────────────────────────┤
│  이동 대상: 연선 300SQ → 4/5 → 4/7          │
│                                              │
│  영향받는 작업:                               │
│  ┌─────────────────────────────────────────┐│
│  │ ☑ 저압절연 300SQ  4/8→4/10  B100EXT    ││
│  │ ☑ 저압시스 300SQ  4/12→4/14 SH-A100    ││
│  └─────────────────────────────────────────┘│
│                                              │
│  ⚠ 충돌 1건:                                │
│  저압절연 이동 시 B100EXT에서 기존 작업       │
│  (120SQ 절연)과 120분 겹침                   │
│  → 기존 작업을 2시간 뒤로 밀어서 해소         │
│                                              │
│  [전체 적용]  [취소]                          │
└─────────────────────────────────────────────┘
```

- "전체 적용": bulk-update API 호출 → 모달 닫기 → 간트 새로고침
- "취소": 원래 위치로 복원

### Task W3-B: 스케줄 스토어 통합

**파일:** scheduleStore.ts
**변경:**

1. `conflictModal` 상태 추가: `{ isOpen, preview, originalTask }`
2. `applyCascade()`: bulk-update → 성공 시 모달 닫기 + task 갱신
3. `cancelCascade()`: 원본 task 위치 복원 + 모달 닫기

## 검증 기준

- [ ] 400SQ task를 range_max=300 설비로 드래그 → 비활성화 (드롭 불가)
- [ ] AL task를 CU-only 설비로 드래그 → 비활성화
- [ ] 백엔드 PUT에서 SQ 범위 초과 시 422 반환
- [ ] 연선 블록 뒤로 이동 → cascade-preview에 절연/시스 포함
- [ ] 충돌 없는 cascade → 자동 적용 (모달 없이)
- [ ] 충돌 있는 cascade → ConflictResolutionModal 표시
- [ ] "전체 적용" → bulk-update 성공 → 간트 갱신
- [ ] "취소" → 원래 위치 복원
