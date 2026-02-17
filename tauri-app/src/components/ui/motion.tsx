import { motion, type HTMLMotionProps } from "framer-motion";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// Card with hover scale and shadow effect
export function MotionCard({
  children,
  ...props
}: { children: ReactNode } & HTMLMotionProps<"div">) {
  return (
    <motion.div
      whileHover={{ scale: 1.01, boxShadow: "0 8px 30px rgba(0,0,0,0.25)" }}
      whileTap={{ scale: 0.99 }}
      transition={{ duration: 0.2 }}
      {...props}
    >
      {children}
    </motion.div>
  );
}

// Glass card with optional glow color
export function GlassMotionCard({
  children,
  glowColor,
  className,
  ...props
}: { children: ReactNode; glowColor?: "teal" | "green" | "yellow" | "red" } & HTMLMotionProps<"div">) {
  const glowClass = glowColor ? `glow-${glowColor}` : "";

  return (
    <motion.div
      whileHover={{ scale: 1.01, boxShadow: "0 8px 40px rgba(0,0,0,0.3)" }}
      whileTap={{ scale: 0.99 }}
      transition={{ duration: 0.2 }}
      className={cn(glowClass, className)}
      {...props}
    >
      {children}
    </motion.div>
  );
}

// Stagger container for list animations
export const staggerContainer = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.08 }
  }
};

// Stagger item for list animations
export const staggerItem = {
  hidden: { opacity: 0, y: 15 },
  show: { opacity: 1, y: 0, transition: { duration: 0.25 } }
};

// Number/counter animation component
export function CountUp({
  value,
  suffix = "",
  className = ""
}: {
  value: number | string;
  suffix?: string;
  className?: string
}) {
  return (
    <motion.span
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      key={String(value)}
      className={className}
    >
      {value}{suffix}
    </motion.span>
  );
}

// Status pulse indicator with color and glow
export function StatusPulse({
  color,
  glow = false,
  className = ""
}: {
  color: "green" | "yellow" | "red" | "gray";
  glow?: boolean;
  className?: string
}) {
  const colorClasses = {
    green: "bg-green-500",
    yellow: "bg-yellow-500",
    red: "bg-red-500",
    gray: "bg-gray-400"
  };

  const glowClasses = {
    green: "glow-green",
    yellow: "glow-yellow",
    red: "glow-red",
    gray: ""
  };

  return (
    <motion.div
      className={cn(
        "w-2 h-2 rounded-full",
        colorClasses[color],
        glow && glowClasses[color],
        className
      )}
      animate={glow ? { scale: [1, 1.3, 1], opacity: [1, 0.6, 1] } : {}}
      transition={glow ? { duration: 2, repeat: Infinity, ease: "easeInOut" } : {}}
    />
  );
}

// Button with press feedback
export function MotionButton({
  children,
  ...props
}: { children: ReactNode } & HTMLMotionProps<"button">) {
  return (
    <motion.button
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.96 }}
      transition={{ duration: 0.15 }}
      {...props}
    >
      {children}
    </motion.button>
  );
}

// Fade in on mount wrapper
export function FadeIn({
  children,
  delay = 0,
  className = ""
}: {
  children: ReactNode;
  delay?: number;
  className?: string
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

// Slide in from bottom
export function SlideIn({
  children,
  delay = 0,
  className = ""
}: {
  children: ReactNode;
  delay?: number;
  className?: string
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay, ease: "easeOut" }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

// Rotating loading spinner animation
export function SpinLoader({
  className = ""
}: {
  className?: string
}) {
  return (
    <motion.div
      animate={{ rotate: 360 }}
      transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
      className={className}
    />
  );
}

// Progress bar animation
export function AnimatedProgress({
  value,
  className = ""
}: {
  value: number;
  className?: string
}) {
  return (
    <motion.div
      className={cn("h-2 bg-primary rounded-full", className)}
      initial={{ width: 0 }}
      animate={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      transition={{ duration: 0.5, ease: "easeOut" }}
    />
  );
}
