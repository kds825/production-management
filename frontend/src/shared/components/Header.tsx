"use client";
import Image from "next/image";

export function Header({ onAddTask }: { onAddTask?: () => void }) {
  return (
    <header className="h-14 bg-white border-b border-gray-200 flex items-center justify-between px-6 shadow-sm sticky top-0 z-50">
      <div className="flex items-center gap-4">
        <Image
          src="/kbi-group-logo.jpg"
          alt="KBI GROUP"
          width={80}
          height={40}
          className="object-contain"
        />
        <div className="h-6 w-px bg-gray-300" />
        <Image
          src="/kbi-cosmolink-logo.png"
          alt="KBI COSMOLINK"
          width={140}
          height={24}
          className="object-contain"
        />
        <div className="h-6 w-px bg-gray-300" />
        <h1
          className="text-sm font-semibold tracking-tight"
          style={{ color: "#4A2C2A" }}
        >
          생산계획 스케줄러
        </h1>
      </div>
      <div className="flex items-center gap-3">
        {onAddTask && (
          <button
            onClick={onAddTask}
            className="px-3 py-1.5 text-xs font-medium text-white rounded-md transition-colors"
            style={{ backgroundColor: "#C41230" }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.backgroundColor = "#9E0E27")
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.backgroundColor = "#C41230")
            }
          >
            + 작업 추가
          </button>
        )}
        <span className="text-[10px] text-gray-400 bg-gray-100 px-2 py-1 rounded">
          PoC v0.1
        </span>
      </div>
    </header>
  );
}
