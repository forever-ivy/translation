import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/appStore";
import { useEffect, useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { CountUp, staggerContainer, staggerItem } from "@/components/ui/motion";
import { Search, Download, AlertTriangle, AlertCircle, RefreshCw, FileText } from "lucide-react";

const levelColors: Record<string, string> = {
  INFO: "text-blue-500",
  WARN: "text-yellow-500",
  ERROR: "text-red-500",
  DEBUG: "text-gray-400",
};

export function Logs() {
  const { logs, selectedLogService, fetchLogs, isLoading } = useAppStore();
  const [filter, setFilter] = useState("");
  const [levelFilter, setLevelFilter] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [newLogCount, setNewLogCount] = useState(0);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const prevLogsLengthRef = useRef(logs.length);

  useEffect(() => {
    fetchLogs(selectedLogService, 200);
  }, [fetchLogs, selectedLogService]);

  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  // Track new logs
  useEffect(() => {
    if (logs.length > prevLogsLengthRef.current) {
      setNewLogCount((c) => c + (logs.length - prevLogsLengthRef.current));
    }
    prevLogsLengthRef.current = logs.length;
  }, [logs]);

  // Auto-refresh logs every 5 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      fetchLogs(selectedLogService, 200);
    }, 5000);
    return () => clearInterval(interval);
  }, [fetchLogs, selectedLogService]);

  const filteredLogs = logs.filter((log) => {
    if (levelFilter && log.level !== levelFilter) return false;
    if (filter && !log.message.toLowerCase().includes(filter.toLowerCase())) return false;
    return true;
  });

  const errorCount = logs.filter((l) => l.level === "ERROR").length;
  const warnCount = logs.filter((l) => l.level === "WARN").length;

  const handleExport = () => {
    const content = logs.map((l) => `${l.time} [${l.level}] [${l.service}] ${l.message}`).join("\n");
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedLogService}-logs-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleRefresh = () => {
    fetchLogs(selectedLogService, 200);
    setNewLogCount(0);
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Logs</h2>
          <p className="text-muted-foreground">Real-time system logs and diagnostics</p>
        </div>
        <div className="flex gap-2 items-center">
          <select
            className="px-3 py-1.5 border rounded-lg text-sm bg-background text-foreground"
            value={selectedLogService}
            onChange={(e) => useAppStore.getState().setSelectedLogService(e.target.value)}
          >
            <option value="telegram">Telegram Bot</option>
            <option value="worker">Run Worker</option>
          </select>
          <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
            <Button variant="outline" onClick={handleExport} disabled={logs.length === 0}>
              <Download className="h-4 w-4 mr-2" />
              Export
            </Button>
          </motion.div>
        </div>
      </div>

      {/* Error Summary */}
      <motion.div
        className="grid grid-cols-2 gap-4"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <motion.div
          whileHover={{ scale: 1.02, boxShadow: "0 4px 20px rgba(239, 68, 68, 0.15)" }}
          transition={{ duration: 0.2 }}
        >
          <Card variant="glass" className={errorCount > 0 ? "border-red-500/30" : ""}>
            <CardContent className="flex items-center gap-4 p-4">
              <motion.div
                animate={errorCount > 0 ? { scale: [1, 1.1, 1] } : {}}
                transition={{ duration: 1, repeat: Infinity }}
              >
                <AlertCircle className={`h-8 w-8 ${errorCount > 0 ? "text-red-500" : "text-gray-400"}`} />
              </motion.div>
              <div>
                <p className="text-2xl font-bold">
                  <CountUp value={errorCount} />
                </p>
                <p className="text-sm text-muted-foreground">Errors (current view)</p>
              </div>
            </CardContent>
          </Card>
        </motion.div>
        <motion.div
          whileHover={{ scale: 1.02, boxShadow: "0 4px 20px rgba(234, 179, 8, 0.15)" }}
          transition={{ duration: 0.2 }}
        >
          <Card variant="glass" className={warnCount > 0 ? "border-yellow-500/30" : ""}>
            <CardContent className="flex items-center gap-4 p-4">
              <motion.div
                animate={warnCount > 0 ? { rotate: [0, 10, -10, 0] } : {}}
                transition={{ duration: 0.5, repeat: Infinity, repeatDelay: 2 }}
              >
                <AlertTriangle className={`h-8 w-8 ${warnCount > 0 ? "text-yellow-500" : "text-gray-400"}`} />
              </motion.div>
              <div>
                <p className="text-2xl font-bold">
                  <CountUp value={warnCount} />
                </p>
                <p className="text-sm text-muted-foreground">Warnings (current view)</p>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </motion.div>

      {/* Filters */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
      >
        <Card>
          <CardContent className="flex items-center gap-4 p-4 flex-wrap">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <motion.input
                type="text"
                placeholder="Search logs..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="w-full pl-9 pr-4 py-2 border rounded-lg text-sm bg-background text-foreground focus:ring-2 focus:ring-primary focus:border-transparent transition-all"
                whileFocus={{ scale: 1.01 }}
              />
            </div>
            <div className="flex gap-1">
              {["INFO", "WARN", "ERROR"].map((level) => (
                <motion.div key={level} whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
                  <Button
                    variant={levelFilter === level ? "default" : "outline"}
                    size="sm"
                    onClick={() => setLevelFilter(levelFilter === level ? null : level)}
                  >
                    {level}
                  </Button>
                </motion.div>
              ))}
            </div>
            <motion.div whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }}>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleRefresh}
                disabled={isLoading}
              >
                <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
              </Button>
            </motion.div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <motion.input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="rounded"
                whileTap={{ scale: 0.9 }}
              />
              Auto-scroll
            </label>
          </CardContent>
        </Card>
      </motion.div>

      {/* Log Stream */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <FileText className="h-4 w-4" />
              Log Stream
              <span className="text-xs text-muted-foreground font-normal">
                {filteredLogs.length} entries
              </span>
              <AnimatePresence>
                {newLogCount > 0 && (
                  <motion.span
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0, opacity: 0 }}
                    className="text-xs bg-primary text-primary-foreground px-2 py-0.5 rounded-full"
                  >
                    +{newLogCount} new
                  </motion.span>
                )}
              </AnimatePresence>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {filteredLogs.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p>No logs available</p>
                <p className="text-sm text-muted-foreground/70 mt-1">
                  Logs will appear here when the service is running
                </p>
              </div>
            ) : (
              <div
                ref={logContainerRef}
                className="font-mono text-xs space-y-1 max-h-96 overflow-auto bg-card/50 backdrop-blur-sm rounded-lg p-4 border border-border/50"
              >
                <AnimatePresence initial={false}>
                  {filteredLogs.map((log, i) => (
                    <motion.div
                      key={`${log.time}-${i}`}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: Math.min(i * 0.01, 0.5) }}
                      className={`flex gap-2 py-0.5 hover:bg-muted/50 rounded px-1 ${
                        log.level === "ERROR" ? "bg-red-500/5" : log.level === "WARN" ? "bg-yellow-500/5" : ""
                      }`}
                    >
                      <span className="text-muted-foreground whitespace-nowrap">{log.time}</span>
                      <span className={`font-medium whitespace-nowrap ${levelColors[log.level] || "text-gray-400"}`}>
                        [{log.level}]
                      </span>
                      <span className="text-blue-500 whitespace-nowrap">[{log.service}]</span>
                      <span className="text-foreground">{log.message}</span>
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {/* Troubleshooting Guide */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
      >
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <AlertCircle className="h-4 w-4" />
              Common Issues
            </CardTitle>
          </CardHeader>
          <CardContent>
            <motion.div
              className="space-y-2"
              variants={staggerContainer}
              initial="hidden"
              animate="show"
            >
              {[
                { title: "RAG backend not available", desc: "Check if ClawRAG is running: openclaw health --json" },
                { title: "Gemini API rate limit", desc: "System will auto-retry with fallback model" },
                { title: "Telegram connection lost", desc: "Check TELEGRAM_BOT_TOKEN in .env.v4.local" },
              ].map((item, i) => (
                <motion.div
                  key={i}
                  variants={staggerItem}
                  whileHover={{ scale: 1.01, backgroundColor: "var(--surface-hover)" }}
                  className="p-3 rounded-lg border cursor-pointer transition-colors"
                >
                  <p className="font-medium text-sm">{item.title}</p>
                  <p className="text-xs text-muted-foreground">{item.desc}</p>
                </motion.div>
              ))}
            </motion.div>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
