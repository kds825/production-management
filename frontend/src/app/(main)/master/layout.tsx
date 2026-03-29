"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const MASTER_PAGES = [
  { href: "/master/constraints", label: "제약조건 관리", icon: "⚙️" },
  { href: "/master/speed", label: "선속 마스터", icon: "⚡" },
  { href: "/master/calendar", label: "가동 캘린더", icon: "📅" },
  { href: "/master/equipment", label: "설비 관리", icon: "🏭" },
  { href: "/master/drum-lots", label: "틀단위 관리", icon: "🥁" },
  { href: "/master/customers", label: "거래처 관리", icon: "👥" },
  { href: "/master/routing", label: "공정 라우팅", icon: "🔀" },
  { href: "/master/items", label: "품목 관리", icon: "📦" },
];

export default function MasterLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <div className="flex h-full">
      <aside className="w-56 shrink-0 border-r border-gray-200 bg-gray-50 p-4">
        <h2 className="mb-4 text-sm font-bold uppercase tracking-wider text-gray-500">
          마스터 데이터
        </h2>
        <nav className="space-y-1">
          {MASTER_PAGES.map((page) => {
            const active = pathname === page.href;
            return (
              <Link
                key={page.href}
                href={page.href}
                className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-blue-50 text-blue-700 font-medium"
                    : "text-gray-700 hover:bg-gray-100"
                }`}
              >
                <span>{page.icon}</span>
                {page.label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  );
}
