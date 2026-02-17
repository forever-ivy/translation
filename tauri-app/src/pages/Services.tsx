import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore, type ServiceStatusType } from "@/stores/appStore";
import { useEffect } from "react";
import { motion } from "framer-motion";
import {
  Play,
  Square,
  RotateCcw,
  FileText,
  CheckCircle2,
  AlertCircle,
  XCircle,
  Circle,
  Settings,
  Terminal,
  RefreshCw,
} from "lucide-react";

const preflightItems = [
  { name: "Python", key: "python" },
  { name: "venv", key: "venv" },
  { name: "requirements", key: "requirements" },
  { name: ".env.v4.local", key: "env" },
  { name: "OpenClaw", key: "openclaw" },
  { name: "LibreOffice", key: "libreoffice", optional: true },
];

const statusIcons: Record<ServiceStatusType, React.ReactNode> = {
  running: <CheckCircle2 className="h-4 w-4 text-green-400" />,
  stopped: <Square className="h-4 w-4 text-gray-400" />,
  degraded: <AlertCircle className="h-4 w-4 text-yellow-400" />,
  unknown: <Circle className="h-4 w-4 text-gray-500" />,
};

const preflightVariants = {
  hidden: { opacity: 0, scale: 0.9 },
  show: (i: number) => ({
    opacity: 1,
    scale: 1,
    transition: { delay: i * 0.05 }
  })
};

export function Services() {
  const {
    services,
    preflightChecks,
    isLoading,
    isRefreshing,
    refreshCurrentPage,
    fetchServices,
    fetchPreflightChecks,
    autoFixPreflight,
    startOpenclaw,
    startServices,
    stopServices,
    restartServices,
    setActiveTab,
  } = useAppStore();

  useEffect(() => {
    fetchServices();
    fetchPreflightChecks();
  }, [fetchServices, fetchPreflightChecks]);

  const getPreflightStatus = (key: string, optional?: boolean) => {
    const check = preflightChecks.find((c) => c.key === key);
    if (!check && optional) return "warning";
    if (!check) return "blocker";
    return check.status;
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold">Services</h2>
        <p className="text-muted-foreground">Manage system services and pre-flight checks</p>
      </div>

      {/* Pre-flight Check */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <Card variant="glass">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Terminal className="h-4 w-4" />
              Pre-flight Check
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4 mb-4">
              {preflightItems.map((item, i) => {
                const status = getPreflightStatus(item.key, item.optional);

                return (
                  <motion.div
                    key={item.key}
                    custom={i}
                    variants={preflightVariants}
                    initial="hidden"
                    animate="show"
                    whileHover={{ scale: 1.02, backgroundColor: "var(--surface-hover, rgba(0,0,0,0.02))" }}
                    className="flex items-center gap-2 p-2 rounded-xl border cursor-default"
                  >
                    {status === "pass" && (
                      <motion.div
                        initial={{ scale: 0 }}
                        animate={{ scale: 1 }}
                        transition={{ type: "spring", stiffness: 500 }}
                      >
                        <CheckCircle2 className="h-4 w-4 text-green-400" />
                      </motion.div>
                    )}
                    {status === "warning" && <AlertCircle className="h-4 w-4 text-yellow-400" />}
                    {status === "blocker" && <XCircle className="h-4 w-4 text-red-400" />}
                    <span className="text-sm">{item.name}</span>
                    {item.optional && (
                      <Badge variant="outline" className="text-xs">
                        optional
                      </Badge>
                    )}
                  </motion.div>
                );
              })}
            </div>
            <div className="flex gap-2">
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Button variant="outline" size="sm" onClick={fetchPreflightChecks} disabled={isRefreshing}>
                  <RotateCcw className={`h-4 w-4 mr-2 ${isRefreshing ? "animate-spin" : ""}`} />
                  Run Pre-flight Check
                </Button>
              </motion.div>
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Button variant="secondary" size="sm" onClick={autoFixPreflight} disabled={isLoading}>
                  Auto Fix All
                </Button>
              </motion.div>
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Button size="sm" onClick={startOpenclaw} disabled={isLoading}>
                  <Play className="h-4 w-4 mr-2" />
                  Start OpenClaw
                </Button>
              </motion.div>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Service Control */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.1 }}
      >
        <Card variant="glass">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Settings className="h-4 w-4" />
              Service Control
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {services.map((service, index) => (
              <motion.div
                key={service.name}
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: index * 0.1 }}
                whileHover={{ backgroundColor: "var(--surface-hover, rgba(0,0,0,0.02))" }}
                className="flex items-center justify-between p-4 rounded-xl border"
              >
                <div className="flex items-center gap-4">
                  <motion.div
                    animate={service.status === "running" ? { scale: [1, 1.1, 1] } : {}}
                    transition={{ duration: 2, repeat: Infinity }}
                  >
                    {statusIcons[service.status]}
                  </motion.div>
                  <div>
                    <p className="font-medium">{service.name}</p>
                    <p className="text-sm text-muted-foreground">
                      {service.status === "running" ? (
                        <>
                          Running • PID: {service.pid} • Uptime: {service.uptime || "0m"}
                        </>
                      ) : (
                        <span className="capitalize">{service.status}</span>
                      )}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-sm text-muted-foreground">Restarts: {service.restarts}</span>
                  <div className="flex gap-2">
                    {service.status === "running" ? (
                      <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
                        <Button variant="outline" size="sm" onClick={stopServices} disabled={isLoading}>
                          <Square className="h-4 w-4 mr-1" />
                          Stop
                        </Button>
                      </motion.div>
                    ) : (
                      <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
                        <Button size="sm" onClick={startServices} disabled={isLoading}>
                          <Play className="h-4 w-4 mr-1" />
                          Start
                        </Button>
                      </motion.div>
                    )}
                    <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          useAppStore.getState().setSelectedLogService(
                            service.name === "Telegram Bot" ? "telegram" : "worker"
                          );
                          setActiveTab("logs");
                        }}
                      >
                        <FileText className="h-4 w-4" />
                      </Button>
                    </motion.div>
                  </div>
                </div>
              </motion.div>
            ))}
          </CardContent>
        </Card>
      </motion.div>

      {/* Global Controls */}
      <motion.div
        className="flex items-center gap-4 flex-wrap"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
      >
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
      </motion.div>

      {/* Auto-restart Settings */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
      >
        <Card variant="glass">
          <CardHeader>
            <CardTitle className="text-sm">Auto-restart Settings</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-6 flex-wrap">
              <label className="flex items-center gap-2 cursor-pointer">
                <motion.input
                  type="checkbox"
                  defaultChecked
                  className="rounded"
                  whileTap={{ scale: 0.9 }}
                />
                <span className="text-sm">Enable auto-restart</span>
              </label>
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Delay:</span>
                <motion.input
                  type="number"
                  defaultValue={5}
                  className="w-16 px-2 py-1 border rounded-xl text-sm bg-background"
                  whileFocus={{ scale: 1.02 }}
                />
                <span className="text-sm text-muted-foreground">seconds</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Max:</span>
                <motion.input
                  type="number"
                  defaultValue={3}
                  className="w-16 px-2 py-1 border rounded-xl text-sm bg-background"
                  whileFocus={{ scale: 1.02 }}
                />
                <span className="text-sm text-muted-foreground">times</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
