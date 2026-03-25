"use client";

import { useState, useRef, useEffect } from "react";

export interface CollapsiblePanelProps {
  title: string;
  count?: number;
  defaultExpanded?: boolean;
  /** Called when animation starts (true) or finishes (false). Use to disable drag in children. */
  onAnimatingChange?: (animating: boolean) => void;
  children: React.ReactNode;
}

/** Generic collapsible bottom panel with 200ms ease-out animation */
export function CollapsiblePanel({
  title,
  count,
  defaultExpanded = true,
  onAnimatingChange,
  children,
}: CollapsiblePanelProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [isAnimating, setIsAnimating] = useState(false);
  const animationTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Sync expanded state when defaultExpanded changes (e.g. orders loaded)
  useEffect(() => {
    setExpanded(defaultExpanded);
  }, [defaultExpanded]);

  function toggle() {
    if (isAnimating) return;
    setIsAnimating(true);
    onAnimatingChange?.(true);
    setExpanded((prev) => !prev);
    // Animation duration matches CSS transition (200ms), add small buffer
    animationTimer.current = setTimeout(() => {
      setIsAnimating(false);
      onAnimatingChange?.(false);
    }, 220);
  }

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (animationTimer.current) clearTimeout(animationTimer.current);
    };
  }, []);

  return (
    <div
      className="w-full bg-white border-t border-gray-200 flex flex-col"
      style={{ flexShrink: 0 }}
      data-animating={isAnimating ? "true" : undefined}
    >
      {/* Header: always 40px */}
      <div
        className="flex items-center justify-between px-3 cursor-pointer select-none"
        style={{ height: 40, minHeight: 40 }}
        onClick={toggle}
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold" style={{ color: "#4A2C2A" }}>
            {title}
          </span>
          {count != null && count > 0 && (
            <span
              className="text-[10px] font-medium text-white px-1.5 py-0.5 rounded-full"
              style={{ backgroundColor: "#C41230" }}
            >
              {count}
            </span>
          )}
        </div>

        {/* Chevron toggle icon */}
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          style={{
            stroke: "#6B7280",
            transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 200ms ease-out",
          }}
        >
          <path
            d="M4 6L8 10L12 6"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>

      {/* Collapsible body */}
      <div
        style={{
          maxHeight: expanded ? 160 : 0,
          overflow: "hidden",
          transition: "max-height 200ms ease-out",
        }}
      >
        {/* Inner scroll area — overflow-y handled inside when content overflows */}
        <div style={{ maxHeight: 160, overflowY: "auto" }}>{children}</div>
      </div>
    </div>
  );
}
