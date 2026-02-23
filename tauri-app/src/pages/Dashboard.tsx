import { type ReactNode, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore, type OverviewTrendMetric } from "@/stores/appStore";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  BarChart3,
  Check,
  ClipboardList,
  Clock3,
  Copy,
  ListChecks,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Workflow,
} from "lucide-react";

type MetricCard = {
  title: string;
  value: string;
  helper: string;
  icon: ReactNode;
};

type AlertFilter = "open" | "critical" | "warning" | "acknowledged" | "all";

const trendMetricOptions: Array<{ id: OverviewTrendMetric; label: string; barClass: string; emptyState: string }> = [
  { id: "throughput", label: "Throughput", barClass: "bg-primary/70 hover:bg-primary", emptyState: "No recent throughput data." },
  { id: "failures", label: "Failures", barClass: "bg-destructive/70 hover:bg-destructive", emptyState: "No recent failure data." },
  {
    id: "review_ready",
    label: "Review Ready",
    barClass: "bg-yellow-500/70 hover:bg-yellow-500",
    emptyState: "No recent review-ready data.",
  },
];

const alertFilters: Array<{ id: AlertFilter; label: string }> = [
  { id: "open", label: "Open" },
  { id: "critical", label: "Critical" },
  { id: "warning", label: "Warning" },
  { id: "acknowledged", label: "Acknowledged" },
  { id: "all", label: "All" },
];

function toFixed(n: number | undefined, digits = 1) {
  if (typeof n !== "number" || Number.isNaN(n)) return "0";
  return n.toFixed(digits);
}

