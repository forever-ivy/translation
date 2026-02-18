import { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Sidebar } from "@/components/layout/Sidebar";
import { useAppStore } from "@/stores/appStore";
import { ToastContainer } from "@/components/ui/toast";
import { Dashboard } from "@/pages/Dashboard";
import { Services } from "@/pages/Services";
import { Jobs } from "@/pages/Jobs";
import { Verify } from "@/pages/Verify";
import { Logs } from "@/pages/Logs";
import { Settings } from "@/pages/Settings";
import { KBHealth } from "@/pages/KBHealth";
import { ApiConfig } from "@/pages/ApiConfig";

const pageVariants = {
  initial: { opacity: 0, y: 8 },
  enter: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 }
};

const pageTransition = {
  type: "tween" as const,
  duration: 0.2,
  ease: [0.4, 0, 0.2, 1] as const
};

function App() {
  const { activeTab, theme, toasts, dismissToast } = useAppStore();

  useEffect(() => {
    // Apply theme class to document
    const applyTheme = () => {
      const isDark =
        theme === "dark" ||
        (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);

      document.documentElement.classList.toggle("dark", isDark);
    };

    applyTheme();

    // Listen for system theme changes
    if (theme === "system") {
      const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
      const handleChange = () => applyTheme();
      mediaQuery.addEventListener("change", handleChange);
      return () => mediaQuery.removeEventListener("change", handleChange);
    }
  }, [theme]);

  const renderPage = () => {
    switch (activeTab) {
      case "dashboard": return <Dashboard />;
      case "services": return <Services />;
      case "jobs": return <Jobs />;
      case "verify": return <Verify />;
      case "logs": return <Logs />;
      case "settings": return <Settings />;
      case "kb-health": return <KBHealth />;
      case "api-config": return <ApiConfig />;
      default: return <Dashboard />;
    }
  };

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto overflow-x-hidden overscroll-none bg-gradient-to-br from-background via-background to-accent/5">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            variants={pageVariants}
            initial="initial"
            animate="enter"
            exit="exit"
            transition={pageTransition}
          >
            {renderPage()}
          </motion.div>
        </AnimatePresence>
      </main>
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}

export default App;
