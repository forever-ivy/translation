import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useJobStore } from "@/stores/jobStore";
import { useUiStore } from "@/stores/uiStore";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { GlassMotionCard, staggerContainer, staggerItem } from "@/components/ui/motion";
import { AlertTriangle, CheckCircle2, FileText, Loader2, RefreshCw } from "lucide-react";

const statusColors: Record<string, "success" | "warning" | "secondary" | "default"> = {
  queued: "secondary",
  received: "secondary",
  preflight: "secondary",
  running: "default",
  review_ready: "success",
  needs_attention: "warning",
  needs_revision: "warning",
  missing_inputs: "warning",
  incomplete_input: "warning",
  verified: "secondary",
  failed: "warning",
  collecting: "secondary",
  canceled: "secondary",
  discarded: "secondary",
};

const STATUS_FILTER_OPTIONS = [
  "queued",
  "preflight",
  "running",
  "review_ready",
  "needs_attention",
  "needs_revision",
  "missing_inputs",
  "collecting",
  "received",
  "verified",
  "failed",
  "incomplete_input",
  "canceled",
  "discarded",
] as const;

function labelFromEventType(eventType: string) {
  const explicit: Record<string, string> = {
    received: "Received",
    new_created: "Created",
    run_enqueued: "Enqueued",
    preflight_started: "Preflight started",
    preflight_done: "Preflight passed",
    preflight_failed: "Preflight failed",
    kb_retrieve_done: "KB retrieve done",
    intent_classified: "Intent classified",
    running: "Running",
    review_ready: "Review ready",
    needs_attention: "Needs attention",
    failed: "Failed",
    verified: "Verified",
  };
  if (explicit[eventType]) return explicit[eventType];

  const roundMatch = eventType.match(/^round_(\d+)_(started|done)$/);
  if (roundMatch) {
    const [, round, phase] = roundMatch;
    return `Round ${round} ${phase}`;
  }

  const cleaned = eventType.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  return cleaned.length ? cleaned : eventType;
}

function parseTimestampMs(ts: string) {
  const ms = Date.parse(ts);
  return Number.isNaN(ms) ? 0 : ms;
}

