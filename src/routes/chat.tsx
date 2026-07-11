import { createFileRoute, Link } from "@tanstack/react-router";
import { api, SPEC_LABELS } from "@/lib/api";
import { useBackend } from "@/hooks/useBackend";
import { useEffect, useRef, useState } from "react";

export const Route = createFileRoute("/chat")({
  component: ChatPage,
});

interface ChatMessage {
  role: "user" | "civilization";
  content: string;
  routedTo?: string;
  agent?: string;
}

function ChatPage() {
  const { connected, ready } = useBackend();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [busy]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const res = await api.chat(text);
      const answer =
        res.outcome?.result?.answer ??
        (res.outcome?.result?.llm_error
          ? `⚠ ${res.outcome.result.llm_error}`
          : "لم يصل رد من الوكيل");
      setMessages((m) => [
        ...m,
        { role: "civilization", content: answer, routedTo: res.routed_to, agent: res.agent },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: "civilization",
          content:
            e instanceof Error && e.message === "NO_BACKEND"
              ? "الحضارة غير متصلة بعد — اربط الباكند من صفحة الإعدادات ثم عد للدردشة."
              : `خطأ في الاتصال: ${String(e)}`,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-[calc(100vh-12rem)] flex-col">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="font-display text-2xl font-bold">دردشة الحضارة</h1>
        {ready && !connected && (
          <Link to="/settings" className="text-sm text-warning underline underline-offset-2">
            غير متصل — الإعدادات
          </Link>
        )}
      </div>

      <div className="panel flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <p className="text-glow font-display text-lg text-primary">✦</p>
            <p className="mt-2 text-sm text-muted-foreground">
              اكتب أمراً بأي لغة — الحضارة توجّهه تلقائياً للوكيل المتخصص الأنسب
            </p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-start" : "justify-end"}`}>
            <div
              className={`max-w-[85%] rounded-lg px-4 py-3 text-sm ${
                msg.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-secondary text-foreground"
              }`}
            >
              {msg.routedTo && (
                <p className="mb-1 text-xs opacity-70">
                  وُجّه إلى: {SPEC_LABELS[msg.routedTo] ?? msg.routedTo}
                  {msg.agent && (
                    <span className="font-mono" dir="ltr">
                      {" "}
                      ({msg.agent})
                    </span>
                  )}
                </p>
              )}
              <p className="whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-end">
            <div className="rounded-lg bg-secondary px-4 py-3 text-sm text-muted-foreground">
              الحضارة تفكّر<span className="animate-pulse">...</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="mt-4 flex gap-2">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={2}
          placeholder="مثال: ابحث لي عن أحدث تقنيات الذكاء الاصطناعي..."
          className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <button
          onClick={send}
          disabled={busy || !input.trim()}
          className="rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          إرسال
        </button>
      </div>
    </div>
  );
}