export function Dashboard() {
  const overviewMetrics = useAppStore((s) => s.overviewMetrics);
  const overviewTrends = useAppStore((s) => s.overviewTrends);
  const overviewTrendMetric = useAppStore((s) => s.overviewTrendMetric);
  const setOverviewTrendMetric = useAppStore((s) => s.setOverviewTrendMetric);
  const fetchOverviewTrends = useAppStore((s) => s.fetchOverviewTrends);
  const overviewAlerts = useAppStore((s) => s.overviewAlerts);
  const queueSnapshot = useAppStore((s) => s.queueSnapshot);
  const runSummary = useAppStore((s) => s.runSummary);
  const ackOverviewAlert = useAppStore((s) => s.ackOverviewAlert);
  const refreshCurrentPage = useAppStore((s) => s.refreshCurrentPage);
  const isRefreshing = useAppStore((s) => s.isRefreshing);
  const addToast = useAppStore((s) => s.addToast);
  const setActiveTab = useAppStore((s) => s.setActiveTab);
  const [isCopied, setIsCopied] = useState(false);
  const [alertFilter, setAlertFilter] = useState<AlertFilter>("open");

  const alertsWithoutNominal = useMemo(
    () => overviewAlerts.filter((alert) => alert.id !== "system_nominal"),
    [overviewAlerts]
  );

  const openAlerts = useMemo(
    () => alertsWithoutNominal.filter((alert) => alert.status === "open"),
    [alertsWithoutNominal]
  );

  const filteredAlerts = useMemo(() => {
    if (alertFilter === "all") return alertsWithoutNominal;
    if (alertFilter === "open") return alertsWithoutNominal.filter((alert) => alert.status === "open");
    if (alertFilter === "acknowledged") return alertsWithoutNominal.filter((alert) => alert.status === "acknowledged");
    return alertsWithoutNominal.filter((alert) => alert.status === "open" && alert.severity === alertFilter);
  }, [alertsWithoutNominal, alertFilter]);

  const metricCards: MetricCard[] = [
    {
      title: "Jobs (24h)",
      value: String(overviewMetrics?.totalJobs ?? 0),
      helper: `${overviewMetrics?.completedJobs ?? 0} completed`,
      icon: <ClipboardList className="h-4 w-4" />,
    },
    {
      title: "Success Rate",
      value: `${toFixed(overviewMetrics?.successRate)}%`,
      helper: `${overviewMetrics?.failedJobs ?? 0} failed`,
      icon: <TrendingUp className="h-4 w-4" />,
    },
    {
      title: "Avg Turnaround",
      value: `${toFixed(overviewMetrics?.avgTurnaroundMinutes)} min`,
      helper: "End-to-end processing time",
      icon: <Clock3 className="h-4 w-4" />,
    },
    {
      title: "Open Alerts",
      value: String(overviewMetrics?.openAlerts ?? openAlerts.length),
      helper: openAlerts.length > 0 ? "Action recommended" : "System stable",
      icon: <AlertTriangle className="h-4 w-4" />,
    },
    {
      title: "Backlog",
      value: String(overviewMetrics?.backlogJobs ?? 0),
      helper: "Pending + running + review",
      icon: <Workflow className="h-4 w-4" />,
    },
    {
      title: "Service Health",
      value: `${overviewMetrics?.servicesRunning ?? 0}/${overviewMetrics?.servicesTotal ?? 0}`,
      helper: "Running services",
      icon: <ShieldCheck className="h-4 w-4" />,
    },
  ];

  const trendPoints = overviewTrends.slice(-24);
  const trendMax = Math.max(1, ...trendPoints.map((p) => p.value));
  const trendTotal = trendPoints.reduce((sum, point) => sum + point.value, 0);
  const trendOption = trendMetricOptions.find((option) => option.id === overviewTrendMetric) ?? trendMetricOptions[0];

  const queueStats: Array<{
    label: string;
    value: number;
    tone: "default" | "secondary" | "warning" | "success" | "destructive";
  }> = [
    { label: "Pending", value: queueSnapshot?.pending ?? 0, tone: "secondary" as const },
    { label: "Running", value: queueSnapshot?.running ?? 0, tone: "default" as const },
    { label: "Review", value: queueSnapshot?.reviewReady ?? 0, tone: "warning" as const },
    { label: "Done", value: queueSnapshot?.done ?? 0, tone: "success" as const },
    { label: "Failed", value: queueSnapshot?.failed ?? 0, tone: "destructive" as const },
  ];

  const handleCopySummary = async () => {
    if (!runSummary?.text) return;
    try {
      await navigator.clipboard.writeText(runSummary.text);
      setIsCopied(true);
      addToast("success", "Daily summary copied");
      window.setTimeout(() => setIsCopied(false), 1500);
    } catch {
      addToast("error", "Failed to copy summary");
    }
  };

  const handleTrendMetricChange = (metric: OverviewTrendMetric) => {
    if (metric === overviewTrendMetric) return;
    setOverviewTrendMetric(metric);
    fetchOverviewTrends(metric, 24).catch(() => undefined);
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold">Operations Overview</h2>
          <p className="text-muted-foreground">A manager-friendly snapshot of workload, risk, and next steps.</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="flex items-center gap-1">
            <Sparkles className="h-3 w-3" />
            Beginner Mode
          </Badge>
          <Button variant="outline" size="sm" onClick={refreshCurrentPage} disabled={isRefreshing}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isRefreshing ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button variant="secondary" size="sm" onClick={handleCopySummary} disabled={!runSummary?.text}>
            {isCopied ? <Check className="h-4 w-4 mr-2" /> : <Copy className="h-4 w-4 mr-2" />}
            Copy Daily Summary
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {metricCards.map((metric) => (
          <motion.div key={metric.title} whileHover={{ y: -2 }}>
            <Card variant="glass">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center justify-between">
                  {metric.title}
                  <span className="text-muted-foreground">{metric.icon}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <p className="text-2xl font-semibold">{metric.value}</p>
                <p className="text-xs text-muted-foreground">{metric.helper}</p>
              </CardContent>
            </Card>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card variant="glass">
          <CardHeader className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <BarChart3 className="h-4 w-4" />
                {trendOption.label} Trend (last 24h)
              </CardTitle>
              <Button variant="ghost" size="sm" onClick={() => setActiveTab("jobs")}>
                Task Center
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              {trendMetricOptions.map((option) => (
                <Button
                  key={option.id}
                  variant={option.id === overviewTrendMetric ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => handleTrendMetricChange(option.id)}
                >
                  {option.label}
                </Button>
              ))}
            </div>
          </CardHeader>
          <CardContent>
            {trendPoints.length === 0 ? (
              <p className="text-sm text-muted-foreground">{trendOption.emptyState}</p>
            ) : (
              <div className="space-y-3">
                <div className="h-28 flex items-end gap-1">
                  {trendPoints.map((point) => {
                    const h = Math.max(8, Math.round((point.value / trendMax) * 100));
                    return (
                      <div
                        key={point.timestamp}
                        className={`flex-1 rounded-t transition-colors ${trendOption.barClass}`}
                        style={{ height: `${h}%` }}
                        title={`${point.label}: ${point.value}`}
                        aria-label={`${point.label} ${point.value}`}
                      />
                    );
                  })}
                </div>
                <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                  <span>{trendPoints[0]?.label}</span>
                  <span>Total {trendTotal}</span>
                  <span>{trendPoints[trendPoints.length - 1]?.label}</span>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card variant="glass">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              <ListChecks className="h-4 w-4" />
              Queue Board
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={() => setActiveTab("verify")}>
              Review Desk
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              {queueStats.map((queue) => (
                <div key={queue.label} className="rounded-xl border border-border/50 p-3 bg-background/40">
                  <div className="text-[10px] text-muted-foreground">{queue.label}</div>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-xl font-semibold">{queue.value}</span>
                    <Badge variant={queue.tone} className="text-[10px]">{queue.label}</Badge>
                  </div>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              Tip: focus on <strong>Review</strong> and <strong>Failed</strong> first to reduce delivery risk.
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card variant="glass">
          <CardHeader className="space-y-3">
            <div className="flex flex-row items-center justify-between">
              <CardTitle className="text-sm flex items-center gap-2">
                <AlertTriangle className="h-4 w-4" />
                Alert Center
              </CardTitle>
              <Badge variant={openAlerts.length > 0 ? "warning" : "success"}>
                {openAlerts.length > 0 ? `${openAlerts.length} Open` : "Healthy"}
              </Badge>
            </div>
            <div className="flex flex-wrap gap-2">
              {alertFilters.map((filter) => (
                <Button
                  key={filter.id}
                  variant={filter.id === alertFilter ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => setAlertFilter(filter.id)}
                >
                  {filter.label}
                </Button>
              ))}
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {filteredAlerts.length === 0 ? (
              <p className="text-sm text-muted-foreground">No alerts in this filter.</p>
            ) : (
              filteredAlerts.slice(0, 6).map((alert) => (
                <div key={alert.id} className="rounded-xl border border-border/50 p-3 bg-background/40 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-sm font-medium">{alert.title}</p>
                      <p className="text-xs text-muted-foreground mt-1">{alert.message}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge
                        variant={alert.severity === "critical" ? "destructive" : alert.severity === "warning" ? "warning" : "secondary"}
                        className="capitalize"
                      >
                        {alert.severity}
                      </Badge>
                      <Badge variant={alert.status === "open" ? "outline" : "secondary"} className="capitalize">
                        {alert.status}
                      </Badge>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-muted-foreground uppercase">{alert.source}</span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={alert.status === "acknowledged"}
                      onClick={() => ackOverviewAlert(alert.id)}
                    >
                      {alert.status === "acknowledged" ? "Acknowledged" : "Acknowledge"}
                    </Button>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card variant="glass">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              <ClipboardList className="h-4 w-4" />
              Today&apos;s Briefing
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={() => setActiveTab("logs")}>
              Technical Logs
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            <pre className="text-xs whitespace-pre-wrap rounded-xl border border-border/50 bg-background/40 p-3">
              {runSummary?.text || "No summary available yet. Click Refresh to generate."}
            </pre>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => setActiveTab("services")}>
                Open Service Control
              </Button>
              <Button variant="outline" size="sm" onClick={() => setActiveTab("jobs")}>
                Open Task Center
              </Button>
              <Button variant="outline" size="sm" onClick={() => setActiveTab("verify")}>
                Open Review Desk
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
