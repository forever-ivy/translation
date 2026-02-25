import { create } from "zustand";
import * as tauri from "@/lib/tauri";
import { useUiStore } from "@/stores/uiStore";
import type {
  DockerContainer,
  GatewayStatus,
  GatewayProviderStatus,
  PreflightCheck,
  Service,
  ServiceStatusType,
  StartupStepResult,
} from "@/stores/types";

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
let servicesFetchInFlight = false;

function mapServiceStatus(status: tauri.ServiceStatus): Service {
  return {
    name: status.name,
    status: status.status as ServiceStatusType,
    pid: status.pid,
    uptime: status.uptime,
    restarts: status.restarts,
  };
}

function mapPreflightCheck(check: tauri.PreflightCheck): PreflightCheck {
  return {
    name: check.name,
    key: check.key,
    status: check.status as "pass" | "warning" | "blocker",
    message: check.message,
  };
}

function mapGatewayProviderStatus(status: tauri.GatewayProviderStatus): GatewayProviderStatus {
  return {
    provider: status.provider,
    running: status.running,
    healthy: status.healthy,
    loggedIn: status.logged_in,
    baseUrl: status.base_url,
    model: status.model,
    homeUrl: status.home_url,
    lastError: status.last_error,
    updatedAt: status.updated_at,
    sessionCheckedAt: status.session_checked_at,
    profileDir: status.profile_dir,
    lastUrl: status.last_url,
  };
}

function mapGatewayStatus(status: tauri.GatewayStatus): GatewayStatus {
  const providers: Record<string, GatewayProviderStatus> | undefined = status.providers
    ? Object.fromEntries(
        Object.entries(status.providers).map(([k, v]) => [k, mapGatewayProviderStatus(v)]),
      )
    : undefined;
  return {
    running: status.running,
    healthy: status.healthy,
    loggedIn: status.logged_in,
    baseUrl: status.base_url,
    model: status.model,
    lastError: status.last_error,
    updatedAt: status.updated_at,
    version: status.version,
    primaryProvider: status.primary_provider,
    providers,
  };
}

function mapDockerContainer(container: tauri.DockerContainer): DockerContainer {
  return {
    name: container.name,
    status: container.status as DockerContainer["status"],
    image: container.image,
  };
}

export interface ServiceStoreState {
  services: Service[];
  dockerContainers: DockerContainer[];
  preflightChecks: PreflightCheck[];
  gatewayStatus: GatewayStatus | null;
  startupSteps: StartupStepResult[];
  setServices: (services: Service[]) => void;
  updateService: (name: string, data: Partial<Service>) => void;
  setPreflightChecks: (checks: PreflightCheck[]) => void;
  fetchServices: () => Promise<void>;
  fetchDockerStatus: () => Promise<void>;
  startDocker: () => Promise<void>;
  stopDocker: () => Promise<void>;
  fetchPreflightChecks: () => Promise<void>;
  autoFixPreflight: () => Promise<void>;
  startOpenclaw: () => Promise<void>;
  startServices: () => Promise<void>;
  stopServices: () => Promise<void>;
  restartServices: () => Promise<void>;
  startService: (serviceId: "telegram" | "worker") => Promise<void>;
  stopService: (serviceId: "telegram" | "worker") => Promise<void>;
  restartService: (serviceId: "telegram" | "worker") => Promise<void>;
  fetchGatewayStatus: () => Promise<void>;
  startGateway: () => Promise<void>;
  stopGateway: () => Promise<void>;
  loginGateway: (provider?: string) => Promise<void>;
}

