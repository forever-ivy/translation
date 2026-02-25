import { create } from "zustand";
import * as tauri from "@/lib/tauri";

const logsFetchInFlight: Record<string, boolean> = {};

interface LogEntry {
  time: string;
  level: string;
  service: string;
  message: string;
}

interface LogStoreState {
  logs: LogEntry[];
  selectedLogService: string;
  setSelectedLogService: (service: string) => void;
  fetchLogs: (service: string, lines?: number) => Promise<void>;
  refreshLogsData: (opts?: { silent?: boolean; lines?: number }) => Promise<void>;
}

function normalizeLevel(level: string): string {
  const up = (level || "").trim().toUpperCase();
  if (up === "WARNING") return "WARN";
  if (up === "CRITICAL" || up === "FATAL") return "ERROR";
  return up || "INFO";
}

function parseLogLine(line: string, service: string): LogEntry {
  const trimmed = String(line || "").trim();
  if (trimmed.startsWith("{")) {
    return { time: "", level: "INFO", service, message: line };
  }

  const m1 = line.match(
    /^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+\[([^\]]+)\]\s+([A-Z]+)\s+(.*)$/
  );
  if (m1) {
    return {
      time: m1[2],
      level: normalizeLevel(m1[4]),
      service: m1[3] || service,
      message: m1[5],
    };
  }

  const m2 = line.match(/^(\d{4}-\d{2}-\d{2}\s+)?(\d{2}:\d{2}:\d{2})\s*\[([A-Z]+)\]\s*(.*)$/);
  if (m2) {
    return {
      time: m2[2],
      level: normalizeLevel(m2[3]),
      service,
      message: m2[4],
    };
  }

  return { time: "", level: "INFO", service, message: line };
}

export const useLogStore = create<LogStoreState>((set, get) => ({
  logs: [],
  selectedLogService: "telegram",

  setSelectedLogService: (selectedLogService) => set({ selectedLogService }),

  fetchLogs: async (service, lines = 100) => {
    const key = `${service}:${lines}`;
    if (logsFetchInFlight[key]) return;
    logsFetchInFlight[key] = true;
    try {
      const logLines = await tauri.readLogFile(service, lines);
      set({
        logs: logLines.map((line) => parseLogLine(line, service)),
        selectedLogService: service,
      });
    } catch (error) {
      console.warn(`fetchLogs failed for ${service}:`, error);
    } finally {
      logsFetchInFlight[key] = false;
    }
  },

  refreshLogsData: async (opts) => {
    const service = get().selectedLogService;
    await get().fetchLogs(service, opts?.lines ?? 200);
  },
}));
