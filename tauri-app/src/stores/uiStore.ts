import { create } from "zustand";
import type { ToastItem, ToastType } from "@/components/ui/toast";

interface UiState {
  isLoading: boolean;
  error: string | null;
  sidebarCollapsed: boolean;
  isRefreshing: boolean;
  toasts: ToastItem[];
  theme: "light" | "dark" | "system";
  setIsLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setIsRefreshing: (refreshing: boolean) => void;
  addToast: (type: ToastType, message: string) => void;
  dismissToast: (id: string) => void;
  setTheme: (theme: "light" | "dark" | "system") => void;
}

export const useUiStore = create<UiState>((set) => ({
  isLoading: false,
  error: null,
  sidebarCollapsed: false,
  isRefreshing: false,
  toasts: [],
  theme: (localStorage.getItem("theme") as "light" | "dark" | "system") || "system",
  setIsLoading: (isLoading) => set({ isLoading }),
  setError: (error) => set({ error }),
  setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
  setIsRefreshing: (isRefreshing) => set({ isRefreshing }),
  addToast: (type, message) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    set((state) => ({ toasts: [...state.toasts.slice(-4), { id, type, message }] }));
  },
  dismissToast: (id) => {
    set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) }));
  },
  setTheme: (theme) => {
    localStorage.setItem("theme", theme);
    set({ theme });
  },
}));
