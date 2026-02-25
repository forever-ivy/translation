import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useJobStore } from "@/stores/jobStore";
import { useLogStore } from "@/stores/logStore";
import { useServiceStore } from "@/stores/serviceStore";
import { useStartupStore } from "@/stores/startupStore";
import { routeFromPathname } from "@/shared/routes";
import { POLLING_INTERVALS_MS, isVisible } from "@/shared/polling/policies";

export function usePollingOrchestrator() {
  const location = useLocation();
  const route = routeFromPathname(location.pathname);

  const refreshJobsData = useJobStore((s) => s.refreshJobsData);
  const refreshSelectedJobMilestones = useJobStore((s) => s.refreshSelectedJobMilestones);
  const refreshLogsData = useLogStore((s) => s.refreshLogsData);
  const refreshVerifyData = useJobStore((s) => s.refreshVerifyData);
  const fetchServices = useServiceStore((s) => s.fetchServices);
  const fetchGatewayStatus = useServiceStore((s) => s.fetchGatewayStatus);
  const fetchPreflightChecks = useServiceStore((s) => s.fetchPreflightChecks);
  const fetchSnapshot = useStartupStore((s) => s.fetchSnapshot);
  const diagnoseTelegram = useStartupStore((s) => s.diagnoseTelegram);

  useEffect(() => {
    if (route !== "start-openclaw") return;
    void fetchSnapshot();
    void diagnoseTelegram();
    void fetchServices();
    void fetchGatewayStatus();
    void fetchPreflightChecks();
    const id = window.setInterval(() => {
      if (!isVisible()) return;
      void fetchSnapshot();
      void fetchServices();
      void fetchGatewayStatus();
    }, POLLING_INTERVALS_MS.runtime);
    return () => window.clearInterval(id);
  }, [route, diagnoseTelegram, fetchGatewayStatus, fetchPreflightChecks, fetchServices, fetchSnapshot]);

  useEffect(() => {
    if (route !== "jobs") return;
    void refreshJobsData({ silent: true });
    void refreshSelectedJobMilestones({ silent: true });
    const jobsId = window.setInterval(() => {
      if (!isVisible()) return;
      void refreshJobsData({ silent: true });
    }, POLLING_INTERVALS_MS.jobs);
    const milestonesId = window.setInterval(() => {
      if (!isVisible()) return;
      void refreshSelectedJobMilestones({ silent: true });
    }, POLLING_INTERVALS_MS.milestones);
    return () => {
      window.clearInterval(jobsId);
      window.clearInterval(milestonesId);
    };
  }, [route, refreshJobsData, refreshSelectedJobMilestones]);

  useEffect(() => {
    if (route !== "logs") return;
    void refreshLogsData({ silent: true, lines: 200 });
    const id = window.setInterval(() => {
      if (!isVisible()) return;
      void refreshLogsData({ silent: true, lines: 200 });
    }, POLLING_INTERVALS_MS.logs);
    return () => window.clearInterval(id);
  }, [route, refreshLogsData]);

  useEffect(() => {
    if (route !== "verify") return;
    void refreshVerifyData({ silent: true });
    const id = window.setInterval(() => {
      if (!isVisible()) return;
      void refreshVerifyData({ silent: true });
    }, POLLING_INTERVALS_MS.verify);
    return () => window.clearInterval(id);
  }, [route, refreshVerifyData]);

  useEffect(() => {
    const handleVisible = () => {
      if (!isVisible()) return;
      if (route === "start-openclaw") {
        void fetchSnapshot();
        void diagnoseTelegram();
        void fetchServices();
        void fetchGatewayStatus();
      } else if (route === "jobs") {
        void refreshJobsData({ silent: true });
        void refreshSelectedJobMilestones({ silent: true });
      } else if (route === "logs") {
        void refreshLogsData({ silent: true, lines: 200 });
      } else if (route === "verify") {
        void refreshVerifyData({ silent: true });
      }
    };
    document.addEventListener("visibilitychange", handleVisible);
    return () => document.removeEventListener("visibilitychange", handleVisible);
  }, [
    route,
    diagnoseTelegram,
    fetchGatewayStatus,
    fetchServices,
    fetchSnapshot,
    refreshJobsData,
    refreshLogsData,
    refreshSelectedJobMilestones,
    refreshVerifyData,
  ]);
}
