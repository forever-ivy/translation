import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore } from "@/stores/appStore";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { GlassMotionCard, staggerContainer, staggerItem, AnimatedProgress } from "@/components/ui/motion";
import { Clock, CheckCircle2, Loader2, FileText, RefreshCw } from "lucide-react";

const statusColors: Record<string, "success" | "warning" | "secondary" | "default"> = {
  review_ready: "success",
  needs_attention: "warning",
  running: "default",
  verified: "secondary",
  failed: "warning",
  collecting: "secondary",
};

const milestoneOrder = [
  "job_created",
  "run_accepted",
  "kb_sync_done",
  "intent_classified",
  "round_1_done",
  "round_2_done",
  "round_3_done",
  "review_ready",
  "verified",
];

const milestoneLabels: Record<string, string> = {
  job_created: "Job Created",
  run_accepted: "Run Accepted",
  kb_sync_done: "KB Sync",
  intent_classified: "Intent Classified",
  round_1_done: "Round 1",
  round_2_done: "Round 2",
  round_3_done: "Round 3",
  review_ready: "Review Ready",
  verified: "Verified",
};

export function Jobs() {
  const { jobs, selectedJobId, selectedJobMilestones, fetchJobs, fetchJobMilestones, isLoading } = useAppStore();
  const [statusFilter, setStatusFilter] = useState<string | null>(null);

  useEffect(() => {
    fetchJobs(statusFilter ?? undefined);
  }, [fetchJobs, statusFilter]);

  const handleJobClick = async (jobId: string) => {
    useAppStore.getState().setSelectedJobId(jobId);
    await fetchJobMilestones(jobId);
  };

  const filteredJobs = statusFilter ? jobs.filter((j) => j.status === statusFilter) : jobs;

  const getMilestoneIcon = (_eventType: string, isComplete: boolean, isCurrent: boolean) => {
    if (isComplete) {
      return (
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: "spring", stiffness: 500 }}
        >
          <CheckCircle2 className="h-3 w-3 text-green-400" />
        </motion.div>
      );
    }
    if (isCurrent) {
      return <Loader2 className="h-3 w-3 text-primary animate-spin" />;
    }
    return <Clock className="h-3 w-3 text-gray-500" />;
  };

  const getMilestoneTime = (eventType: string) => {
    const milestone = selectedJobMilestones.find((m) => m.eventType === eventType);
    return milestone?.timestamp ? milestone.timestamp.split(" ")[1]?.slice(0, 8) : null;
  };

  const isMilestoneComplete = (eventType: string) => {
    return selectedJobMilestones.some((m) => m.eventType === eventType);
  };

  const getCurrentMilestone = () => {
    for (const milestone of milestoneOrder) {
      if (!isMilestoneComplete(milestone)) {
        return milestone;
      }
    }
    return null;
  };

  const getProgress = () => {
    const completed = milestoneOrder.filter(isMilestoneComplete).length;
    return (completed / milestoneOrder.length) * 100;
  };

  const currentMilestone = getCurrentMilestone();

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
            <option value="running">Running</option>
            <option value="review_ready">Review Ready</option>
            <option value="verified">Verified</option>
            <option value="failed">Failed</option>
          </select>
          <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
            <Button variant="outline" size="sm" onClick={() => fetchJobs(statusFilter ?? undefined)} disabled={isLoading}>
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
                        {selectedJobId === job.jobId && selectedJobMilestones.length > 0 && (
                          <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            transition={{ duration: 0.3 }}
                            className="mt-4 overflow-hidden"
                          >
                            {/* Progress bar */}
                            <div className="mb-3">
                              <div className="flex justify-between text-xs text-muted-foreground mb-1">
                                <span>Progress</span>
                                <span>{Math.round(getProgress())}%</span>
                              </div>
                              <div className="h-2 bg-muted rounded-full overflow-hidden">
                                <AnimatedProgress value={getProgress()} />
                              </div>
                            </div>

                            <div className="pl-2 border-l-2 border-muted">
                              <div className="space-y-2">
                                {milestoneOrder.map((milestone, index) => {
                                  const isComplete = isMilestoneComplete(milestone);
                                  const isCurrent = currentMilestone === milestone;
                                  const time = getMilestoneTime(milestone);

                                  if (!isComplete && !isCurrent) {
                                    return (
                                      <motion.div
                                        key={milestone}
                                        initial={{ opacity: 0, x: -10 }}
                                        animate={{ opacity: 0.5, x: 0 }}
                                        transition={{ delay: index * 0.03 }}
                                        className="flex items-center gap-2 text-sm text-muted-foreground/50"
                                      >
                                        <Clock className="h-3 w-3" />
                                        <span>{milestoneLabels[milestone] || milestone}</span>
                                      </motion.div>
                                    );
                                  }

                                  return (
                                    <motion.div
                                      key={milestone}
                                      initial={{ opacity: 0, x: -10 }}
                                      animate={{ opacity: 1, x: 0 }}
                                      transition={{ delay: index * 0.03 }}
                                      className={`flex items-center gap-2 text-sm ${isCurrent ? "text-foreground font-medium" : ""}`}
                                    >
                                      {getMilestoneIcon(milestone, isComplete, isCurrent)}
                                      <span className={isComplete ? "text-muted-foreground" : ""}>
                                        {milestoneLabels[milestone] || milestone}
                                      </span>
                                      {time && <span className="text-xs text-muted-foreground/70 ml-auto">{time}</span>}
                                      {isCurrent && (
                                        <motion.span
                                          className="text-xs text-primary ml-auto"
                                          animate={{ opacity: [1, 0.5, 1] }}
                                          transition={{ duration: 1.5, repeat: Infinity }}
                                        >
                                          running...
                                        </motion.span>
                                      )}
                                    </motion.div>
                                  );
                                })}
                              </div>
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>

                      {/* Show hint if no milestones loaded */}
                      {selectedJobId === job.jobId && selectedJobMilestones.length === 0 && (
                        <motion.div
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          className="mt-4 pl-2 text-sm text-muted-foreground"
                        >
                          Loading milestones...
                        </motion.div>
                      )}
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
