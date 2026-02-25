import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useConfigStore } from "@/stores/configStore";
import { useKbStore } from "@/stores/kbStore";
import { useUiStore } from "@/stores/uiStore";
import { useEffect, useMemo, useState } from "react";
import * as tauri from "@/lib/tauri";
import {
  Database,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Clock,
  Activity,
  FolderOpen,
} from "lucide-react";

export function KBHealth() {
  const kbSyncReport = useKbStore((s) => s.kbSyncReport);
  const kbStats = useKbStore((s) => s.kbStats);
  const config = useConfigStore((s) => s.config);
  const fetchConfig = useConfigStore((s) => s.fetchConfig);
  const fetchKbSyncReport = useKbStore((s) => s.fetchKbSyncReport);
  const fetchKbStats = useKbStore((s) => s.fetchKbStats);
  const syncKbNow = useKbStore((s) => s.syncKbNow);
  const isLoading = useUiStore((s) => s.isLoading);
  const [isSyncing, setIsSyncing] = useState(false);

  useEffect(() => {
    void fetchConfig();
    void fetchKbSyncReport();
    void fetchKbStats();
  }, [fetchConfig, fetchKbSyncReport, fetchKbStats]);

  const kbRoot = config?.kbRoot || kbSyncReport?.kbRoot || "";

  const totalDocs = kbStats?.totalFiles ?? 0;
  const totalChunks = kbStats?.totalChunks ?? 0;
  const lastIndexedAt = kbSyncReport?.indexedAt || kbStats?.lastIndexedAt || "—";
  const errorCount = kbSyncReport?.errors?.length ?? 0;

  const byGroup = useMemo(() => kbStats?.bySourceGroup ?? [], [kbStats?.bySourceGroup]);
  const errorsPreview = (kbSyncReport?.errors || []).slice(0, 10);

  const handleRefresh = async () => {
    await Promise.all([fetchConfig(), fetchKbSyncReport(), fetchKbStats()]);
  };

  const handleSyncNow = async () => {
    if (isSyncing) return;
    setIsSyncing(true);
    try {
      await syncKbNow();
      await handleRefresh();
    } finally {
      setIsSyncing(false);
    }
  };

  const openKbRoot = async () => {
    if (!kbRoot) return;
    try {
      await tauri.openInFinder(kbRoot);
    } catch (err) {
      console.error("Failed to open KB root:", err);
    }
  };

  const openLatestSyncReport = async () => {
    if (!kbRoot) return;
    try {
      await tauri.openInFinder(`${kbRoot}/.system/kb/kb_sync_latest.json`);
    } catch (err) {
      console.error("Failed to open kb_sync_latest.json:", err);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">KB Health</h2>
          <p className="text-muted-foreground">Knowledge base indexing status</p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <Button variant="outline" onClick={() => void openKbRoot()} disabled={!kbRoot}>
            <FolderOpen className="h-4 w-4 mr-2" />
            Open KB Root
          </Button>
          <Button variant="outline" onClick={() => void handleSyncNow()} disabled={isLoading || isSyncing}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading || isSyncing ? "animate-spin" : ""}`} />
            Sync Now
          </Button>
          <Button variant="outline" onClick={() => void handleRefresh()} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Latest sync status */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Database className="h-4 w-4" />
            Latest KB Sync
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-4">
            {kbSyncReport ? (
              <>
                {kbSyncReport.ok ? (
                  <CheckCircle2 className="h-8 w-8 text-green-500" />
                ) : (
                  <AlertCircle className="h-8 w-8 text-yellow-500" />
                )}
                <div>
                  <p className="font-medium text-lg">{kbSyncReport.ok ? "OK" : "Completed with warnings"}</p>
                  <p className="text-sm text-muted-foreground">Indexed at: {kbSyncReport.indexedAt || "—"}</p>
                </div>
              </>
            ) : (
              <>
                <AlertCircle className="h-8 w-8 text-muted-foreground" />
                <div>
                  <p className="font-medium text-lg">No sync report</p>
                  <p className="text-sm text-muted-foreground">
                    Run <span className="font-mono">Sync Now</span> to generate{" "}
                    <span className="font-mono">kb_sync_latest.json</span>.
                  </p>
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <Clock className="h-8 w-8 text-blue-500" />
            <div>
              <p className="text-sm text-muted-foreground">Last Indexed</p>
              <p className="font-medium">{lastIndexedAt}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <Database className="h-8 w-8 text-purple-500" />
            <div>
              <p className="text-sm text-muted-foreground">Total Docs</p>
              <p className="font-medium">{totalDocs}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <Activity className="h-8 w-8 text-green-500" />
            <div>
              <p className="text-sm text-muted-foreground">Total Chunks</p>
              <p className="font-medium">{totalChunks}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <AlertCircle className="h-8 w-8 text-yellow-500" />
            <div>
              <p className="text-sm text-muted-foreground">Errors</p>
              <p className="font-medium">{errorCount}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Documents by source group */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Documents by Source Group</CardTitle>
        </CardHeader>
        <CardContent>
          {!kbStats ? (
            <p className="text-sm text-muted-foreground">KB stats unavailable.</p>
          ) : byGroup.length === 0 ? (
            <p className="text-sm text-muted-foreground">No documents indexed yet.</p>
          ) : (
            <div className="space-y-3">
              {byGroup.map((item) => {
                const pct = totalDocs > 0 ? (item.count / totalDocs) * 100 : 0;
                const color =
                  item.sourceGroup === "glossary"
                    ? "bg-purple-500"
                    : item.sourceGroup === "previously_translated"
                      ? "bg-blue-500"
                      : item.sourceGroup === "source_text"
                        ? "bg-green-500"
                        : "bg-gray-400";
                return (
                  <div key={item.sourceGroup} className="flex items-center gap-3">
                    <div className={`h-3 w-3 rounded-full ${color}`} />
                    <span className="text-sm capitalize flex-1">
                      {item.sourceGroup.replace(/_/g, " ")}
                    </span>
                    <span className="text-sm font-medium">{item.count}</span>
                    <div className="w-32 bg-muted rounded-full h-2">
                      <div className={`h-2 rounded-full ${color}`} style={{ width: `${pct}%` }} />
                    </div>
                    <Badge variant="outline" className="text-xs">
                      {item.chunkCount} chunks
                    </Badge>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Error summary */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <AlertCircle className="h-4 w-4" />
            Errors
            <Badge variant="outline" className="text-xs">
              {errorCount}
            </Badge>
          </CardTitle>
          <Button variant="outline" size="sm" onClick={() => void openLatestSyncReport()} disabled={!kbRoot}>
            <FolderOpen className="h-4 w-4 mr-2" />
            Open kb_sync_latest.json
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {!kbSyncReport ? (
            <p className="text-sm text-muted-foreground">No sync report available.</p>
          ) : errorsPreview.length === 0 ? (
            <p className="text-sm text-muted-foreground">No errors reported in the latest sync.</p>
          ) : (
            <div className="space-y-2">
              {errorsPreview.map((e, idx) => (
                <div key={idx} className="text-xs font-mono whitespace-pre-wrap break-words rounded-lg border p-3">
                  {typeof e === "string" ? e : JSON.stringify(e)}
                </div>
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            More details: <span className="font-mono">kb_sync_latest.json</span> lives in{" "}
            <span className="font-mono">KB Root/.system/kb</span>.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
