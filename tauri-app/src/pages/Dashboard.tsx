import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore, type ServiceStatusType } from "@/stores/appStore";
import * as tauri from "@/lib/tauri";
import { useEffect } from "react";
import { motion } from "framer-motion";
import { GlassMotionCard, StatusPulse, CountUp, staggerContainer, staggerItem } from "@/components/ui/motion";
import {
  Play,
  Square,
  RotateCcw,
  RefreshCw,
  FolderOpen,
  FileText,
  CheckCircle2,
  AlertCircle,
  Circle,
  Container,
} from "lucide-react";

const statusColors: Record<ServiceStatusType, "green" | "gray" | "yellow" | "gray"> = {
  running: "green",
  stopped: "gray",
  degraded: "yellow",
  unknown: "gray",
};

const statusIcons: Record<ServiceStatusType, React.ReactNode> = {
  running: <CheckCircle2 className="h-4 w-4 text-green-400" />,
  stopped: <Square className="h-4 w-4 text-gray-400" />,
  degraded: <AlertCircle className="h-4 w-4 text-yellow-400" />,
  unknown: <Circle className="h-4 w-4 text-gray-500" />,
};

export function Dashboard() {
  const {
    services, jobs, isLoading, isRefreshing,
    refreshCurrentPage, fetchServices, fetchJobs, startServices, stopServices, restartServices,
    dockerContainers, startDocker, stopDocker, fetchDockerStatus,
  } = useAppStore();

  useEffect(() => {
    fetchServices();
    fetchJobs();
    fetchDockerStatus();
    const interval = setInterval(() => {
      fetchServices();
      fetchDockerStatus();
    }, 10000);
    return () => clearInterval(interval);
  }, [fetchServices, fetchJobs, fetchDockerStatus]);

  const allRunning = services.every((s) => s.status === "running");
  const anyRunning = services.some((s) => s.status === "running");

  const dockerAllRunning = dockerContainers.length > 0 && dockerContainers.every((c) => c.status === "running");
  const dockerAnyRunning = dockerContainers.some((c) => c.status === "running");

  const handleOpenVerifyFolder = async () => {
    try {
      const path = await tauri.getVerifyFolderPath();
      await tauri.openInFinder(path);
    } catch (err) {
      console.error("Failed to open verify folder:", err);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Dashboard</h2>
          <p className="text-muted-foreground">System overview and quick actions</p>
        </div>
        <div className="flex items-center gap-2">
          {allRunning ? (
            <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>
              <Badge variant="success" className="flex items-center gap-1">
                <CheckCircle2 className="h-3 w-3" />
                All Running
              </Badge>
            </motion.div>
          ) : anyRunning ? (
            <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>
              <Badge variant="warning" className="flex items-center gap-1">
                <AlertCircle className="h-3 w-3" />
                Partial
              </Badge>
            </motion.div>
          ) : (
            <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>
              <Badge variant="secondary" className="flex items-center gap-1">
                <Square className="h-3 w-3" />
                Stopped
              </Badge>
            </motion.div>
          )}
        </div>
      </div>

      {/* Service Status Cards */}
      <motion.div
        className="grid grid-cols-2 gap-4"
        variants={staggerContainer}
        initial="hidden"
        animate="show"
      >
        {services.map((service) => (
          <motion.div key={service.name} variants={staggerItem}>
            <GlassMotionCard glowColor={service.status === "running" ? "teal" : undefined}>
              <Card variant="glass" className="h-full gradient-border">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium flex items-center justify-between">
                    {service.name}
                    {statusIcons[service.status]}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <StatusPulse color={statusColors[service.status]} glow={service.status === "running"} />
                    {service.status === "running" ? (
                      <span>
                        Running • PID: <CountUp value={service.pid || "-"} /> • Uptime: {service.uptime || "0m"}
                      </span>
                    ) : (
                      <span className="capitalize">{service.status}</span>
                    )}
                  </div>
                </CardContent>
              </Card>
            </GlassMotionCard>
          </motion.div>
        ))}
      </motion.div>

      {/* Quick Actions */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        <Card variant="glass">
          <CardHeader>
            <CardTitle className="text-sm">Quick Actions</CardTitle>
          </CardHeader>
          <CardContent className="flex gap-2 flex-wrap">
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
              <Button onClick={startServices} disabled={isLoading}>
                <Play className="h-4 w-4 mr-2" />
                Start All
              </Button>
            </motion.div>
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
              <Button variant="outline" onClick={stopServices} disabled={isLoading}>
                <Square className="h-4 w-4 mr-2" />
                Stop All
              </Button>
            </motion.div>
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
              <Button variant="outline" onClick={restartServices} disabled={isLoading}>
                <RotateCcw className="h-4 w-4 mr-2" />
                Restart All
              </Button>
            </motion.div>
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
              <Button variant="secondary" onClick={refreshCurrentPage} disabled={isRefreshing}>
                <RefreshCw className={`h-4 w-4 mr-2 ${isRefreshing ? "animate-spin" : ""}`} />
                Refresh
              </Button>
            </motion.div>
          </CardContent>
        </Card>
      </motion.div>

      {/* ClawRAG Docker Containers */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.25 }}
      >
        <Card variant="glass">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              <Container className="h-4 w-4" />
              ClawRAG Docker
            </CardTitle>
            <div className="flex items-center gap-2">
              {dockerAllRunning ? (
                <Badge variant="success" className="text-[10px]">All Up</Badge>
              ) : dockerAnyRunning ? (
                <Badge variant="warning" className="text-[10px]">Partial</Badge>
              ) : dockerContainers.length > 0 ? (
                <Badge variant="destructive" className="text-[10px]">Down</Badge>
              ) : null}
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {dockerContainers.length === 0 ? (
              <p className="text-sm text-muted-foreground">Docker not available or containers not found</p>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {dockerContainers.map((c) => (
                  <motion.div
                    key={c.name}
                    className="flex items-center gap-2 p-2 rounded-xl border border-border/50"
                    whileHover={{ backgroundColor: "var(--surface-hover)" }}
                  >
                    <StatusPulse
                      color={c.status === "running" ? "green" : "gray"}
                      glow={c.status === "running"}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium truncate">
                        {c.name.replace("clawrag-", "")}
                      </p>
                      <p className="text-[10px] text-muted-foreground capitalize">{c.status}</p>
                    </div>
                  </motion.div>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Button
                  size="sm"
                  onClick={startDocker}
                  disabled={isLoading || dockerAllRunning}
                >
                  <Play className="h-3.5 w-3.5 mr-1.5" />
                  Start ClawRAG
                </Button>
              </motion.div>
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={stopDocker}
                  disabled={isLoading || !dockerAnyRunning}
                >
                  <Square className="h-3.5 w-3.5 mr-1.5" />
                  Stop ClawRAG
                </Button>
              </motion.div>
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Button variant="secondary" size="sm" onClick={fetchDockerStatus}>
                  <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                  Refresh
                </Button>
              </motion.div>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Recent Jobs */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
      >
        <Card variant="glass">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm">Recent Jobs</CardTitle>
            <Button variant="ghost" size="sm" onClick={() => useAppStore.getState().setActiveTab("jobs")}>
              View All →
            </Button>
          </CardHeader>
          <CardContent>
            {jobs.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p>No recent jobs</p>
              </div>
            ) : (
              <motion.div
                className="space-y-2"
                variants={staggerContainer}
                initial="hidden"
                animate="show"
              >
                {jobs.slice(0, 5).map((job) => (
                  <motion.div
                    key={job.jobId}
                    variants={staggerItem}
                    className="flex items-center justify-between p-2 rounded-xl hover:bg-muted/50 cursor-pointer transition-colors"
                    onClick={() => {
                      useAppStore.getState().setSelectedJobId(job.jobId);
                      useAppStore.getState().setActiveTab("jobs");
                    }}
                    whileHover={{ x: 4 }}
                  >
                    <div className="flex items-center gap-3">
                      <FileText className="h-4 w-4 text-muted-foreground" />
                      <div>
                        <p className="text-sm font-medium">{job.jobId}</p>
                        <p className="text-xs text-muted-foreground">{job.taskType}</p>
                      </div>
                    </div>
                    <Badge variant={job.status === "review_ready" ? "success" : "secondary"}>{job.status}</Badge>
                  </motion.div>
                ))}
              </motion.div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {/* Utility Buttons */}
      <motion.div
        className="flex gap-2 flex-wrap"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.4 }}
      >
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button variant="outline" onClick={handleOpenVerifyFolder}>
            <FolderOpen className="h-4 w-4 mr-2" />
            Open Verify Folder
          </Button>
        </motion.div>
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button variant="outline" disabled>
            <FileText className="h-4 w-4 mr-2" />
            Export Diagnostics
          </Button>
        </motion.div>
      </motion.div>
    </div>
  );
}
