import { create } from "zustand";
import * as tauri from "@/lib/tauri";
import { useUiStore } from "@/stores/uiStore";
import type { KbStats, KbSyncReport } from "@/stores/types";

function mapSyncReport(report: tauri.KbSyncReport): KbSyncReport {
  return {
    ok: report.ok,
    kbRoot: report.kb_root,
    scannedCount: report.scanned_count,
    created: report.created,
    updated: report.updated,
    metadataOnly: report.metadata_only,
    metadataOnlyPaths: report.metadata_only_paths || [],
    unscopedSkipped: report.unscoped_skipped,
    unscopedSkippedPaths: report.unscoped_skipped_paths || [],
    removed: report.removed,
    removedPaths: report.removed_paths || [],
    skipped: report.skipped,
    errors: report.errors || [],
    indexedAt: report.indexed_at,
  };
}

function mapStats(stats: tauri.KbStats): KbStats {
  return {
    totalFiles: stats.total_files,
    totalChunks: stats.total_chunks,
    lastIndexedAt: stats.last_indexed_at ?? null,
    bySourceGroup: (stats.by_source_group || []).map((group) => ({
      sourceGroup: group.source_group,
      count: group.count,
      chunkCount: group.chunk_count,
    })),
  };
}

interface KbStoreState {
  kbSyncReport: KbSyncReport | null;
  kbStats: KbStats | null;
  fetchKbSyncReport: () => Promise<void>;
  fetchKbStats: () => Promise<void>;
  syncKbNow: () => Promise<void>;
}

export const useKbStore = create<KbStoreState>((set, get) => ({
  kbSyncReport: null,
  kbStats: null,

  fetchKbSyncReport: async () => {
    try {
      const report = await tauri.getKbSyncReport();
      set({ kbSyncReport: report ? mapSyncReport(report) : null });
    } catch (error) {
      useUiStore.getState().addToast("error", `Failed to fetch KB sync report: ${error}`);
    }
  },

  fetchKbStats: async () => {
    try {
      const stats = await tauri.getKbStats();
      set({ kbStats: mapStats(stats) });
    } catch (error) {
      useUiStore.getState().addToast("error", `Failed to fetch KB stats: ${error}`);
    }
  },

  syncKbNow: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      const report = await tauri.kbSyncNow();
      set({ kbSyncReport: mapSyncReport(report) });
      await get().fetchKbStats();
      ui.addToast(report.ok ? "success" : "warning", "KB sync completed");
    } catch (error) {
      ui.addToast("error", `KB sync failed: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },
}));
