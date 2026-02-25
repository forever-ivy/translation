import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useJobStore } from "@/stores/jobStore";
import { useUiStore } from "@/stores/uiStore";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { MotionCard, CountUp, staggerContainer, staggerItem } from "@/components/ui/motion";
import * as tauri from "@/lib/tauri";
import {
  FileText,
  FileSpreadsheet,
  FileCheck,
  FolderOpen,
  Copy,
  ExternalLink,
  RefreshCw,
} from "lucide-react";

export function Verify() {
  const jobs = useJobStore((s) => s.jobs);
  const jobArtifactsById = useJobStore((s) => s.jobArtifactsById);
  const jobQualityById = useJobStore((s) => s.jobQualityById);
  const jobArtifactsLoadingById = useJobStore((s) => s.jobArtifactsLoadingById);
  const refreshVerifyData = useJobStore((s) => s.refreshVerifyData);
  const fetchJobArtifacts = useJobStore((s) => s.fetchJobArtifacts);
  const setSelectedJobId = useJobStore((s) => s.setSelectedJobId);
  const isLoading = useUiStore((s) => s.isLoading);
  const addToast = useUiStore((s) => s.addToast);
  const [expandedJob, setExpandedJob] = useState<string | null>(null);

  const handleJobExpand = async (jobId: string) => {
    if (expandedJob === jobId) {
      setExpandedJob(null);
    } else {
      setExpandedJob(jobId);
      setSelectedJobId(jobId);
      await fetchJobArtifacts(jobId);
    }
  };

  const handleOpenInFinder = async (jobId: string) => {
    try {
      const verifyPath = await tauri.getVerifyFolderPath();
      await tauri.openInFinder(`${verifyPath}/${jobId}`);
    } catch (err) {
      console.error("Failed to open folder:", err);
      const msg = String(err);
      if (msg.includes("not found") || msg.includes("does not exist")) {
        addToast("warning", "Folder not found. The job files may have been moved or deleted.");
      } else {
        addToast("error", `Failed to open folder: ${err}`);
      }
    }
  };

  const handleOpenWebCalls = async (jobId: string) => {
    try {
      const verifyPath = await tauri.getVerifyFolderPath();
      await tauri.openInFinder(`${verifyPath}/${jobId}/.system/web_calls`);
    } catch (err) {
      console.error("Failed to open web_calls:", err);
      addToast("error", `Failed to open web_calls: ${err}`);
    }
  };

  const handleCopyCommand = async (text: string, label: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(textarea);
        if (!ok) throw new Error("Copy command failed");
      }
      addToast("success", `${label} copied`);
    } catch (err) {
      console.error("Failed to copy command:", err);
      addToast("error", `Failed to copy: ${err}`);
    }
  };

  const handleOpenArtifact = async (path: string) => {
    try {
      await tauri.openInFinder(path);
    } catch (err) {
      console.error("Failed to open artifact:", err);
      addToast("error", `Failed to open file: ${err}`);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const reviewReadyJobs = jobs.filter((j) => j.status === "review_ready");
  const needsAttentionJobs = jobs.filter((j) => j.status === "needs_attention");

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Verify</h2>
          <p className="text-muted-foreground">Review and approve translated artifacts</p>
        </div>
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button variant="outline" size="sm" onClick={() => refreshVerifyData()} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </motion.div>
      </div>

      {[
        {
          key: "review_ready",
          title: "Review Ready",
          description: "Jobs ready for human review and approval.",
          badgeVariant: "success" as const,
          jobs: reviewReadyJobs,
          emptyTitle: "No jobs review-ready",
          emptyHint: "When a job reaches review_ready, its artifacts will appear here.",
        },
        {
          key: "needs_attention",
          title: "Needs Attention",
          description: "Jobs that need manual intervention before they can proceed.",
          badgeVariant: "warning" as const,
          jobs: needsAttentionJobs,
          emptyTitle: "No jobs need attention",
          emptyHint: "If a job fails preflight/login, it will show up here with evidence in web_calls.",
        },
      ].map((section) => (
        <div key={section.key} className="space-y-3">
          <div>
            <h3 className="text-lg font-semibold">{section.title}</h3>
            <p className="text-sm text-muted-foreground">{section.description}</p>
          </div>

          <motion.div className="space-y-4" variants={staggerContainer} initial="hidden" animate="show">
            <AnimatePresence>
              {section.jobs.length === 0 ? (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                >
                  <Card variant="glass">
                    <CardContent className="flex flex-col items-center justify-center py-10 text-center">
                      <motion.div animate={{ y: [0, -5, 0] }} transition={{ duration: 2, repeat: Infinity }}>
                        <FileCheck className="h-10 w-10 text-muted-foreground/50 mb-3" />
                      </motion.div>
                      <p className="text-muted-foreground">{section.emptyTitle}</p>
                      <p className="text-sm text-muted-foreground/70 mt-2">{section.emptyHint}</p>
                    </CardContent>
                  </Card>
                </motion.div>
              ) : (
                section.jobs.map((job) => {
                  const jobArtifacts = jobArtifactsById[job.jobId] || [];
                  const jobQuality = jobQualityById[job.jobId] ?? null;
                  const isArtifactsLoading = !!jobArtifactsLoadingById[job.jobId];
                  const okCommand = `ok ${job.jobId}`;
                  const noCommand = `no ${job.jobId} needs_manual_revision`;

                  return (
                    <motion.div key={job.jobId} variants={staggerItem} layout layoutId={`${section.key}-${job.jobId}`}>
                      <MotionCard>
                        <Card variant="glass" className={expandedJob === job.jobId ? "ring-2 ring-primary" : ""}>
                          <CardHeader className="flex flex-row items-center justify-between">
                            <div className="flex items-center gap-3">
                              <CardTitle className="text-base">
                                <button
                                  type="button"
                                  className="cursor-pointer hover:text-primary transition-colors text-left"
                                  onClick={() => handleJobExpand(job.jobId)}
                                  aria-label={`Toggle details for ${job.jobId}`}
                                >
                                  {job.jobId}
                                </button>
                              </CardTitle>
                              <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>
                                <Badge variant={section.badgeVariant}>{job.status}</Badge>
                              </motion.div>
                            </div>
                            <div className="flex gap-2">
                              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                                <Button variant="outline" size="sm" onClick={() => handleOpenInFinder(job.jobId)}>
                                  <FolderOpen className="h-4 w-4 mr-2" />
                                  Open _VERIFY Folder
                                </Button>
                              </motion.div>
                              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                                <Button variant="ghost" size="sm" onClick={() => handleJobExpand(job.jobId)}>
                                  {expandedJob === job.jobId ? "Collapse" : "Expand"}
                                </Button>
                              </motion.div>
                            </div>
                          </CardHeader>

                          <AnimatePresence>
                            {expandedJob === job.jobId && (
                              <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: "auto" }}
                                exit={{ opacity: 0, height: 0 }}
                                transition={{ duration: 0.3 }}
                                className="overflow-hidden"
                              >
                                <CardContent className="space-y-4">
                                  {/* Actions */}
                                  <div className="flex flex-wrap gap-2">
                                    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                                      <Button variant="outline" size="sm" onClick={() => handleOpenWebCalls(job.jobId)}>
                                        <FolderOpen className="h-4 w-4 mr-2" />
                                        Open web_calls
                                      </Button>
                                    </motion.div>
                                    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => void handleCopyCommand(okCommand, "Approve command")}
                                      >
                                        <Copy className="h-4 w-4 mr-2" />
                                        Copy Telegram Approve
                                      </Button>
                                    </motion.div>
                                    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => void handleCopyCommand(noCommand, "Revision command")}
                                      >
                                        <Copy className="h-4 w-4 mr-2" />
                                        Copy Telegram Revision
                                      </Button>
                                    </motion.div>
                                  </div>

                                  {/* Artifacts */}
                                  <div>
                                    <h4 className="text-sm font-medium mb-2">Artifacts</h4>
                                    {isArtifactsLoading ? (
                                      <p className="text-sm text-muted-foreground">Loading artifacts...</p>
                                    ) : jobArtifacts.length === 0 ? (
                                      <p className="text-sm text-muted-foreground">No artifacts found.</p>
                                    ) : (
                                      <motion.div
                                        className="grid grid-cols-1 md:grid-cols-2 gap-2"
                                        variants={staggerContainer}
                                        initial="hidden"
                                        animate="show"
                                      >
                                        {jobArtifacts.map((artifact) => (
                                          <motion.div
                                            key={artifact.name}
                                            variants={staggerItem}
                                            whileHover={{ scale: 1.02, backgroundColor: "var(--surface-hover)" }}
                                            className="flex items-center justify-between p-3 rounded-lg border cursor-pointer"
                                          >
                                            <div className="flex items-center gap-2">
                                              {artifact.artifactType === "docx" ? (
                                                <FileText className="h-4 w-4 text-blue-500" />
                                              ) : artifact.artifactType === "xlsx" ? (
                                                <FileSpreadsheet className="h-4 w-4 text-green-500" />
                                              ) : (
                                                <FileText className="h-4 w-4 text-gray-500" />
                                              )}
                                              <div>
                                                <p className="text-sm font-medium">{artifact.name}</p>
                                                <p className="text-xs text-muted-foreground">{formatSize(artifact.size)}</p>
                                              </div>
                                            </div>
                                            <div className="flex gap-1">
                                              <motion.div whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }}>
                                                <Button
                                                  variant="ghost"
                                                  size="icon"
                                                  className="h-8 w-8"
                                                  onClick={() => handleOpenArtifact(artifact.path)}
                                                  aria-label={`Open ${artifact.name}`}
                                                >
                                                  <ExternalLink className="h-4 w-4" />
                                                </Button>
                                              </motion.div>
                                            </div>
                                          </motion.div>
                                        ))}
                                      </motion.div>
                                    )}
                                  </div>

                                  {/* Quality Report (only meaningful for review_ready, but safe to show) */}
                                  <AnimatePresence>
                                    {jobQuality ? (
                                      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }}>
                                        <h4 className="text-sm font-medium mb-2">Quality Report</h4>
                                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 p-4 rounded-lg bg-card/50 backdrop-blur-sm border border-border/50">
                                          <motion.div
                                            className="text-center"
                                            initial={{ scale: 0.8, opacity: 0 }}
                                            animate={{ scale: 1, opacity: 1 }}
                                            transition={{ delay: 0.1 }}
                                          >
                                            <p
                                              className={`text-2xl font-bold ${
                                                jobQuality.terminologyHit >= 80
                                                  ? "text-green-600"
                                                  : jobQuality.terminologyHit >= 60
                                                    ? "text-yellow-600"
                                                    : "text-red-600"
                                              }`}
                                            >
                                              <CountUp value={jobQuality.terminologyHit} suffix="%" />
                                            </p>
                                            <p className="text-xs text-muted-foreground">Terminology Hit</p>
                                          </motion.div>
                                          <motion.div
                                            className="text-center"
                                            initial={{ scale: 0.8, opacity: 0 }}
                                            animate={{ scale: 1, opacity: 1 }}
                                            transition={{ delay: 0.2 }}
                                          >
                                            <p
                                              className={`text-2xl font-bold ${
                                                jobQuality.structureFidelity >= 90
                                                  ? "text-green-600"
                                                  : jobQuality.structureFidelity >= 70
                                                    ? "text-yellow-600"
                                                    : "text-red-600"
                                              }`}
                                            >
                                              <CountUp value={jobQuality.structureFidelity} suffix="%" />
                                            </p>
                                            <p className="text-xs text-muted-foreground">Structure Fidelity</p>
                                          </motion.div>
                                          <motion.div
                                            className="text-center"
                                            initial={{ scale: 0.8, opacity: 0 }}
                                            animate={{ scale: 1, opacity: 1 }}
                                            transition={{ delay: 0.3 }}
                                          >
                                            <p
                                              className={`text-2xl font-bold ${
                                                jobQuality.purityScore >= 95
                                                  ? "text-green-600"
                                                  : jobQuality.purityScore >= 90
                                                    ? "text-yellow-600"
                                                    : "text-red-600"
                                              }`}
                                            >
                                              <CountUp value={jobQuality.purityScore} suffix="%" />
                                            </p>
                                            <p className="text-xs text-muted-foreground">Purity Score</p>
                                          </motion.div>
                                        </div>
                                      </motion.div>
                                    ) : isArtifactsLoading ? (
                                      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }}>
                                        <h4 className="text-sm font-medium mb-2">Quality Report</h4>
                                        <p className="text-sm text-muted-foreground">Loading quality report...</p>
                                      </motion.div>
                                    ) : (
                                      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }}>
                                        <h4 className="text-sm font-medium mb-2">Quality Report</h4>
                                        <p className="text-sm text-muted-foreground">Quality report unavailable or skipped.</p>
                                      </motion.div>
                                    )}
                                  </AnimatePresence>
                                </CardContent>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </Card>
                      </MotionCard>
                    </motion.div>
                  );
                })
              )}
            </AnimatePresence>
          </motion.div>
        </div>
      ))}
    </div>
  );
}