function formatTimestamp(ts: string) {
  const d = new Date(ts);
  if (!Number.isNaN(d.getTime())) {
    return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  // Fallbacks for non-ISO formats
  if (ts.includes("T")) {
    const timePart = ts.split("T")[1] || "";
    return timePart.replace(/Z$/, "").split(/[.+]/)[0]?.slice(0, 8) || null;
  }
  if (ts.includes(" ")) {
    return ts.split(" ")[1]?.slice(0, 8) || null;
  }
  return ts.slice(0, 8);
}

export function Jobs() {
  const jobs = useJobStore((s) => s.jobs);
  const selectedJobId = useJobStore((s) => s.selectedJobId);
  const selectedJobMilestones = useJobStore((s) => s.selectedJobMilestones);
  const fetchJobs = useJobStore((s) => s.fetchJobs);
  const fetchJobMilestones = useJobStore((s) => s.fetchJobMilestones);
  const setSelectedJobId = useJobStore((s) => s.setSelectedJobId);
  const isLoading = useUiStore((s) => s.isLoading);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);

  useEffect(() => {
    void fetchJobs(undefined);
  }, [fetchJobs]);

  const handleJobClick = async (jobId: string) => {
    setSelectedJobId(jobId);
    await fetchJobMilestones(jobId);
  };

  const filteredJobs = statusFilter ? jobs.filter((j) => j.status === statusFilter) : jobs;

  const activeJobStatuses = new Set(["queued", "received", "collecting", "preflight", "running"]);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Jobs</h2>
          <p className="text-muted-foreground">Task status and milestones</p>
        </div>
        <div className="flex gap-2">
          <select
            className="px-3 py-1.5 border rounded-xl text-sm bg-background text-foreground"
            value={statusFilter ?? ""}
            onChange={(e) => setStatusFilter(e.target.value || null)}
          >
            <option value="">All Statuses</option>
            {STATUS_FILTER_OPTIONS.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
          <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
            <Button variant="outline" size="sm" onClick={() => fetchJobs(undefined)} disabled={isLoading}>
              <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </motion.div>
        </div>
      </div>

      {/* Jobs List */}
      <motion.div
        className="space-y-4"
        variants={staggerContainer}
        initial="hidden"
        animate="show"
      >
        <AnimatePresence>
          {filteredJobs.length === 0 ? (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
            >
              <Card variant="glass">
                <CardContent className="flex flex-col items-center justify-center py-12">
                  <FileText className="h-12 w-12 text-muted-foreground/50 mb-4" />
                  <p className="text-muted-foreground">No jobs found</p>
                  <p className="text-sm text-muted-foreground/70">Jobs will appear here when tasks are created via Telegram</p>
                </CardContent>
              </Card>
            </motion.div>
          ) : (
            filteredJobs.map((job) => (
              <motion.div
                key={job.jobId}
                variants={staggerItem}
                layout
                layoutId={job.jobId}
              >
                <GlassMotionCard>
                  <Card
                    variant="glass"
                    className={`cursor-pointer transition-colors ${selectedJobId === job.jobId ? "ring-2 ring-primary" : ""}`}
                    onClick={() => handleJobClick(job.jobId)}
                  >
                    <CardContent className="p-4">
                      <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-3">
                          <motion.div
                            whileHover={{ rotate: 5 }}
                            transition={{ duration: 0.2 }}
                          >
                            <FileText className="h-5 w-5 text-muted-foreground" />
                          </motion.div>
                          <div>
                            <p className="font-medium">{job.jobId}</p>
                            <p className="text-sm text-muted-foreground">From: {job.sender || "Unknown"}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <motion.div
                            key={job.status}
                            initial={{ scale: 0.8, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                          >
                            <Badge variant={statusColors[job.status] || "secondary"}>{job.status}</Badge>
                          </motion.div>
                          <span className="text-xs text-muted-foreground">{job.taskType}</span>
                        </div>
                      </div>

                      {/* Milestone Timeline - only show for selected job */}
                      <AnimatePresence>
                        {selectedJobId === job.jobId && (
                          <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            transition={{ duration: 0.3 }}
                            className="mt-4 overflow-hidden"
                          >
                            {selectedJobMilestones.length === 0 ? (
                              <div className="pl-2 text-sm text-muted-foreground">No milestones recorded yet.</div>
                            ) : (
                              (() => {
                                const sorted = [...selectedJobMilestones].sort(
                                  (a, b) => parseTimestampMs(a.timestamp) - parseTimestampMs(b.timestamp),
                                );
                                const recent = sorted.slice(-30);
                                const isActive = activeJobStatuses.has(job.status);
                                return (
                                  <div className="pl-2 border-l-2 border-muted">
                                    <div className="space-y-2">
                                      {recent.map((milestone, index) => {
                                        const isLatest = index === recent.length - 1;
                                        const time = formatTimestamp(milestone.timestamp);
                                        const label = labelFromEventType(milestone.eventType);
                                        const isFailure = /failed|error/i.test(milestone.eventType);
                                        const isAttention = /needs_attention/i.test(milestone.eventType);
                                        const Icon = isFailure || isAttention ? AlertTriangle : isLatest && isActive ? Loader2 : CheckCircle2;
                                        const iconClass = isFailure
                                          ? "text-red-400"
                                          : isAttention
                                            ? "text-yellow-400"
                                            : isLatest && isActive
                                              ? "text-primary animate-spin"
                                              : "text-green-400";

                                        return (
                                          <motion.div
                                            key={`${milestone.eventType}-${milestone.timestamp}`}
                                            initial={{ opacity: 0, x: -10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: index * 0.01 }}
                                            className="flex items-center gap-2 text-sm"
                                          >
                                            <Icon className={`h-3 w-3 ${iconClass}`} />
                                            <span className="text-muted-foreground">{label}</span>
                                            <span className="text-[11px] text-muted-foreground/70">{milestone.eventType}</span>
                                            {time && <span className="text-xs text-muted-foreground/70 ml-auto">{time}</span>}
                                          </motion.div>
                                        );
                                      })}
                                    </div>
                                  </div>
                                );
                              })()
                            )}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </CardContent>
                  </Card>
                </GlassMotionCard>
              </motion.div>
            ))
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
