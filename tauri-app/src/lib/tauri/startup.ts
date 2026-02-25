import {
  diagnoseTelegramBot as diagnoseTelegramBotLegacy,
  getStartupSnapshot as getStartupSnapshotLegacy,
  restartOpenclawComponent as restartOpenclawComponentLegacy,
  startOpenclawV2 as startOpenclawV2Legacy,
  startTelegramBotV2 as startTelegramBotV2Legacy,
  stopOpenclawComponent as stopOpenclawComponentLegacy,
  type StartOpenclawPayload,
  type StartTelegramPayload,
} from "@/lib/tauri_legacy";
import type {
  GatewayProviderStatus,
  ServiceStatusType,
  StartupSnapshot,
  StartupStepResult,
  TelegramHealth,
} from "@/stores/types";

function mapStartupStepResult(result: {
  phase: string;
  status: string;
  message: string;
  hint_action?: string;
  started_at: string;
  ended_at: string;
}): StartupStepResult {
  return {
    phase: result.phase,
    status: result.status,
    message: result.message,
    hintAction: result.hint_action,
    startedAt: result.started_at,
    endedAt: result.ended_at,
  };
}

function mapTelegramHealth(health: {
  running: boolean;
  single_instance_ok: boolean;
  conflict_409: boolean;
  pid_lock: boolean;
  poll_conflict: boolean;
  network: string;
  last_error: string;
  log_tail: string[];
  updated_at: string;
}): TelegramHealth {
  return {
    running: health.running,
    singleInstanceOk: health.single_instance_ok,
    conflict409: health.conflict_409,
    pidLock: health.pid_lock,
    pollConflict: health.poll_conflict,
    network: health.network,
    lastError: health.last_error,
    logTail: health.log_tail,
    updatedAt: health.updated_at,
  };
}

function mapGatewayProviderStatus(status: {
  provider: string;
  running: boolean;
  healthy: boolean;
  logged_in: boolean;
  base_url: string;
  model: string;
  home_url: string;
  last_error: string;
  updated_at: string;
  session_checked_at: string;
  profile_dir: string;
  last_url: string;
}): GatewayProviderStatus {
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

export async function startOpenclawV2(payload?: { forceRestart?: boolean }): Promise<StartupStepResult[]> {
  const rawPayload: StartOpenclawPayload | undefined = payload
    ? { force_restart: payload.forceRestart }
    : undefined;
  const results = await startOpenclawV2Legacy(rawPayload);
  return results.map(mapStartupStepResult);
}

export async function startTelegramBotV2(payload?: { forceRestart?: boolean }): Promise<TelegramHealth> {
  const rawPayload: StartTelegramPayload | undefined = payload
    ? { force_restart: payload.forceRestart }
    : undefined;
  const health = await startTelegramBotV2Legacy(rawPayload);
  return mapTelegramHealth(health);
}

export async function diagnoseTelegramBot(): Promise<TelegramHealth> {
  const health = await diagnoseTelegramBotLegacy();
  return mapTelegramHealth(health);
}

export async function getStartupSnapshot(): Promise<StartupSnapshot> {
  const snapshot = await getStartupSnapshotLegacy();
  const providers: Record<string, GatewayProviderStatus> | undefined = snapshot.gateway.providers
    ? Object.fromEntries(
        Object.entries(snapshot.gateway.providers).map(([k, v]) => [k, mapGatewayProviderStatus(v)]),
      )
    : undefined;
  return {
    services: snapshot.services.map((service) => ({
      name: service.name,
      status: service.status as ServiceStatusType,
      pid: service.pid,
      uptime: service.uptime,
      restarts: service.restarts,
    })),
    gateway: {
      running: snapshot.gateway.running,
      healthy: snapshot.gateway.healthy,
      loggedIn: snapshot.gateway.logged_in,
      baseUrl: snapshot.gateway.base_url,
      model: snapshot.gateway.model,
      lastError: snapshot.gateway.last_error,
      updatedAt: snapshot.gateway.updated_at,
      version: snapshot.gateway.version,
      primaryProvider: snapshot.gateway.primary_provider,
      providers,
    },
    telegram: mapTelegramHealth(snapshot.telegram),
  };
}

export async function stopOpenclawComponent(name: "gateway" | "worker" | "telegram"): Promise<unknown> {
  return stopOpenclawComponentLegacy(name);
}

export async function restartOpenclawComponent(name: "gateway" | "worker" | "telegram"): Promise<unknown> {
  return restartOpenclawComponentLegacy(name);
}
