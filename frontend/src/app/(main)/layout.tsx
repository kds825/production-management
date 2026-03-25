"use client";

import { AppLayout } from "@/shared/components/AppLayout";

export default function MainLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppLayout>{children}</AppLayout>;
}
