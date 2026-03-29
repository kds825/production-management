"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import Image from "next/image";

interface SidebarProps {
  expanded: boolean;
  onToggle: () => void;
}

const NAV_ITEMS = [
  {
    href: "/master/constraints",
    label: "마스터 데이터",
    icon: (
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <ellipse cx="12" cy="5" rx="9" ry="3" />
        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
      </svg>
    ),
    matchPrefix: "/master",
  },
  {
    href: "/plan-register",
    label: "생산계획등록",
    icon: (
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="12" y1="18" x2="12" y2="12" />
        <line x1="9" y1="15" x2="15" y2="15" />
      </svg>
    ),
  },
  {
    href: "/scheduling-review",
    label: "생산스케줄링 검토",
    icon: (
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="4" rx="1" />
        <rect x="14" y="10" width="7" height="7" rx="1" />
        <rect x="3" y="13" width="7" height="4" rx="1" />
        <line x1="3" y1="20" x2="21" y2="20" />
      </svg>
    ),
  },
  {
    href: "/scheduler",
    label: "생산계획 작성",
    icon: (
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
        <line x1="16" y1="2" x2="16" y2="6" />
        <line x1="8" y1="2" x2="8" y2="6" />
        <line x1="3" y1="10" x2="21" y2="10" />
        <rect x="6" y="13" width="4" height="2" rx="0.5" />
        <rect x="14" y="13" width="4" height="2" rx="0.5" />
        <rect x="6" y="17" width="4" height="2" rx="0.5" />
      </svg>
    ),
  },
];

export function Sidebar({ expanded, onToggle }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside
      className="flex flex-col border-r border-gray-200 bg-white shrink-0"
      style={{
        width: expanded ? 200 : 56,
        transition: "width 150ms ease",
      }}
    >
      {/* 토글 버튼 */}
      <button
        onClick={onToggle}
        className="flex items-center justify-center h-12 border-b border-gray-200 hover:bg-gray-50 transition-colors"
        title={expanded ? "사이드바 접기" : "사이드바 펼치기"}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#6B7280"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 150ms ease",
          }}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>

      {/* 네비게이션 메뉴 */}
      <nav className="flex-1 py-2">
        {NAV_ITEMS.map((item) => {
          const isActive = (item as any).matchPrefix
            ? pathname.startsWith((item as any).matchPrefix)
            : pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className="relative flex items-center gap-3 mx-1.5 my-0.5 rounded transition-colors"
              style={{
                padding: expanded ? "8px 12px" : "8px 0",
                justifyContent: expanded ? "flex-start" : "center",
                backgroundColor: isActive ? "#FEF2F2" : "transparent",
                color: isActive ? "#C41230" : "#374151",
              }}
              title={!expanded ? item.label : undefined}
            >
              {/* 활성 accent bar */}
              {isActive && (
                <div
                  className="absolute left-0 top-1 bottom-1 rounded-r"
                  style={{ width: 3, backgroundColor: "#C41230" }}
                />
              )}
              <span
                className="shrink-0"
                style={{ color: isActive ? "#C41230" : "#6B7280" }}
              >
                {item.icon}
              </span>
              {expanded && (
                <span
                  className="text-xs font-medium truncate"
                  style={{ color: isActive ? "#C41230" : "#374151" }}
                >
                  {item.label}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* 하단: KBI Cosmolink 로고 */}
      <div
        className="border-t border-gray-200 flex items-center justify-center py-3"
        style={{ minHeight: 48 }}
      >
        {expanded ? (
          <Image
            src="/kbi-cosmolink-logo.png"
            alt="KBI COSMOLINK"
            width={120}
            height={20}
            className="object-contain"
          />
        ) : (
          <Image
            src="/kbi-group-logo.jpg"
            alt="KBI"
            width={28}
            height={28}
            className="object-contain rounded"
          />
        )}
      </div>
    </aside>
  );
}
