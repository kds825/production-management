import { Header } from "@/shared/components/Header";

export default function SchedulerPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="p-4">
        <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-8 text-center">
          <h2 className="text-lg font-semibold" style={{ color: "#4A2C2A" }}>
            공정 도식화
          </h2>
          <p className="text-sm text-gray-500 mt-2">
            스케줄러 컴포넌트가 여기에 렌더링됩니다 (Task 4에서 구현)
          </p>
        </div>
      </main>
    </div>
  );
}
