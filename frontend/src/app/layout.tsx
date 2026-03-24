import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KBI 생산계획 스케줄러",
  description: "KBI 코스모링크 생산계획 시각화 도구",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="bg-gray-50 text-gray-900 antialiased min-h-screen">
        {children}
      </body>
    </html>
  );
}
