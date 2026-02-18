import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore } from "@/stores/appStore";
import { useEffect, useMemo, useState } from "react";
import {
  Database,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Clock,
  Activity,
} from "lucide-react";

export function KBHealth() {
  const { kbSyncReport, kbStats, fetchKbSyncReport, fetchKbStats, syncKbNow, isLoading } = useAppStore();
  const [isSyncing, setIsSyncing] = useState(false);

  useEffect(() => {
    fetchKbSyncReport();
    fetchKbStats();
  }, [fetchKbSyncReport, fetchKbStats]);

  const totalDocs = kbStats?.totalFiles ?? 0;
  const lastSync = kbSyncReport?.indexedAt || kbStats?.lastIndexedAt || "—";
  const errorCount = kbSyncReport?.errors?.length ?? 0;

  const byGroup = useMemo(() => {
    return kbStats?.bySourceGroup ?? [];
  }, [kbStats?.bySourceGroup]);

  const handleSyncNow = async () => {
    if (isSyncing) return;
    setIsSyncing(true);
    try {
      await syncKbNow();
      await Promise.all([fetchKbSyncReport(), fetchKbStats()]);
    } finally {
      setIsSyncing(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">KB Health</h2>
          <p className="text-muted-foreground">Knowledge base and RAG status</p>
        </div>
        <Button variant="outline" onClick={handleSyncNow} disabled={isLoading || isSyncing}>
          <RefreshCw className={`h-4 w-4 mr-2 ${isLoading || isSyncing ? "animate-spin" : ""}`} />
          Sync Now
        </Button>
      </div>

      {/* Latest Sync Status */}
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
                    Run <span className="font-mono">Sync Now</span> to generate <span className="font-mono">kb_sync_latest.json</span>.
                  </p>
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Stats Grid */}
      <div className="grid grid-cols-3 gap-4">
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <Clock className="h-8 w-8 text-blue-500" />
            <div>
              <p className="text-sm text-muted-foreground">Last Sync</p>
              <p className="font-medium">{lastSync}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <Database className="h-8 w-8 text-purple-500" />
            <div>
              <p className="text-sm text-muted-foreground">Total Documents</p>
              <p className="font-medium">{totalDocs}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <Activity className="h-8 w-8 text-green-500" />
            <div>
              <p className="text-sm text-muted-foreground">Errors (last sync)</p>
              <p className="font-medium">{errorCount}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Sync Summary */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Activity className="h-4 w-4" />
            Sync Summary
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!kbSyncReport ? (
            <p className="text-sm text-muted-foreground">No sync report available.</p>
          ) : (
            <div className="grid grid-cols-4 gap-3">
              <div className="p-3 rounded-lg border">
                <p className="text-xs text-muted-foreground">Created</p>
                <p className="text-lg font-medium">{kbSyncReport.created}</p>
              </div>
              <div className="p-3 rounded-lg border">
                <p className="text-xs text-muted-foreground">Updated</p>
                <p className="text-lg font-medium">{kbSyncReport.updated}</p>
              </div>
              <div className="p-3 rounded-lg border">
                <p className="text-xs text-muted-foreground">Skipped</p>
                <p className="text-lg font-medium">{kbSyncReport.skipped}</p>
              </div>
              <div className="p-3 rounded-lg border">
                <p className="text-xs text-muted-foreground">Scanned</p>
                <p className="text-lg font-medium">{kbSyncReport.scannedCount}</p>
              </div>

              <div className="p-3 rounded-lg border">
                <p className="text-xs text-muted-foreground">Unscoped Skipped</p>
                <p className="text-lg font-medium">{kbSyncReport.unscopedSkipped}</p>
              </div>
              <div className="p-3 rounded-lg border">
                <p className="text-xs text-muted-foreground">Metadata Only</p>
                <p className="text-lg font-medium">{kbSyncReport.metadataOnly}</p>
              </div>
              <div className="p-3 rounded-lg border">
                <p className="text-xs text-muted-foreground">Removed</p>
                <p className="text-lg font-medium">{kbSyncReport.removed}</p>
              </div>
              <div className="p-3 rounded-lg border">
                <p className="text-xs text-muted-foreground">Errors</p>
                <p className="text-lg font-medium">{errorCount}</p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* KB by Source Group */}
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
    </div>
  );
}
