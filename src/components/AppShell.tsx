import { Link, useRouterState } from "@tanstack/react-router";
import type { ReactNode } from "react";

const NAV = [
  { to: "/", label: "نظرة عامة", icon: "◉" },
  { to: "/agents", label: "الوكلاء", icon: "⬡" },
  { to: "/command", label: "مركز الأوامر", icon: "▸" },
  { to: "/chat", label: "دردشة الحضارة", icon: "✦" },
  { to: "/settings", label: "الإعدادات", icon: "⚙" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3">
          <Link to="/" className="flex items-center gap-2">
            <span className="text-glow font-display text-xl font-bold text-primary">AGOS</span>
            <span className="hidden text-sm text-muted-foreground sm:inline">حضارة الوكلاء</span>
          </Link>
          <nav className="flex items-center gap-1 overflow-x-auto">
            {NAV.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition-colors ${
                  pathname === item.to
                    ? "bg-secondary text-primary"
                    : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
                }`}
              >
                <span className="ml-1 text-xs">{item.icon}</span>
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
      <footer className="border-t border-border py-4 text-center text-xs text-muted-foreground">
        AGOS — حضارة وكلاء ذكاء اصطناعي حقيقية · 20 تخصصاً
      </footer>
    </div>
  );
}
