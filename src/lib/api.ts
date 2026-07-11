/**
 * API client for the AGOS Agent Civilization backend.
 * Backend URL is configurable (Render deployment) and stored in localStorage.
 * When no backend is configured, the UI runs in demo mode with sample data.
 */

const STORAGE_KEY = "agos_api_url";

export function getApiUrl(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(STORAGE_KEY);
}

export function setApiUrl(url: string) {
  localStorage.setItem(STORAGE_KEY, url.replace(/\/+$/, ""));
}

export function clearApiUrl() {
  localStorage.removeItem(STORAGE_KEY);
}

export interface AgentSnapshot {
  id: number;
  name: string;
  specialization: string;
  status: string;
  stats: { tasks_completed: number; tasks_failed: number; total_time_s: number };
}

export interface SystemStatus {
  status: string;
  uptime_s: number;
  llm_configured: boolean;
  llm_model: string;
  specializations: number;
  total_agents: number;
  external_agents: number;
  tasks_completed: number;
  tasks_failed: number;
  tasks_in_memory: number;
}

export interface TaskRecord {
  id: string;
  specialization: string;
  prompt: string;
  status: string;
  created_at: number;
  agent?: string;
  result?: {
    ok: boolean;
    agent: string;
    elapsed_s: number;
    result: { answer?: string | null; tool_output?: unknown; llm_error?: string; error?: string };
  } | null;
}

export interface SpecializationInfo {
  name: string;
  description: string;
  agents: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const base = getApiUrl();
  if (!base) throw new Error("NO_BACKEND");
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  systemStatus: () => request<SystemStatus>("/system/status"),
  agents: () => request<{ count: number; agents: AgentSnapshot[] }>("/agents"),
  specializations: () =>
    request<{ specializations: SpecializationInfo[] }>("/specializations"),
  tasks: (limit = 50) =>
    request<{ count: number; tasks: TaskRecord[] }>(`/tasks?limit=${limit}`),
  task: (id: string) => request<TaskRecord>(`/tasks/${id}`),
  createTask: (specialization: string, prompt: string, data?: unknown) =>
    request<{ task_id: string; status: string }>("/tasks", {
      method: "POST",
      body: JSON.stringify({ specialization, prompt, data }),
    }),
  chat: (message: string) =>
    request<{
      routed_to: string;
      agent: string;
      outcome: TaskRecord["result"];
    }>("/chat", { method: "POST", body: JSON.stringify({ message }) }),
};

/* ------------------------- Demo mode data ------------------------- */

export const SPEC_LABELS: Record<string, string> = {
  analyst: "محلل",
  api_integrator: "مكامل واجهات",
  architect: "مهندس معماري",
  builder: "بنّاء",
  code_generator: "مولّد أكواد",
  communicator: "متواصل",
  data_processor: "معالج بيانات",
  db_manager: "مدير قواعد بيانات",
  designer: "مصمم",
  educator: "معلّم",
  modifier: "معدِّل أكواد",
  monitor: "مراقب",
  network_client: "خبير شبكات",
  researcher: "باحث",
  reviewer: "مراجع",
  self_developer: "مطوّر ذاتي",
  strategist: "استراتيجي",
  surgeon: "جرّاح أعطال",
  test_runner: "مشغّل اختبارات",
  validator: "مدقّق",
};

export const DEMO_STATUS: SystemStatus = {
  status: "demo",
  uptime_s: 0,
  llm_configured: false,
  llm_model: "meta-llama/llama-3.3-70b-instruct:free",
  specializations: 20,
  total_agents: 100,
  external_agents: 0,
  tasks_completed: 0,
  tasks_failed: 0,
  tasks_in_memory: 0,
};

export function demoAgents(): AgentSnapshot[] {
  const specs = Object.keys(SPEC_LABELS);
  return specs.flatMap((spec, si) =>
    Array.from({ length: 5 }, (_, i) => ({
      id: si * 5 + i,
      name: `${spec}_${String(si * 5 + i).padStart(4, "0")}`,
      specialization: spec,
      status: "idle",
      stats: { tasks_completed: 0, tasks_failed: 0, total_time_s: 0 },
    })),
  );
}
