import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore } from "@/stores/appStore";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { MotionCard, CountUp, staggerContainer, staggerItem } from "@/components/ui/motion";
import * as tauri from "@/lib/tauri";
import {
  FileText,
  FileSpreadsheet,
  FileCheck,
  FolderOpen,
  CheckCircle2,
  ExternalLink,
  RefreshCw,
} from "lucide-react";

export function Verify() {
  const { jobs, selectedJobArtifacts, selectedJobQuality, fetchJobs, fetchJobArtifacts, isLoading, addToast } =
    useAppStore();
  const [expandedJob, setExpandedJob] = useState<string | null>(null);
  const [artifactsLoadingJobId, setArtifactsLoadingJobId] = useState<string | null>(null);

  useEffect(() => {
    fetchJobs("review_ready");
  }, [fetchJobs]);

  const handleJobExpand = async (jobId: string) => {
    if (expandedJob === jobId) {
      setExpandedJob(null);
    } else {
      setExpandedJob(jobId);
      useAppStore.getState().setSelectedJobId(jobId);
      setArtifactsLoadingJobId(jobId);
      try {
        await fetchJobArtifacts(jobId);
      } finally {
        setArtifactsLoadingJobId((cur) => (cur === jobId ? null : cur));
      }
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

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Verify</h2>
          <p className="text-muted-foreground">Review and approve translated artifacts</p>
        </div>
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button variant="outline" size="sm" onClick={() => fetchJobs("review_ready")} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </motion.div>
      </div>

      {/* Jobs Awaiting Review */}
      <motion.div
        className="space-y-4"
        variants={staggerContainer}
        initial="hidden"
        animate="show"
      >
        <AnimatePresence>
          {reviewReadyJobs.length === 0 ? (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
            >
              <Card variant="glass">
                <CardContent className="flex flex-col items-center justify-center py-12">
                  <motion.div
                    animate={{ y: [0, -5, 0] }}
                    transition={{ duration: 2, repeat: Infinity }}
                  >
                    <FileCheck className="h-12 w-12 text-muted-foreground/50 mb-4" />
                  </motion.div>
                  <p className="text-muted-foreground">No jobs awaiting review</p>
                  <p className="text-sm text-muted-foreground/70 mt-2">Completed jobs will appear here for verification</p>
                </CardContent>
              </Card>
            </motion.div>
          ) : (
            reviewReadyJobs.map((job) => (
              <motion.div
                key={job.jobId}
                variants={staggerItem}
                layout
                layoutId={job.jobId}
              >
                <MotionCard>
                  <Card variant="glass" className={expandedJob === job.jobId ? "ring-2 ring-primary" : ""}>
                    <CardHeader className="flex flex-row items-center justify-between">
                      <div className="flex items-center gap-3">
                        <CardTitle
                          className="text-base cursor-pointer hover:text-primary transition-colors"
                          onClick={() => handleJobExpand(job.jobId)}
                        >
                          {job.jobId}
                        </CardTitle>
                        <motion.div
                          initial={{ scale: 0.8, opacity: 0 }}
                          animate={{ scale: 1, opacity: 1 }}
                        >
                          <Badge variant="success">{job.status}</Badge>
                        </motion.div>
                      </div>
                      <div className="flex gap-2">
                        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                          <Button variant="outline" size="sm" onClick={() => handleOpenInFinder(job.jobId)}>
                            <FolderOpen className="h-4 w-4 mr-2" />
                            Open in Finder
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
                            {/* Artifacts */}
                            <div>
                              <h4 className="text-sm font-medium mb-2">Artifacts</h4>
                              {artifactsLoadingJobId === job.jobId ? (
                                <p className="text-sm text-muted-foreground">Loading artifacts...</p>
                              ) : selectedJobArtifacts.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No artifacts found.</p>
                              ) : (
                                <motion.div
                                  className="grid grid-cols-2 gap-2"
                                  variants={staggerContainer}
                                  initial="hidden"
                                  animate="show"
                                >
                                  {selectedJobArtifacts.map((artifact) => (
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

                            {/* Quality Report */}
                            <AnimatePresence>
                              {selectedJobQuality ? (
                                <motion.div
                                  initial={{ opacity: 0, y: 10 }}
                                  animate={{ opacity: 1, y: 0 }}
                                  exit={{ opacity: 0, y: 10 }}
                                >
                                  <h4 className="text-sm font-medium mb-2">Quality Report</h4>
                                  <div className="grid grid-cols-3 gap-4 p-4 rounded-lg bg-card/50 backdrop-blur-sm border border-border/50">
                                    <motion.div
                                      className="text-center"
                                      initial={{ scale: 0.8, opacity: 0 }}
                                      animate={{ scale: 1, opacity: 1 }}
                                      transition={{ delay: 0.1 }}
                                    >
                                      <p
                                        className={`text-2xl font-bold ${
                                          selectedJobQuality.terminologyHit >= 80
                                            ? "text-green-600"
                                            : selectedJobQuality.terminologyHit >= 60
                                              ? "text-yellow-600"
                                              : "text-red-600"
                                        }`}
                                      >
                                        <CountUp value={selectedJobQuality.terminologyHit} suffix="%" />
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
                                          selectedJobQuality.structureFidelity >= 90
                                            ? "text-green-600"
                                            : selectedJobQuality.structureFidelity >= 70
                                              ? "text-yellow-600"
                                              : "text-red-600"
                                        }`}
                                      >
                                        <CountUp value={selectedJobQuality.structureFidelity} suffix="%" />
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
                                          selectedJobQuality.purityScore >= 95
                                            ? "text-green-600"
                                            : selectedJobQuality.purityScore >= 90
                                              ? "text-yellow-600"
                                              : "text-red-600"
                                        }`}
                                      >
                                        <CountUp value={selectedJobQuality.purityScore} suffix="%" />
                                      </p>
                                      <p className="text-xs text-muted-foreground">Purity Score</p>
                                    </motion.div>
                                  </div>
                                </motion.div>
                              ) : artifactsLoadingJobId === job.jobId ? (
                                <motion.div
                                  initial={{ opacity: 0, y: 10 }}
                                  animate={{ opacity: 1, y: 0 }}
                                  exit={{ opacity: 0, y: 10 }}
                                >
                                  <h4 className="text-sm font-medium mb-2">Quality Report</h4>
                                  <p className="text-sm text-muted-foreground">Loading quality report...</p>
                                </motion.div>
                              ) : (
                                <motion.div
                                  initial={{ opacity: 0, y: 10 }}
                                  animate={{ opacity: 1, y: 0 }}
                                  exit={{ opacity: 0, y: 10 }}
                                >
                                  <h4 className="text-sm font-medium mb-2">Quality Report</h4>
                                  <p className="text-sm text-muted-foreground">
                                    Quality report unavailable or skipped.
                                  </p>
                                </motion.div>
                              )}
                            </AnimatePresence>

                            {/* Actions */}
                            <div className="flex gap-2 pt-2">
                              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                                <Button variant="outline" disabled>
                                  <CheckCircle2 className="h-4 w-4 mr-2" />
                                  Mark as Reviewed
                                </Button>
                              </motion.div>
                              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                                <Button variant="ghost" disabled>
                                  <FileCheck className="h-4 w-4 mr-2" />
                                  View Report Details
                                </Button>
                              </motion.div>
                            </div>
                          </CardContent>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </Card>
                </MotionCard>
              </motion.div>
            ))
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
