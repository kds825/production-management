import type { Metadata } from "next";
import { Noto_Sans_KR } from "next/font/google";
import "./globals.css";

const notoSansKR = Noto_Sans_KR({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-noto-sans-kr",
});

export const metadata: Metadata = {
  title: "KBI 생산계획 스케줄러",
  description: "KBI 코스모링크 생산계획 시각화 도구",
  icons: {
    icon: [{ url: "/favicon.png", type: "image/png", sizes: "96x92" }],
    apple: "/apple-icon.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko" className={notoSansKR.variable}>
      <body className="bg-gray-50 text-gray-900 antialiased min-h-screen font-sans">
        {children}
      </body>
    </html>
  );
}
