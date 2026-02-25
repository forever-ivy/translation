import { useEffect, lazy, Suspense } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";
import { useUiStore } from "@/stores/uiStore";
import { ToastContainer } from "@/components/ui/toast";
import { usePollingOrchestrator } from "@/hooks/usePollingOrchestrator";

const StartOpenClaw = lazy(() => import("@/pages/StartOpenClaw").then((m) => ({ default: m.StartOpenClaw })));
const Jobs = lazy(() => import("@/pages/Jobs").then((m) => ({ default: m.Jobs })));
const Verify = lazy(() => import("@/pages/Verify").then((m) => ({ default: m.Verify })));
const Logs = lazy(() => import("@/pages/Logs").then((m) => ({ default: m.Logs })));
const Settings = lazy(() => import("@/pages/Settings").then((m) => ({ default: m.Settings })));
const KBHealth = lazy(() => import("@/pages/KBHealth").then((m) => ({ default: m.KBHealth })));
const Glossary = lazy(() => import("@/pages/Glossary").then((m) => ({ default: m.Glossary })));

const pageVariants = {
  initial: { opacity: 0, y: 8 },
  enter: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
};

const pageTransition = {
  type: "tween" as const,
  duration: 0.2,
  ease: [0.4, 0, 0.2, 1] as const,
};

function App() {
  const location = useLocation();
  const theme = useUiStore((s) => s.theme);
  const toasts = useUiStore((s) => s.toasts);
  const dismissToast = useUiStore((s) => s.dismissToast);

  usePollingOrchestrator();

  useEffect(() => {
    const applyTheme = () => {
      const isDark =
        theme === "dark" ||
        (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);

      document.documentElement.classList.toggle("dark", isDark);
    };

    applyTheme();

    if (theme === "system") {
      const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
      const handleChange = () => applyTheme();
      mediaQuery.addEventListener("change", handleChange);
      return () => mediaQuery.removeEventListener("change", handleChange);
    }
  }, [theme]);

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto overflow-x-hidden overscroll-none bg-gradient-to-br from-background via-background to-accent/5">
        <Suspense fallback={<div className="flex items-center justify-center h-full text-muted-foreground">Loading…</div>}>
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              variants={pageVariants}
              initial="initial"
              animate="enter"
              exit="exit"
              transition={pageTransition}
            >
              <Routes location={location}>
                <Route path="/" element={<Navigate to="/start-openclaw" replace />} />
                <Route path="/start-openclaw" element={<StartOpenClaw />} />
                <Route path="/jobs" element={<Jobs />} />
                <Route path="/verify" element={<Verify />} />
                <Route path="/logs" element={<Logs />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/kb-health" element={<KBHealth />} />
                <Route path="/glossary" element={<Glossary />} />
                <Route path="*" element={<Navigate to="/start-openclaw" replace />} />
              </Routes>
            </motion.div>
          </AnimatePresence>
        </Suspense>
      </main>
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}

export default App;
