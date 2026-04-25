# Dry-Run Friction Log

> **운영용**. KBI 담당자가 `docs/kbi-dry-run-playbook.md` 의 9개 시나리오를 실행하면서 관찰된 모든 친찰 (friction) 항목을 여기에 추가한다. **Week 8 (P0 fixes)** 의 입력이 된다.

## 분류

- **P0 (pilot blocker)**: pilot 발진을 막거나, 사용자가 회피책 없이 막힘. 반드시 Week 8 안에 fix.
- **P1 (pilot annoyance)**: pilot 가능하지만 사용자 경험 저하. Week 8 시간 허용 시 fix; 아니면 post-pilot.
- **post-pilot**: pilot 후 백로그.

## 결과 요약 (dry-run 종료 시 기록)

- 실행 일자:
- 담당자:
- 회수: P0 \_\_건 / P1 \_\_건 / post-pilot \_\_건
- 전체 시나리오 통과율: \_\_/9

## 상세 항목

| #   | 시나리오          | 관찰된 동작                                           | 기대 동작                             | Severity | 재현 단계      | 의심 파일                                                     |
| --- | ----------------- | ----------------------------------------------------- | ------------------------------------- | -------- | -------------- | ------------------------------------------------------------- |
| 1   | (예시) 시나리오 4 | Decision Card Zone 2 막대그래프가 항상 빈 칸으로 나옴 | 가중치 상위 3개가 막대그래프로 렌더링 | P0       | 임의 배치 클릭 | `frontend/src/features/scheduler/components/DecisionCard.tsx` |
|     |                   |                                                       |                                       |          |                |                                                               |

## P0 → Week 8 작업 큐 (자동 라우팅)

- [ ] (P0 항목 발견 시 여기에 task 형태로 옮김)

## post-pilot 백로그 라우팅

- [ ] (post-pilot 항목 발견 시 `docs/post-pilot-backlog.md` 로 이동)
