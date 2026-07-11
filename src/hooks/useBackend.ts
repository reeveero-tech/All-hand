import { useQuery } from "@tanstack/react-query";
import { api, getApiUrl, DEMO_STATUS, type SystemStatus } from "@/lib/api";
import { useEffect, useState } from "react";

export function useBackend() {
  const [url, setUrl] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setUrl(getApiUrl());
    setReady(true);
  }, []);
  return { url, ready, connected: !!url };
}

export function useSystemStatus() {
  const { connected, ready } = useBackend();
  const query = useQuery<SystemStatus>({
    queryKey: ["system-status"],
    queryFn: () => api.systemStatus(),
    enabled: ready && connected,
    refetchInterval: 10000,
    retry: 1,
  });
  return {
    status: connected ? query.data : DEMO_STATUS,
    isDemo: ready && !connected,
    isError: query.isError,
    ready,
  };
}
