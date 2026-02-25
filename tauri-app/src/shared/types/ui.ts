import type { ToastType } from "@/components/ui/toast";

export type AppRoute =
  | "start-openclaw"
  | "jobs"
  | "verify"
  | "logs"
  | "kb-health"
  | "glossary"
  | "settings";

export interface DomainError {
  code: string;
  message: string;
  cause?: unknown;
}

export interface AsyncState<T> {
  data: T;
  loading: boolean;
  error: DomainError | null;
  updatedAt?: number;
}

export interface ToastEvent {
  id: string;
  type: ToastType;
  message: string;
  createdAt: number;
}

export interface PollingPolicy {
  route: AppRoute;
  intervalMs: number;
  onVisibleOnly?: boolean;
}
