import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useStartupStore } from "@/stores/startupStore";
import { useServiceStore } from "@/stores/serviceStore";
import { useUiStore } from "@/stores/uiStore";
import * as tauri from "@/lib/tauri";
import { RotateCcw, Play, ShieldAlert, Wrench, CheckCircle2, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";

const phaseLabel: Record<string, string> = {
  preflight: "Preflight",
  login_check: "Login Check",
  start_gateway: "Start Gateway",
  start_worker: "Start Worker",
  start_telegram: "Start Telegram",
  verify: "Verify",
  done: "Done",
  failed: "Failed",
};

const providerLabel: Record<string, string> = {
  deepseek_web: "DeepSeek Web",
  chatgpt_web: "ChatGPT Web",
};

export function StartOpenClaw() {
  const steps = useStartupStore((s) => s.steps);
  const snapshot = useStartupStore((s) => s.snapshot);
  const telegramHealth = useStartupStore((s) => s.telegramHealth);
  const isRunning = useStartupStore((s) => s.isRunning);
  const runGuidedStartup = useStartupStore((s) => s.runGuidedStartup);
  const fetchSnapshot = useStartupStore((s) => s.fetchSnapshot);
  const diagnoseTelegram = useStartupStore((s) => s.diagnoseTelegram);
  const restartTelegram = useStartupStore((s) => s.restartTelegram);
  const restartComponent = useStartupStore((s) => s.restartComponent);
  const isLoading = useUiStore((s) => s.isLoading);

  const preflightChecks = useServiceStore((s) => s.preflightChecks);
  const fetchPreflightChecks = useServiceStore((s) => s.fetchPreflightChecks);
  const autoFixPreflight = useServiceStore((s) => s.autoFixPreflight);
  const startGateway = useServiceStore((s) => s.startGateway);
  const stopGateway = useServiceStore((s) => s.stopGateway);
  const loginGateway = useServiceStore((s) => s.loginGateway);

  const [showDetails, setShowDetails] = useState(false);
  const [showAllSteps, setShowAllSteps] = useState(false);
  const [showPassedPreflight, setShowPassedPreflight] = useState(false);

  const combinedTelegram = telegramHealth ?? snapshot?.telegram ?? null;
  const gatewayProviders = snapshot?.gateway.providers ? Object.values(snapshot.gateway.providers) : [];
  const gatewayRunning = Boolean(snapshot?.gateway.running);
  const workerStatus = snapshot?.services.find((s) => s.name === "Run Worker")?.status ?? "unknown";
  const telegramServiceStatus = snapshot?.services.find((s) => s.name === "Telegram Bot")?.status ?? "unknown";
  const telegramRunning = Boolean(combinedTelegram?.running ?? telegramServiceStatus === "running");
  const workerRunning = workerStatus === "running";

  const nonPassingPreflight = preflightChecks.filter((c) => c.status !== "pass");
  const visiblePreflight = showPassedPreflight ? preflightChecks : nonPassingPreflight;
  const preflightCounts = {
    pass: preflightChecks.filter((c) => c.status === "pass").length,
    warning: preflightChecks.filter((c) => c.status === "warning").length,
    blocker: preflightChecks.filter((c) => c.status === "blocker").length,
  };

  const visibleSteps = showAllSteps ? steps : steps.slice(-6);

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold">Runtime</h2>
          <p className="text-muted-foreground">Start, login, verify health, and keep the pipeline moving.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => void runGuidedStartup({ forceRestart: true })} disabled={isRunning || isLoading}>
            <Play className="h-4 w-4 mr-2" />
            One-Click Start
          </Button>
          <Button variant="outline" onClick={() => void fetchSnapshot()} disabled={isLoading}>
            <RotateCcw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button variant="ghost" onClick={() => setShowDetails((v) => !v)} disabled={isLoading}>
            {showDetails ? <ChevronUp className="h-4 w-4 mr-2" /> : <ChevronDown className="h-4 w-4 mr-2" />}
            {showDetails ? "Hide details" : "Show details"}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="rounded-xl border bg-background/40 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs text-muted-foreground">Gateway</div>
              <div className="mt-1 flex items-center gap-2">
                {gatewayRunning ? (
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-yellow-500" />
                )}
                <div className="text-sm font-medium">{gatewayRunning ? "Running" : "Stopped"}</div>
              </div>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                if (gatewayRunning) {
                  await stopGateway();
                } else {
                  await startGateway();
                }
                await fetchSnapshot();
              }}
              disabled={isLoading}
            >
              {gatewayRunning ? "Stop" : "Start"}
            </Button>
          </div>
        </div>

        <div className="rounded-xl border bg-background/40 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs text-muted-foreground">Worker</div>
              <div className="mt-1 flex items-center gap-2">
                {workerRunning ? (
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-yellow-500" />
                )}
                <div className="text-sm font-medium">{workerRunning ? "Running" : workerStatus}</div>
              </div>
            </div>
            <Button size="sm" variant="outline" onClick={() => void restartComponent("worker")} disabled={isLoading}>
              Restart
            </Button>
          </div>
        </div>

        <div className="rounded-xl border bg-background/40 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs text-muted-foreground">Telegram</div>
              <div className="mt-1 flex items-center gap-2">
                {telegramRunning ? (
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-yellow-500" />
                )}
                <div className="text-sm font-medium">{telegramRunning ? "Running" : "Stopped"}</div>
              </div>
            </div>
            <Button size="sm" variant="outline" onClick={() => void restartTelegram()} disabled={isLoading}>
              Restart
            </Button>
          </div>
        </div>
      </div>

      <Card variant="glass">
        <CardHeader>
          <CardTitle className="text-sm">Gateway Providers</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {gatewayProviders.length === 0 ? (
            <div className="text-sm text-muted-foreground">No gateway provider status yet. Start gateway and refresh.</div>
          ) : (
            gatewayProviders.map((p) => (
              <div key={p.provider} className="rounded-lg border px-3 py-2 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium">{providerLabel[p.provider] ?? p.provider}</div>
                  <div className="text-xs text-muted-foreground truncate">{p.model}</div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant={p.loggedIn ? "default" : "secondary"} className="text-[10px]">
                    {p.loggedIn ? "logged-in" : "login"}
                  </Badge>
                  <Badge variant={p.healthy ? "secondary" : "warning"} className="text-[10px]">
                    {p.healthy ? "healthy" : "unhealthy"}
                  </Badge>
                  <Button size="sm" variant="outline" onClick={() => void loginGateway(p.provider)} disabled={isLoading}>
                    Login
                  </Button>
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {showDetails ? (
        <Card variant="glass">
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle className="text-sm">Details</CardTitle>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowAllSteps((v) => !v)}
                disabled={steps.length <= 6}
              >
                {showAllSteps ? "Show Less Steps" : "Show All Steps"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowPassedPreflight((v) => !v)}
                disabled={preflightChecks.length === 0}
              >
                {showPassedPreflight ? "Hide Passed" : "Show Passed"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-8">
            <div className="space-y-2">
              <div className="text-xs text-muted-foreground">Guided Startup</div>
              {steps.length === 0 ? (
                <div className="text-sm text-muted-foreground">No startup run yet.</div>
              ) : (
                <div className="rounded-lg border divide-y">
                  {visibleSteps.map((step) => (
                    <div key={`${step.phase}-${step.endedAt}`} className="p-3 flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{phaseLabel[step.phase] ?? step.phase}</div>
                        <div className="text-xs text-muted-foreground mt-1 whitespace-pre-wrap break-words">
                          {step.message}
                        </div>
                        {step.hintAction ? (
                          <div className="text-[11px] mt-1 text-muted-foreground">Hint: {step.hintAction}</div>
                        ) : null}
                      </div>
                      <Badge
                        variant={
                          step.status === "success"
                            ? "default"
                            : step.status === "warning"
                            ? "secondary"
                            : "destructive"
                        }
                      >
                        {step.status}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-3">
              <div className="text-xs text-muted-foreground">Preflight</div>
              <div className="flex flex-wrap gap-2">
                <Badge variant="secondary">pass: {preflightCounts.pass}</Badge>
                <Badge variant={preflightCounts.warning > 0 ? "warning" : "secondary"}>warning: {preflightCounts.warning}</Badge>
                <Badge variant={preflightCounts.blocker > 0 ? "destructive" : "secondary"}>blocker: {preflightCounts.blocker}</Badge>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => void fetchPreflightChecks()} disabled={isLoading}>
                  Run Preflight
                </Button>
                <Button variant="outline" size="sm" onClick={() => void autoFixPreflight()} disabled={isLoading}>
                  Auto Fix
                </Button>
              </div>
              {preflightChecks.length === 0 ? (
                <div className="text-sm text-muted-foreground">No preflight run yet.</div>
              ) : visiblePreflight.length === 0 ? (
                <div className="text-sm text-muted-foreground">All checks passed.</div>
              ) : (
                <div className="space-y-2">
                  {visiblePreflight.map((check) => (
                    <div key={check.key} className="rounded-lg border p-3 flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{check.name}</div>
                        <div className="text-xs text-muted-foreground mt-1 whitespace-pre-wrap break-words">{check.message}</div>
                        <div className="text-[10px] text-muted-foreground mt-1 font-mono">{check.key}</div>
                      </div>
                      <Badge
                        variant={
                          check.status === "pass" ? "secondary" : check.status === "warning" ? "warning" : "destructive"
                        }
                      >
                        {check.status}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-2">
              <div className="text-xs text-muted-foreground">Gateway Provider Details</div>
              {gatewayProviders.length === 0 ? (
                <div className="text-sm text-muted-foreground">No provider details yet.</div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {gatewayProviders.map((p) => {
                    const err = (p.lastError || "").trim();
                    const errShort = err.length > 180 ? `${err.slice(0, 180)}…` : err || "none";
                    const url = (p.lastUrl || "").trim();
                    const urlShort = url.length > 120 ? `${url.slice(0, 120)}…` : url || "—";
                    return (
                      <div key={`detail-${p.provider}`} className="rounded-lg border p-3 space-y-1">
                        <div className="text-sm font-medium">{providerLabel[p.provider] ?? p.provider}</div>
                        <div className="text-xs text-muted-foreground">Last URL: {urlShort}</div>
                        <div className="text-xs text-muted-foreground break-words">Last error: {errShort}</div>
                        <div className="text-[11px] text-muted-foreground font-mono break-all">{p.profileDir}</div>
                        {p.profileDir ? (
                          <div className="pt-1">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => void tauri.openInFinder(p.profileDir)}
                              disabled={isLoading}
                            >
                              Open Profile Folder
                            </Button>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="space-y-3">
              <div className="text-xs text-muted-foreground">Telegram Diagnostics</div>
              <div className="flex flex-wrap gap-2">
                <Badge variant={combinedTelegram?.running ? "default" : "destructive"}>
                  {combinedTelegram?.running ? "running" : "stopped"}
                </Badge>
                <Badge variant={combinedTelegram?.singleInstanceOk ? "default" : "destructive"}>
                  {combinedTelegram?.singleInstanceOk ? "single-instance" : "multi-instance detected"}
                </Badge>
                <Badge variant={combinedTelegram?.conflict409 ? "destructive" : "secondary"}>
                  {combinedTelegram?.conflict409 ? "409 conflict" : "no 409 conflict"}
                </Badge>
              </div>
              <div className="text-sm text-muted-foreground">
                Last error: {combinedTelegram?.lastError || "none"}
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => void diagnoseTelegram()} disabled={isLoading}>
                  <ShieldAlert className="h-4 w-4 mr-2" />
                  Diagnose
                </Button>
                <Button variant="outline" size="sm" onClick={() => void restartTelegram()} disabled={isLoading}>
                  <Wrench className="h-4 w-4 mr-2" />
                  Restart Telegram
                </Button>
              </div>
              {combinedTelegram?.logTail?.length ? (
                <div className="rounded-lg border bg-background/60 p-2 max-h-48 overflow-auto text-[11px] font-mono space-y-1">
                  {combinedTelegram.logTail.slice(-20).map((line, idx) => (
                    <div key={`${idx}-${line.slice(0, 12)}`}>{line}</div>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-muted-foreground">No telegram logs collected yet.</div>
              )}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
