import { create } from "zustand";
import * as tauri from "@/lib/tauri";
import { useUiStore } from "@/stores/uiStore";
import type {
  Artifact,
  Job,
  Milestone,
  QualityReport,
} from "@/stores/types";

const artifactRequestSeqByJob: Record<string, number> = {};

function mapJob(item: tauri.Job): Job {
  return {
    jobId: item.job_id,
    status: item.status,
    taskType: item.task_type,
    sender: item.sender,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

function mapMilestone(item: tauri.Milestone): Milestone {
  return {
    eventType: item.event_type,
    timestamp: item.timestamp,
    payload: item.payload,
  };
}

function mapArtifact(item: tauri.Artifact): Artifact {
  return {
    name: item.name,
    path: item.path,
    size: item.size,
    artifactType: item.artifact_type,
  };
}

function mapQuality(item: tauri.QualityReport | null): QualityReport | null {
  if (!item) return null;
  return {
    terminologyHit: item.terminology_hit,
    structureFidelity: item.structure_fidelity,
    purityScore: item.purity_score,
  };
}

interface JobStoreState {
  jobs: Job[];
  selectedJobId: string | null;
  selectedJobMilestones: Milestone[];
  jobArtifactsById: Record<string, Artifact[]>;
  jobQualityById: Record<string, QualityReport | null>;
  jobArtifactsLoadingById: Record<string, boolean>;
  setJobs: (jobs: Job[]) => void;
  setSelectedJobId: (id: string | null) => void;
  fetchJobs: (status?: string, opts?: { silent?: boolean }) => Promise<void>;
  fetchJobMilestones: (jobId: string, opts?: { silent?: boolean }) => Promise<void>;
  fetchJobArtifacts: (jobId: string) => Promise<void>;
  refreshJobsData: (opts?: { silent?: boolean }) => Promise<void>;
  refreshSelectedJobMilestones: (opts?: { silent?: boolean }) => Promise<void>;
  refreshVerifyData: (opts?: { silent?: boolean }) => Promise<void>;
}

export const useJobStore = create<JobStoreState>((set, get) => ({
  jobs: [],
  selectedJobId: null,
  selectedJobMilestones: [],
  jobArtifactsById: {},
  jobQualityById: {},
  jobArtifactsLoadingById: {},

  setJobs: (jobs) => set({ jobs }),
  setSelectedJobId: (selectedJobId) => set({ selectedJobId }),

  fetchJobs: async (status, opts) => {
    try {
      const jobs = await tauri.getJobs(status);
      set({ jobs: jobs.map(mapJob) });
    } catch (error) {
      if (!opts?.silent) {
        useUiStore.getState().addToast("error", `Failed to fetch jobs: ${error}`);
      }
    }
  },

  fetchJobMilestones: async (jobId, opts) => {
    try {
      const milestones = await tauri.getJobMilestones(jobId);
      set({ selectedJobMilestones: milestones.map(mapMilestone) });
    } catch (error) {
      if (!opts?.silent) {
        useUiStore.getState().addToast("error", `Failed to fetch milestones: ${error}`);
      }
    }
  },

  fetchJobArtifacts: async (jobId) => {
    const requestSeq = (artifactRequestSeqByJob[jobId] || 0) + 1;
    artifactRequestSeqByJob[jobId] = requestSeq;
    set((state) => ({
      jobArtifactsLoadingById: {
        ...state.jobArtifactsLoadingById,
        [jobId]: true,
      },
    }));

    try {
      const [artifacts, quality] = await Promise.all([
        tauri.listVerifyArtifacts(jobId),
        tauri.getQualityReport(jobId).catch(() => null),
      ]);

      if (artifactRequestSeqByJob[jobId] !== requestSeq) {
        return;
      }

      set((state) => ({
        jobArtifactsById: {
          ...state.jobArtifactsById,
          [jobId]: artifacts.map(mapArtifact),
        },
        jobQualityById: {
          ...state.jobQualityById,
          [jobId]: mapQuality(quality),
        },
      }));
    } catch (error) {
      useUiStore.getState().addToast("error", `Failed to fetch artifacts: ${error}`);
    } finally {
      if (artifactRequestSeqByJob[jobId] === requestSeq) {
        set((state) => ({
          jobArtifactsLoadingById: {
            ...state.jobArtifactsLoadingById,
            [jobId]: false,
          },
        }));
      }
    }
  },

  refreshJobsData: async (opts) => {
    await get().fetchJobs(undefined, { silent: opts?.silent });
  },

  refreshSelectedJobMilestones: async (opts) => {
    const selectedJobId = get().selectedJobId;
    if (!selectedJobId) return;
    const selectedJob = get().jobs.find((job) => job.jobId === selectedJobId);
    const activeStatuses = new Set(["queued", "received", "collecting", "preflight", "running"]);
    if (!selectedJob || !activeStatuses.has(selectedJob.status)) return;
    await get().fetchJobMilestones(selectedJobId, { silent: opts?.silent });
  },

  refreshVerifyData: async (opts) => {
    await get().fetchJobs(undefined, { silent: opts?.silent });
  },
}));