export const useServiceStore = create<ServiceStoreState>((set, get) => ({
  services: [
    { name: "Telegram Bot", status: "unknown", restarts: 0 },
    { name: "Run Worker", status: "unknown", restarts: 0 },
  ],
  dockerContainers: [],
  preflightChecks: [],
  gatewayStatus: null,
  startupSteps: [],

  setServices: (services) => set({ services }),
  updateService: (name, data) =>
    set((state) => ({
      services: state.services.map((service) =>
        service.name === name ? { ...service, ...data } : service
      ),
    })),
  setPreflightChecks: (preflightChecks) => set({ preflightChecks }),

  fetchServices: async () => {
    if (servicesFetchInFlight) return;
    servicesFetchInFlight = true;
    try {
      const services = await tauri.getServiceStatus();
      set({ services: services.map(mapServiceStatus) });
    } catch (error) {
      console.warn("fetchServices failed:", error);
    } finally {
      servicesFetchInFlight = false;
    }
  },

  fetchDockerStatus: async () => {
    try {
      const containers = await tauri.getDockerStatus();
      set({ dockerContainers: containers.map(mapDockerContainer) });
    } catch {
      set({ dockerContainers: [] });
    }
  },

  startDocker: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    try {
      const containers = await tauri.startDockerServices();
      set({ dockerContainers: containers.map(mapDockerContainer) });
      const running = containers.filter((c) => c.status === "running").length;
      if (running === containers.length) {
        ui.addToast("success", "ClawRAG containers started");
      } else {
        ui.addToast("warning", `${running}/${containers.length} containers running`);
      }
    } catch (error) {
      ui.addToast("error", `Failed to start Docker: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  stopDocker: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    try {
      await tauri.stopDockerServices();
      await delay(2000);
      await get().fetchDockerStatus();
      ui.addToast("success", "ClawRAG containers stopped");
    } catch (error) {
      ui.addToast("error", `Failed to stop Docker: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  fetchPreflightChecks: async () => {
    try {
      const checks = await tauri.runPreflightCheck();
      set({ preflightChecks: checks.map(mapPreflightCheck) });
    } catch (error) {
      useUiStore.getState().addToast("error", `Preflight checks failed: ${error}`);
    }
  },

  autoFixPreflight: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    try {
      const checks = await tauri.autoFixPreflight();
      set({ preflightChecks: checks.map(mapPreflightCheck) });
      const blockers = checks.filter((c) => c.status === "blocker");
      if (blockers.length === 0) {
        ui.addToast("success", "All issues resolved");
      } else {
        ui.addToast("warning", `${blockers.length} issues require manual fix`);
      }
      void tauri
        .auditOperation({
          source: "tauri",
          action: "preflight_autofix",
          status: "success",
          summary: "auto_fix_preflight completed",
          detail: { blockers: blockers.length },
        })
        .catch(() => undefined);
    } catch (error) {
      ui.addToast("error", `Auto-fix failed: ${error}`);
      void tauri
        .auditOperation({
          source: "tauri",
          action: "preflight_autofix",
          status: "failed",
          summary: "auto_fix_preflight failed",
          detail: { error: String(error) },
        })
        .catch(() => undefined);
    } finally {
      ui.setIsLoading(false);
    }
  },

  startOpenclaw: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    try {
      const steps = await tauri.startOpenclawV2({ forceRestart: true });
      const checks = await tauri.runPreflightCheck();
      set({
        startupSteps: steps,
        preflightChecks: checks.map(mapPreflightCheck),
      });
      const failed = steps.find((step) => step.status === "failed");
      if (failed) {
        ui.addToast("warning", `OpenClaw started with issues: ${failed.message}`);
      } else {
        ui.addToast("success", "OpenClaw started successfully");
      }
      await get().fetchServices();
      await get().fetchGatewayStatus();
    } catch (error) {
      ui.addToast("error", `Failed to start OpenClaw: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  startServices: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      await tauri.startAllServices();
      await delay(2000);
      await get().fetchServices();
      const services = get().services;
      const running = services.filter((service) => service.status === "running").length;
      if (running === services.length) {
        ui.addToast("success", "All services started successfully");
      } else if (running > 0) {
        ui.addToast("warning", `${running}/${services.length} services running`);
      } else {
        ui.addToast("error", "Services failed to start");
      }
    } catch (error) {
      ui.addToast("error", `Failed to start services: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  stopServices: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      await tauri.stopAllServices();
      await delay(2000);
      await get().fetchServices();
      ui.addToast("success", "All services stopped");
    } catch (error) {
      ui.addToast("error", `Failed to stop services: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  restartServices: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      await tauri.restartAllServices();
      await delay(2000);
      await get().fetchServices();
      ui.addToast("success", "All services restarted");
    } catch (error) {
      ui.addToast("error", `Failed to restart services: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  startService: async (serviceId) => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      const services = await tauri.startService(serviceId);
      set({ services: services.map(mapServiceStatus) });
      ui.addToast("success", `Started ${serviceId}`);
    } catch (error) {
      ui.addToast("error", `Failed to start ${serviceId}: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  stopService: async (serviceId) => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      const services = await tauri.stopService(serviceId);
      set({ services: services.map(mapServiceStatus) });
      ui.addToast("success", `Stopped ${serviceId}`);
    } catch (error) {
      ui.addToast("error", `Failed to stop ${serviceId}: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  restartService: async (serviceId) => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      const services = await tauri.restartService(serviceId);
      set({ services: services.map(mapServiceStatus) });
      ui.addToast("success", `Restarted ${serviceId}`);
    } catch (error) {
      ui.addToast("error", `Failed to restart ${serviceId}: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  fetchGatewayStatus: async () => {
    try {
      const status = await tauri.gatewayStatus();
      set({ gatewayStatus: mapGatewayStatus(status) });
    } catch (error) {
      useUiStore.getState().addToast("error", `Failed to fetch gateway status: ${error}`);
    }
  },

  startGateway: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      const status = await tauri.gatewayStart();
      set({ gatewayStatus: mapGatewayStatus(status) });
      ui.addToast("success", "Gateway started");
    } catch (error) {
      ui.addToast("error", `Failed to start gateway: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  stopGateway: async () => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      const status = await tauri.gatewayStop();
      set({ gatewayStatus: mapGatewayStatus(status) });
      ui.addToast("success", "Gateway stopped");
    } catch (error) {
      ui.addToast("error", `Failed to stop gateway: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },

  loginGateway: async (provider) => {
    const ui = useUiStore.getState();
    ui.setIsLoading(true);
    ui.setError(null);
    try {
      const providerNorm = provider?.trim() || undefined;
      const status = await tauri.gatewayLogin({
        provider: providerNorm,
        interactive_login: true,
        timeout_seconds: 60,
      });
      set({ gatewayStatus: mapGatewayStatus(status) });
      const providerKey = providerNorm || status.primary_provider || "";
      const providerLoggedIn = providerKey && status.providers && status.providers[providerKey]
        ? Boolean(status.providers[providerKey].logged_in)
        : Boolean(status.logged_in);
      ui.addToast(
        providerLoggedIn ? "success" : "warning",
        providerLoggedIn ? "Gateway login verified" : "Gateway login not completed",
      );
    } catch (error) {
      ui.addToast("error", `Gateway login check failed: ${error}`);
    } finally {
      ui.setIsLoading(false);
    }
  },
}));
