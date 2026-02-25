import { create } from "zustand";
import * as tauri from "@/lib/tauri";
import { useServiceStore } from "@/stores/serviceStore";
import { useUiStore } from "@/stores/uiStore";
import type {
  StartupSnapshot,
  StartupStepResult,
  TelegramHealth,
} from "@/stores/types";

interface StartupStoreState {
  steps: StartupStepResult[];
  snapshot: StartupSnapshot | null;
  telegramHealth: TelegramHealth | null;
  isRunning: boolean;
  runGuidedStartup: (opts?: { forceRestart?: boolean }) => Promise<void>;
  fetchSnapshot: () => Promise<void>;
  diagnoseTelegram: () => Promise<void>;
  restartTelegram: () => Promise<void>;
  stopComponent: (name: "gateway" | "worker" | "telegram") => Promise<void>;
  restartComponent: (name: "gateway" | "worker" | "telegram") => Promise<void>;
}

export const useStartupStore = create<StartupStoreState>((set, get) => ({
  steps: [],
  snapshot: null,
  telegramHealth: null,
  isRunning: false,

  runGuidedStartup: async (opts) => {
    const ui = useUiStore.getState();
    set({ isRunning: true });
    ui.setIsLoading(true);
    try {
      const steps = await tauri.startOpenclawV2({ forceRestart: opts?.forceRestart ?? true });
      set({ steps });
      await Promise.all([get().fetchSnapshot(), useServiceStore.getState().fetchPreflightChecks()]);
      const hasFailure = steps.some((step) => step.status === "failed");
      ui.addToast(hasFailure ? "warning" : "success", hasFailure ? "Startup completed with issues" : "Startup completed");
    } catch (error) {
      ui.addToast("error", `Guided startup failed: ${error}`);
    } finally {
      set({ isRunning: false });
      ui.setIsLoading(false);
    }
  },

  fetchSnapshot: async () => {
    try {
      const snapshot = await tauri.getStartupSnapshot();
      set({ snapshot });
    } catch (error) {
      useUiStore.getState().addToast("error", `Failed to fetch startup snapshot: ${error}`);
    }
  },

  diagnoseTelegram: async () => {
    try {
      const telegramHealth = await tauri.diagnoseTelegramBot();
      set({ telegramHealth });
    } catch (error) {
      useUiStore.getState().addToast("error", `Failed to diagnose telegram bot: ${error}`);
    }
  },

  restartTelegram: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    try {
      const health = await tauri.startTelegramBotV2({ forceRestart: true });
      set({ telegramHealth: health });
      await get().fetchSnapshot();
      ui.addToast("success", "Telegram restarted");
    } catch (error) {
      ui.addToast("error", `Failed to restart telegram: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  stopComponent: async (name) => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    try {
      await tauri.stopOpenclawComponent(name);
      await get().fetchSnapshot();
      ui.addToast("success", `Stopped ${name}`);
    } catch (error) {
      ui.addToast("error", `Failed to stop ${name}: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  restartComponent: async (name) => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    try {
      await tauri.restartOpenclawComponent(name);
      await get().fetchSnapshot();
      ui.addToast("success", `Restarted ${name}`);
    } catch (error) {
      ui.addToast("error", `Failed to restart ${name}: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },
}));
