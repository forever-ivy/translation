import { create } from "zustand";
import * as tauri from "@/lib/tauri";
import { useUiStore } from "@/stores/uiStore";
import type { AppConfig } from "@/stores/types";

interface ConfigStoreState {
  config: AppConfig | null;
  setConfig: (config: AppConfig) => void;
  fetchConfig: () => Promise<void>;
  saveConfig: (config: AppConfig) => Promise<void>;
}

export const useConfigStore = create<ConfigStoreState>((set) => ({
  config: null,
  setConfig: (config) => set({ config }),

  fetchConfig: async () => {
    try {
      const config = await tauri.getConfig();
      set({
        config: {
          workRoot: config.work_root,
          kbRoot: config.kb_root,
          strictRouter: config.strict_router,
          requireNew: config.require_new,
          ragBackend: config.rag_backend,
        },
      });
    } catch (error) {
      useUiStore.getState().addToast("error", `Failed to fetch config: ${error}`);
    }
  },

  saveConfig: async (config) => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      await tauri.saveConfig({
        work_root: config.workRoot,
        kb_root: config.kbRoot,
        strict_router: config.strictRouter,
        require_new: config.requireNew,
        rag_backend: config.ragBackend,
      });
      set({ config });
      ui.addToast("success", "Settings saved successfully");
    } catch (error) {
      ui.addToast("error", `Failed to save config: ${error}`);
      throw error;
    } finally {
      ui.setIsLoading(false);
    }
  },
}));
